from collections.abc import Callable

import httpx

from tc.notify import Notifier, Pinger


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
