"""The desk's end-of-day report: everything that happened, everything that
didn't, and why every loss lost.

Sections
  summary          equity, day PnL, win rate (extends core.equity's numbers)
  trades           every trade closed that day, with duration
  strategies       per-strategy day performance + lifetime stats
  agents           per-agent counters, auditor trust weights, desk activity
  deliberations    counts, outcomes, vetoes, durations, reviewer timeouts
  errors           errors.jsonl grouped by source
  delays           engine stalls (equity-snapshot gaps) + slow deliberations
  missing          stale/absent reports, unanalyzed symbols, store mismatches
  loss_postmortems per-loss diagnosis (see core.trade_postmortem)

Generated once per completed UTC day from main.maintenance_cycle, saved to
DATA_DIR/daily_reports/<date>.{json,md}, summary pushed through the Notifier.
(Own directory, not under reports/ — that dir belongs to SharedMemory, which
treats every entry in it as a flat report file.)
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DATA_DIR, WATCHED_SYMBOLS, TRADING_INTERVAL_MINUTES
from core.database import fetchall, get_strategy_stats_list
from core.equity import build_daily_summary
from core.memory import SharedMemory
from core.portfolio import load_portfolio
from core.trade_postmortem import (
    postmortems_for_day, summarize_postmortems, _parse_ts,
)

REPORT_DIR = DATA_DIR / "daily_reports"

# Report files the dashboard depends on, with the cadence they should refresh at.
EXPECTED_REPORTS = (
    ("analyses", "market_scan"),
    ("analyses", "sentiment_scan"),
    ("analyses", "regime_scan"),
    ("decisions", "risk_assessment"),
    ("decisions", "compliance_gate"),
    ("reports", "health"),
    ("reports", "audit"),
)


def _day_bounds(date_str):
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return day.timestamp(), (day + timedelta(days=1)).timestamp()


def _duration_str(opened_at, closed_at):
    a, b = _parse_ts(opened_at), _parse_ts(closed_at)
    if a is None or b is None or b < a:
        return ""
    mins = int((b - a) / 60)
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"


def _trades_section(date_str):
    rows = fetchall("SELECT * FROM trades WHERE date(closed_at) = ? "
                    "ORDER BY closed_at ASC", [date_str])
    trades = []
    for r in rows:
        t = dict(r)
        t["duration"] = _duration_str(t.get("opened_at"), t.get("closed_at"))
        trades.append(t)
    return trades


def _strategies_section(trades):
    day = {}
    for t in trades:
        s = day.setdefault(t.get("strategy") or "unknown", {
            "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "pnl_pcts": []})
        s["trades"] += 1
        pnl = float(t.get("pnl") or 0)
        s["pnl"] += pnl
        s["wins" if pnl > 0 else "losses"] += 1
        s["pnl_pcts"].append(float(t.get("pnl_pct") or 0))
    for name, s in day.items():
        s["pnl"] = round(s["pnl"], 2)
        s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0
        s["avg_pnl_pct"] = round(sum(s["pnl_pcts"]) / len(s["pnl_pcts"]), 2) if s["pnl_pcts"] else 0
        del s["pnl_pcts"]
    lifetime = {}
    try:
        for row in get_strategy_stats_list():
            d = dict(row)
            lifetime[d.get("strategy") or "unknown"] = {
                "trades": d.get("trades"), "win_rate": d.get("win_rate"),
                "pnl": d.get("pnl"),
            }
    except Exception:
        pass
    return {"day": day, "lifetime": lifetime}


def _agents_section(date_str):
    start, end = _day_bounds(date_str)
    agents = {}
    for row in fetchall("SELECT agent, state, updated_at FROM agent_state"):
        try:
            state = json.loads(row["state"])
        except (ValueError, TypeError):
            state = {}
        keep = {k: v for k, v in state.items()
                if isinstance(v, (int, float)) and k != "last_attribution_id"}
        agents[row["agent"]] = {"counters": keep, "updated_at": row["updated_at"]}
        if row["agent"] == "auditor":
            agents["auditor"]["reviewer_weights"] = state.get("reviewer_weights", {})
    msgs = fetchall(
        "SELECT sender, COUNT(*) AS n FROM agent_messages "
        "WHERE created_at >= datetime(?, 'unixepoch') AND created_at < datetime(?, 'unixepoch') "
        "GROUP BY sender", [start, end])
    for m in msgs:
        agents.setdefault(m["sender"], {}).setdefault("counters", {})
        agents[m["sender"]]["messages_today"] = m["n"]
    return agents


def _deliberations_section(date_str):
    start, end = _day_bounds(date_str)
    rows = fetchall(
        "SELECT correlation_id, topic, payload, created_at FROM agent_messages "
        "WHERE correlation_id IS NOT NULL "
        "AND created_at >= datetime(?, 'unixepoch') AND created_at < datetime(?, 'unixepoch') "
        "ORDER BY id ASC", [start, end])
    threads = {}
    for r in rows:
        t = threads.setdefault(r["correlation_id"], {"first": None, "verdict": None,
                                                     "verdict_at": None, "topics": 0})
        t["topics"] += 1
        if t["first"] is None:
            t["first"] = r["created_at"]
        if r["topic"].endswith(".verdict"):
            try:
                t["verdict"] = json.loads(r["payload"])
            except (ValueError, TypeError):
                t["verdict"] = {}
            t["verdict_at"] = r["created_at"]

    decisions = {"approved": 0, "rejected": 0}
    vetoes_by = {}
    timeouts_by = {}
    durations, rounds = [], []
    reviewer_universe = set()
    verdicts = [t for t in threads.values() if t["verdict"]]
    for t in verdicts:
        reviewer_universe.update((t["verdict"].get("tally") or {}).keys())
    for t in verdicts:
        v = t["verdict"]
        decisions[v.get("decision", "rejected")] = decisions.get(v.get("decision", "rejected"), 0) + 1
        for name in v.get("vetoes") or []:
            vetoes_by[name] = vetoes_by.get(name, 0) + 1
        # A reviewer we know exists but absent from this tally never answered.
        for name in reviewer_universe - set((v.get("tally") or {}).keys()):
            timeouts_by[name] = timeouts_by.get(name, 0) + 1
        rounds.append(v.get("rounds") or 0)
        a, b = _parse_ts(t["first"]), _parse_ts(t["verdict_at"])
        if a is not None and b is not None and b >= a:
            durations.append(b - a)
    return {
        "total": len(verdicts),
        "decisions": decisions,
        "vetoes_by": vetoes_by,
        "review_timeouts_by": timeouts_by,
        "avg_rounds": round(sum(rounds) / len(rounds), 2) if rounds else 0,
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else 0,
        "max_duration_s": round(max(durations), 1) if durations else 0,
    }


def _errors_section(date_str):
    start, end = _day_bounds(date_str)
    memory = SharedMemory()
    by_source = {}
    for e in memory.get_recent_errors(500):
        ts = e.get("time") or 0
        if not (start <= ts < end):
            continue
        s = by_source.setdefault(e.get("source") or "unknown", {
            "count": 0, "last_message": "", "last_at": ""})
        s["count"] += 1
        s["last_message"] = (e.get("message") or "")[:300]
        s["last_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
    return by_source


def _delays_section(date_str, deliberations):
    """Engine stalls show up as gaps between equity snapshots (one per
    cycle); slow deliberations come from the message timestamps."""
    rows = fetchall(
        "SELECT snapped_at FROM equity_history WHERE date(snapped_at) = ? "
        "ORDER BY id ASC", [date_str])
    expected = TRADING_INTERVAL_MINUTES * 60
    stalls = []
    prev = None
    for r in rows:
        ts = _parse_ts(r["snapped_at"])
        if prev is not None and ts is not None and ts - prev > expected * 3:
            stalls.append({
                "at": datetime.fromtimestamp(prev, tz=timezone.utc).strftime("%H:%M:%S"),
                "gap_s": int(ts - prev),
            })
        prev = ts
    out = {"engine_stalls": stalls[:20], "snapshots": len(rows)}
    if deliberations.get("max_duration_s", 0) > 60:
        out["slow_deliberations"] = {
            "max_s": deliberations["max_duration_s"],
            "avg_s": deliberations["avg_duration_s"],
        }
    if deliberations.get("review_timeouts_by"):
        out["review_timeouts_by"] = deliberations["review_timeouts_by"]
    return out


def _missing_section():
    memory = SharedMemory()
    now = datetime.now(timezone.utc).timestamp()
    missing, stale = [], []
    for category, name in EXPECTED_REPORTS:
        data = memory.read(category, name)
        if not data:
            missing.append(f"{category}/{name}")
        elif now - (data.get("_timestamp") or 0) > 24 * 3600:
            age_h = (now - data["_timestamp"]) / 3600
            stale.append(f"{category}/{name} ({age_h:.0f}h old)")
    scan = memory.read("analyses", "market_scan") or {}
    analyzed = set((scan.get("all_analyses") or {}).keys())
    unanalyzed = [s for s in WATCHED_SYMBOLS if s not in analyzed]
    # Store desync: SQLite open positions vs the JSON portfolio.
    db_open = {r["symbol"] for r in fetchall(
        "SELECT symbol FROM positions WHERE status='open'")}
    try:
        json_open = set(load_portfolio().positions.keys())
    except Exception:
        json_open = set()
    desync = sorted(db_open.symmetric_difference(json_open))
    return {
        "reports_missing": missing,
        "reports_stale": stale,
        "symbols_unanalyzed": unanalyzed,
        "position_store_mismatch": desync,
    }


def _fill_diagnostics_section():
    """Limit-fill microstructure metrics for the report (empty when the maker
    path is off or no quotes rested)."""
    try:
        from core.fill_monitor import diagnostics
        return diagnostics(days=1)
    except Exception:
        return {"per_symbol": [], "totals": {"total_quotes": 0}, "window_days": 1}


def build_daily_report(date_str, bars_by_symbol=None):
    trades = _trades_section(date_str)
    deliberations = _deliberations_section(date_str)
    postmortems = postmortems_for_day(date_str, bars_by_symbol=bars_by_symbol)
    report = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": build_daily_summary(date_str),
        "trades": trades,
        "strategies": _strategies_section(trades),
        "agents": _agents_section(date_str),
        "deliberations": deliberations,
        "errors": _errors_section(date_str),
        "delays": _delays_section(date_str, deliberations),
        "missing": _missing_section(),
        "loss_postmortems": postmortems,
        "loss_summary": summarize_postmortems(postmortems),
        "fill_diagnostics": _fill_diagnostics_section(),
    }
    return report


# ── rendering ──
def render_markdown(report):
    s = report["summary"]
    lines = [
        f"# Daily desk report — {report['date']}",
        "",
        f"**Equity** ${s['equity']:,.2f}  |  **Day** {s['day_pnl_pct']:+.2f}%  |  "
        f"**All-time** {s['total_pnl_pct']:+.2f}%",
        f"**Closed trades** {s['trades_closed']}  |  **Win rate** {s['win_rate']:.0f}%  |  "
        f"**Realized** ${s['pnl_closed']:+,.2f}  |  **Open positions** {s['open_positions']}",
        f"**Net expectancy/trade** ${s.get('net_expectancy_usd', 0):+.2f} today  |  "
        f"${s.get('net_expectancy_all_usd', 0):+.2f} all-time "
        f"(n={s.get('net_expectancy_all_n', 0)}) — positive is the edge bar",
        "",
        "## Trades",
    ]
    if report["trades"]:
        lines.append("| symbol | side | strategy | entry | exit | pnl | pnl% | held | exit reason |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for t in report["trades"]:
            lines.append(
                f"| {t['symbol']} | {t['side']} | {t.get('strategy') or '—'} "
                f"| {t['entry_price']:.5g} | {t['exit_price']:.5g} "
                f"| {t['pnl']:+.2f} | {t['pnl_pct']:+.2f}% | {t.get('duration') or '—'} "
                f"| {t.get('reason') or '—'} |")
    else:
        lines.append("_No trades closed._")

    lines += ["", "## Strategies (today)"]
    day = report["strategies"]["day"]
    if day:
        lines.append("| strategy | trades | win rate | pnl | avg pnl% | lifetime WR |")
        lines.append("|---|---|---|---|---|---|")
        life = report["strategies"]["lifetime"]
        for name, st in sorted(day.items(), key=lambda kv: kv[1]["pnl"]):
            lw = life.get(name, {}).get("win_rate")
            lines.append(
                f"| {name} | {st['trades']} | {st['win_rate']:.0f}% | {st['pnl']:+.2f} "
                f"| {st['avg_pnl_pct']:+.2f}% | {lw if lw is not None else '—'} |")
    else:
        lines.append("_No strategy activity._")

    lines += ["", "## Why the losers lost"]
    if report["loss_postmortems"]:
        for p in report["loss_postmortems"]:
            lines.append(
                f"- **{p['symbol']} {p['side']}** ({p.get('strategy') or '?'}, "
                f"{p['pnl']:+.2f}) — **{p['verdict']}**: "
                + "; ".join(p["diagnosis"]))
            if p.get("suggestion"):
                lines.append(f"  - fix: {p['suggestion']}")
    else:
        lines.append("_No losing trades._")

    d = report["deliberations"]
    lines += [
        "", "## Desk activity",
        f"- deliberations: {d['total']} "
        f"(approved {d['decisions'].get('approved', 0)}, "
        f"rejected {d['decisions'].get('rejected', 0)}), "
        f"avg {d['avg_rounds']} rounds, avg {d['avg_duration_s']}s",
    ]
    if d["vetoes_by"]:
        lines.append("- vetoes: " + ", ".join(f"{k}×{v}" for k, v in d["vetoes_by"].items()))
    if d["review_timeouts_by"]:
        lines.append("- reviewers that missed deadlines: "
                     + ", ".join(f"{k}×{v}" for k, v in d["review_timeouts_by"].items()))
    weights = (report["agents"].get("auditor") or {}).get("reviewer_weights") or {}
    if weights:
        lines.append("- earned voting weights: "
                     + ", ".join(f"{k}={v:.2f}" for k, v in sorted(weights.items())))

    fd = report.get("fill_diagnostics") or {}
    ft = fd.get("totals") or {}
    if ft.get("total_quotes"):
        lines += ["", "## Limit-fill execution (maker path)"]
        adv = ft.get("adverse_1m_pct")
        lines.append(
            f"- {ft['total_quotes']} quotes, fill rate {ft['fill_rate_pct']:.0f}%, "
            f"avg time-to-fill {ft.get('avg_time_to_fill_s') or '—'}s, "
            f"adverse-selection 1m {adv:+.2f}% " if adv is not None else
            f"- {ft['total_quotes']} quotes, fill rate {ft['fill_rate_pct']:.0f}%")
        lines.append(f"- net spread saved: ${ft.get('net_spread_saved_usd', 0):+.2f}"
                     + ("  |  ⚠ adverse-selection throttle ACTIVE" if ft.get("throttle_active") else ""))

    lines += ["", "## Problems"]
    problems = False
    for src, e in report["errors"].items():
        problems = True
        lines.append(f"- errors[{src}]: {e['count']}× (last {e['last_at']}: {e['last_message'][:120]})")
    for stall in report["delays"].get("engine_stalls", []):
        problems = True
        lines.append(f"- engine stall: {stall['gap_s']}s at {stall['at']} UTC")
    m = report["missing"]
    for item in m["reports_missing"]:
        problems = True
        lines.append(f"- missing report: {item}")
    for item in m["reports_stale"]:
        problems = True
        lines.append(f"- stale report: {item}")
    if m["symbols_unanalyzed"]:
        problems = True
        lines.append("- symbols with no analysis: " + ", ".join(m["symbols_unanalyzed"]))
    if m["position_store_mismatch"]:
        problems = True
        lines.append("- position store mismatch (SQLite vs portfolio.json): "
                     + ", ".join(m["position_store_mismatch"]))
    if not problems:
        lines.append("_None detected._")
    return "\n".join(lines) + "\n"


def render_telegram(report):
    """Compact summary for Telegram (~<3500 chars); the full story lives in
    the saved markdown/JSON and the dashboard."""
    s = report["summary"]
    sign = "+" if s["day_pnl_pct"] >= 0 else ""
    out = [
        f"<b>Daily desk report — {report['date']}</b>",
        f"Equity ${s['equity']:,.2f} ({sign}{s['day_pnl_pct']:.2f}% today, "
        f"{s['total_pnl_pct']:+.2f}% all-time)",
        f"Trades {s['trades_closed']} | WR {s['win_rate']:.0f}% | "
        f"P&L ${s['pnl_closed']:+,.2f} | Open {s['open_positions']}",
        f"Net exp/trade ${s.get('net_expectancy_usd', 0):+.2f} today, "
        f"${s.get('net_expectancy_all_usd', 0):+.2f} all-time (n={s.get('net_expectancy_all_n', 0)})",
    ]
    day = report["strategies"]["day"]
    if day:
        out.append("")
        out.append("<b>Strategies</b>")
        for name, st in sorted(day.items(), key=lambda kv: kv[1]["pnl"])[:6]:
            out.append(f"• {name}: {st['trades']}t, WR {st['win_rate']:.0f}%, "
                       f"{st['pnl']:+.2f}")
    if report["loss_postmortems"]:
        out.append("")
        out.append("<b>Losses — why</b>")
        for p in report["loss_postmortems"][:5]:
            out.append(f"• {p['symbol']} {p['side']} {p['pnl']:+.2f} — {p['verdict']}")
            if p.get("suggestion"):
                out.append(f"   fix: {p['suggestion'][:120]}")
        extra = len(report["loss_postmortems"]) - 5
        if extra > 0:
            out.append(f"   …and {extra} more in the full report")
    issues = []
    err_total = sum(e["count"] for e in report["errors"].values())
    if err_total:
        issues.append(f"{err_total} errors ({', '.join(list(report['errors'])[:3])})")
    if report["delays"].get("engine_stalls"):
        issues.append(f"{len(report['delays']['engine_stalls'])} engine stalls")
    if report["delays"].get("review_timeouts_by"):
        issues.append("review timeouts: " + ", ".join(
            f"{k}×{v}" for k, v in report["delays"]["review_timeouts_by"].items()))
    m = report["missing"]
    if m["reports_missing"] or m["reports_stale"]:
        issues.append(f"{len(m['reports_missing']) + len(m['reports_stale'])} reports missing/stale")
    if m["symbols_unanalyzed"]:
        issues.append(f"{len(m['symbols_unanalyzed'])} symbols unanalyzed")
    if m["position_store_mismatch"]:
        issues.append("position store mismatch: " + ", ".join(m["position_store_mismatch"][:3]))
    out.append("")
    out.append("<b>Issues</b>: " + ("; ".join(issues) if issues else "none"))
    d = report["deliberations"]
    if d["total"]:
        out.append(f"Desk: {d['total']} deliberations, "
                   f"{d['decisions'].get('approved', 0)} approved, "
                   f"avg {d['avg_duration_s']}s")
    out.append(f"Full report: data/daily_reports/{report['date']}.md")
    text = "\n".join(out)
    return text[:3800]


def save_daily_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"{report['date']}.json"
    md_path = REPORT_DIR / f"{report['date']}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def generate_daily_report(date_str, notifier=None):
    """Build, persist, and deliver the report for one completed day.
    Never raises — a broken report must not break the trading cycle."""
    try:
        report = build_daily_report(date_str)
        save_daily_report(report)
        if notifier is not None:
            notifier.send(render_telegram(report))
        return report
    except Exception as e:
        SharedMemory().log_error("daily_report", str(e))
        if notifier is not None:
            try:
                notifier.send(f"<b>Daily report failed for {date_str}:</b> {e}")
            except Exception:
                pass
        return None


def list_report_dates():
    if not REPORT_DIR.exists():
        return []
    return sorted(p.stem for p in REPORT_DIR.glob("*.json"))


def load_report(date_str=None):
    dates = list_report_dates()
    if not dates:
        return None
    target = date_str if date_str in dates else (date_str or dates[-1])
    path = REPORT_DIR / f"{target}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
