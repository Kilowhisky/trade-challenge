"""Explicit ET schedule, expanded at load (spec §3). No cron syntax.

A fire is due in the minute it names. A fire the engine slept through is
recorded as `missed` by mark_missed and never run late: a 09:47 tick run at
10:20 would write a row claiming a sweep that did not happen.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, cast

from tc.clock import ET

Days = Literal["weekdays", "daily", "sat", "sun"]
_EVERY = re.compile(r"^every (\d+)m (\d\d:\d\d)-(\d\d:\d\d) (weekdays|daily|sat|sun)$")
_AT = re.compile(r"^at (\d\d:\d\d) (weekdays|daily|sat|sun)$")
GRACE = timedelta(seconds=59)


@dataclass(frozen=True)
class Fire:
    job: str
    at: datetime


@dataclass(frozen=True)
class ScheduleSpec:
    days: Days
    start: time
    end: time
    every_min: int  # 0 => single fire at `start`

    @classmethod
    def parse(cls, text: str) -> ScheduleSpec:
        t = text.strip()
        if m := _EVERY.match(t):
            n, s, e, d = m.groups()
            start, end = time.fromisoformat(s), time.fromisoformat(e)
            if int(n) <= 0 or end < start:
                raise ValueError(f"bad schedule {text!r}")
            return cls(cast(Days, d), start, end, int(n))
        if m := _AT.match(t):
            s, d = m.groups()
            at = time.fromisoformat(s)
            return cls(cast(Days, d), at, at, 0)
        raise ValueError(f"bad schedule {text!r}")

    def day_matches(self, d: date, trading_day: Callable[[date], bool]) -> bool:
        if self.days == "daily":
            return True
        if self.days == "sat":
            return d.weekday() == 5
        if self.days == "sun":
            return d.weekday() == 6
        return d.weekday() < 5 and trading_day(d)

    def fires_on(
        self, d: date, trading_day: Callable[[date], bool] | None = None
    ) -> list[datetime]:
        if not self.day_matches(d, trading_day or (lambda x: True)):
            return []
        first = datetime.combine(d, self.start, tzinfo=ET)
        if self.every_min == 0:
            return [first]
        last = datetime.combine(d, self.end, tzinfo=ET)
        out, t = [], first
        while t <= last:
            out.append(t)
            t += timedelta(minutes=self.every_min)
        return out


class Scheduler:
    def __init__(self, specs: dict[str, ScheduleSpec], trading_day: Callable[[date], bool]) -> None:
        self.specs = specs
        self._trading_day = trading_day
        self._fired: set[Fire] = set()
        self._missed: set[Fire] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def _fires(self, d: date) -> list[Fire]:
        return [
            Fire(j, at)
            for j, s in self.specs.items()
            for at in s.fires_on(d, self._trading_day)
        ]

    def due(self, now: datetime) -> list[Fire]:
        now = now.astimezone(ET)
        out = []
        for f in self._fires(now.date()):
            if f.at <= now <= f.at + GRACE and f not in self._fired and f not in self._missed:
                self._fired.add(f)
                out.append(f)
        return out

    def mark_missed(self, now: datetime) -> list[Fire]:
        now = now.astimezone(ET)
        out = []
        for f in self._fires(now.date()):
            if f.at + GRACE < now and f not in self._fired and f not in self._missed:
                self._missed.add(f)
                out.append(f)
        return out

    def prune(self, before: date) -> int:
        """Forget the fired/missed marks for days already past, returning how
        many were dropped.

        The two sets are the engine's only memory of what has already run, and
        they are consulted for *today* alone — `due` and `mark_missed` both
        enumerate `self._fires(now.date())`. Unpruned they grow by roughly
        thirty entries a day for the life of a process that is meant to run
        for months.
        """
        before_n = len(self._fired) + len(self._missed)
        self._fired = {f for f in self._fired if f.at.date() >= before}
        self._missed = {f for f in self._missed if f.at.date() >= before}
        return before_n - len(self._fired) - len(self._missed)

    def lock(self, job: str) -> asyncio.Lock:
        return self._locks.setdefault(job, asyncio.Lock())
