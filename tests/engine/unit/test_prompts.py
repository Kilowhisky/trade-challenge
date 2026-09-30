from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tc.jobs.spec import JOB_SPECS
from tc.mcp.registry import ROLE_TOOLS, Role

ROOT = Path(__file__).resolve().parents[3]
LIVE_COMMANDS: list[str] = []
LIVE_AGENTS = [
    "analyst-technical", "analyst-earnings", "analyst-news", "analyst-macro", "pm",
]
# The MCP role each live agent's tools are checked against. Everything not
# listed runs as `research` (the analysts reuse that role and its bearer).
AGENT_ROLE: dict[str, Role] = {"pm": "decide"}
DESK_MODELS = {
    "analyst-technical": "sonnet", "analyst-earnings": "sonnet", "analyst-news": "sonnet",
    "analyst-macro": "sonnet", "pm": "opus",
}
RETIRED = [
    ".claude/commands/weekly-universe.md", ".claude/commands/tick.md",
    ".claude/agents/weekly-universe.md", ".claude/agents/tick-watch.md",
    ".claude/agents/session-close.md", ".claude/agents/trader.md",
    ".claude/commands/research.md", ".claude/commands/deep-research.md",
    ".claude/commands/scout.md", ".claude/commands/catalyst.md",
    ".claude/commands/sector-tag.md", ".claude/agents/research-scout.md",
    ".claude/agents/deep-research.md", ".claude/agents/scout.md",
    ".claude/agents/catalyst.md", ".claude/agents/sector-tagger.md",
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


def _role_of(path: Path) -> Role:
    return AGENT_ROLE.get(path.stem, "research") if path.parent.name == "agents" else "research"


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
    role = AGENT_ROLE.get(name, "research")
    for t in tools:
        if t.startswith("mcp__engine__"):
            assert t.removeprefix("mcp__engine__") in ROLE_TOOLS[role], t
        else:
            assert t in {"Read", "WebSearch", "WebFetch"}, t


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_every_engine_tool_named_in_a_live_prompt_is_in_the_registry(path: Path) -> None:
    body = path.read_text()
    role = _role_of(path)
    for name in re.findall(r"mcp__engine__([a-zA-Z_]+)", body):
        assert name in ROLE_TOOLS[role], f"{path} names unknown tool {name!r}"


@pytest.mark.parametrize("name,model", sorted(DESK_MODELS.items()))
def test_desk_agents_run_on_the_models_the_design_chose(name: str, model: str) -> None:
    assert frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")["model"] == model


@pytest.mark.parametrize("name", [n for n in DESK_MODELS if n != "pm"])
def test_no_analyst_is_handed_a_funding_or_call_tool(name: str) -> None:
    tools = frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")["tools"]
    for forbidden in ("call_submit", "option_candidates", "paper_book", "exit_request"):
        assert forbidden not in tools, f"{name} holds {forbidden}"


def test_every_job_spec_agent_exists_and_its_tools_are_a_subset() -> None:
    for spec in JOB_SPECS.values():
        fm = frontmatter(ROOT / ".claude" / "agents" / f"{spec.agent}.md")
        assert fm["name"] == spec.agent
        tools = {t.strip() for t in fm["tools"].split(",")}
        assert tools <= set(spec.allowed_tools), (
            f"{spec.name}: {sorted(tools - set(spec.allowed_tools))}"
        )


def test_shared_agent_tools_equal_the_union_of_every_job_that_dispatches_it() -> None:
    """An agent file's `tools:` is the SDK-enforced ceiling (0c-sdk-facts
    §1.5/§5), not a per-job overlay -- one file's header has to work for
    every mode it might run in. When two JobSpecs name the same agent (e.g.
    "analyst_news" and "preopen_news" both run `analyst-news`), the fix is to
    widen BOTH job specs to the union rather than narrow the frontmatter, so
    the ceiling stays honest about what either mode can actually reach. This
    pins that invariant for every agent shared by 2+ jobs: its declared
    tools are exactly the union of those jobs' allowed_tools -- not a
    subset (a stale, narrower frontmatter silently strands a job's tools,
    the regression this test exists to catch) and not a superset (a
    frontmatter tool no job grants can never actually be called). A
    single-job agent is already covered by the plain subset test above --
    equality there is that job's own tool-selection choice, not an
    SDK-ceiling constraint, so it is out of scope here."""
    by_agent: dict[str, set[str]] = {}
    for spec in JOB_SPECS.values():
        by_agent.setdefault(spec.agent, set()).update(spec.allowed_tools)
    for agent, union in by_agent.items():
        if sum(1 for spec in JOB_SPECS.values() if spec.agent == agent) < 2:
            continue
        fm = frontmatter(ROOT / ".claude" / "agents" / f"{agent}.md")
        tools = {t.strip() for t in fm["tools"].split(",")}
        assert tools == union, f"{agent}: {sorted(tools ^ union)}"


def test_a_single_job_agent_declares_exactly_its_job_s_allowlist() -> None:
    """For an agent only one JobSpec dispatches, the two lists are one fact
    written twice, so they must be equal in both directions.

    A frontmatter NARROWER than the allowlist strands a tool the job spec
    grants: the agent header is the SDK-enforced ceiling (0c-sdk-facts
    §1.5/§5), so the job is refused a tool the engine believes it has, and the
    plain subset test above passes over it silently -- that is exactly how
    `research-scout` ended up without `mcp__engine__rules` while its job spec
    granted it. A frontmatter WIDER than the allowlist is the mirror defect: a
    declared tool no job's `allowed_tools` covers is one the runner's gate
    denies, so the header advertises reach the job does not have.

    Shared agents are the separate case (below): their ceiling is the union of
    every job that dispatches them, not any one job's choice.
    """
    for spec in JOB_SPECS.values():
        if sum(1 for s in JOB_SPECS.values() if s.agent == spec.agent) > 1:
            continue
        fm = frontmatter(ROOT / ".claude" / "agents" / f"{spec.agent}.md")
        tools = {t.strip() for t in fm["tools"].split(",")}
        assert tools == set(spec.allowed_tools), (
            f"{spec.agent}: {sorted(tools ^ set(spec.allowed_tools))}"
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
