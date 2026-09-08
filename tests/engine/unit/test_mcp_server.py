"""The MCP surface over the wire: two mounts, one bearer middleware.

These tests deliberately serve the REAL Starlette app with uvicorn on a
loopback port rather than driving it through `httpx.ASGITransport`. Two
reasons, both learned from `0c-sdk-facts.md` §3.2:

* The gate IS the wire. A 401/403 decided by `RoleAuthMiddleware` before MCP
  ever sees the body is only meaningful if the request actually travelled the
  same path a Claude runner's request will.
* `streamable_http_app()` builds a `StreamableHTTPSessionManager` but does not
  start it, and mounting into our own app replaces FastMCP's lifespan with
  ours. Only a real ASGI server runs a lifespan, so a transport-level test
  would never catch the classic "forgot to enter session_manager.run()" 500.

No secret in this file is real (`test-token-not-real` shape); the tokens are
invented per-test and reach the app through `load_settings`' env file exactly
as the deployed ones will.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tc.broker.fake import FakeBroker
from tc.broker.token import TokenStore
from tc.config import Settings, load_settings
from tc.http.app import EngineState, McpMounts, build_app
from tc.mcp.registry import ROLE_TOOLS, Role
from tc.mcp.server import McpDeps, build_servers
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "engine" / "fixtures" / "broker"
NOW = datetime(2026, 9, 4, 17, 31, tzinfo=UTC)

# Invented for this file. Named so a grep for a leaked real credential can
# tell at a glance that these are not one.
RESEARCH_TOKEN = "research-token-not-real"  # noqa: S105 -- test fixture, not a credential
DECIDE_TOKEN = "decide-token-not-real"  # noqa: S105 -- test fixture, not a credential

CONFIG = """
engine:
  data_dir: {data}
  repo_dir: {repo}
  research_dir: {research}
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""

ENV = f"""
TC_SCHWAB_APP_KEY=k
TC_SCHWAB_APP_SECRET=s
TC_MCP_RESEARCH_TOKEN={RESEARCH_TOKEN}
TC_MCP_DECIDE_TOKEN={DECIDE_TOKEN}
"""

Served = tuple[str, dict[str, str]]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _token_store(settings: Settings, tmp_path: Path) -> TokenStore:
    return TokenStore(
        tmp_path / "token.json", settings.token, settings.schwab_app_key,
        settings.schwab_app_secret,
    )


@contextlib.asynccontextmanager
async def _serve(tmp_path: Path) -> AsyncIterator[Served]:
    """The real app -- routes, middleware and both MCP lifespans -- on a free
    loopback port."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG.format(data=tmp_path, repo=REPO, research=tmp_path / "research"))
    env = tmp_path / ".env"
    env.write_text(ENV)
    settings = load_settings(cfg, env)

    store = Store(tmp_path / "e.db")
    await store.open()
    deps = McpDeps(
        store=store,
        broker=FakeBroker(FIXTURES, NOW),
        docs=DocStore(tmp_path / "research", store),
        rules=Rules.load(REPO / "rules.yml"),
        settings=settings,
        clock=lambda: NOW,
        account_hash=lambda: "HASH_REDACTED",
    )
    state = EngineState(
        token=_token_store(settings, tmp_path),
        store=store,
        started_at=NOW,
        now=lambda: NOW,
        version="0.2.0",
        shadow=True,
    )
    app = build_app(
        state, mcp=McpMounts(servers=build_servers(deps), tokens=settings.mcp_tokens())
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    # uvicorn.Server publishes readiness as a polled attribute and offers no
    # event to await, so the poll is the only signal there is.
    while not server.started:  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    try:
        yield (
            f"http://127.0.0.1:{port}",
            {"research": RESEARCH_TOKEN, "decide": DECIDE_TOKEN},
        )
    finally:
        server.should_exit = True
        await task
        await store.close()


@pytest.fixture
def served(tmp_path: Path) -> Callable[[], contextlib.AbstractAsyncContextManager[Served]]:
    def factory() -> contextlib.AbstractAsyncContextManager[Served]:
        return _serve(tmp_path)

    return factory


# --- the gate ----------------------------------------------------------


async def test_no_bearer_is_401(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    async with served() as (base, _):
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{base}/mcp/research/", json={})
    assert r.status_code == 401


async def test_unknown_token_is_403(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    async with served() as (base, _):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={"Authorization": "Bearer nope"},
            )
    assert r.status_code == 403


async def test_research_token_on_the_decide_mount_is_403(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    """The decisive property: the roles are not merely different tool lists,
    they are different endpoints, and the credential names which one."""
    async with served() as (base, tokens):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/decide/", json={},
                headers={"Authorization": f"Bearer {tokens['research']}"},
            )
    assert r.status_code == 403


async def test_decide_token_on_the_research_mount_is_403(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    async with served() as (base, tokens):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={"Authorization": f"Bearer {tokens['decide']}"},
            )
    assert r.status_code == 403


async def test_non_bearer_scheme_is_401(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    async with served() as (base, tokens):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={"Authorization": f"Basic {tokens['research']}"},
            )
    assert r.status_code == 401


async def test_health_still_answers_unauthenticated_with_mcp_mounted(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    """The middleware is scoped to /mcp. An unattended probe carries no
    bearer and must still read the health body."""
    async with served() as (base, _):
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/health")
    assert r.status_code == 200
    assert r.json()["version"] == "0.2.0"


# --- the sessions ------------------------------------------------------


async def test_each_role_lists_exactly_its_registry(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    async with served() as (base, tokens):
        for role, expected in ROLE_TOOLS.items():
            headers = {"Authorization": f"Bearer {tokens[role]}"}
            async with streamablehttp_client(f"{base}/mcp/{role}/", headers=headers) as (
                r_, w_, _,
            ):
                async with ClientSession(r_, w_) as session:
                    await session.initialize()
                    names = sorted(t.name for t in (await session.list_tools()).tools)
            assert names == sorted(expected), role


async def test_ping_answers_over_http_on_both_roles(
    served: Callable[[], contextlib.AbstractAsyncContextManager[Served]],
) -> None:
    """One live call per role. It proves the lifespan entered both session
    managers -- the failure mode that is otherwise a 500 on first use."""
    roles: list[Role] = ["research", "decide"]
    async with served() as (base, tokens):
        for role in roles:
            headers = {"Authorization": f"Bearer {tokens[role]}"}
            async with streamablehttp_client(f"{base}/mcp/{role}/", headers=headers) as (
                r_, w_, _,
            ):
                async with ClientSession(r_, w_) as session:
                    await session.initialize()
                    res = await session.call_tool("ping", {})
            assert res.isError is False, role
            assert res.structuredContent is not None
            assert res.structuredContent["role"] == role
