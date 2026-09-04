"""Outbound-only side channels. Neither may raise into a loop: a missed
notification must never take down the sweep it was reporting on."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
DISCORD_MAX = 1900


class Notifier:
    def __init__(self, webhook: str | None, client: httpx.AsyncClient) -> None:
        self._url = webhook
        self._c = client

    async def post(self, text: str) -> bool:
        if not self._url:
            return False
        if len(text) > DISCORD_MAX:
            text = text[: DISCORD_MAX - 1] + "…"
        try:
            r = await self._c.post(self._url, json={"content": text}, timeout=10)
            return 200 <= r.status_code < 300
        except httpx.HTTPError as e:
            log.warning("discord post failed: %s", e)
            return False


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
