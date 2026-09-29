"""Who reacted to a proposal (spec §9.4): ❌ vetoes, ✅ executes at once.

REST, not the gateway: Discord serves the users behind one emoji on one
message through a plain GET with the bot token, and the desk only needs the
answer at a proposal's deadline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from tc.notify import DISCORD_API, BotChannel

log = logging.getLogger(__name__)
VETO = "❌"
APPROVE = "✅"


@dataclass(frozen=True)
class Reaction:
    veto: bool
    approve: bool
    unreadable: bool = False


class Reactions(Protocol):
    async def read(self, message_id: str) -> Reaction: ...


class NoReactions:
    """An engine with no bot channel: nobody can react, so nothing is vetoed
    and nothing is approved early -- a proposal simply runs at its deadline."""

    async def read(self, message_id: str) -> Reaction:
        return Reaction(veto=False, approve=False)


class DiscordReactions:
    def __init__(
        self, channel: BotChannel, client: httpx.AsyncClient, approver_id: str | None
    ) -> None:
        self._ch = channel
        self._c = client
        self._approver = approver_id

    async def _users(self, message_id: str, emoji: str) -> list[dict[str, Any]] | None:
        url = (
            f"{DISCORD_API}/channels/{self._ch.channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji)}"
        )
        try:
            r = await self._c.get(url, headers={"Authorization": f"Bot {self._ch.token}"},
                                  timeout=10)
        except httpx.HTTPError as e:
            log.warning("discord reactions read failed: %s", type(e).__name__)
            return None
        if r.status_code == 404:
            return []          # nobody has reacted with this emoji
        if not 200 <= r.status_code < 300:
            log.warning("discord reactions read rejected: HTTP %d", r.status_code)
            return None
        try:
            body = r.json()
        except ValueError:
            return None
        return body if isinstance(body, list) else None

    def _counts(self, users: list[dict[str, Any]]) -> bool:
        return any(
            not u.get("bot") and (self._approver is None or str(u.get("id")) == self._approver)
            for u in users
        )

    async def read(self, message_id: str) -> Reaction:
        vetoes = await self._users(message_id, VETO)
        approvals = await self._users(message_id, APPROVE)
        if vetoes is None or approvals is None:
            return Reaction(veto=False, approve=False, unreadable=True)
        return Reaction(veto=self._counts(vetoes), approve=self._counts(approvals))
