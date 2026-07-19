"""Pairwise return correlation for portfolio risk.

Cluster caps treat all crypto as one family; correlation makes the finer
call — two genuinely uncorrelated alts can coexist while a fifth BTC-clone
gets its size cut.
"""


def pearson(a, b):
    """Pearson correlation of two equal-length numeric sequences.
    Returns None when there are fewer than 10 aligned points or a series
    is flat (zero variance)."""
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    da = [x - mean_a for x in a]
    db = [x - mean_b for x in b]
    var_a = sum(x * x for x in da)
    var_b = sum(x * x for x in db)
    if var_a == 0 or var_b == 0:
        return None
    cov = sum(x * y for x, y in zip(da, db))
    return cov / (var_a ** 0.5 * var_b ** 0.5)


def daily_returns(closes):
    """Percent day-over-day returns from a close series."""
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes)) if closes[i - 1]
    ]
