"""Task 12: job specs and verdict models — allowlists, budgets, noop predicates.

`JOB_SPECS` is typed data, not prose: a typo in a tool name is a `KeyError` at
import time (`tools_for_role`), not a tool the model silently cannot call and
not a runtime 403 from the MCP gate discovered mid-job.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from tc.jobs.spec import JOB_SPECS, AnalystVerdict, PmVerdict, output_schema, tools_for_role
from tc.mcp.registry import ROLE_TOOLS

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo_root() -> Path:
    return REPO


def test_every_spec_names_a_real_agent_file(repo_root: Path) -> None:
    for spec in JOB_SPECS.values():
        assert (repo_root / ".claude" / "agents" / f"{spec.agent}.md").exists(), spec.name


def test_every_allowed_tool_is_reachable_by_that_role() -> None:
    for spec in JOB_SPECS.values():
        for tool in spec.allowed_tools:
            if tool.startswith("mcp__engine__"):
                assert tool.removeprefix("mcp__engine__") in ROLE_TOOLS[spec.role], tool
            else:
                assert tool in {"WebSearch", "WebFetch", "Read"}, tool


def test_no_job_is_allowed_a_write_surface_tool() -> None:
    banned = {"Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Task", "Agent"}
    for spec in JOB_SPECS.values():
        assert not (set(spec.allowed_tools) & banned), spec.name


def test_no_allowed_tool_is_order_shaped() -> None:
    """Spec §10, checked again at the job-spec layer: `tools_for_role` only
    ever draws from a role's own `ROLE_TOOLS` entry, which
    `test_mcp_no_order_tools.py` already proves is order-free — this pins
    that guarantee survives the trip through `JOB_SPECS` too, so a future
    job spec cannot smuggle an order-shaped name in by hand-typing the
    `mcp__engine__` prefix."""
    from tc.mcp.registry import FORBIDDEN

    for spec in JOB_SPECS.values():
        for tool in spec.allowed_tools:
            name = tool.removeprefix("mcp__engine__")
            assert not FORBIDDEN.search(name), tool


def test_schema_forbids_extra_keys_so_a_stray_field_fails_the_verdict() -> None:
    schema = output_schema(AnalystVerdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"pitched", "withdrawn", "summary"}


def test_no_job_schema_carries_a_ref_or_defs() -> None:
    """The schema goes to the CLI as `--output-format json_schema`, and a
    `$ref` puts the constraints on a nested field (e.g. `held[]`) somewhere
    the object being described does not point at in plain sight. Every
    nested model is inlined.
    """
    for name, spec in JOB_SPECS.items():
        text = json.dumps(output_schema(spec.verdict))
        assert "$ref" not in text, name
        assert "$defs" not in text, name


def test_the_inlined_schema_still_constrains_the_nested_model() -> None:
    """Inlining must move the definition, not drop it: `HeldDecision` keeps
    its own `additionalProperties: false`, required keys and action enum."""
    held = output_schema(PmVerdict)["properties"]["held"]["items"]
    assert held["type"] == "object" and held["additionalProperties"] is False
    assert set(held["required"]) == {"symbol", "action", "reason"}
    assert held["properties"]["action"]["enum"] == ["hold", "exit", "tighten"]


def test_a_schema_that_cannot_be_flattened_is_a_failure_not_a_dangling_ref() -> None:
    """A recursive verdict model is a model to restructure. It must not emit a
    schema whose `$ref` points at a `$defs` block this just removed."""

    class Recursive(BaseModel):
        model_config = ConfigDict(extra="forbid")
        child: Recursive | None = None

    with pytest.raises(ValueError, match="cycle"):
        output_schema(Recursive)


def test_tools_for_rejects_a_tool_no_role_has() -> None:
    with pytest.raises(KeyError):
        tools_for_role("research", "doc_delete")
    with pytest.raises(KeyError):
        tools_for_role("research", "call_submit")      # a decide tool


def test_every_spec_has_a_sane_window_and_budget() -> None:
    for spec in JOB_SPECS.values():
        start, end = spec.window
        assert start < end, spec.name
        assert spec.timeout_s > 0, spec.name
        assert 0 < spec.max_turns <= 200, spec.name
        assert spec.role in ROLE_TOOLS, spec.name


def test_verdict_models_forbid_extra_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AnalystVerdict(pitched=[], withdrawn=[], summary="", bogus=1)  # type: ignore[call-arg]
