"""Task 11: call rows and the paper book's arithmetic."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tc.desk.calls import (
    NewCall,
    calls_made_on,
    current_call_id,
    effective_invalidation,
    get_call,
    insert_call,
    is_extended,
    open_call_for,
    tighten,
)
from tc.desk.models import DeskRefused
from tc.desk.paper import (
    book_row,
    book_state,
    cash,
    closed_trades,
    create_proposal,
    ensure_book,
    expire_stale_proposals,
    mark_exit_done,
    mark_posted,
    open_positions,
    pending_exit_requests,
    pending_proposals,
    record_fill,
    record_outcome,
    request_exit,
    unposted_proposals,
    upsert_mark,
)
from tc.store.db import Store

NOW = datetime(2026, 9, 29, 13, 55, tzinfo=UTC)
TODAY = date(2026, 9, 29)


def _call(**kw: object) -> NewCall:
    base: dict[str, object] = dict(
        made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
        symbol="AAA", direction="up", thesis="a thesis long enough", target=Decimal(55),
        invalidation=Decimal(48), horizon_days=10, conviction=3, benchmark="XLK",
        ref_price=Decimal(50), spy_ref=Decimal(500), bench_ref=Decimal(200), funding="shares",
    )
    base.update(kw)
    return NewCall.model_validate(base)


async def test_calls_round_trip_and_count_only_fresh_non_legacy_calls(desk_store: Store) -> None:
    c = await insert_call(desk_store, _call())
    assert (await get_call(desk_store, c.id)) == c
    await insert_call(desk_store, _call(symbol="BBB", origin="legacy", funding="none"))
    await insert_call(desk_store, _call(symbol="CCC", extends_call_id=c.id))
    assert await calls_made_on(desk_store, TODAY) == 1
    assert (await open_call_for(desk_store, "AAA", "up")) is not None
    assert (await open_call_for(desk_store, "BBB", "up")) is None
    assert (await open_call_for(desk_store, "BBB", "up", legacy=True)) is not None


async def test_an_extension_chain_is_followed_forward(desk_store: Store) -> None:
    a = await insert_call(desk_store, _call())
    b = await insert_call(desk_store, _call(extends_call_id=a.id))
    assert await current_call_id(desk_store, a.id) == b.id
    assert await is_extended(desk_store, a.id) and not await is_extended(desk_store, b.id)


async def test_tightening_moves_the_effective_invalidation(desk_store: Store) -> None:
    c = await insert_call(desk_store, _call())
    assert await effective_invalidation(desk_store, c) == 48
    await tighten(desk_store, c.id, Decimal("49.5"), NOW)
    assert await effective_invalidation(desk_store, c) == Decimal("49.5")


async def test_the_book_row_is_written_once(desk_store: Store) -> None:
    assert await ensure_book(desk_store, TODAY, Decimal("3700"), NOW) == (TODAY, Decimal("3700"))
    later = TODAY + timedelta(days=1)
    assert await ensure_book(desk_store, later, Decimal("9999"), NOW) == (TODAY, Decimal("3700"))
    assert await book_row(desk_store) == (TODAY, Decimal("3700"))


async def test_a_proposal_is_pending_until_it_has_an_outcome(desk_store: Store) -> None:
    c = await insert_call(desk_store, _call())
    p = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="shares",
                              symbol="AAA", underlying="AAA", quantity=7,
                              max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    assert [x.id for x in await unposted_proposals(desk_store)] == [p.id]
    await mark_posted(desk_store, p.id, NOW, NOW + timedelta(minutes=10), "m1")
    assert await unposted_proposals(desk_store) == []
    assert [x.message_id for x in await pending_proposals(desk_store)] == ["m1"]
    await record_outcome(desk_store, p.id, NOW, "filled", vetoed=False, approved=False, detail={})
    assert await pending_proposals(desk_store) == []


async def test_fills_drive_positions_cash_and_closed_trades(desk_store: Store) -> None:
    await ensure_book(desk_store, TODAY, Decimal("3700"), NOW)
    c = await insert_call(desk_store, _call())
    shares = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="shares",
                                   symbol="AAA", underlying="AAA", quantity=7,
                                   max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    opt = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="call",
                                symbol="AAA   261120C00050000", underlying="AAA", quantity=1,
                                max_entry_price=Decimal("2.60"), atr_pct=None)
    await record_fill(desk_store, shares.id, NOW, "buy", 7, Decimal("50.00"), "entry",
                      Decimal("48.00"), Decimal("45.60"))
    await record_fill(desk_store, opt.id, NOW, "buy", 1, Decimal("2.50"), "entry")
    assert await cash(desk_store, Decimal("3700")) == Decimal("3700") - 350 - 250
    pos = {p.symbol: p for p in await open_positions(desk_store)}
    assert pos["AAA"].stop_trigger == Decimal("48.00") and pos["AAA"].quantity == 7
    await upsert_mark(desk_store, "AAA   261120C00050000", Decimal("3.00"), NOW)
    state = await book_state(desk_store)
    assert state.open_premium == 250
    assert state.equity == Decimal("3100") + 350 + 300     # AAA unmarked -> entry price
    await record_fill(desk_store, shares.id, NOW, "sell", 7, Decimal("55.00"), "target")
    assert [p.symbol for p in await open_positions(desk_store)] == ["AAA   261120C00050000"]
    [t] = await closed_trades(desk_store)
    assert (t.proposal_id, t.ret_pct) == (shares.id, Decimal(10))


async def test_a_pending_proposal_is_committed_at_its_worst_case(desk_store: Store) -> None:
    """Final review C1: a proposal nobody has filled yet still spends the
    book's cash, open premium and name exposure at max_entry x qty x mult."""
    await ensure_book(desk_store, TODAY, Decimal("3700"), NOW)
    c = await insert_call(desk_store, _call())
    shares = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="shares",
                                   symbol="AAA", underlying="AAA", quantity=7,
                                   max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    opt = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="call",
                                symbol="AAA   261120C00050000", underlying="AAA", quantity=1,
                                max_entry_price=Decimal("2.60"), atr_pct=None)
    state = await book_state(desk_store)
    assert state.cash == Decimal("3700") - Decimal("353.50") - Decimal("260.00")
    assert state.equity == Decimal("3700")                  # a commitment is not a loss
    assert state.open_premium == Decimal("260.00")
    assert (state.positions, state.pending) == (0, 2)
    assert {(h.symbol, h.benchmark, h.market_value) for h in state.holdings} == {
        ("AAA", "XLK", Decimal("353.50")), ("AAA", "XLK", Decimal("260.00"))}
    # desk_watch re-checks one proposal against the book without itself.
    alone = await book_state(desk_store, exclude_proposal=shares.id)
    assert alone.cash == Decimal("3700") - Decimal("260.00") and alone.pending == 1
    # Once it fills (fill written, outcome not yet) it counts once, as a position.
    await record_fill(desk_store, shares.id, NOW, "buy", 7, Decimal("50.00"), "entry")
    state = await book_state(desk_store)
    assert state.cash == Decimal("3700") - Decimal("350.00") - Decimal("260.00")
    assert (state.positions, state.pending) == (1, 1)
    # A finished proposal (expired) commits nothing.
    await record_outcome(desk_store, opt.id, NOW, "expired", vetoed=False, approved=False,
                         detail={})
    assert (await book_state(desk_store)).open_premium == 0


async def test_a_proposal_from_an_earlier_session_is_expired_and_stops_committing(
    desk_store: Store,
) -> None:
    """A proposal left unfilled overnight (an early close, a missed 15:55
    fire) can never fill, so it must stop spending the book's cash before
    the next morning's PM sizes."""
    await ensure_book(desk_store, TODAY, Decimal("3700"), NOW)
    c = await insert_call(desk_store, _call())
    old = await create_proposal(desk_store, call_id=c.id, created_at=NOW - timedelta(days=1),
                                instrument="shares", symbol="AAA", underlying="AAA",
                                quantity=7, max_entry_price=Decimal("50.50"),
                                atr_pct=Decimal(2))
    fresh = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="shares",
                                  symbol="AAA", underlying="AAA", quantity=2,
                                  max_entry_price=Decimal("50.00"), atr_pct=Decimal(2))
    assert (await book_state(desk_store)).cash == Decimal("3700") - Decimal("353.50") - 100
    assert await expire_stale_proposals(desk_store, NOW) == [old.id]
    state = await book_state(desk_store)
    assert state.cash == Decimal("3700") - 100 and state.pending == 1
    row = await desk_store.fetchone(
        "SELECT outcome, detail_json FROM proposal_outcomes WHERE proposal_id=?", (old.id,))
    assert row is not None and row["outcome"] == "expired" and "stale" in row["detail_json"]
    assert [p.id for p in await pending_proposals(desk_store)] == [fresh.id]
    assert await expire_stale_proposals(desk_store, NOW) == []       # idempotent


async def test_book_state_refuses_before_the_book_starts(desk_store: Store) -> None:
    with pytest.raises(DeskRefused, match="has not started"):
        await book_state(desk_store)


async def test_exit_requests_queue_until_done(desk_store: Store) -> None:
    rid = await request_exit(desk_store, "CSX", None, "legacy: thesis gone", NOW)
    assert [r.id for r in await pending_exit_requests(desk_store)] == [rid]
    await mark_exit_done(desk_store, rid, NOW)
    assert await pending_exit_requests(desk_store) == []
