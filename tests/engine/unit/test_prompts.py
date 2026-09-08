from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tc.jobs.spec import JOB_SPECS
from tc.mcp.registry import ROLE_TOOLS

ROOT = Path(__file__).resolve().parents[3]
LIVE_COMMANDS = ["research", "deep-research", "scout", "catalyst", "sector-tag"]
LIVE_AGENTS = ["research-scout", "deep-research", "scout", "catalyst", "sector-tagger"]
RETIRED = [
    ".claude/commands/weekly-universe.md", ".claude/commands/tick.md",
    ".claude/agents/weekly-universe.md", ".claude/agents/tick-watch.md",
    ".claude/agents/session-close.md", ".claude/agents/trader.md",
]


def frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    assert text.startswith("---\n")
    result: dict[str, Any] = yaml.safe_load(text.split("---\n", 2)[1])
    return result


def live_files() -> list[Path]:
    return (
        [ROOT / ".claude" / "commands" / f"{n}.md" for n in LIVE_COMMANDS]
        + [ROOT / ".claude" / "agents" / f"{n}.md" for n in LIVE_AGENTS]
    )


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_no_live_prompt_mentions_a_script_or_a_schwab_tool(path: Path) -> None:
    body = path.read_text()
    assert "scripts/" not in body, f"{path} still calls a shell script"
    assert "mcp__schwab__" not in body, f"{path} still names the old broker"
    assert "TC_RESEARCH_DIR" not in body


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_no_live_prompt_names_the_forbidden_write_tool(path: Path) -> None:
    # `doc_write` is the whole-document write tool (registry.py: "It is NOT
    # named `doc_replace`" -- FORBIDDEN matches `replace` as a substring, and
    # the model must never be taught that name).
    body = path.read_text()
    assert "doc_replace" not in body, f"{path} names the non-existent doc_replace tool"


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_no_live_prompt_contains_a_bash_call_or_heredoc(path: Path) -> None:
    body = path.read_text()
    assert "Bash(" not in body, f"{path} still shells out"
    assert "heredoc" not in body.lower(), f"{path} still describes a heredoc write"


@pytest.mark.parametrize("name", LIVE_AGENTS)
def test_agent_tools_are_engine_tools_only(name: str) -> None:
    fm = frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")
    tools = [t.strip() for t in fm["tools"].split(",")]
    banned = {"Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Task", "Agent"}
    assert not (set(tools) & banned), f"{name}: {sorted(set(tools) & banned)}"
    for t in tools:
        if t.startswith("mcp__engine__"):
            assert t.removeprefix("mcp__engine__") in ROLE_TOOLS["research"], t
        else:
            assert t in {"Read", "WebSearch", "WebFetch"}, t


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_every_engine_tool_named_in_a_live_prompt_is_in_the_registry(path: Path) -> None:
    body = path.read_text()
    for name in re.findall(r"mcp__engine__([a-zA-Z_]+)", body):
        assert name in ROLE_TOOLS["research"], f"{path} names unknown tool {name!r}"


def test_every_job_spec_agent_exists_and_its_tools_are_a_subset() -> None:
    for spec in JOB_SPECS.values():
        fm = frontmatter(ROOT / ".claude" / "agents" / f"{spec.agent}.md")
        assert fm["name"] == spec.agent
        tools = {t.strip() for t in fm["tools"].split(",")}
        assert tools <= set(spec.allowed_tools), (
            f"{spec.name}: {sorted(tools - set(spec.allowed_tools))}"
        )


@pytest.mark.parametrize("name", LIVE_AGENTS)
def test_every_agent_states_its_verdict_contract(name: str) -> None:
    body = (ROOT / ".claude" / "agents" / f"{name}.md").read_text()
    assert "Return the JSON object matching" in body
    assert "summary" in body


@pytest.mark.parametrize("rel", RETIRED)
def test_retired_files_are_tombstones_that_point_at_the_engine(rel: str) -> None:
    body = (ROOT / rel).read_text()
    assert "RETIRED" in body.splitlines()[0] or "RETIRED" in body[:400]
    assert "scripts/" not in body
    assert re.search(r"engine|Plan 1|tc/loops|tc/jobs", body)


def test_tick_tombstone_does_not_carry_the_cadence_string_check_5_greps() -> None:
    # scripts/check-consistency.sh check 5 greps tick.md for "**15 min** baseline"
    # and compares it against the crontab. With the string gone it skips the
    # comparison; with the string present in a tombstone it would compare a
    # cadence no document owns any more.
    assert "**15 min** baseline" not in (ROOT / ".claude/commands/tick.md").read_text()


def test_research_md_keeps_the_schedule_strings_check_5_greps() -> None:
    body = (ROOT / ".claude/commands/research.md").read_text()
    assert "hourly at :57" in body and "hours 9-14" in body
