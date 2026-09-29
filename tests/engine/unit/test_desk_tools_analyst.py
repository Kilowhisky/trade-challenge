"""Task 8: the analyst tools, driven the way the model drives them
(`ToolManager.call_tool`, so the schema validation layer is exercised)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from desk_fixtures import bar, desk_deps, flat_bars
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.fake import FakeBroker
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
