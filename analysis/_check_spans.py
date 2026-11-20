import pickle, pandas as pd
raw = pickle.loads(open("/opt/propr-bot/candles_1y.pkl", "rb").read())
print("assets:", len(raw))
spans = []
for c, df in raw.items():
    spans.append((len(df), df.index.min(), df.index.max()))
spans.sort(key=lambda x: x[1])
for c, mn, mx in list(spans)[:5]: pass
# global earliest/latest
mine = min(s[1] for s in spans)
maxe = max(s[2] for s in spans)
print("earliest bar:", mine)
print("latest   bar:", maxe)
print("span days:", (maxe - mine).days)
# how many assets have >330 days
n330 = sum(1 for l, mn, mx in spans if (mx - mn).days > 330)
n250 = sum(1 for l, mn, mx in spans if (mx - mn).days > 250)
print("assets >330days:", n330, " >250days:", n250)
# median days
import statistics
days = sorted((mx - mn).days for l, mn, mx in spans)
print("median days:", days[len(days)//2], " min:", days[0], " max:", days[-1])
