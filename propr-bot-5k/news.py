# -*- coding: utf-8 -*-
"""
News calendar gate. Hard-coded high-impact macro events for the REST OF 2026
(UTC release times), verified against federalreserve.gov (FOMC) and
bls.gov/schedule/2026 (CPI / NFP / PPI). Keep this table current -- it is
the one thing between "no trade on a signal" and "chasing an NFP spike".

Times are already UTC. US times changed with DST on Nov 1 2026 (08:30 ET ->
12:30 UTC before, 13:30 UTC after; FOMC 14:00 ET -> 18:00 UTC / 19:00 UTC).

Design: the bot refuses NEW entries inside a halo around these events and
logs + Telegram-notifies once when entering the halo, so you can also decide
about open positions by hand timing matters. Stops/TPs on the exchange are
never disturbed.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass(frozen=True)
class Event:
    name: str
    utc: datetime
    halo_before_h: float
    halo_after_h: float


# (name, "YYYY-MM-DD HH:MM" UTC, hours before/after)  <- 2026 only
_EVENTS_RAW = [
    # Q4 2026 -- biggest market movers first
    ("NFP Sep",              "2026-10-02 12:30", 6.0, 3.0),
    ("CPI Sep",              "2026-10-14 12:30", 6.0, 3.0),
    ("PPI Sep",              "2026-10-15 12:30", 3.0, 1.0),
    ("FOMC rate decision",   "2026-10-28 18:00", 12.0, 6.0),
    ("NFP Oct",              "2026-11-06 13:30", 6.0, 3.0),
    ("CPI Oct",              "2026-11-10 13:30", 6.0, 3.0),
    ("PPI Oct",              "2026-11-13 13:30", 3.0, 1.0),
    ("FOMC rate decision",   "2026-12-09 19:00", 12.0, 6.0),
    ("NFP Nov",              "2026-12-04 13:30", 6.0, 3.0),
    ("CPI Nov",              "2026-12-10 13:30", 6.0, 3.0),
    ("PPI Nov",              "2026-12-15 13:30", 3.0, 1.0),
]

EVENTS = [Event(n, datetime.strptime(t, "%Y-%m-%d %H:%M").replace(
    tzinfo=timezone.utc), b, a) for n, t, b, a in _EVENTS_RAW]


def upcoming(now: datetime | None = None, horizon_h: float = 72.0) -> list[Event]:
    now = now or datetime.now(timezone.utc)
    return [e for e in EVENTS
            if now <= e.utc <= now + timedelta(hours=horizon_h)]


def in_halo(now: datetime | None = None) -> Event | None:
    """Return the event whose halo we are inside, else None."""
    now = now or datetime.now(timezone.utc)
    for e in EVENTS:
        lo = e.utc - timedelta(hours=e.halo_before_h)
        hi = e.utc + timedelta(hours=e.halo_after_h)
        if lo <= now <= hi:
            return e
    return None


def next_event(now: datetime | None = None) -> Event | None:
    upcoming_ = upcoming(now or datetime.now(timezone.utc), 24 * 366)
    return upcoming_[0] if upcoming_ else None


def describe(e: Event, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    dt = e.utc - now
    mins = int(dt.total_seconds() // 60)
    if mins < 0:
        elapsed = int(-dt.total_seconds() // 60)
        return f"{e.name} was {elapsed}m ago ({e.utc:%b %d %H:%M}Z)"
    if mins < 60:
        return f"{e.name} in {mins}m ({e.utc:%b %d %H:%M}Z)"
    h = mins // 60
    if h < 48:
        return f"{e.name} in {h}h ({e.utc:%b %d %H:%M}Z)"
    return f"{e.name} in {h // 24}d {h % 24}h ({e.utc:%b %d %H:%M}Z)"


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    eve = in_halo(now)
    print("now:", now.strftime("%Y-%m-%d %H:%M") + "Z")
    if eve:
        print("INSIDE HALO:", describe(eve))
    else:
        print("outside all halos; next:", describe(next_event(now)))
    for e in upcoming(now, 24 * 30):
        print("  up: ", describe(e))