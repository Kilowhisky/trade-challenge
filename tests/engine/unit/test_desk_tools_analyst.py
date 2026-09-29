"""Task 8: the analyst tools, driven the way the model drives them
(`ToolManager.call_tool`, so the schema validation layer is exercised)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from desk_fixtures import bar, desk_deps, flat_bars
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.fake import FakeBroker
from tc.desk.models import ActiveJob
from tc.mcp import tools_desk
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)
PITCH: dict[str, Any] = {
    "symbol": "XLK", "direction": "up", "thesis": "semis leadership is broadening out",
    "evidence": [{"url": "https://example.com/x", "claim": "breadth improved", "date": "2026-09-28"}],
    "target": "110", "invalidation": "95", "horizon_days": 5, "conviction": 3, "benchmark": "XLK",
}


async def _server(store: Store, tmp_path: Path, job: str | None) -> FastMCP:
    await store.upsert_bars("XLK", flat_bars(date(2026, 9, 1), 20, "100"))
    server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_desk.register(server, desk_deps(store, tmp_path, FakeBroker(FIX, EVENING), EVENING, job),
                        "research")
    return server


async def call(server: FastMCP, tool: str, /, **arguments: Any) -> Any:
    return await server._tool_manager.call_tool(tool, arguments)


# --- fakes for the MCP `Context` the runner's X-TC-Job header travels in ---
#
# `ToolManager.call_tool` and `Tool.run` never check that `context` IS a real
# `mcp.server.fastmcp.Context` -- they only read `.request_context.request`
# off whatever is handed in -- so a minimal stand-in exercises the REAL tool
# function's real header-reading code path (tools_desk._caller_job) without
# standing up streamable-http transport end to end, which nothing else in
# this test module (or test_tools_read.py) does either.


class _FakeHeaders:
    def __init__(self, data: dict[str, str]) -> None:
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key.lower(), default)


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = _FakeHeaders(headers)


class _FakeRequestContext:
    def __init__(self, request: _FakeRequest | None) -> None:
        self.request = request


class _FakeCtx:
    """Stands in for `Context`: `.request_context.request.headers.get(...)`,
    with `request=None` for "a context exists but not inside an HTTP
    request" (`headers=None`)."""

    def __init__(self, headers: dict[str, str] | None) -> None:
        self.request_context = _FakeRequestContext(
            _FakeRequest(headers) if headers is not None else None
        )


class _CtxWithNoRequestContextAtAll:
    """Stands in for the `Context() ` built with no request context at all:
    `.request_context` itself raises, the way the real property does when
    `get_context()` is called outside of any request."""

    @property
    def request_context(self) -> Any:
        raise ValueError("Context is not available outside of a request")


async def test_no_running_analyst_job_means_no_desk_tools(desk_store: Store, tmp_path: Path) -> None:
    server = await _server(desk_store, tmp_path, None)
    with pytest.raises(ToolError, match="no analyst job is running"):
        await call(server, "pitch_submit", **PITCH)


async def test_pitch_submit_files_under_the_running_analyst(desk_store: Store, tmp_path: Path) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    out = await call(server, "pitch_submit", **PITCH)
    assert out.session == "2026-09-29"
    row = await desk_store.fetchone("SELECT analyst FROM pitches WHERE id=?", (out.id,))
    assert row is not None and row["analyst"] == "macro"


async def test_the_analyst_is_not_an_argument_the_model_can_pass(
    desk_store: Store, tmp_path: Path,
) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    tool = server._tool_manager.get_tool("pitch_submit")
    assert tool is not None and "analyst" not in tool.parameters["properties"]


async def test_a_refusal_reaches_the_model_as_text_it_can_act_on(
    desk_store: Store, tmp_path: Path,
) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    with pytest.raises(ToolError, match="target > last price > invalidation"):
        await call(server, "pitch_submit", **{**PITCH, "target": "99"})


async def test_withdraw_and_record_round_trip(desk_store: Store, tmp_path: Path) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    out = await call(server, "pitch_submit", **PITCH)
    # still before the 9/29 open, so withdrawable
    got = await call(server, "pitch_withdraw", pitch_id=out.id)
    assert got.withdrawn == out.id
    rec = await call(server, "my_record")
    assert rec.rows == []


async def test_briefing_answers_for_the_running_analyst(desk_store: Store, tmp_path: Path) -> None:
    await desk_store.upsert_bars("SPY", [bar(d, 1, 1, 1, 1) for d in
                                         [date(2026, 9, 28)]])
    server = await _server(desk_store, tmp_path, "analyst_macro")
    b = await call(server, "briefing")
    assert b.analyst == "macro" and [r.symbol for r in b.rows] == ["XLK"]


async def test_the_x_tc_job_header_names_the_caller_even_with_no_active_job(
    desk_store: Store, tmp_path: Path,
) -> None:
    """The deploy plan's seeding path: `tc run --once analyst_technical` is a
    SEPARATE process from the serving engine the runner's MCP calls land on,
    so that engine's own `deps.active` is None even though a job plainly is
    running. The runner stamps `X-TC-Job` on every call for exactly this
    case (runner/tc_runner/app.py `mcp_servers`); this drives the real
    `pitch_submit` tool with a header-carrying context and no active job at
    all, and the pitch must still land under the header's analyst."""
    server = await _server(desk_store, tmp_path, None)
    ctx = cast(Any, _FakeCtx({"X-TC-Job": "analyst_news"}))
    out = await server._tool_manager.call_tool("pitch_submit", PITCH, context=ctx)
    assert out.session == "2026-09-29"
    row = await desk_store.fetchone("SELECT analyst FROM pitches WHERE id=?", (out.id,))
    assert row is not None and row["analyst"] == "news"


def _deps_stub(active_name: str | None) -> Any:
    class _DepsStub:
        def __init__(self) -> None:
            self.active = ActiveJob(active_name)

    return _DepsStub()


def test_caller_job_prefers_the_header_over_the_active_job() -> None:
    deps = _deps_stub("analyst_macro")
    ctx = cast(Any, _FakeCtx({"X-TC-Job": "analyst_news"}))
    assert tools_desk._caller_job(deps, ctx) == "analyst_news"


def test_a_live_request_without_the_header_is_refused_not_attributed() -> None:
    """Final review M4: a second MCP client holding the research bearer,
    calling during the evening chain, must not be filed as the running
    analyst."""
    deps = _deps_stub("analyst_macro")
    ctx = cast(Any, _FakeCtx({}))
    assert tools_desk._caller_job(deps, ctx) is None


def test_a_context_with_no_live_request_is_refused_too() -> None:
    deps = _deps_stub("analyst_macro")
    ctx = cast(Any, _FakeCtx(None))
    assert tools_desk._caller_job(deps, ctx) is None


def test_caller_job_falls_back_to_the_active_job_with_no_context_at_all() -> None:
    deps = _deps_stub("analyst_macro")
    assert tools_desk._caller_job(deps, None) == "analyst_macro"


def test_caller_job_never_raises_when_the_context_has_no_request_context_at_all() -> None:
    """It answers "nobody" rather than raising, and does not fall back to the
    running job either: only a call with no context at all does (M4)."""
    deps = _deps_stub("analyst_macro")
    ctx = cast(Any, _CtxWithNoRequestContextAtAll())
    assert tools_desk._caller_job(deps, ctx) is None
