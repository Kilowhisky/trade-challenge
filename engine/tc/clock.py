"""Eastern-time helpers. Gate on the clock against the market-hours window,
never on get_market_hours' isOpen (tick.md §B1: it means 'trading day', and
reads true at 23:20 ET)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
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
