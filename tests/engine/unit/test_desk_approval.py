"""Task 14: reading ✅/❌ off a proposal's Discord message."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import httpx

from tc.desk.approval import APPROVE, VETO, DiscordReactions, NoReactions, Reaction
from tc.notify import BotChannel


def _client(by_emoji: dict[str, Any], status: int = 200) -> httpx.AsyncClient:
    def h(req: httpx.Request) -> httpx.Response:
        emoji = unquote(req.url.path.rsplit("/", 1)[1])
        assert req.headers["authorization"] == "Bot tok"
        if emoji not in by_emoji:
            return httpx.Response(404, json={"message": "Unknown Emoji"})
        return httpx.Response(status, json=by_emoji[emoji])

    return httpx.AsyncClient(transport=httpx.MockTransport(h))


CH = BotChannel("tok", "555")


async def test_a_human_veto_counts_and_the_bot_s_own_reaction_does_not() -> None:
    r = DiscordReactions(CH, _client({VETO: [{"id": "1", "bot": False}],
                                      APPROVE: [{"id": "2", "bot": True}]}), None)
    assert await r.read("m") == Reaction(veto=True, approve=False)


async def test_with_an_approver_set_only_that_user_counts() -> None:
    r = DiscordReactions(CH, _client({VETO: [{"id": "1"}], APPROVE: [{"id": "7"}]}), "7")
    assert await r.read("m") == Reaction(veto=False, approve=True)


async def test_no_reactions_at_all_is_not_unreadable() -> None:
    assert await DiscordReactions(CH, _client({}), None).read("m") == Reaction(False, False)


async def test_a_failed_read_is_unreadable_not_a_silent_no() -> None:
    got = await DiscordReactions(CH, _client({VETO: []}, status=500), None).read("m")
    assert got.unreadable is True


async def test_no_reactions_reader() -> None:
    assert await NoReactions().read("m") == Reaction(False, False)
