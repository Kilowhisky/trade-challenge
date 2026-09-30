"""Task 5: filing, refusing and withdrawing pitches (spec §5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from desk_fixtures import RULES

from tc.desk.models import Analyst, DeskRefused
from tc.desk.pitches import (
    EvidenceItem,
    Pitch,
    PitchIn,
    open_pitches,
    parse_price,
    reference_session,
    submit_pitch,
    withdraw_pitch,
)
from tc.store.db import Store

EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)   # Mon 16:45 ET
PREOPEN = datetime(2026, 9, 29, 12, 5, tzinfo=UTC)    # Tue 08:05 ET
MIDDAY = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)     # Tue 12:00 ET
LAST = Decimal("100")
TRADEABLE = {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "XLK"}


def weekday(d: date) -> bool:
    return d.weekday() < 5


def _pin(**kw: Any) -> PitchIn:
    base: dict[str, Any] = {
        "symbol": "AAA", "direction": "up", "thesis": "demand is inflecting, see the channel checks",
        "evidence": [EvidenceItem(url="https://example.com/a", claim="a claim", date="2026-09-28")],
        "target": "110", "invalidation": "95", "horizon_days": 5, "conviction": 3,
        "benchmark": "XLK",
    }
    base.update(kw)
    return PitchIn.model_validate(base)


async def _submit(
    store: Store, now: datetime = EVENING, analyst: Analyst = "news", **kw: Any
) -> Pitch:
    return await submit_pitch(
        store, analyst=analyst, pin=_pin(**kw), now=now, last=LAST,
        tradeable=TRADEABLE, rules=RULES, is_trading_day=weekday,
    )


async def test_an_evening_pitch_references_the_next_session(desk_store: Store) -> None:
    p = await _submit(desk_store)
    assert (p.session, p.analyst, p.target) == (date(2026, 9, 29), "news", Decimal("110"))


async def test_a_preopen_pitch_references_today(desk_store: Store) -> None:
    assert (await _submit(desk_store, now=PREOPEN)).session == date(2026, 9, 29)


def test_reference_session_skips_weekends() -> None:
    friday_evening = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)
    assert reference_session(friday_evening, weekday) == date(2026, 10, 5)


async def test_filing_during_the_session_is_refused(desk_store: Store) -> None:
    with pytest.raises(DeskRefused, match="outside the regular session"):
        await _submit(desk_store, now=MIDDAY)


@pytest.mark.parametrize("kw,match", [
    ({"target": "99"}, "target > last price > invalidation"),
    ({"direction": "down"}, "target < last price < invalidation"),
    ({"target": "140"}, "more than 30"),
    ({"horizon_days": 1}, "horizon_days must be 2-20"),
    ({"horizon_days": 21}, "horizon_days must be 2-20"),
    ({"benchmark": "QQQ"}, "benchmark must be one of"),
    ({"symbol": "ZZZ"}, "not in the qualified universe"),
])
async def test_bad_pitches_are_refused_with_a_reason(
    desk_store: Store, kw: dict[str, Any], match: str,
) -> None:
    with pytest.raises(DeskRefused, match=match):
        await _submit(desk_store, **kw)


@pytest.mark.parametrize("raw", ["$48.25", "48,25", "NaN", "-1", "", "Infinity", "0"])
def test_prices_the_model_might_write_are_refused_with_a_reason(raw: str) -> None:
    with pytest.raises(DeskRefused, match="target must be"):
        parse_price(raw, "target")


def test_a_clean_price_string_parses_exactly() -> None:
    assert parse_price(" 48.25 ", "target") == Decimal("48.25")


async def test_the_evening_cap_is_five_per_analyst(desk_store: Store) -> None:
    for sym in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        await _submit(desk_store, symbol=sym)
    with pytest.raises(DeskRefused, match="limit \\(5\\)"):
        await _submit(desk_store, symbol="FFF")
    await _submit(desk_store, symbol="FFF", analyst="macro")  # another analyst's budget


async def test_the_preopen_cap_is_two_new(desk_store: Store) -> None:
    for sym in ("AAA", "BBB"):
        await _submit(desk_store, now=PREOPEN, symbol=sym)
    with pytest.raises(DeskRefused, match="limit \\(2\\)"):
        await _submit(desk_store, now=PREOPEN, symbol="CCC")


async def test_an_open_duplicate_is_refused_but_another_analyst_may_pitch_it(
    desk_store: Store,
) -> None:
    first = await _submit(desk_store)
    with pytest.raises(DeskRefused, match=f"open pitch {first.id}"):
        await _submit(desk_store)
    await _submit(desk_store, analyst="technical")


async def test_withdrawal_only_before_the_open_and_only_your_own(desk_store: Store) -> None:
    p = await _submit(desk_store)
    with pytest.raises(DeskRefused, match="no open pitch"):
        await withdraw_pitch(desk_store, analyst="macro", pitch_id=p.id, now=PREOPEN)
    after_open = datetime(2026, 9, 29, 13, 31, tzinfo=UTC)  # 09:31 ET
    with pytest.raises(DeskRefused, match="its session opened"):
        await withdraw_pitch(desk_store, analyst="news", pitch_id=p.id, now=after_open)
    await withdraw_pitch(desk_store, analyst="news", pitch_id=p.id, now=PREOPEN)
    assert await open_pitches(desk_store) == []
    # Withdrawn frees the symbol for a fresh pitch.
    await _submit(desk_store, now=PREOPEN)


async def test_open_pitches_excludes_resolved(desk_store: Store) -> None:
    p = await _submit(desk_store)
    await desk_store.execute(
        "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how,"
        " exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at)"
        " VALUES ('pitch', ?, '2026-09-29', '100', '2026-09-30', 'target', '110', '10', '0', '0', 'x')",
        (p.id,),
    )
    assert await open_pitches(desk_store) == []
