"""Eastern-time helpers. Gate on the clock against the market-hours window,
never on get_market_hours' isOpen (tick.md §B1: it means 'trading day', and
reads true at 23:20 ET)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from tc.broker.models import MarketWindow

ET = ZoneInfo("America/New_York")
SessionPhase = Literal["PRE", "RTH", "POST", "CLOSED"]


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; the engine only handles aware times")
    return dt.astimezone(ET)


def et_now(clock: Callable[[], datetime]) -> datetime:
    return _aware(clock())


def et_stamp(dt: datetime) -> str:
    return _aware(dt).strftime("%Y-%m-%d %H:%M")


def phase_for(now: datetime, window: MarketWindow) -> SessionPhase:
    if not window.is_trading_day or window.rth_start is None or window.rth_end is None:
        return "CLOSED"
    n = _aware(now)
    if n < window.rth_start:
        return "PRE"
    if n < window.rth_end:
        return "RTH"
    return "POST"


def in_window(now_et: datetime, start: str, end: str) -> bool:
    n = _aware(now_et).time()
    s = time.fromisoformat(start)
    e = time.fromisoformat(end)
    return s <= n < e


def fallback_window(d: date) -> MarketWindow:
    """Only for a BLIND engine. Weekday => assume a trading day so monitoring
    keeps running; weekend => closed. Holidays are unknown here by design."""
    if d.weekday() >= 5:
        return MarketWindow(date=d, is_trading_day=False, rth_start=None, rth_end=None)
    return MarketWindow(
        date=d, is_trading_day=True,
        rth_start=datetime.combine(d, time(9, 30), tzinfo=ET),
        rth_end=datetime.combine(d, time(16, 0), tzinfo=ET),
    )


def trading_days_between(a: date, b: date) -> int:
    """Weekday count in ``(a, b]`` — exclusive of `a`, inclusive of `b`.

    Sessions, approximated as weekdays. **Holidays are unknown here by
    design**: the only holiday oracle the engine has is `get_market_hours`,
    one day per call, and a blind engine has none at all. The approximation
    errs in the safe direction — a holiday inside the span makes this count
    HIGH, so §3.5's five-session hold clock fires early rather than late.

    One counter, shared by the §3.3/§3.5 clocks and (via the cached
    `MarketWindow`) the scheduler's trading-day test, so the unknown lives in
    exactly one place.
    """
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n
