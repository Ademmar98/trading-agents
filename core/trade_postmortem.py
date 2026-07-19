"""Why did a losing trade lose?

Each loss is reconstructed against the market data around it and classified:

  SL_TOO_TIGHT    stopped out, then price came back and reached the original
                  TP (or reclaimed the entry) — the stop sat inside noise
  BREAKEVEN_STOP  the breakeven stop fired: the trade was up, gave it back
  BAD_ENTRY       price never moved in favor — the entry was mistimed
  TP_TOO_FAR      price ran most of the way to the target but never touched it
  WRONG_SIGNAL    price went straight against the trade and stayed there
  FEE_EATEN       gross PnL was positive; round-trip fees turned it into a loss
  NO_DATA         market data unavailable — no diagnosis possible

Every verdict carries the evidence (MFE/MAE, ATR ratios, bars-to-exit) and a
concrete suggestion tied to the tunable that would have changed the outcome.
Pure functions over injected bars — network only in the fetch helper.
"""
from datetime import datetime, timezone

from config import TRADING_TIMEFRAME, TRADE_FEE_PCT
from core.database import fetchall, fetchone


def _parse_ts(value):
    """Epoch seconds from an ISO string or 'YYYY-MM-DD HH:MM:SS' (both occur
    in the trades table). Naive timestamps are treated as UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _bar_ts(bar):
    ts = bar.get("ts")
    if ts:
        return float(ts)
    return _parse_ts(bar.get("date")) or 0.0


def _atr_pct(bars, period=14):
    """Average true range as % of the last close, over the given bars."""
    if len(bars) < 2:
        return 0.0
    trs = []
    for prev, cur in zip(bars[-period - 1:-1], bars[-period:]):
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    close = bars[-1]["close"] or 1e-9
    return (sum(trs) / len(trs)) / close * 100 if trs else 0.0


def fetch_trade_bars(symbol, limit=600):
    """Recent bars at the trading timeframe; [] on any failure."""
    try:
        from core.backtester import fetch_klines
        return fetch_klines(symbol, interval=TRADING_TIMEFRAME, limit=limit) or []
    except Exception:
        return []


def analyze_trade(trade, position=None, bars=None, fee_pct=None):
    """Post-mortem one losing trade.

    trade     dict from the trades table (entry_price, exit_price, qty, side,
              reason, opened_at, closed_at, pnl, strategy)
    position  matching positions row (stop_loss / take_profit) or None
    bars      OHLC bars covering the trade window (injected in tests;
              fetched at TRADING_TIMEFRAME when None)
    """
    fee_pct = TRADE_FEE_PCT if fee_pct is None else fee_pct
    side = (trade.get("side") or "BUY").upper()
    entry = float(trade.get("entry_price") or 0)
    exit_price = float(trade.get("exit_price") or 0)
    qty = float(trade.get("qty") or 0)
    reason = (trade.get("reason") or "").lower()
    sl = float((position or {}).get("stop_loss") or 0)
    tp = float((position or {}).get("take_profit") or 0)

    result = {
        "symbol": trade.get("symbol"),
        "strategy": trade.get("strategy") or "",
        "side": side,
        "entry": entry, "exit": exit_price, "sl": sl, "tp": tp,
        "qty": qty,
        "pnl": trade.get("pnl"),
        "pnl_pct": trade.get("pnl_pct"),
        "reason": trade.get("reason"),
        "opened_at": trade.get("opened_at"),
        "closed_at": trade.get("closed_at"),
        "verdict": "NO_DATA",
        "diagnosis": [],
        "suggestion": "",
        "evidence": {},
    }
    if entry <= 0 or qty <= 0:
        result["diagnosis"].append("trade record incomplete — cannot analyze")
        return result

    # Fees first: a gross winner that nets negative is a cost problem, not a
    # signal problem, whatever the exit reason says.
    gross = (exit_price - entry) * qty if side == "BUY" else (entry - exit_price) * qty
    fees = (entry + exit_price) * qty * (fee_pct / 100.0)
    result["evidence"]["gross_pnl"] = round(gross, 4)
    result["evidence"]["fees"] = round(fees, 4)
    if gross > 0 and (trade.get("pnl") or 0) < 0:
        result["verdict"] = "FEE_EATEN"
        result["diagnosis"].append(
            f"gross PnL was +{gross:.2f} but fees took {fees:.2f} — the move "
            "was right, the target was too small for the cost of trading")
        result["suggestion"] = ("TP too small vs round-trip fees — raise "
                                "MIN_TP_PCT / TP_VOL_MULT or trade less often")
        return result

    if bars is None:
        bars = fetch_trade_bars(trade.get("symbol") or "")
    entry_ts = _parse_ts(trade.get("opened_at"))
    exit_ts = _parse_ts(trade.get("closed_at"))
    if not bars or entry_ts is None or exit_ts is None:
        result["diagnosis"].append("no market data for the trade window")
        result["suggestion"] = "re-run the post-mortem when the data feed is back"
        return result

    pre = [b for b in bars if _bar_ts(b) < entry_ts]
    during = [b for b in bars if entry_ts <= _bar_ts(b) <= exit_ts]
    after_all = [b for b in bars if _bar_ts(b) > exit_ts]
    lookahead = after_all[:max(12, min(2 * len(during), 100))]
    if not during and not lookahead:
        result["diagnosis"].append("no bars inside the trade window")
        result["suggestion"] = "re-run the post-mortem when the data feed is back"
        return result

    if side == "BUY":
        mfe = max((b["high"] for b in during), default=entry) - entry
        mae = entry - min((b["low"] for b in during), default=entry)
        tp_hit_after = tp and any(b["high"] >= tp for b in lookahead)
        entry_reclaimed = any(b["high"] >= entry for b in lookahead)
    else:
        mfe = entry - min((b["low"] for b in during), default=entry)
        mae = max((b["high"] for b in during), default=entry) - entry
        tp_hit_after = tp and any(b["low"] <= tp for b in lookahead)
        entry_reclaimed = any(b["low"] <= entry for b in lookahead)

    mfe_pct = mfe / entry * 100
    mae_pct = mae / entry * 100
    sl_dist_pct = abs(entry - sl) / entry * 100 if sl else 0.0
    tp_dist_pct = abs(tp - entry) / entry * 100 if tp else 0.0
    atr_at_entry = _atr_pct(pre) if len(pre) >= 2 else 0.0
    sl_atr = (sl_dist_pct / atr_at_entry) if atr_at_entry else 0.0
    result["evidence"].update({
        "mfe_pct": round(mfe_pct, 3), "mae_pct": round(mae_pct, 3),
        "sl_dist_pct": round(sl_dist_pct, 3), "tp_dist_pct": round(tp_dist_pct, 3),
        "atr_pct_at_entry": round(atr_at_entry, 3),
        "sl_atr_ratio": round(sl_atr, 2),
        "bars_in_trade": len(during), "bars_looked_ahead": len(lookahead),
        "tp_hit_after_exit": bool(tp_hit_after),
        "entry_reclaimed_after_exit": bool(entry_reclaimed),
    })

    stop_exit = "stop" in reason or reason in ("sl",)
    if stop_exit and sl and abs(sl - entry) / entry < 0.0015:
        result["verdict"] = "BREAKEVEN_STOP"
        result["diagnosis"].append(
            "the breakeven stop fired: the trade was in profit and gave it "
            "back to entry — the loss is just fees")
        result["suggestion"] = ("consider taking partial profit at breakeven "
                                "activation instead of only moving the stop")
    elif stop_exit:
        if tp_hit_after:
            result["verdict"] = "SL_TOO_TIGHT"
            result["diagnosis"].append(
                f"price hit the original TP within {len(lookahead)} bars AFTER "
                f"the stop-out — the stop ({sl_atr:.1f}x ATR) was inside noise")
            result["suggestion"] = (
                f"widen the stop: SL sat at {sl_atr:.1f}x ATR "
                f"({sl_dist_pct:.2f}% vs ATR {atr_at_entry:.2f}%) — raise "
                "SL_VOL_MULT for this regime")
        elif entry_reclaimed and mfe_pct > 0.2 * sl_dist_pct:
            result["verdict"] = "SL_TOO_TIGHT"
            result["diagnosis"].append(
                "price reclaimed the entry shortly after the stop-out — "
                "stopped by a wick, not by a trend change")
            result["suggestion"] = (
                f"widen the stop or wait for a close beyond SL: it sat at "
                f"{sl_atr:.1f}x ATR")
        elif (mfe_pct < 0.25 * sl_dist_pct) if sl_dist_pct else (mfe_pct < 0.1):
            result["verdict"] = "BAD_ENTRY"
            result["diagnosis"].append(
                f"price never moved in favor (best {mfe_pct:.2f}% vs SL "
                f"distance {sl_dist_pct:.2f}%) — the entry chased a move that "
                "was already over or fought the flow")
            result["suggestion"] = (
                f"strategy {result['strategy'] or '?'}: require pullback / "
                "confirmation before entry; check regime fit at entry time")
        else:
            result["verdict"] = "WRONG_SIGNAL"
            result["diagnosis"].append(
                f"price went {mae_pct:.2f}% against and stayed there — the "
                "direction call was wrong, not the trade geometry")
            result["suggestion"] = (
                f"review strategy {result['strategy'] or '?'} — the auditor "
                "weight / regime filter should demote it if this repeats")
        if atr_at_entry and sl_atr and sl_atr < 1.0 and result["verdict"] != "BREAKEVEN_STOP":
            result["diagnosis"].append(
                f"stop was only {sl_atr:.1f}x ATR — inside the noise band by "
                "construction")
    else:
        # Manual / end-of-day / desk_exit / trailing losses.
        if tp and mfe_pct >= 0.7 * tp_dist_pct > 0:
            result["verdict"] = "TP_TOO_FAR"
            result["diagnosis"].append(
                f"price covered {mfe_pct / tp_dist_pct * 100:.0f}% of the way "
                "to TP but never touched it before the exit")
            result["suggestion"] = ("trim TP_VOL_MULT or take partials at "
                                    "~70% of target")
        else:
            result["verdict"] = "WRONG_SIGNAL"
            result["diagnosis"].append(
                f"closed {result['reason'] or 'manually'} at a loss with best "
                f"excursion {mfe_pct:.2f}% — the setup never worked")
            result["suggestion"] = (
                f"review strategy {result['strategy'] or '?'} signal quality")
    return result


def postmortems_for_day(date_str, bars_by_symbol=None):
    """Post-mortem every losing trade closed on the given UTC day.

    bars_by_symbol lets tests (and the report cache) inject data; live use
    fetches per symbol once.
    """
    rows = fetchall(
        "SELECT * FROM trades WHERE date(closed_at) = ? AND pnl < 0 "
        "ORDER BY closed_at ASC", [date_str])
    reports = []
    cache = dict(bars_by_symbol or {})
    for row in rows:
        trade = dict(row)
        position = None
        if trade.get("position_id"):
            pos_row = fetchone("SELECT * FROM positions WHERE id=?",
                               [trade["position_id"]])
            position = dict(pos_row) if pos_row else None
        symbol = trade.get("symbol") or ""
        if symbol not in cache:
            cache[symbol] = fetch_trade_bars(symbol)
        reports.append(analyze_trade(trade, position=position, bars=cache[symbol]))
    return reports


def summarize_postmortems(reports):
    """Aggregate loss verdicts per strategy: dominant failure mode + counts."""
    by_strategy = {}
    for r in reports:
        s = by_strategy.setdefault(r.get("strategy") or "unknown", {
            "losses": 0, "total_pnl": 0.0, "verdicts": {}})
        s["losses"] += 1
        s["total_pnl"] += float(r.get("pnl") or 0)
        s["verdicts"][r["verdict"]] = s["verdicts"].get(r["verdict"], 0) + 1
    for s in by_strategy.values():
        s["total_pnl"] = round(s["total_pnl"], 2)
        s["dominant"] = max(s["verdicts"], key=s["verdicts"].get) if s["verdicts"] else ""
    return by_strategy
