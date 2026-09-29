"""Task 15: desk_watch fills paper entries at the veto deadline and runs the
paper exits (spec §9.4, §9.5, §10)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from desk_fixtures import RULES, trend_bars

from tc.broker.client import BrokerUnauthorized
from tc.broker.fake import FakeBroker
from tc.broker.models import Quote
from tc.config import DeskConfig
from tc.desk.approval import Reaction
from tc.desk.calls import NewCall, insert_call, tighten
from tc.desk.models import Direction, Funding
from tc.desk.paper import (
    create_proposal,
    ensure_book,
    mark_posted,
    open_positions,
    pending_proposals,
    record_fill,
    record_outcome,
    request_exit,
)
from tc.desk.watch import run_desk_watch
from tc.notify import Notifier
from tc.store.db import Store

T0 = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)          # 10:00 ET
TODAY = date(2026, 9, 29)
OSI = "AAA   261120C00050000"
RESERVE = Decimal("900.00")


class Notes(Notifier):
    def __init__(self) -> None:
        super().__init__(None, httpx.AsyncClient())
        self.posts: list[str] = []

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


class Fixed:
    def __init__(self, r: Reaction) -> None:
        self.r = r

    async def read(self, message_id: str) -> Reaction:
        return self.r


def _quotes(d: Path, at: datetime, **px: tuple[float, float, float]) -> None:
    """px: SYMBOL=(last, bid, ask)."""
    ms = int(at.timestamp() * 1000)
    body = {s.replace("_", " "): {"quote": {"lastPrice": last, "bidPrice": bid, "askPrice": ask,
                                            "quoteTime": ms}} for s, (last, bid, ask) in px.items()}
    (d / "quotes.json").write_text(json.dumps(body))


async def _setup(store: Store, *, funding: Funding = "shares", direction: Direction = "up",
                 target: str = "55", inval: str = "48") -> tuple[int, int]:
    await ensure_book(store, TODAY, Decimal("3700"), T0)
    await store.upsert_bars("AAA", trend_bars(date(2026, 6, 1), 80, first="40", step="0.125"))
    call = await insert_call(store, NewCall(
        made_at=T0, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None, symbol="AAA",
        direction=direction, thesis="t" * 12, target=Decimal(target), invalidation=Decimal(inval),
        horizon_days=5, conviction=3, benchmark="XLK", ref_price=Decimal(50),
        spy_ref=Decimal(500), bench_ref=Decimal(200), funding=funding))
    shares = funding == "shares"
    p = await create_proposal(store, call_id=call.id, created_at=T0,
                              instrument="shares" if shares else "call",
                              symbol="AAA" if shares else OSI, underlying="AAA",
                              quantity=7 if shares else 1,
                              max_entry_price=Decimal("50.50") if shares else Decimal("2.60"),
                              atr_pct=Decimal(2) if shares else None)
    await mark_posted(store, p.id, T0, T0 + timedelta(minutes=10), "m1")
    return call.id, p.id


NOBODY = Reaction(veto=False, approve=False)


async def _run(store: Store, fx: Path, now: datetime, r: Reaction = NOBODY,
               notes: Notes | None = None) -> Any:
    return await run_desk_watch(store=store, broker=FakeBroker(fx, now), notifier=notes or Notes(),
                                reactions=Fixed(r), rules=RULES, desk=DeskConfig(),
                                reserve=RESERVE, now=now)


async def test_a_proposal_waits_for_its_window_then_fills_at_the_ask(
    desk_store: Store, tmp_path: Path,
) -> None:
    _, pid = await _setup(desk_store)
    _quotes(tmp_path, T0 + timedelta(minutes=5), AAA=(50.0, 49.98, 50.02))
    assert (await _run(desk_store, tmp_path, T0 + timedelta(minutes=5))).filled == []
    _quotes(tmp_path, T0 + timedelta(minutes=10), AAA=(50.0, 49.98, 50.02))
    rep = await _run(desk_store, tmp_path, T0 + timedelta(minutes=10))
    assert rep.filled == [pid]
    [pos] = await open_positions(desk_store)
    assert (pos.entry_price, pos.quantity) == (Decimal("50.02"), 7)
    assert pos.stop_trigger == Decimal("48.00")          # the invalidation beat the 8% formula
    assert await pending_proposals(desk_store) == []


async def test_approve_fills_early_and_a_veto_still_fills_flagged(
    desk_store: Store, tmp_path: Path,
) -> None:
    _, pid = await _setup(desk_store)
    now = T0 + timedelta(minutes=2)
    _quotes(tmp_path, now, AAA=(50.0, 49.98, 50.02))
    notes = Notes()
    rep = await _run(desk_store, tmp_path, now, Reaction(veto=True, approve=True), notes)
    assert rep.filled == [pid]
    row = await desk_store.fetchone("SELECT vetoed, approved FROM proposal_outcomes")
    assert row is not None and (row["vetoed"], row["approved"]) == (1, 1)
    assert "vetoed" in notes.posts[0]


async def test_a_price_that_ran_past_max_entry_is_skipped(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)
    now = T0 + timedelta(minutes=10)
    _quotes(tmp_path, now, AAA=(50.9, 50.8, 50.95))
    rep = await _run(desk_store, tmp_path, now)
    assert rep.filled == [] and rep.skipped[0][1] == "skipped_price"


async def test_an_ask_already_below_the_invalidation_is_skipped(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)
    now = T0 + timedelta(minutes=10)
    _quotes(tmp_path, now, AAA=(47.9, 47.8, 47.95))
    assert (await _run(desk_store, tmp_path, now)).skipped[0][1] == "skipped_invalid"


async def test_pending_entries_expire_at_15_55(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)
    late = datetime(2026, 9, 29, 19, 55, tzinfo=UTC)      # 15:55 ET
    _quotes(tmp_path, late, AAA=(50.0, 49.98, 50.02))
    assert (await _run(desk_store, tmp_path, late)).skipped[0][1] == "expired"


async def _held(store: Store, fx: Path, **kw: Any) -> int:
    _, pid = await _setup(store, **kw)
    now = T0 + timedelta(minutes=10)
    if kw.get("funding", "shares") == "shares":
        _quotes(fx, now, AAA=(50.0, 49.98, 50.02))
    else:
        _quotes(fx, now, AAA=(50.0, 49.98, 50.02), **{OSI.replace(" ", "_"): (2.45, 2.40, 2.50)})
    await _run(store, fx, now)
    return pid


async def test_a_share_stop_fills_at_the_trigger_or_at_the_bid_below_the_limit(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(47.5, 47.45, 47.55))       # through 48.00, above the 45.60 limit
    rep = await _run(desk_store, tmp_path, now)
    assert rep.exits and rep.exits[0][1] == "stop"
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "48.00"


async def test_a_gap_below_the_limit_fills_at_the_bid(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(44.0, 43.9, 44.1))
    await _run(desk_store, tmp_path, now)
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "43.90"


async def test_a_tightened_invalidation_raises_the_paper_stop(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    cid_row = await desk_store.fetchone("SELECT id FROM calls")
    assert cid_row is not None
    await tighten(desk_store, cid_row["id"], Decimal("49.5"), T0)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(49.4, 49.38, 49.42))
    assert (await _run(desk_store, tmp_path, now)).exits[0][1] == "stop"


async def test_the_target_exits_at_the_bid(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(55.1, 55.0, 55.2))
    assert (await _run(desk_store, tmp_path, now)).exits[0][1] == "target"


async def test_an_option_exits_when_its_underlying_crosses_the_invalidation(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _held(desk_store, tmp_path, funding="call")
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(47.9, 47.8, 48.0), **{OSI.replace(" ", "_"): (1.2, 1.1, 1.3)})
    rep = await _run(desk_store, tmp_path, now)
    assert rep.exits[0][1] == "invalidation"
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "1.10"          # the option's bid


async def test_an_option_closes_at_five_days_to_expiry(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path, funding="call")
    near = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)             # 4 days before 11/20
    _quotes(tmp_path, near, AAA=(50.0, 49.98, 50.02), **{OSI.replace(" ", "_"): (2.0, 1.95, 2.05)})
    assert (await _run(desk_store, tmp_path, near)).exits[0][1] == "dte_close"


async def test_a_resolved_call_exits_after_ten_not_before(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    cid_row = await desk_store.fetchone("SELECT id FROM calls")
    assert cid_row is not None
    await desk_store.execute(
        "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how, exit_price,"
        " ret_pct, spy_ret_pct, bench_ret_pct, written_at) VALUES ('call', ?, '2026-09-22', '50',"
        " '2026-09-28', 'horizon', '50', '0', '0', '0', 'x')", (cid_row["id"],))
    early = datetime(2026, 9, 30, 13, 55, tzinfo=UTC)      # 09:55 ET next day
    _quotes(tmp_path, early, AAA=(50.5, 50.4, 50.6))
    assert (await _run(desk_store, tmp_path, early)).exits == []
    later = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)       # 10:00 ET
    _quotes(tmp_path, later, AAA=(50.5, 50.4, 50.6))
    assert (await _run(desk_store, tmp_path, later)).exits[0][1] == "call_resolved"


async def test_pm_exits_and_legacy_recommendations(desk_store: Store, tmp_path: Path) -> None:
    pid = await _held(desk_store, tmp_path)
    await request_exit(desk_store, "AAA", pid, "thesis done", T0)
    await request_exit(desk_store, "CSX", None, "legacy: momentum broke", T0)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(51.0, 50.9, 51.1))
    notes = Notes()
    rep = await _run(desk_store, tmp_path, now, notes=notes)
    assert rep.exits[0][1] == "pm_exit" and rep.recommended == ["CSX"]
    assert any("CSX" in p and "Schwab" in p for p in notes.posts)


async def test_a_dead_token_is_reported_not_raised(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)

    class Dead(FakeBroker):
        async def quotes(self, symbols: Any) -> dict[str, Quote]:
            raise BrokerUnauthorized("401")

    now = T0 + timedelta(minutes=10)
    rep = await run_desk_watch(store=desk_store, broker=Dead(tmp_path, now), notifier=Notes(),
                               reactions=Fixed(Reaction(False, False)), rules=RULES,
                               desk=DeskConfig(), reserve=RESERVE, now=now)
    assert rep.blind is True and rep.skipped[0][1] == "skipped_blind"


# --- fix round 1 -------------------------------------------------------


async def test_a_proposal_from_an_earlier_session_expires_without_filling(
    desk_store: Store, tmp_path: Path,
) -> None:
    """A missed 15:55 fire must not let a proposal posted on an earlier ET
    date fill the next morning -- that is the overnight-resting entry
    CLAUDE.md §4.2 forbids."""
    await ensure_book(desk_store, TODAY, Decimal("3700"), T0)
    await desk_store.upsert_bars("AAA", trend_bars(date(2026, 6, 1), 80, first="40", step="0.125"))
    call = await insert_call(desk_store, NewCall(
        made_at=T0, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None, symbol="AAA",
        direction="up", thesis="t" * 12, target=Decimal("55"), invalidation=Decimal("48"),
        horizon_days=5, conviction=3, benchmark="XLK", ref_price=Decimal(50),
        spy_ref=Decimal(500), bench_ref=Decimal(200), funding="shares"))
    p = await create_proposal(desk_store, call_id=call.id, created_at=T0, instrument="shares",
                              symbol="AAA", underlying="AAA", quantity=7,
                              max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    posted = datetime(2026, 9, 28, 18, 50, tzinfo=UTC)              # 14:50 ET the day before
    await mark_posted(desk_store, p.id, posted, posted + timedelta(minutes=10), "m1")
    next_morning = datetime(2026, 9, 29, 13, 55, tzinfo=UTC)        # 09:55 ET
    _quotes(tmp_path, next_morning, AAA=(50.0, 49.98, 50.02))
    rep = await _run(desk_store, tmp_path, next_morning)
    assert rep.filled == [] and rep.skipped[0][1] == "expired"


async def test_a_raised_stop_recomputes_its_limit_and_gaps_to_the_bid(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _held(desk_store, tmp_path)
    cid_row = await desk_store.fetchone("SELECT id FROM calls")
    assert cid_row is not None
    await tighten(desk_store, cid_row["id"], Decimal("49.5"), T0)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(46.5, 46.45, 46.55))       # below the recomputed 47.02 limit
    rep = await _run(desk_store, tmp_path, now)
    assert rep.exits and rep.exits[0][1] == "stop"
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "46.45"


async def test_a_raised_stop_still_fills_at_the_trigger_above_its_recomputed_limit(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _held(desk_store, tmp_path)
    cid_row = await desk_store.fetchone("SELECT id FROM calls")
    assert cid_row is not None
    await tighten(desk_store, cid_row["id"], Decimal("49.5"), T0)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(48.0, 47.95, 48.05))       # between the 47.02 limit and the 49.50 trigger
    rep = await _run(desk_store, tmp_path, now)
    assert rep.exits and rep.exits[0][1] == "stop"
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "49.50"


async def test_an_option_entry_skips_when_the_underlying_is_already_through_the_invalidation(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _setup(desk_store, funding="call")
    now = T0 + timedelta(minutes=10)
    _quotes(tmp_path, now, AAA=(47.5, 47.4, 47.6), **{OSI.replace(" ", "_"): (2.45, 2.40, 2.50)})
    rep = await _run(desk_store, tmp_path, now)
    assert rep.filled == [] and rep.skipped[0][1] == "skipped_invalid"


async def test_a_missing_exit_quote_logs_a_warning(
    desk_store: Store, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now)                      # no symbols recorded: AAA's quote is missing
    with caplog.at_level(logging.WARNING, logger="tc.desk.watch"):
        rep = await _run(desk_store, tmp_path, now)
    assert rep.exits == []
    assert "AAA" in caplog.text


# --- final review C1: the caps are re-checked at the fill price ---------


async def test_a_fill_that_would_breach_a_cap_is_skipped_as_invalid(
    desk_store: Store, tmp_path: Path,
) -> None:
    """The PM proposed inside every cap, but the book moved during the veto
    window (here: another position filled and took 2,500 of cash). At the
    fill, 7 x 50.02 would leave 849.86 against the 900.00 reserve."""
    _, pid = await _setup(desk_store)
    other = await insert_call(desk_store, NewCall(
        made_at=T0, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
        symbol="ZZZ", direction="up", thesis="t" * 12, target=Decimal(60),
        invalidation=Decimal(40), horizon_days=5, conviction=4, benchmark="SPY",
        ref_price=Decimal(50), spy_ref=Decimal(500), bench_ref=Decimal(500), funding="shares"))
    z = await create_proposal(desk_store, call_id=other.id, created_at=T0, instrument="shares",
                              symbol="ZZZ", underlying="ZZZ", quantity=50,
                              max_entry_price=Decimal(50), atr_pct=Decimal(2))
    await record_fill(desk_store, z.id, T0, "buy", 50, Decimal(50), "entry")
    await record_outcome(desk_store, z.id, T0, "filled", vetoed=False, approved=False, detail={})
    now = T0 + timedelta(minutes=10)
    _quotes(tmp_path, now, AAA=(50.0, 49.98, 50.02), ZZZ=(50.0, 49.98, 50.02))
    rep = await _run(desk_store, tmp_path, now)
    assert rep.filled == [] and rep.skipped == [(pid, "skipped_invalid")]
    row = await desk_store.fetchone(
        "SELECT detail_json FROM proposal_outcomes WHERE proposal_id=?", (pid,))
    assert row is not None and "at the fill price" in row["detail_json"]
    assert "reserve" in row["detail_json"]
    assert [p.symbol for p in await open_positions(desk_store)] == ["ZZZ"]
