"""Outbound-only side channels. Neither may raise into a loop: a missed
notification must never take down the sweep it was reporting on."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)
DISCORD_MAX = 1900
DISCORD_API = "https://discord.com/api/v10"


@dataclass(frozen=True)
class BotChannel:
    """Post as the account's own bot instead of through a webhook.

    Sending a message needs no gateway connection and no `discord.py`: it is
    one REST call carrying `Authorization: Bot <token>`, the same body a
    webhook takes. Reading reactions WOULD need the gateway, which is why the
    ✅/❌ approval gate is not built here — it arrives with the order path.
    """

    token: str
    channel_id: str

    @property
    def url(self) -> str:
        return f"{DISCORD_API}/channels/{self.channel_id}/messages"


class Notifier:
    """`prefix` tags every message the engine sends. Shadow mode may route to
    a different destination, so the tag is belt-and-braces — but it is the
    half a human reads, and it must apply to the loops that post for
    themselves (the token loop) as well as to the ones the engine posts for.
    Prefixing at the notifier rather than at each call site is what makes that
    true by construction.

    `target` is a webhook URL, a `BotChannel`, or None for an engine with no
    Discord at all.
    """

    def __init__(
        self,
        target: str | BotChannel | None,
        client: httpx.AsyncClient,
        prefix: str = "",
    ) -> None:
        self._target = target
        self._c = client
        self._prefix = prefix

    async def post(self, text: str) -> bool:
        if self._target is None:
            return False
        text = f"{self._prefix}{text}"
        if len(text) > DISCORD_MAX:
            text = text[: DISCORD_MAX - 1] + "…"
        if isinstance(self._target, BotChannel):
            url = self._target.url
            headers = {"Authorization": f"Bot {self._target.token}"}
        else:
            url, headers = self._target, {}
        try:
            r = await self._c.post(
                url, json={"content": text}, headers=headers, timeout=10
            )
        except httpx.HTTPError as e:
            log.warning("discord post failed: %s", e)
            return False
        if not 200 <= r.status_code < 300:
            # Status only. A revoked bot token answers 401 and an operator
            # needs to see that, but this line is exactly where the token
            # would leak if the request were logged instead.
            log.warning("discord post rejected: HTTP %d", r.status_code)
            return False
        return True


class Pinger:
    def __init__(self, base_url: str | None, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/") if base_url else None
        self._c = client

    async def _hit(self, path: str, body: str) -> bool:
        if not self._base:
            return False
        try:
            r = await self._c.post(f"{self._base}/{path}", content=body, timeout=10)
            return 200 <= r.status_code < 300
        except httpx.HTTPError as e:
            log.warning("healthcheck ping failed: %s", e)
            return False

    async def start(self, job: str) -> bool:
        return await self._hit(f"{job}/start", "")

    async def ok(self, job: str, verdict: str) -> bool:
        return await self._hit(job, verdict)

    async def fail(self, job: str, verdict: str) -> bool:
        return await self._hit(f"{job}/fail", verdict)
