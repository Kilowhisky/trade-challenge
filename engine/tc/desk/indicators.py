"""Numbers the desk computes from stored daily bars. Pure: no I/O, no clock.

Every function answers None rather than guessing when the history is too
short -- a briefing row with an unknown ATR says "unknown", and a screen that
needs the number skips the name.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from decimal import Decimal, localcontext

from tc.broker.models import DailyBar

HUNDRED = Decimal(100)


def _mean(xs: Sequence[Decimal]) -> Decimal:
    return sum(xs, Decimal(0)) / len(xs)


def atr_pct(bars: Sequence[DailyBar], n: int = 14) -> Decimal | None:
    """Mean true range over the last `n` sessions, as % of the last close."""
    if len(bars) < n + 1 or bars[-1].close <= 0:
        return None
    trs = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[-n - 1:-1], bars[-n:], strict=True)
    ]
    return _mean(trs) / bars[-1].close * HUNDRED


def sma(bars: Sequence[DailyBar], n: int) -> Decimal | None:
    if len(bars) < n:
        return None
    return _mean([b.close for b in bars[-n:]])


def pct_from(value: Decimal, ref: Decimal | None) -> Decimal | None:
    if ref is None or ref == 0:
        return None
    return (value - ref) / ref * HUNDRED


def pct_change(bars: Sequence[DailyBar], n: int) -> Decimal | None:
    if len(bars) < n + 1 or bars[-1 - n].close == 0:
        return None
    return (bars[-1].close - bars[-1 - n].close) / bars[-1 - n].close * HUNDRED


def rel_strength(
    bars: Sequence[DailyBar], bench: Sequence[DailyBar], n: int
) -> Decimal | None:
    """The name's n-session change minus the benchmark's over the SAME dates."""
    own = pct_change(bars, n)
    if own is None:
        return None
    start, end = bars[-1 - n].date, bars[-1].date
    closes = {b.date: b.close for b in bench}
    if start not in closes or end not in closes or closes[start] == 0:
        return None
    return own - (closes[end] - closes[start]) / closes[start] * HUNDRED


def high_low(bars: Sequence[DailyBar], n: int = 252) -> tuple[Decimal, Decimal] | None:
    if not bars:
        return None
    window = bars[-n:]
    return max(b.high for b in window), min(b.low for b in window)


def prior_high_low(bars: Sequence[DailyBar], n: int = 252) -> tuple[Decimal, Decimal] | None:
    """The range BEFORE the last bar: a breakout is today's close above it."""
    return high_low(bars[:-1], n) if len(bars) > 1 else None


def volume_ratio(bars: Sequence[DailyBar], n: int = 20) -> Decimal | None:
    """The last session's volume over the mean of the `n` before it."""
    if len(bars) < n + 1:
        return None
    avg = _mean([Decimal(b.volume) for b in bars[-n - 1:-1]])
    if avg == 0:
        return None
    return Decimal(bars[-1].volume) / avg


def gap_pct(bars: Sequence[DailyBar]) -> Decimal | None:
    if len(bars) < 2 or bars[-2].close == 0:
        return None
    return (bars[-1].open - bars[-2].close) / bars[-2].close * HUNDRED


def log_return_corr(
    a: Sequence[DailyBar], b: Sequence[DailyBar], n: int = 60
) -> Decimal | None:
    """Pearson correlation of daily log returns over the last `n` common dates
    (CLAUDE.md §3.8's measure: trailing 60-day daily-return correlation)."""
    ca = {x.date: x.close for x in a}
    cb = {x.date: x.close for x in b}
    dates = sorted(set(ca) & set(cb))[-(n + 1):]
    if len(dates) < n + 1 or any(ca[d] <= 0 or cb[d] <= 0 for d in dates):
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        ra = [(ca[d1] / ca[d0]).ln() for d0, d1 in itertools.pairwise(dates)]
        rb = [(cb[d1] / cb[d0]).ln() for d0, d1 in itertools.pairwise(dates)]
        ma, mb = _mean(ra), _mean(rb)
        cov = sum(((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True)), Decimal(0))
        va = sum(((x - ma) ** 2 for x in ra), Decimal(0))
        vb = sum(((y - mb) ** 2 for y in rb), Decimal(0))
        if va == 0 or vb == 0:
            return None
        return cov / (va.sqrt() * vb.sqrt())
