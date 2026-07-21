"""fill_dashboard.py — lightweight terminal view of the live limit-fill metrics.

Reads the running firm's /api/fill-diagnostics (the integrated, live monitor in
core/fill_monitor.py) and prints the execution scorecard. No deps beyond stdlib.

Usage:
  python fill_dashboard.py                       # localhost:8080
  python fill_dashboard.py http://69.48.202.100  # a remote firm
  python fill_dashboard.py --json                # raw JSON
"""
import json
import sys
import urllib.request


def fetch(base, days=7):
    url = f"{base.rstrip('/')}/api/fill-diagnostics?days={days}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def _fmt_adv(v):
    return f"{v:+.2f}" if v is not None else "—"


def _line(sym, quotes, fill, ttf, adv, saved):
    return (f"  {sym:10s} {quotes:>7d} {fill:>6.1f} {str(ttf if ttf is not None else '—'):>10s} "
            f"{_fmt_adv(adv):>11s} {saved:>13.2f}")


def render(d):
    rows = d.get("per_symbol", [])
    t = d.get("totals", {})
    print(f"\n  Limit-fill execution — last {d.get('window_days', 7)}d"
          + ("   ! ADVERSE-SELECTION THROTTLE ACTIVE" if t.get("throttle_active") else ""))
    print("  " + "-" * 92)
    print(f"  {'Symbol':10s} {'Quotes':>7s} {'Fill%':>6s} {'AvgTTF(s)':>10s} "
          f"{'Adverse1m%':>11s} {'SpreadSaved$':>13s}")
    print("  " + "-" * 92)
    for r in rows:
        print(_line(r["symbol"], r["total_quotes"], r["fill_rate_pct"],
                    r["avg_time_to_fill_s"], r["adverse_1m_pct"], r["net_spread_saved_usd"]))
    print("  " + "-" * 92)
    print(_line("TOTAL", t.get("total_quotes", 0), t.get("fill_rate_pct", 0),
                t.get("avg_time_to_fill_s"), t.get("adverse_1m_pct"),
                t.get("net_spread_saved_usd", 0)))
    if not rows:
        print("  (no quotes rested yet in the window)")
    print()


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    base = args[0] if args else "http://127.0.0.1:8080"
    try:
        d = fetch(base)
    except Exception as e:
        print(f"could not reach {base}/api/fill-diagnostics: {e}")
        sys.exit(1)
    print(json.dumps(d, indent=2)) if as_json else render(d)


if __name__ == "__main__":
    main()
