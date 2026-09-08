"""Task 12: job specs and verdict models — allowlists, budgets, noop predicates.

`JOB_SPECS` is typed data, not prose: a typo in a tool name is a `KeyError` at
import time (`tools_for`), not a tool the model silently cannot call and not a
runtime 403 from the MCP gate discovered mid-job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tc.jobs.spec import JOB_SPECS, DeepVerdict, ScoutVerdict, output_schema, tools_for
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
    """Spec §10, checked again at the job-spec layer: `tools_for` only ever
    draws from `ROLE_TOOLS["research"]`, which `test_mcp_no_order_tools.py`
    already proves is order-free — this pins that guarantee survives the
    trip through `JOB_SPECS` too, so a future job spec cannot smuggle an
    order-shaped name in by hand-typing the `mcp__engine__` prefix."""
    from tc.mcp.registry import FORBIDDEN

    for spec in JOB_SPECS.values():
        for tool in spec.allowed_tools:
            name = tool.removeprefix("mcp__engine__")
            assert not FORBIDDEN.search(name), tool


def test_schema_forbids_extra_keys_so_a_stray_field_fails_the_verdict() -> None:
    schema = output_schema(ScoutVerdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {"cohort", "observed", "escalations", "summary"}


def test_tools_for_rejects_a_tool_no_role_has() -> None:
    with pytest.raises(KeyError):
        tools_for("doc_delete")


def test_noop_predicates_fire_only_on_a_genuinely_empty_pass() -> None:
    scout = JOB_SPECS["scout"]
    assert scout.noop_when is not None
    assert scout.noop_when(ScoutVerdict(cohort=0, observed=0, escalations=[], summary="")) is True
    assert scout.noop_when(ScoutVerdict(cohort=4, observed=0, escalations=[], summary="")) is False


def test_deep_verdict_accepts_a_postclose_with_no_hot_fresh() -> None:
    v = DeepVerdict(kind="postclose", wrote=["scorecard"], hot_fresh=[], notes="", summary="ok")
    assert v.hot_fresh == []


def test_research_and_sector_tag_noop_predicates_are_none() -> None:
    """§ brief: zero HOT / zero new tags are ordinary results of a pass that
    did its work, not an empty pass — these two jobs never noop."""
    assert JOB_SPECS["research"].noop_when is None
    assert JOB_SPECS["sector_tag"].noop_when is None


def test_catalyst_and_deep_noop_predicates() -> None:
    from tc.jobs.spec import CatalystVerdict

    catalyst = JOB_SPECS["catalyst"]
    assert catalyst.noop_when is not None
    assert catalyst.noop_when(
        CatalystVerdict(scanned=0, observed=0, escalations=[], summary="")
    ) is True
    assert catalyst.noop_when(
        CatalystVerdict(scanned=3, observed=0, escalations=[], summary="")
    ) is False

    for kind in ("preopen", "postclose"):
        spec = JOB_SPECS[kind]
        noop_when = spec.noop_when
        assert noop_when is not None
        assert noop_when(
            DeepVerdict(kind=kind, wrote=[], hot_fresh=[], notes="", summary="")
        ) is True
        assert noop_when(
            DeepVerdict(kind=kind, wrote=["x"], hot_fresh=[], notes="", summary="")
        ) is False


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
        ScoutVerdict(cohort=0, observed=0, escalations=[], summary="", bogus=1)  # type: ignore[call-arg]
