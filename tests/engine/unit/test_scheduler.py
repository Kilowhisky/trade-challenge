from datetime import date, datetime, time

import pytest

from tc.clock import ET
from tc.scheduler import Fire, Scheduler, ScheduleSpec


def et(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=ET)


def test_every_expands_inclusive_end() -> None:
    s = ScheduleSpec.parse("every 15m 09:32-15:47 weekdays")
    fires = s.fires_on(date(2026, 9, 3))  # Thursday
    assert fires[0] == et(date(2026, 9, 3), 9, 32)
    assert fires[-1] == et(date(2026, 9, 3), 15, 47)
    assert len(fires) == 26


def test_at_single_fire_and_day_filters() -> None:
    assert ScheduleSpec.parse("at 16:04 weekdays").fires_on(date(2026, 9, 5)) == []  # Saturday
    assert ScheduleSpec.parse("at 07:40 sat").fires_on(date(2026, 9, 5)) == [et(date(2026, 9, 5), 7, 40)]
    assert ScheduleSpec.parse("at 07:05 daily").fires_on(date(2026, 9, 6)) == [et(date(2026, 9, 6), 7, 5)]


@pytest.mark.parametrize("bad", ["every 15 09:32-15:47", "at 25:00 daily", "at 07:00 monday", "weekly"])
def test_parse_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        ScheduleSpec.parse(bad)


def test_weekdays_defers_to_trading_day_callable() -> None:
    s = ScheduleSpec.parse("at 16:04 weekdays")
    sched = Scheduler({"close": s}, trading_day=lambda d: False)  # holiday
    assert sched.due(et(date(2026, 9, 7), 16, 4)) == []


def test_due_fires_once_and_records_missed() -> None:
    s = ScheduleSpec.parse("every 15m 09:32-15:47 weekdays")
    sched = Scheduler({"tick": s}, trading_day=lambda d: True)
    d = date(2026, 9, 3)
    assert sched.due(et(d, 9, 32)) == [Fire("tick", et(d, 9, 32))]
    assert sched.due(et(d, 9, 32)) == []                       # same minute, once
    assert sched.due(et(d, 9, 40)) == []                       # nothing between fires
    # engine was down 09:47..10:17: those are missed, not run late
    missed = sched.mark_missed(et(d, 10, 20))
    assert [f.at for f in missed] == [et(d, 9, 47), et(d, 10, 2), et(d, 10, 17)]
    assert sched.due(et(d, 10, 32)) == [Fire("tick", et(d, 10, 32))]


def test_due_tolerates_late_wakeup_inside_grace() -> None:
    s = ScheduleSpec.parse("at 16:04 weekdays")
    sched = Scheduler({"close": s}, trading_day=lambda d: True)
    d = date(2026, 9, 3)
    assert sched.due(et(d, 16, 4).replace(second=40)) == [Fire("close", et(d, 16, 4))]


def test_lock_is_per_job_and_stable() -> None:
    sched = Scheduler({}, trading_day=lambda d: True)
    assert sched.lock("a") is sched.lock("a")
    assert sched.lock("a") is not sched.lock("b")


def test_prune_forgets_earlier_days_and_keeps_today() -> None:
    """The fired/missed sets are consulted for today alone. Unpruned they grow
    for the life of a process meant to run for months."""
    s = ScheduleSpec.parse("every 15m 09:32-15:47 weekdays")
    sched = Scheduler({"tick": s}, trading_day=lambda d: True)
    thu, fri = date(2026, 9, 3), date(2026, 9, 4)
    sched.due(et(thu, 9, 32))
    sched.mark_missed(et(thu, 10, 20))
    sched.due(et(fri, 9, 32))

    assert sched.prune(fri) == 4  # Thursday's one fired + three missed
    assert sched.prune(fri) == 0  # idempotent
    # Today's mark survives: the 09:32 fire must not run a second time.
    assert sched.due(et(fri, 9, 32)) == []
