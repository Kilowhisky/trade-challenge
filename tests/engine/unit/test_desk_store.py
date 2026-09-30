"""Task 1: the bars cache and the desk tables' append-only guarantees."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from desk_fixtures import bar, flat_bars

from tc.broker.models import DailyBar
from tc.store.db import Store

PITCH_SQL = (
    "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
    " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
    " VALUES ('news','2026-09-28T20:00:00+00:00','2026-09-29','AAA','up','t','[]',"
    "'11','9',5,3,'SPY')"
)


def test_daily_bar_reads_volume_and_defaults_it_to_zero() -> None:
    raw = {"datetime": 1756771200000, "open": 1, "high": 2, "low": 0.5, "close": 1.5}
    assert DailyBar.from_payload({**raw, "volume": 1234}).volume == 1234
    assert DailyBar.from_payload(raw).volume == 0


@pytest.mark.asyncio
async def test_upsert_bars_replaces_a_corrected_bar(desk_store: Store) -> None:
    d = date(2026, 9, 21)
    await desk_store.upsert_bars("AAA", [bar(d, 10, 11, 9, 10.5)])
    await desk_store.upsert_bars("AAA", [bar(d, 10, 11, 9, 10.75)])
    got = await desk_store.bars_for("AAA")
    assert [b.close for b in got] == [Decimal("10.75")]


@pytest.mark.asyncio
async def test_bars_for_is_oldest_first_and_limit_keeps_the_newest(desk_store: Store) -> None:
    bars = flat_bars(date(2026, 9, 1), 5)
    await desk_store.upsert_bars("AAA", bars)
    assert [b.date for b in await desk_store.bars_for("AAA", limit=2)] == [
        bars[3].date, bars[4].date,
    ]
    since = await desk_store.bars_for("AAA", since=bars[2].date)
    assert [b.date for b in since] == [b.date for b in bars[2:]]
    assert (await desk_store.bars_for("AAA"))[0].volume == 1_000_000


@pytest.mark.asyncio
async def test_bar_counts_per_symbol(desk_store: Store) -> None:
    await desk_store.upsert_bars("AAA", flat_bars(date(2026, 9, 1), 3))
    await desk_store.upsert_bars("BBB", flat_bars(date(2026, 9, 1), 5))
    assert await desk_store.bar_counts() == {"AAA": 3, "BBB": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("table_sql,update", [
    (PITCH_SQL, "UPDATE pitches SET target='12'"),
])
async def test_desk_ledgers_are_append_only(desk_store: Store, table_sql: str, update: str) -> None:
    await desk_store.execute(table_sql)
    with pytest.raises(Exception, match="append-only"):
        await desk_store.execute(update)
    with pytest.raises(Exception, match="append-only"):
        await desk_store.execute("DELETE FROM pitches")


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_error(desk_store: Store) -> None:
    with pytest.raises(RuntimeError):
        async with desk_store.transaction() as c:
            await c.execute(PITCH_SQL)
            raise RuntimeError("boom")
    assert await desk_store.fetchall("SELECT id FROM pitches") == []
