"""Verdicts, budgets and allowlists for every scheduled Claude job.

`JOB_SPECS` is the one place the engine composes a `RunRequest`
(`runner/tc_runner/app.py`) from: which agent to dispatch, what it may call,
what shape its answer must take, and when it is allowed to fire. Everything
here is typed data rather than prose so a mistake is a startup-time failure —
`tools_for_role` raises `KeyError` on a tool the named role's server does not
expose, `output_schema` fails to build if a verdict model is malformed —
instead of a 403 the model discovers mid-job or a stray field a downstream
reader has to guess about.

Every `summary` field on a verdict model carries forward the v2 return line
(e.g. `"PASS 14:44 | HOT 2 | WATCH 5 …"`, `research.md` §D) as the
human-readable half of the JSON contract: nothing greps stdout any more
(spec §4), but the one line Chris reads in Discord still exists, now living
*inside* the structured result rather than beside it.

Budgets, windows and timeouts are the desk's own
(`docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md` §4, §13),
not v2 carry-overs — operational constants, not `rules.yml` risk parameters,
so they are not `<!--rule:...-->` numbers and do not appear in `rules.yml`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tc.mcp.registry import ROLE_TOOLS, Role

# ---------------------------------------------------------------------------
# Verdict models. `extra="forbid"` everywhere: a stray field is a defect in
# the model's answer, not a value silently dropped on the floor.
# ---------------------------------------------------------------------------


class AnalystVerdict(BaseModel):
    """An analyst pass. The pitches themselves are rows written through
    `pitch_submit`; the verdict only names them, so a verdict that fails to
    parse loses nothing but the summary line."""

    model_config = ConfigDict(extra="forbid")
    pitched: list[int]
    withdrawn: list[int]
    summary: str


class HeldDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    action: Literal["hold", "exit", "tighten"]
    reason: str


class PmVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    held: list[HeldDecision]
    calls: list[int]
    summary: str


class MiddayVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    held: list[HeldDecision]
    summary: str


# How deep a `$ref` chain may be before inlining calls it a cycle. The verdict
# models nest one level (`PmVerdict` -> `HeldDecision`); anything approaching
# this is a model shape nobody meant to write.
MAX_REF_DEPTH = 8


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The verdict's JSON schema with every `$ref` resolved and no `$defs`.

    `model_json_schema()` already emits `additionalProperties: false`, because
    every verdict model above forbids extras — but it also lifts each nested
    model (`HeldDecision`) into `$defs` and leaves a `$ref` behind.
    That schema is handed straight to the CLI as `--output-format json_schema`,
    and a reference is one more thing between the model and a valid answer:
    the constraints on `held[]` are stated somewhere the object being
    described does not point at in plain sight. Inlining costs a few duplicated
    lines and removes the indirection entirely.

    Raises on an unresolvable or recursive reference rather than emitting a
    schema with a dangling `$ref`: a verdict model this cannot flatten is a
    model to restructure, and the failure belongs at import time (where every
    `JOB_SPECS` entry is built) rather than in the CLI's parser.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    flat = _inline(schema, defs, ())
    assert isinstance(flat, dict)  # a JSON-schema root is an object by construction
    return flat


def _inline(node: Any, defs: dict[str, Any], seen: tuple[str, ...]) -> Any:
    """Replace `{"$ref": "#/$defs/X"}` with X's definition, everywhere.

    Sibling keys win over the target's own: pydantic writes
    `{"$ref": ..., "description": ...}` when a field carries its own
    description, and that description is about the field, not the model.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.removeprefix("#/$defs/")
            if name == ref or name not in defs:
                raise ValueError(f"output schema carries an unresolvable $ref: {ref!r}")
            if name in seen or len(seen) >= MAX_REF_DEPTH:
                raise ValueError(
                    f"output schema cannot be flattened: $ref cycle through {name!r}"
                )
            target = _inline(defs[name], defs, (*seen, name))
            siblings = {k: _inline(v, defs, seen) for k, v in node.items() if k != "$ref"}
            return {**target, **siblings}
        return {k: _inline(v, defs, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs, seen) for v in node]
    return node


# ---------------------------------------------------------------------------
# Allowlist construction. Every job below runs as either the "research" or
# the "decide" MCP role (registry.py) — `tools_for_role` checks a declared
# name against that role's own registry, so a typo, or a tool the OTHER role
# declares, fails at import time rather than as a refusal mid-job.
# ---------------------------------------------------------------------------


def tools_for_role(role: Role, *names: str) -> tuple[str, ...]:
    """Prefix each name with `mcp__engine__` after checking it against that
    role's registry, so a typo -- or a tool of the other role -- fails at
    import time rather than as a refusal mid-job."""
    role_tools = ROLE_TOOLS[role]
    for name in names:
        if name not in role_tools:
            raise KeyError(name)
    return tuple(f"mcp__engine__{name}" for name in names)


# ---------------------------------------------------------------------------
# `JobSpec`
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobSpec:
    name: str
    agent: str
    command: str
    prompt: str
    allowed_tools: tuple[str, ...]
    verdict: type[BaseModel]
    max_turns: int
    timeout_s: float
    window: tuple[time, time]
    # Typed as the registry's `Role`, not a bare `str`: every job here is
    # "research" (registry.py — no job in this file holds an account or
    # order tool), and typing it precisely is what lets `ROLE_TOOLS[spec.role]`
    # type-check anywhere a spec is inspected, rather than needing a cast.
    role: Role
    noop_when: Callable[[BaseModel], bool] | None
    # Only the PM carries this (spec §14): a failure inside the market's one
    # daily entry window is worth one retry before falling back to "no
    # entries today"; every other job's next scheduled fire is retry enough.
    retry_failed_after_s: float | None = None


# ---------------------------------------------------------------------------
# The trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md §4)
# ---------------------------------------------------------------------------

_WEB: tuple[str, ...] = ("WebSearch", "WebFetch")
_ANALYST_ALLOW = tools_for_role(
    "research", "get_datetime", "market_hours", "quotes", "price_history", "instruments",
    "briefing", "pitch_submit", "pitch_withdraw", "my_record",
) + _WEB
_PM_ALLOW = tools_for_role(
    "decide", "get_datetime", "market_hours", "quotes", "price_history", "option_chain", "book",
    "paper_book", "pitches_read", "scorecard", "option_candidates", "call_submit", "call_extend",
    "call_tighten", "exit_request",
) + _WEB

_EVENING_PROMPT = (
    "EVENING PASS. Follow your agent instructions for one evening pass: read "
    "mcp__engine__briefing, file at most five pitches with mcp__engine__pitch_submit, and "
    "never size, fund or trade. Return a single JSON object matching the AnalystVerdict "
    "schema and nothing else."
)
_PREOPEN_PROMPT = (
    "PRE-OPEN MODE. It is before the 09:30 open. Read overnight news and pre-market reports "
    "against your open pitches and your briefing. File at most two NEW pitches, and withdraw "
    "any of your own open pitches whose premise broke overnight with "
    "mcp__engine__pitch_withdraw. Return a single JSON object matching the AnalystVerdict "
    "schema and nothing else."
)


def _evening(name: str, agent: str) -> JobSpec:
    return JobSpec(
        name=name, agent=agent, command=f"/desk {name}", prompt=_EVENING_PROMPT,
        allowed_tools=_ANALYST_ALLOW, verdict=AnalystVerdict, max_turns=40, timeout_s=1500.0,
        window=(time(16, 25), time(20, 0)), role="research", noop_when=None,
    )


def _preopen(name: str, agent: str) -> JobSpec:
    return JobSpec(
        name=name, agent=agent, command=f"/desk {name}", prompt=_PREOPEN_PROMPT,
        allowed_tools=_ANALYST_ALLOW, verdict=AnalystVerdict, max_turns=20, timeout_s=600.0,
        window=(time(7, 55), time(9, 0)), role="research", noop_when=None,
    )


DESK_SPECS: dict[str, JobSpec] = {
    "analyst_technical": _evening("analyst_technical", "analyst-technical"),
    "analyst_earnings": _evening("analyst_earnings", "analyst-earnings"),
    "analyst_news": _evening("analyst_news", "analyst-news"),
    "analyst_macro": _evening("analyst_macro", "analyst-macro"),
    "preopen_news": _preopen("preopen_news", "analyst-news"),
    "preopen_earnings": _preopen("preopen_earnings", "analyst-earnings"),
    "pm": JobSpec(
        name="pm", agent="pm", command="/desk pm",
        prompt=(
            "Follow your agent instructions for the 09:50 run: the book first, then up to "
            "five calls, then funding. Return a single JSON object matching the PmVerdict "
            "schema and nothing else."
        ),
        allowed_tools=_PM_ALLOW, verdict=PmVerdict, max_turns=40, timeout_s=900.0,
        # Latest start 10:30 (spec §13): a PM that cannot start by then makes
        # no entries that day, and the 900s retry must also land inside it.
        window=(time(9, 45), time(10, 30)), role="decide", noop_when=None,
        retry_failed_after_s=900.0,
    ),
    "pm_midday": JobSpec(
        name="pm_midday", agent="pm", command="/desk pm_midday",
        prompt=(
            "MIDDAY MODE. Held positions and today's news only: hold, mcp__engine__exit_request "
            "or mcp__engine__call_tighten. No new calls. Return a single JSON object matching "
            "the MiddayVerdict schema and nothing else."
        ),
        allowed_tools=_PM_ALLOW, verdict=MiddayVerdict, max_turns=15, timeout_s=300.0,
        window=(time(12, 25), time(13, 30)), role="decide", noop_when=None,
    ),
}
JOB_SPECS: dict[str, JobSpec] = dict(DESK_SPECS)

# Chains run their jobs one after another inside one engine job, so the
# one-job-at-a-time runner never sees two of them collide (the 2026-09-08
# catalyst run was lost to "runner busy"). A chained job has no schedule entry
# of its own.
DESK_CHAINS: dict[str, tuple[str, ...]] = {
    "desk_evening": ("analyst_technical", "analyst_earnings", "analyst_news", "analyst_macro"),
    "desk_preopen": ("preopen_news", "preopen_earnings"),
}
CHAINED_JOBS: frozenset[str] = frozenset(j for jobs in DESK_CHAINS.values() for j in jobs)
