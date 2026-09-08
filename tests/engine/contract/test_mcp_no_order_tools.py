"""Spec §10: for EVERY MCP role, no tool name may match place|cancel|replace|order.

This is the contract test the design calls "the single largest safety
simplification": the order path is deterministic engine code, and the model's
only write-shaped tools will be `propose_*` (Plan 1). It is checked twice, on
purpose:

* against the declared registry table, which is what `jobs/spec.py` builds each
  job's allowlist from and what `rules/consistency.py` fails the build on; and
* against the LIVE servers, so a tool registered without being listed -- the
  case a table-only check is blind to -- still trips it.

The live half also pins live == declared in both directions. A name in the
table that nothing registers is as much a defect as a tool nothing declared:
the first ships an allowlist entry for a tool that does not exist, the second
ships a tool no allowlist reviewed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from tc.broker.fake import FakeBroker
from tc.config import Settings, load_settings
from tc.mcp.registry import FORBIDDEN, ROLE_TOOLS, Role, forbidden_tools
from tc.mcp.server import McpDeps, build_servers
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "engine" / "fixtures" / "broker"
NOW = datetime(2026, 9, 4, 17, 31, tzinfo=UTC)

CONFIG = """
engine:
  data_dir: {p}
  repo_dir: {repo}
  research_dir: {p}/research
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\n"


def _settings(tmp_path: Path) -> Settings:
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG.format(p=tmp_path, repo=REPO))
    env = tmp_path / ".env"
    env.write_text(ENV)
    return load_settings(cfg, env)


@pytest.fixture
async def engine_servers(tmp_path: Path) -> AsyncIterator[dict[Role, FastMCP]]:
    store = Store(tmp_path / "e.db")
    await store.open()
    deps = McpDeps(
        store=store,
        broker=FakeBroker(FIXTURES, NOW),
        docs=DocStore(tmp_path / "research", store),
        rules=Rules.load(REPO / "rules.yml"),
        settings=_settings(tmp_path),
        clock=lambda: NOW,
        account_hash=lambda: "HASH_REDACTED",
    )
    try:
        # allow_stubs: tasks 8 and 9 supply the bodies. The §10 contract is
        # about NAMES, and a stub carries its declared name exactly, so the
        # check is meaningful before the tools exist. `build_servers` refuses
        # a stub-filled server by default (test_mcp_server.py) precisely so
        # nothing else gets one by accident.
        yield build_servers(deps, allow_stubs=True)
    finally:
        await store.close()


def test_registry_declares_no_order_shaped_tool() -> None:
    assert forbidden_tools() == []


def test_the_pattern_would_catch_the_names_it_exists_to_catch() -> None:
    """A guard on the guard: a regex that matched nothing would make every
    assertion above vacuously true."""
    for name in (
        "place_order", "cancel_order", "replace_order", "get_orders", "order",
        # The near misses that a plausible registry actually proposes. The
        # document writer is `doc_write` and the universe writer is
        # `universe_write` precisely because these names are refused.
        "doc_replace", "replace_universe", "order_status", "cancel_stop",
    ):
        assert FORBIDDEN.search(name), name


async def test_no_live_server_exposes_an_order_shaped_tool(
    engine_servers: dict[Role, FastMCP],
) -> None:
    for role, server in engine_servers.items():
        names = [t.name for t in await server.list_tools()]
        assert names, f"{role} registered no tools at all"
        offenders = [n for n in names if FORBIDDEN.search(n)]
        assert offenders == [], f"{role} exposes {offenders}"


async def test_live_servers_match_the_declared_registry(
    engine_servers: dict[Role, FastMCP],
) -> None:
    assert sorted(engine_servers) == sorted(ROLE_TOOLS)
    for role, server in engine_servers.items():
        live = sorted(t.name for t in await server.list_tools())
        assert live == sorted(ROLE_TOOLS[role]), role


async def test_the_two_roles_have_disjoint_write_surfaces(
    engine_servers: dict[Role, FastMCP],
) -> None:
    """Not one server filtering by token: FastMCP's registry is process-global,
    so a filtered single server would be a gate inside the thing being gated.
    Two instances mean a decide-only tool is unreachable with a research
    token rather than merely unchosen."""
    assert engine_servers["research"] is not engine_servers["decide"]
    research = {t.name for t in await engine_servers["research"].list_tools()}
    decide = {t.name for t in await engine_servers["decide"].list_tools()}
    assert "book" in decide and "book" not in research
    assert "evidence_append" in research and "evidence_append" not in decide
