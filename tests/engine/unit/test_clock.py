from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from tc.broker.models import MarketWindow
from tc.clock import ET, et_stamp, fallback_window, in_window, phase_for

D = date(2026, 9, 2)
W = MarketWindow(date=D, is_trading_day=True,
                 rth_start=datetime(2026, 9, 2, 9, 30, tzinfo=ET), rth_end=datetime(2026, 9, 2, 16, 0, tzinfo=ET))


def _et(h: int, m: int) -> datetime:
    return datetime(2026, 9, 2, h, m, tzinfo=ET)


def test_phases() -> None:
    assert phase_for(_et(9, 29), W) == "PRE"
    assert phase_for(_et(9, 30), W) == "RTH"
    assert phase_for(_et(15, 59), W) == "RTH"
    assert phase_for(_et(16, 0), W) == "POST"
    assert phase_for(_et(12, 0), MarketWindow(date=D, is_trading_day=False, rth_start=None, rth_end=None)) == "CLOSED"


def test_phase_accepts_utc_input() -> None:
    assert phase_for(datetime(2026, 9, 2, 13, 30, tzinfo=UTC), W) == "RTH"  # 09:30 EDT


def test_stamp_and_window() -> None:
    assert et_stamp(datetime(2026, 9, 2, 18, 35, tzinfo=UTC)) == "2026-09-02 14:35"
    assert in_window(_et(9, 32), "09:32", "15:47")
    assert not in_window(_et(15, 47), "09:32", "15:47")


def test_fallback_window() -> None:
    wk = fallback_window(date(2026, 9, 2))   # Wednesday
    assert wk.is_trading_day and wk.rth_start == datetime(2026, 9, 2, 9, 30, tzinfo=ET)
    assert not fallback_window(date(2026, 9, 5)).is_trading_day  # Saturday
    assert ZoneInfo("America/New_York") == ET
