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


def _scripted(responses: dict[str, list[httpx.Response]]) -> httpx.AsyncClient:
    """Each emoji answers from its own queue, in order."""

    def h(req: httpx.Request) -> httpx.Response:
        return responses[unquote(req.url.path.rsplit("/", 1)[1])].pop(0)

    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def _limited(retry_after: float) -> httpx.Response:
    return httpx.Response(429, json={"message": "You are being rate limited.",
                                     "retry_after": retry_after, "global": False})


async def test_a_rate_limited_read_waits_as_told_and_retries() -> None:
    # 2026-09-30: the third proposal's ✅ read hit 429 on every desk_watch run.
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    client = _scripted({VETO: [httpx.Response(200, json=[])],
                        APPROVE: [_limited(0.4), httpx.Response(200, json=[{"id": "1"}])]})
    got = await DiscordReactions(CH, client, None, sleep=sleep).read("m")
    assert got == Reaction(veto=False, approve=True)
    assert waits == [0.4]


async def test_a_rate_limit_that_persists_is_unreadable_and_the_wait_is_capped() -> None:
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    client = _scripted({VETO: [httpx.Response(200, json=[])],
                        APPROVE: [_limited(60), _limited(60), _limited(60)]})
    got = await DiscordReactions(CH, client, None, sleep=sleep).read("m")
    assert got.unreadable is True
    assert waits == [5.0, 5.0]


async def test_a_veto_that_was_read_counts_even_when_the_approval_read_failed() -> None:
    client = _scripted({VETO: [httpx.Response(200, json=[{"id": "1"}])],
                        APPROVE: [httpx.Response(500, json={})]})
    got = await DiscordReactions(CH, client, None).read("m")
    assert got == Reaction(veto=True, approve=False, unreadable=True)


async def test_an_approval_does_not_count_when_the_veto_could_not_be_read() -> None:
    client = _scripted({VETO: [httpx.Response(500, json={})],
                        APPROVE: [httpx.Response(200, json=[{"id": "1"}])]})
    got = await DiscordReactions(CH, client, None).read("m")
    assert got == Reaction(veto=False, approve=False, unreadable=True)
