"""Task 6: resolution arithmetic and the scoring pass (spec §7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from desk_fixtures import bar, flat_bars, sessions

from tc.desk.scoring import Item, analyst_record, missing_bar, resolve, score
from tc.store.db import Store

S = sessions(date(2026, 9, 28), 12)   # Mon 9/28 onward
SPY = [bar(d, 500, 500, 500, 500) for d in S]
_BASE = Item(kind="pitch", item_id=1, symbol="AAA", direction="up", target=Decimal(110),
             invalidation=Decimal(95), horizon_days=5, benchmark="XLK", session=S[0])


def _pitch_item(**kw: Any) -> Item:
    return replace(_BASE, **kw)


def test_a_pitch_hits_its_target_at_the_target_price() -> None:
    bars = [bar(S[0], 100, 104, 99, 103), bar(S[1], 103, 111, 102, 108)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None
    assert (r.how, r.resolved_on, r.exit_price, r.ret_pct) == ("target", S[1], Decimal(110), Decimal(10))
    assert r.hit and r.excess_spy == 10


def test_a_same_day_double_touch_is_an_invalidation() -> None:
    bars = [bar(S[0], 100, 111, 94, 100)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.exit_price) == ("invalidation", Decimal(95))
    assert r.ret_pct == -5 and not r.hit


def test_a_gap_through_the_target_resolves_at_the_open() -> None:
    bars = [bar(S[0], 100, 104, 99, 103), bar(S[1], 115, 116, 112, 113)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.exit_price) == ("gap_target", Decimal(115))


def test_a_pitch_whose_open_is_already_through_a_level_resolves_at_zero() -> None:
    bars = [bar(S[0], 112, 113, 111, 112)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.ret_pct, r.spy_ret_pct) == ("open_through", Decimal(0), Decimal(0))


def test_the_horizon_resolves_at_that_close() -> None:
    bars = [bar(d, 100, 101, 99, 100) for d in S[:4]] + [bar(S[4], 100, 102, 99, 102)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.resolved_on, r.exit_price) == ("horizon", S[4], Decimal(102))


def test_a_down_call_is_signed_and_its_benchmark_too() -> None:
    item = _pitch_item(direction="down", target=Decimal(90), invalidation=Decimal(105))
    bars = [bar(S[0], 100, 101, 95, 96), bar(S[1], 96, 97, 89, 91)]
    bench = [bar(S[0], 50, 50, 50, 50), bar(S[1], 50, 51, 49, 51)]
    spy = [bar(S[0], 500, 500, 500, 500), bar(S[1], 500, 510, 500, 505)]
    r = resolve(item, bars, spy, bench)
    assert r is not None and r.how == "target"
    assert r.ret_pct == 10                      # 100 -> 90, called down
    assert r.spy_ret_pct == 1                   # SPY 500 -> 505, unsigned
    assert r.bench_ret_pct == -2                # bench 50 -> 51, signed down
    assert r.excess_spy == 9 and r.excess_bench == 12


def test_a_calls_first_session_touch_is_not_counted() -> None:
    item = _pitch_item(kind="call", ref_price=Decimal(100), spy_ref=Decimal(500),
                       bench_ref=Decimal(500), horizon_days=2)
    bars = [bar(S[0], 99, 115, 90, 100), bar(S[1], 100, 101, 99, 100)]
    r = resolve(item, bars, SPY, SPY)
    assert r is not None and r.how == "horizon" and r.ret_pct == 0


def test_a_pitch_stamped_on_a_holiday_references_the_next_session() -> None:
    # 9/28 is a "holiday": no SPY bar, no symbol bar. The pitch was stamped 9/28.
    spy = SPY[1:]
    bars = [bar(S[1], 100, 111, 99, 108)]
    r = resolve(_pitch_item(), bars, spy, spy)
    assert r is not None and (r.ref_date, r.how) == (S[1], "target")


def test_missing_bars_keep_an_item_open_and_are_named_after_3_sessions() -> None:
    bars = [bar(S[0], 100, 101, 99, 100)]           # nothing after the first session
    assert resolve(_pitch_item(), bars, SPY[:3], SPY[:3]) is None
    assert missing_bar(_pitch_item(), bars, SPY[:3]) is False   # S[1] is only 1 session old
    assert missing_bar(_pitch_item(), bars, SPY[:5]) is True    # S[1] now 3 sessions old


async def test_score_writes_each_resolution_once(desk_store: Store) -> None:
    await desk_store.execute(
        "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
        " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
        " VALUES ('technical','2026-09-25T20:45:00+00:00',?,'AAA','up','t','[]','110','95',5,3,'XLK')",
        (S[0].isoformat(),),
    )
    await desk_store.upsert_bars("SPY", SPY)
    await desk_store.upsert_bars("XLK", flat_bars(S[0], 12, "50"))
    await desk_store.upsert_bars("AAA", [bar(S[0], 100, 104, 99, 103), bar(S[1], 103, 111, 102, 108)])
    first = await score(desk_store)
    second = await score(desk_store)
    assert len(first.resolved) == 1 and second.resolved == []
    rows = await desk_store.fetchall("SELECT how FROM resolutions")
    assert [r["how"] for r in rows] == ["target"]
    rec = await analyst_record(desk_store, "technical")
    assert [(x.symbol, x.how, x.ret_pct) for x in rec] == [("AAA", "target", "10.00")]
