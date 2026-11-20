import re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
log = open("/var/log/propr-bot.log", "r", encoding="utf-8", errors="replace").read()
lines = log.splitlines()
print("total log lines:", len(lines))
# first and last timestamp
first = next((l for l in lines if l[:4].isdigit()), "")
last = next((l for l in reversed(lines) if l[:4].isdigit()), "")
print("first line:", first[:80])
print("last  line:", last[:80])

# find equity/profit progression milestones
targets = [l for l in lines if "profit" in l and ("+" in l or "-" in l)]
print("\nequity line count:", len(targets))
# print every ~Nth equity line to show progression
step = max(1, len(targets) // 25)
for i in range(0, len(targets), step):
    print("  ", targets[i].split("|")[0].strip()[:60], "|", targets[i].split("|")[1].strip()[:40] if len(targets[i].split("|"))>1 else "")

# find fills / closes
print("\nfirst 10 lines containing 'filled':")
fl = [l for l in lines if "filled" in l.lower()][:10]
for l in fl: print("  ", l[:140])
print("\nlast 10 lines containing 'filled':")
fl = [l for l in lines if "filled" in l.lower()][-10:]
for l in fl: print("  ", l[:140])

# search for target / breach / challenge end
for kw in ("TARGET", "BREACH", "reached", "challenge", "halt", "stopped", "win", "take profit", "exit"):
    h = [l for l in lines if kw.lower() in l.lower()][-6:]
    if h:
        print(f"\n--- last {len(h)} lines containing '{kw}':")
        for l in h: print("  ", l[:150])