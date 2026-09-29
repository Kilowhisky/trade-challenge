"""Task 17 fix round 1 (task-17-findings.md): posting a proposal to Discord.

Covers what `post_proposals` and `render_proposal` must get right that the
review caught:

* a proposal whose `created_at` predates today (ET) is stale and must expire
  rather than open a fresh veto window on yesterday's decision;
* a proposal whose Discord post itself failed must expire too -- marking it
  posted with a deadline anyway would let `desk_watch` fill it later with no
  veto window ever having existed;
* the rendered message states the stop (spec §9.4), or says plainly that the
  instrument carries none (CLAUDE.md §3.4: no stops on long options);
* a proposal naming a call that does not exist is a data-integrity bug and
  must raise, never drop silently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from tc.config import DeskConfig
from tc.desk.calls import NewCall, insert_call
from tc.desk.paper import create_proposal, pending_proposals, unposted_proposals
from tc.desk.post import post_proposals
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store

REPO = Path(__file__).resolve().parents[3]
RULES = Rules.load(REPO / "rules.yml")
DESK = DeskConfig()
TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 14, 5, tzinfo=UTC)  # 10:05 ET -- inside the entry window


class RecordingNotifier(Notifier):
    """`mid` is the id `post_message` hands back -- `None` simulates a
    failed Discord post (dead webhook, 401'd bot token, ...)."""

    def __init__(self, client: httpx.AsyncClient, mid: str | None = "12345") -> None:
        super().__init__(None, client)
        self.posts: list[str] = []
        self._mid = mid

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True

    async def post_message(self, text: str) -> str | None:
        self.posts.append(text)
        return self._mid


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def _call(**kw: object) -> NewCall:
    base: dict[str, object] = dict(
        made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
        symbol="AAA", direction="up", thesis="a thesis long enough", target=Decimal(55),
        invalidation=Decimal(48), horizon_days=10, conviction=3, benchmark="XLK",
        ref_price=Decimal(50), spy_ref=Decimal(500), bench_ref=Decimal(200), funding="shares",
    )
    base.update(kw)
    return NewCall.model_validate(base)


async def test_a_stale_unposted_proposal_expires_instead_of_posting(
    desk_store: Store, client: httpx.AsyncClient,
) -> None:
    call = await insert_call(desk_store, _call())
    yesterday = NOW - timedelta(days=1)
    await create_proposal(desk_store, call_id=call.id, created_at=yesterday, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    notifier = RecordingNotifier(client)
    posted = await post_proposals(desk_store, notifier, RULES, DESK, NOW)
    assert posted == []
    assert notifier.posts == []  # never even attempted
    assert await unposted_proposals(desk_store) == []
    assert await pending_proposals(desk_store) == []


async def test_a_failed_post_expires_the_proposal_rather_than_a_silent_later_fill(
    desk_store: Store, client: httpx.AsyncClient,
) -> None:
    """A `None` message id must not still get `mark_posted` with a
    `veto_deadline` -- `desk_watch` reads a missing message id as "no veto"
    and fills at the deadline regardless, which would be an entry executing
    with no veto window ever having existed."""
    call = await insert_call(desk_store, _call())
    await create_proposal(desk_store, call_id=call.id, created_at=NOW, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    notifier = RecordingNotifier(client, mid=None)
    posted = await post_proposals(desk_store, notifier, RULES, DESK, NOW)
    assert posted == []
    assert notifier.posts != []  # the post WAS attempted, and failed
    assert await pending_proposals(desk_store) == []  # resolved, not left dangling


async def test_a_successful_post_marks_the_proposal_posted_with_a_deadline(
    desk_store: Store, client: httpx.AsyncClient,
) -> None:
    call = await insert_call(desk_store, _call())
    await create_proposal(desk_store, call_id=call.id, created_at=NOW, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    notifier = RecordingNotifier(client)
    posted = await post_proposals(desk_store, notifier, RULES, DESK, NOW)
    assert len(posted) == 1
    [p] = await pending_proposals(desk_store)
    assert p.posted_at is not None
    assert p.veto_deadline is not None
    assert p.message_id == "12345"


async def test_the_posted_message_states_the_shares_stop(
    desk_store: Store, client: httpx.AsyncClient,
) -> None:
    call = await insert_call(desk_store, _call())
    await create_proposal(desk_store, call_id=call.id, created_at=NOW, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    notifier = RecordingNotifier(client)
    await post_proposals(desk_store, notifier, RULES, DESK, NOW)
    [text] = notifier.posts
    stop_line = next(line for line in text.splitlines() if line.startswith("stop "))
    assert "n/a" not in stop_line
    assert "/" in stop_line  # trigger/limit


async def test_the_posted_message_states_options_carry_no_stop(
    desk_store: Store, client: httpx.AsyncClient,
) -> None:
    call = await insert_call(desk_store, _call(funding="call"))
    await create_proposal(desk_store, call_id=call.id, created_at=NOW, instrument="call",
                          symbol="AAA  260101C00055000", underlying="AAA", quantity=1,
                          max_entry_price=Decimal("3.50"), atr_pct=None)
    notifier = RecordingNotifier(client)
    await post_proposals(desk_store, notifier, RULES, DESK, NOW)
    [text] = notifier.posts
    stop_line = next(line for line in text.splitlines() if line.startswith("stop "))
    assert "no resting stop" in stop_line


async def test_a_proposal_naming_a_missing_call_raises_rather_than_dropping_silently(
    desk_store: Store, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`call_id` is an enforced foreign key (schema.sql), so this can only
    happen through a bug -- exercised here by faking the lookup miss rather
    than corrupting the store, which the schema will not permit anyway."""
    call = await insert_call(desk_store, _call())
    await create_proposal(desk_store, call_id=call.id, created_at=NOW, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    monkeypatch.setattr("tc.desk.post.get_call", AsyncMock(return_value=None))
    notifier = RecordingNotifier(client)
    with pytest.raises(RuntimeError, match="does not exist"):
        await post_proposals(desk_store, notifier, RULES, DESK, NOW)
