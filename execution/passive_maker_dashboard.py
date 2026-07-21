"""passive_maker_dashboard.py — live terminal view of the passive maker engine.

Reads engine_diagnostics.json (written every loop by passive_maker_engine.py)
and prints fill rate, adverse selection, and net maker-fee savings. Refreshes
in place; Ctrl-C to exit. Stdlib only.

  python passive_maker_dashboard.py                       # ./engine_diagnostics.json
  python passive_maker_dashboard.py /path/to/diag.json    # explicit path
  python passive_maker_dashboard.py --once                # print once, no refresh
"""
import json
import os
import sys
import time


def render(d):
    os.system("cls" if os.name == "nt" else "clear")
    halted = d.get("halted")
    print("\n  PASSIVE MAKER ENGINE — live execution diagnostics"
          + ("   ** HALTED (kill switch) **" if halted else ""))
    print("  " + "=" * 66)
    print(f"  Targets (top-{len(d.get('targets', []))} momentum): "
          + ", ".join(d.get("targets", [])) or "  (none ranked yet)")
    print("  " + "-" * 66)
    print(f"  {'Quotes placed':24s} {d.get('quotes_placed', 0):>10d}")
    print(f"  {'Filled':24s} {d.get('filled', 0):>10d}")
    print(f"  {'Cancelled / replaced':24s} {d.get('cancelled', 0):>10d}")
    print(f"  {'Fill rate':24s} {d.get('fill_rate_pct', 0):>9.1f}%")
    adv = d.get("adverse_60s_pct")
    print(f"  {'Adverse selection (60s)':24s} {(f'{adv:+.2f}%' if adv is not None else '—'):>10s}")
    print(f"  {'Net spread saved':24s} {('$' + format(d.get('net_spread_saved_usd', 0), '+.2f')):>10s}")
    offs = d.get("offsets") or {}
    if offs:
        print("  " + "-" * 66)
        print("  Widened offsets (adverse-selection feedback): "
              + ", ".join(f"{s}={k}xATR" for s, k in offs.items()))
    print("  " + "=" * 66)
    print("  (judge on spread-saved & adverse-selection — NOT P&L; the tilt is")
    print("   an allocation rule, not validated alpha. See engine header.)\n")


def main():
    args = sys.argv[1:]
    once = "--once" in args
    args = [a for a in args if a != "--once"]
    path = args[0] if args else "engine_diagnostics.json"
    while True:
        try:
            d = json.load(open(path))
        except FileNotFoundError:
            print(f"waiting for {path} (start passive_maker_engine.py first)...")
        except Exception as e:
            print(f"read error: {e}")
        else:
            render(d)
        if once:
            break
        time.sleep(3)


if __name__ == "__main__":
    main()
