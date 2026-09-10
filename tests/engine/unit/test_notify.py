import json
import logging
from collections.abc import Callable

import httpx
import pytest

from tc.notify import DISCORD_MAX, BotChannel, Notifier, Pinger


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_notifier_posts_content_and_truncates() -> None:
    seen: dict[str, bytes] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["json"] = req.read()
        return httpx.Response(204)

    n = Notifier("https://discord.test/hook", _client(h))
    assert await n.post("x" * 2500) is True
    body = seen["json"].decode()
    assert '"content"' in body and "…" in body and len(body) < 2100


async def test_notifier_never_raises() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await Notifier("https://discord.test/hook", _client(h)).post("hi") is False


async def test_notifier_disabled_without_webhook() -> None:
    assert await Notifier(None, _client(lambda r: httpx.Response(204))).post("hi") is False


async def test_pinger_paths_and_bodies() -> None:
    calls: list[tuple[str, str]] = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append((str(req.url), req.read().decode()))
        return httpx.Response(200)

    p = Pinger("https://hc.test/abc", _client(h))
    await p.start("tick")
    await p.ok("tick", "done")
    await p.fail("tick", "timeout")
    assert calls == [
        ("https://hc.test/abc/tick/start", ""),
        ("https://hc.test/abc/tick", "done"),
        ("https://hc.test/abc/tick/fail", "timeout"),
    ]


async def test_pinger_disabled_without_base() -> None:
    assert await Pinger(None, _client(lambda r: httpx.Response(200))).ok("tick", "done") is False


# --- posting as the bot ------------------------------------------------------

async def test_notifier_posts_as_a_bot_to_the_channel_endpoint() -> None:
    """A bot destination is the same body against a different URL, with the
    token in a header — no gateway, no discord.py."""
    seen: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization", "")
        seen["content"] = json.loads(req.read())["content"]
        return httpx.Response(200)

    n = Notifier(BotChannel("s3cret", "12345"), _client(h))
    assert await n.post("hi") is True
    assert seen["url"] == "https://discord.com/api/v10/channels/12345/messages"
    assert seen["auth"] == "Bot s3cret"
    assert seen["content"] == "hi"


async def test_notifier_bot_truncates_like_the_webhook() -> None:
    seen: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["content"] = json.loads(req.read())["content"]
        return httpx.Response(200)

    assert await Notifier(BotChannel("t", "1"), _client(h)).post("x" * 2500) is True
    assert len(seen["content"]) == DISCORD_MAX and seen["content"].endswith("…")


async def test_notifier_logs_a_rejection_without_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A revoked bot token returns 401 and the engine must say so — but the
    log line is where a secret would leak, so it carries the status only."""
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    with caplog.at_level(logging.WARNING):
        assert await Notifier(BotChannel("s3cret", "1"), _client(h)).post("hi") is False
    assert "401" in caplog.text
    assert "s3cret" not in caplog.text


async def test_notifier_bot_never_raises() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await Notifier(BotChannel("t", "1"), _client(h)).post("hi") is False
