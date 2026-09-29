"""Builders shared by the desk tests (tc/desk/). Plain functions; each test
file imports what it uses by name, so a reader can see where a helper comes
from. The store fixture is in conftest.py."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from tc.broker.models import DailyBar
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
RULES = Rules.load(REPO / "rules.yml")


def sessions(start: date, n: int) -> list[date]:
    """`n` weekdays from `start` inclusive. Holidays are not modelled; a test
    that needs one drops the date from its bars on purpose."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


Px = str | float | Decimal


def bar(d: date, o: Px, h: Px, lo: Px, c: Px, v: int = 1_000_000) -> DailyBar:
    return DailyBar(date=d, open=Decimal(str(o)), high=Decimal(str(h)),
                    low=Decimal(str(lo)), close=Decimal(str(c)), volume=v)


def flat_bars(start: date, n: int, price: str = "100", volume: int = 1_000_000) -> list[DailyBar]:
    p = Decimal(price)
    return [DailyBar(date=d, open=p, high=p, low=p, close=p, volume=volume)
            for d in sessions(start, n)]


def trend_bars(start: date, n: int, first: str = "50", step: str = "0.5",
               spread: str = "1", volume: int = 1_000_000) -> list[DailyBar]:
    """Opens at `first`, closes `step` higher each session, ranges `spread`
    either side of the open; each open is the previous close."""
    out: list[DailyBar] = []
    p, s, sp = Decimal(first), Decimal(step), Decimal(spread)
    for d in sessions(start, n):
        out.append(DailyBar(date=d, open=p, high=max(p + sp, p + s), low=p - sp,
                            close=p + s, volume=volume))
        p += s
    return out
