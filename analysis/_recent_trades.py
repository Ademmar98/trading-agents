import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
log = open("/var/log/propr-bot.log", "r", encoding="utf-8", errors="replace").read()
lines = log.splitlines()

print("=== trade lifecycle events (SIGNAL / OPEN / CLOSED / order) from 2026-08-25 onward ===")
for l in lines:
    if l[:4].isdigit() and l >= "2026-08-25":
        up = l.upper()
        if any(k in up for k in ("SIGNAL", "OPEN", "CLOSED", "ORDER REJECTED", "FILLED", "BUSINESS")):
            print(l[:150])

print()
print("=== equity lines on 2026-09-05 and 2026-09-06 (the surge) ===")
for l in lines:
    if l[:4].isdigit() and (l.startswith("2026-09-05") or l.startswith("2026-09-06")) and "profit" in l:
        print(l[:120])