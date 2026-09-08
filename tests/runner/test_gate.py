"""The PreToolUse gate: the layer that outranks `allowed_tools` (0c-sdk-facts §1.3)."""

from __future__ import annotations

import pytest

from tc_runner.app import DENY_ALWAYS, make_gate, tool_allowed

ALLOWED = ["mcp__engine__quotes", "mcp__engine__doc_read", "WebSearch", "Read"]


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("mcp__engine__quotes", True),
        ("WebSearch", True),
        ("Read", True),
        ("mcp__engine__doc_replace", False),  # a real tool, not on THIS job's list
        ("Bash", False),
        ("Write", False),
        ("Glob", False),
    ],
)
def test_tool_allowed_is_exact(name: str, ok: bool) -> None:
    assert tool_allowed(name, ALLOWED) is ok


def test_websearch_and_webfetch_pass_when_a_job_lists_them() -> None:
    allowed = ["WebSearch", "WebFetch"]
    assert tool_allowed("WebSearch", allowed) is True
    assert tool_allowed("WebFetch", allowed) is True


def test_wildcard_entry_matches_the_server_prefix_only() -> None:
    allowed = ["mcp__engine__*"]
    assert tool_allowed("mcp__engine__quotes", allowed) is True
    assert tool_allowed("mcp__other__quotes", allowed) is False
    assert tool_allowed("Bash", allowed) is False


def test_a_bare_star_does_not_open_the_deny_list() -> None:
    # A job spec that says "everything" still cannot reach the write surface.
    assert tool_allowed("mcp__engine__quotes", ["*"]) is True
    assert tool_allowed("Bash", ["*"]) is False
    assert tool_allowed("Write", ["*"]) is False


@pytest.mark.parametrize("name", ["Bash", "Write", "Edit", "NotebookEdit", "Agent"])
def test_the_write_surface_is_in_the_permanent_deny_list(name: str) -> None:
    assert name in DENY_ALWAYS


@pytest.mark.parametrize("name", DENY_ALWAYS)
async def test_always_denied_even_when_a_job_lists_them(name: str) -> None:
    gate = make_gate([*ALLOWED, name])  # a mistake in a job spec
    out = await gate({"tool_name": name, "tool_input": {}}, "tu_1", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert name in out["hookSpecificOutput"]["permissionDecisionReason"]


async def test_allowed_tool_gets_no_opinion() -> None:
    gate = make_gate(ALLOWED)
    assert await gate({"tool_name": "WebSearch", "tool_input": {}}, "tu_1", None) == {}


async def test_unlisted_tool_is_denied_with_the_reason_the_model_sees() -> None:
    gate = make_gate(ALLOWED)
    out = await gate({"tool_name": "mcp__engine__doc_replace", "tool_input": {}}, "tu_1", None)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "not on this job's allowlist" in hso["permissionDecisionReason"]


async def test_a_missing_tool_name_is_denied_not_waved_through() -> None:
    gate = make_gate(ALLOWED)
    out = await gate({"tool_input": {}}, "tu_1", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
