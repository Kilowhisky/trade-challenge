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
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

import tc.mcp.server as server_mod
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
STARTUP_TIMEOUT_S = 20.0

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
async def _serve(tmp_path: Path, *, under: str = "") -> AsyncIterator[Served]:
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
    # allow_stubs: tasks 8 and 9 supply the real tool bodies. These tests are
    # about the mounting, the gate and the lifespan, so they say so explicitly
    # rather than getting a stub-filled server by default.
    app = build_app(
        state,
        mcp=McpMounts(
            servers=build_servers(deps, allow_stubs=True), tokens=settings.mcp_tokens()
        ),
    )
    served_app: Starlette = app
    if under:
        # A parent Mount is what puts a `root_path` on the scope, which is the
        # exact condition under which the router's path and `request.url.path`
        # diverge.
        served_app = Starlette(routes=[Mount(under, app=app)])
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(served_app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    # uvicorn.Server publishes readiness as a polled attribute and offers no
    # event to await, so the poll is the only signal there is. Bounded, because
    # an unbounded one turns "the server failed to start" into a hung suite
    # with no message.
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_S
    while not server.started:
        if asyncio.get_running_loop().time() > deadline:
            server.should_exit = True
            await task
            raise TimeoutError(f"uvicorn did not start within {STARTUP_TIMEOUT_S}s")
        await asyncio.sleep(0.05)
    try:
        yield (
            f"http://127.0.0.1:{port}{under}",
            {"research": RESEARCH_TOKEN, "decide": DECIDE_TOKEN},
        )
    finally:
        server.should_exit = True
        await task
        await store.close()


Factory = Callable[..., contextlib.AbstractAsyncContextManager[Served]]


@pytest.fixture
def served(tmp_path: Path) -> Factory:
    def factory(*, under: str = "") -> contextlib.AbstractAsyncContextManager[Served]:
        return _serve(tmp_path, under=under)

    return factory


# --- the gate ----------------------------------------------------------


async def test_no_bearer_is_401(
    served: Factory,
) -> None:
    async with served() as (base, _):
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{base}/mcp/research/", json={})
    assert r.status_code == 401


async def test_unknown_token_is_403(
    served: Factory,
) -> None:
    async with served() as (base, _):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={"Authorization": "Bearer nope"},
            )
    assert r.status_code == 403


async def test_research_token_on_the_decide_mount_is_403(
    served: Factory,
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
    served: Factory,
) -> None:
    async with served() as (base, tokens):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={"Authorization": f"Bearer {tokens['decide']}"},
            )
    assert r.status_code == 403


async def test_non_bearer_scheme_is_401(
    served: Factory,
) -> None:
    async with served() as (base, tokens):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={"Authorization": f"Basic {tokens['research']}"},
            )
    assert r.status_code == 401


async def test_health_still_answers_unauthenticated_with_mcp_mounted(
    served: Factory,
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
    served: Factory,
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
    served: Factory,
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


# --- the gate cannot be walked around by a prefix ----------------------


async def test_gate_holds_when_the_app_is_mounted_under_a_prefix(served: Factory) -> None:
    """A parent `Mount` puts `root_path` on the scope. The router picks a
    mount with the STRIPPED path, so a gate reading `request.url.path` sees
    `/engine/mcp/decide/`, decides it is not an MCP request, and waves it
    through to a mount the router still resolves. The gate must read the same
    string the router does.
    """
    async with served(under="/engine") as (base, _):
        assert base.endswith("/engine")
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{base}/mcp/decide/", json={})
    assert r.status_code == 401


async def test_role_mismatch_still_403s_under_a_prefix(served: Factory) -> None:
    async with served(under="/engine") as (base, tokens):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/decide/", json={},
                headers={"Authorization": f"Bearer {tokens['research']}"},
            )
    assert r.status_code == 403


async def test_non_ascii_bearer_is_403_not_a_500(served: Factory) -> None:
    """`hmac.compare_digest` raises on a non-ASCII str and the attacker
    chooses the header. ASGI decodes header bytes as latin-1, so a high byte
    arrives as a non-ASCII Python string -- sent here as raw bytes because
    httpx will not encode such a value from a str. A rejected credential must
    stay a rejection, not become a 500.
    """
    async with served() as (base, _):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{base}/mcp/research/", json={},
                headers={b"Authorization": b"Bearer \xe9\xe9\xe9"},
            )
    assert r.status_code == 403


# --- build-time refusals ----------------------------------------------


def _deps(tmp_path: Path, store: Store) -> McpDeps:
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG.format(data=tmp_path, repo=REPO, research=tmp_path / "research"))
    env = tmp_path / ".env"
    env.write_text(ENV)
    return McpDeps(
        store=store,
        broker=FakeBroker(FIXTURES, NOW),
        docs=DocStore(tmp_path / "research", store),
        rules=Rules.load(REPO / "rules.yml"),
        settings=load_settings(cfg, env),
        clock=lambda: NOW,
        account_hash=lambda: "HASH_REDACTED",
    )


@pytest.fixture
async def deps(tmp_path: Path) -> AsyncIterator[McpDeps]:
    store = Store(tmp_path / "e.db")
    await store.open()
    try:
        yield _deps(tmp_path, store)
    finally:
        await store.close()


async def test_default_build_refuses_a_declared_tool_with_no_registrar(
    deps: McpDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this, `live == declared` is unfalsifiable: with no registrars
    at all every declared name becomes a stub and every check goes green over
    a server that answers nothing. Production takes the default, so an
    unimplemented tool fails boot instead of shipping.

    The missing name is injected rather than relying on the real table still
    having a hole in it -- otherwise this test quietly stops testing anything
    the day the last tool module lands.
    """
    monkeypatch.setitem(
        ROLE_TOOLS, "research", (*ROLE_TOOLS["research"], "not_yet_built")
    )
    with pytest.raises(RuntimeError) as e:
        build_servers(deps)
    assert "declares tools no registrar supplies" in str(e.value)
    assert "not_yet_built" in str(e.value)


async def test_allow_stubs_stubs_exactly_the_missing_name(
    deps: McpDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        ROLE_TOOLS, "research", (*ROLE_TOOLS["research"], "not_yet_built")
    )
    servers = build_servers(deps, allow_stubs=True)
    tools = {t.name: t for t in await servers["research"].list_tools()}
    assert "not_yet_built" in tools
    assert tools["not_yet_built"].description == "NOT IMPLEMENTED YET: not_yet_built"


async def test_allow_stubs_is_the_only_way_to_get_a_stub_server(deps: McpDeps) -> None:
    servers = build_servers(deps, allow_stubs=True)
    names = {t.name for t in await servers["research"].list_tools()}
    assert names == set(ROLE_TOOLS["research"])


async def test_build_refuses_an_order_shaped_name_in_the_declared_table(
    deps: McpDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §10 enforced at boot, not only in CI: the contract test and the
    consistency check both read this table, and neither runs in the deployed
    process."""
    monkeypatch.setitem(ROLE_TOOLS, "research", ("ping", "cancel_order"))
    with pytest.raises(ValueError, match="order-shaped"):
        build_servers(deps, allow_stubs=True)


async def test_build_refuses_an_order_shaped_name_a_registrar_added(
    deps: McpDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registrar could add a name the table never mentioned. The undeclared
    check would catch it too, but §10 is the louder message and must not
    depend on which check happens to fire first."""

    def bad(server: FastMCP, _deps: McpDeps, _role: Role) -> None:
        server.add_tool(lambda: "no", name="place_order", description="x")

    monkeypatch.setitem(ROLE_TOOLS, "research", ("ping", "place_order"))
    monkeypatch.setitem(server_mod._REGISTRARS, "research", [bad])
    with pytest.raises(ValueError, match="order-shaped"):
        build_servers(deps, allow_stubs=True)


async def test_build_refuses_a_registrar_that_adds_an_undeclared_tool(
    deps: McpDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rogue(server: FastMCP, _deps: McpDeps, _role: Role) -> None:
        server.add_tool(lambda: "hi", name="not_in_the_table", description="x")

    monkeypatch.setitem(server_mod._REGISTRARS, "research", [rogue])
    with pytest.raises(ValueError, match="undeclared"):
        build_servers(deps, allow_stubs=True)


async def test_a_registrar_replaces_the_stub_rather_than_adding_a_name(
    deps: McpDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook the wiring uses, with the signature the tool modules already
    expose -- `(server, deps, role)`, because a module can give one role a tool
    it does not give the other. A registered tool is the live one; the stub
    filler skips names that already exist."""

    def real_quotes(server: FastMCP, _deps: McpDeps, role: Role) -> None:
        server.add_tool(lambda: "real", name="quotes", description=f"the real one for {role}")

    monkeypatch.setitem(server_mod._REGISTRARS, "research", [real_quotes])
    servers = build_servers(deps, allow_stubs=True)
    tools = {t.name: t for t in await servers["research"].list_tools()}
    assert set(tools) == set(ROLE_TOOLS["research"])
    assert tools["quotes"].description == "the real one for research"
