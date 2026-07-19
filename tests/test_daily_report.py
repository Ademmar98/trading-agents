"""Tests for the daily desk report and the loss post-mortem engine."""
from datetime import datetime, timezone

import pytest

from core.database import init_db, execute, save_message, set_agent_state
from core.daily_report import (
    build_daily_report, render_markdown, render_telegram,
    generate_daily_report, save_daily_report, load_report, list_report_dates,
)
from core.positions import PositionManager
from core.trade_postmortem import analyze_trade, summarize_postmortems


TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
BASE_TS = 1_700_000_000   # any stable, positive epoch


_TABLES = ("trades", "positions", "equity_history", "agent_messages", "agent_state")


@pytest.fixture(autouse=True)
def _clean_db():
    init_db()
    for table in _TABLES:
        execute(f"DELETE FROM {table}")
    yield
    # Leave the shared test DB as we found it — seeded losses/equity rows
    # would otherwise trip other modules' compliance and daily-loss checks.
    for table in _TABLES:
        execute(f"DELETE FROM {table}")


def _bars(seq, start_ts=BASE_TS, step=300):
    """Bars from (open, high, low, close) tuples, step seconds apart."""
    return [{
        "open": o, "high": h, "low": l, "close": c,
        "ts": start_ts + i * step,
        "date": datetime.fromtimestamp(start_ts + i * step,
                                       tz=timezone.utc).isoformat(),
    } for i, (o, h, l, c) in enumerate(seq)]


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _loss_trade(**over):
    t = {
        "symbol": "BTC/USD", "side": "BUY", "qty": 1.0, "strategy": "trend",
        "entry_price": 100.0, "exit_price": 97.0, "pnl": -3.2, "pnl_pct": -3.2,
        "reason": "stop_loss",
        "opened_at": _iso(BASE_TS + 15 * 300),
        "closed_at": _iso(BASE_TS + 17 * 300),
    }
    t.update(over)
    return t


# ────────────────────────── post-mortems ──────────────────────────
def test_postmortem_sl_too_tight_when_tp_hit_after_stop():
    """Stopped out at 97, then price rallies through the original TP —
    the diagnosis must be a too-tight stop, with an SL_VOL_MULT suggestion."""
    flat = [(100, 100.5, 99.5, 100)] * 15                  # pre-entry (ATR)
    drop = [(100, 100.2, 98.0, 99), (99, 99.5, 97.2, 98), (98, 98.2, 96.8, 97)]
    recover = [(97, 99, 96.5, 98.5), (98.5, 103, 98, 102), (102, 106.5, 101, 106)]
    bars = _bars(flat + drop + recover)
    pm = analyze_trade(_loss_trade(),
                       position={"stop_loss": 97.0, "take_profit": 106.0},
                       bars=bars)
    assert pm["verdict"] == "SL_TOO_TIGHT"
    assert pm["evidence"]["tp_hit_after_exit"] is True
    assert "SL_VOL_MULT" in pm["suggestion"]


def test_postmortem_bad_entry_when_price_never_moves_in_favor():
    flat = [(100, 100.5, 99.5, 100)] * 15
    drop = [(100, 100.05, 98.0, 98.2), (98.2, 98.3, 97.0, 97.1),
            (97.1, 97.2, 96.8, 97.0)]
    stay_down = [(97, 97.3, 96, 96.5)] * 6                 # never recovers
    bars = _bars(flat + drop + stay_down)
    pm = analyze_trade(_loss_trade(),
                       position={"stop_loss": 97.0, "take_profit": 106.0},
                       bars=bars)
    assert pm["verdict"] == "BAD_ENTRY"
    assert "entry" in " ".join(pm["diagnosis"]).lower()


def test_postmortem_wrong_signal_when_price_keeps_falling():
    flat = [(100, 100.5, 99.5, 100)] * 15
    # Meaningful favorable move first (mfe > 25% of SL distance), then a slide
    # with no recovery: direction call was wrong, not the geometry.
    move = [(100, 102.0, 99.0, 101), (101, 101.5, 98.0, 98.5),
            (98.5, 98.6, 96.9, 97.0)]
    down = [(97, 97.1, 92, 93)] * 6
    bars = _bars(flat + move + down)
    pm = analyze_trade(_loss_trade(),
                       position={"stop_loss": 97.0, "take_profit": 106.0},
                       bars=bars)
    assert pm["verdict"] == "WRONG_SIGNAL"


def test_postmortem_fee_eaten_gross_winner():
    pm = analyze_trade(
        _loss_trade(exit_price=100.05, pnl=-0.15, pnl_pct=-0.15,
                    reason="take_profit"),
        position={"stop_loss": 97.0, "take_profit": 100.05},
        bars=[])
    assert pm["verdict"] == "FEE_EATEN"
    assert "MIN_TP_PCT" in pm["suggestion"]


def test_postmortem_breakeven_stop():
    flat = [(100, 100.5, 99.5, 100)] * 15
    up_then_back = [(100, 103, 99.9, 102), (102, 102.5, 99.9, 100.0)]
    bars = _bars(flat + up_then_back)
    pm = analyze_trade(
        _loss_trade(exit_price=100.0, pnl=-0.2, pnl_pct=-0.2,
                    closed_at=_iso(BASE_TS + 16 * 300)),
        position={"stop_loss": 100.0, "take_profit": 106.0},   # moved to entry
        bars=bars)
    assert pm["verdict"] == "BREAKEVEN_STOP"


def test_postmortem_no_data():
    pm = analyze_trade(_loss_trade(), position={"stop_loss": 97.0}, bars=[])
    assert pm["verdict"] == "NO_DATA"


def test_summarize_postmortems_groups_by_strategy():
    reports = [
        {"strategy": "trend", "verdict": "SL_TOO_TIGHT", "pnl": -3},
        {"strategy": "trend", "verdict": "SL_TOO_TIGHT", "pnl": -2},
        {"strategy": "trend", "verdict": "BAD_ENTRY", "pnl": -1},
        {"strategy": "meanrev", "verdict": "WRONG_SIGNAL", "pnl": -4},
    ]
    summary = summarize_postmortems(reports)
    assert summary["trend"]["losses"] == 3
    assert summary["trend"]["dominant"] == "SL_TOO_TIGHT"
    assert summary["meanrev"]["total_pnl"] == -4


# ────────────────────────── the daily report ──────────────────────────
def _seed_day():
    pm = PositionManager()
    win_id = pm.open_position("BTC/USD", "BUY", 1.0, 100.0,
                              sl=95.0, tp=110.0, strategy="alpha")
    pm.close_position(win_id, 110.0, reason="take_profit")
    loss_id = pm.open_position("ETH/USD", "BUY", 1.0, 100.0,
                               sl=97.0, tp=106.0, strategy="beta")
    pm.close_position(loss_id, 97.0, reason="stop_loss")
    # Equity snapshots: two a minute apart, then a 10-minute engine stall.
    for offset in (0, 60, 660):
        execute(
            "INSERT INTO equity_history (equity, cash, positions_value, "
            "exposure_pct, snapped_at) VALUES (?, ?, ?, ?, "
            "datetime('now', ?))",
            [10000 + offset, 9000.0, 1000.0, 10.0, f"-{700 - offset} seconds"])
    # One deliberation on the desk.
    save_message("m1", "trade.proposal", "analyst",
                 {"symbol": "BTC/USD", "action": "BUY"}, correlation_id="prop_x")
    save_message("m2", "trade.verdict", "orchestrator",
                 {"decision": "approved", "rounds": 2,
                  "tally": {"risk_manager": {"stance": "approve"},
                            "compliance": {"stance": "approve"}},
                  "vetoes": []},
                 correlation_id="prop_x")
    set_agent_state("auditor", {"reviewer_weights": {"risk_manager": 1.1}})


def test_build_daily_report_sections():
    _seed_day()
    report = build_daily_report(TODAY, bars_by_symbol={"ETH/USD": []})

    assert report["summary"]["trades_closed"] == 2
    assert len(report["trades"]) == 2
    day = report["strategies"]["day"]
    assert day["alpha"]["wins"] == 1 and day["alpha"]["pnl"] > 0
    assert day["beta"]["losses"] == 1 and day["beta"]["pnl"] < 0
    assert report["deliberations"]["total"] == 1
    assert report["deliberations"]["decisions"]["approved"] == 1
    assert report["agents"]["auditor"]["reviewer_weights"] == {"risk_manager": 1.1}
    assert len(report["loss_postmortems"]) == 1
    assert report["loss_postmortems"][0]["symbol"] == "ETH/USD"
    assert report["loss_summary"]["beta"]["losses"] == 1
    assert any(s["gap_s"] >= 500 for s in report["delays"]["engine_stalls"])


def test_render_markdown_and_telegram():
    _seed_day()
    report = build_daily_report(TODAY, bars_by_symbol={"ETH/USD": []})
    md = render_markdown(report)
    assert f"Daily desk report — {TODAY}" in md
    assert "Why the losers lost" in md
    assert "ETH/USD" in md
    tg = render_telegram(report)
    assert len(tg) <= 3800
    assert "Losses — why" in tg
    assert "Issues" in tg


def test_generate_saves_files_and_notifies():
    _seed_day()

    class FakeNotifier:
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)

    notifier = FakeNotifier()
    report = generate_daily_report(TODAY, notifier=notifier)
    assert report is not None
    assert notifier.sent and "Daily desk report" in notifier.sent[0]
    assert TODAY in list_report_dates()
    loaded = load_report(TODAY)
    assert loaded["summary"]["trades_closed"] == 2
    assert load_report() is not None   # latest fallback


def test_generate_never_raises(monkeypatch):
    """A broken report must not break the trading cycle."""
    monkeypatch.setattr("core.daily_report.build_daily_report",
                        lambda *a, **k: 1 / 0)

    class FakeNotifier:
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)

    notifier = FakeNotifier()
    assert generate_daily_report(TODAY, notifier=notifier) is None
    assert any("failed" in s for s in notifier.sent)


def test_missing_section_reports_unanalyzed_symbols():
    report = build_daily_report(TODAY, bars_by_symbol={})
    # Sandboxed test dir has no market_scan report — every watched symbol
    # should surface as unanalyzed rather than silently vanishing.
    from config import WATCHED_SYMBOLS
    if WATCHED_SYMBOLS:
        assert report["missing"]["symbols_unanalyzed"]
