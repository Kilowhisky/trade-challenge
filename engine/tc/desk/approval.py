"""Who reacted to a proposal (spec §9.4): ❌ vetoes, ✅ executes at once.

REST, not the gateway: Discord serves the users behind one emoji on one
message through a plain GET with the bot token, and the desk only needs the
answer at a proposal's deadline.

Two reads per proposal per desk_watch run, back to back, trip Discord's
per-route rate limit at the sixth: on 2026-09-30 the third proposal's ✅ read
came back 429 on every run and its reactions were never seen. A 429 is
therefore waited out (Discord says how long) and retried, and the two emoji
are judged separately -- a veto that was read is a veto even when the ✅ read
failed."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from tc.notify import DISCORD_API, BotChannel

log = logging.getLogger(__name__)
VETO = "❌"
APPROVE = "✅"
# Retries after a 429, each waiting what Discord asked for, capped so one
# desk_watch run is never parked for long on a single emoji.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_WAIT_CAP_S = 5.0


@dataclass(frozen=True)
class Reaction:
    """`unreadable` is set when either emoji could not be read. A veto that
    WAS read still counts; an approval counts only when the veto read
    succeeded too, so a ✅ never hastens a fill past a ❌ nobody could see."""

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
        self, channel: BotChannel, client: httpx.AsyncClient, approver_id: str | None,
        *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ch = channel
        self._c = client
        self._approver = approver_id
        self._sleep = sleep

    async def _users(self, message_id: str, emoji: str) -> list[dict[str, Any]] | None:
        url = (
            f"{DISCORD_API}/channels/{self._ch.channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji)}"
        )
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                r = await self._c.get(url, headers={"Authorization": f"Bot {self._ch.token}"},
                                      timeout=10)
            except httpx.HTTPError as e:
                log.warning("discord reactions read failed: %s", type(e).__name__)
                return None
            if r.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            await self._sleep(_retry_after(r))
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
        return Reaction(
            veto=vetoes is not None and self._counts(vetoes),
            approve=vetoes is not None and approvals is not None and self._counts(approvals),
            unreadable=vetoes is None or approvals is None,
        )


def _retry_after(r: httpx.Response) -> float:
    """Seconds Discord asked us to wait: the body's `retry_after`, else the
    `Retry-After` header, else one second -- clamped to the cap."""
    wait: Any = None
    try:
        body = r.json()
        if isinstance(body, dict):
            wait = body.get("retry_after")
    except ValueError:
        pass
    if wait is None:
        wait = r.headers.get("retry-after")
    try:
        secs = float(wait) if wait is not None else 1.0
    except (TypeError, ValueError):
        secs = 1.0
    return min(max(secs, 0.0), RATE_LIMIT_WAIT_CAP_S)
