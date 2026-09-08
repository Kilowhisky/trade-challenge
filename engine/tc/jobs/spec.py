"""Verdicts, budgets and allowlists for every scheduled Claude job.

`JOB_SPECS` is the one place the engine composes a `RunRequest`
(`runner/tc_runner/app.py`) from: which agent to dispatch, what it may call,
what shape its answer must take, and when it is allowed to fire. Everything
here is typed data rather than prose so a mistake is a startup-time failure —
`tools_for` raises `KeyError` on a tool no research-role server exposes,
`output_schema` fails to build if a verdict model is malformed — instead of a
403 the model discovers mid-job or a stray field a downstream reader has to
guess about.

Every `summary` field on a verdict model carries forward the v2 return line
(e.g. `"PASS 14:44 | HOT 2 | WATCH 5 …"`, `research.md` §D) as the
human-readable half of the JSON contract: nothing greps stdout any more
(spec §4), but the one line Chris reads in Discord still exists, now living
*inside* the structured result rather than beside it.

Budgets, windows and timeouts are the v2 crontab and `scheduled-run.sh`
figures carried over unchanged (`.superpowers/research/0c-writers-contract.md`
§2.0) — operational constants, not `rules.yml` risk parameters, so they are
not `<!--rule:...-->` numbers and do not appear in `rules.yml`.
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


class Escalation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    claim: str
    evidence_ids: list[str] = []


class HotFresh(BaseModel):
    """One candidate newly verified HOT this pass — the v2 `HOT-FRESH:` line,
    structured. `ref` carries the price AND its quote timestamp together
    (`"48.99@2026-09-07T14:03:11Z"`) because a reference price with no
    timestamp is unverifiable the moment it is read back."""

    model_config = ConfigDict(extra="forbid")
    symbol: str
    sleeve: Literal["core", "catalyst", "option"]
    ref: str
    thesis: str


class ScoutVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cohort: int
    observed: int
    escalations: list[Escalation]
    summary: str


class CatalystVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scanned: int
    observed: int
    escalations: list[Escalation]
    summary: str


class DeepVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["preopen", "postclose"]
    wrote: list[str]
    hot_fresh: list[HotFresh]
    notes: str
    summary: str


class ResearchVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hot: int
    watch: int
    tomb: int
    hot_fresh: list[HotFresh]
    standing_stale: bool
    summary: str


class SectorVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: int
    tagged: int
    new: int
    retired: int
    cohort: int
    summary: str


# How deep a `$ref` chain may be before inlining calls it a cycle. The verdict
# models nest one level (`ScoutVerdict` -> `Escalation`); anything approaching
# this is a model shape nobody meant to write.
MAX_REF_DEPTH = 8


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The verdict's JSON schema with every `$ref` resolved and no `$defs`.

    `model_json_schema()` already emits `additionalProperties: false`, because
    every verdict model above forbids extras — but it also lifts each nested
    model (`Escalation`, `HotFresh`) into `$defs` and leaves a `$ref` behind.
    That schema is handed straight to the CLI as `--output-format json_schema`,
    and a reference is one more thing between the model and a valid answer:
    the constraints on `escalations[]` are stated somewhere the object being
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
# Allowlist construction. Every scheduled job in this file runs as the
# "research" MCP role (registry.py: no job here holds an account or order
# tool by construction) — `tools_for` is scoped to that role for exactly that
# reason, so a name that only `decide` declares fails the same way a typo
# would.
# ---------------------------------------------------------------------------


def tools_for(*names: str) -> tuple[str, ...]:
    """Prefix each declared tool name with `mcp__engine__`, after checking it
    against `ROLE_TOOLS["research"]`.

    Raises `KeyError` — not a silent pass-through — so a typo'd tool name in
    a job spec is a failure at import time, when this module is first
    evaluated, rather than a tool the model can never reach discovered only
    when a job runs and gets refused.
    """
    role_tools = ROLE_TOOLS["research"]
    for name in names:
        if name not in role_tools:
            raise KeyError(name)
    return tuple(f"mcp__engine__{name}" for name in names)


# The harness tools every job may also reach, beyond the engine's own MCP
# surface: WebSearch/WebFetch are how a job crosses the "non-mainstream
# source" and "web sweep" steps every command file above describes, and Read
# is how it follows the instruction (below) to open its own command file.
# `sector-tagger.md`'s own tool grant excludes WebFetch (§2.6 of the writers
# contract: "No WebFetch" — one Schwab call, total, and no article fetching),
# so it is the one job below that does not get it.
_WEB_AND_READ: tuple[str, ...] = ("WebSearch", "WebFetch", "Read")
_SEARCH_AND_READ: tuple[str, ...] = ("WebSearch", "Read")


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


# `noop_when` predicates. Each takes the base `BaseModel` type (never the
# narrower verdict subtype) so a job spec is never unsound to call with the
# "wrong" verdict shape — the `isinstance` assertion below is what actually
# narrows it, and it is the caller's job (the dispatcher) to hand back the
# verdict this spec itself declared.


def _scout_noop(v: BaseModel) -> bool:
    # An empty cohort is a correct answer between earnings seasons and
    # before the first weekly sweep — `noop`, not `done`, because `done` on
    # sixty consecutive empty passes is the "every job green, nothing ever
    # happens" failure the design exists to catch.
    assert isinstance(v, ScoutVerdict)
    return v.cohort == 0


def _catalyst_noop(v: BaseModel) -> bool:
    # `scanned == 0` means no sectors are tagged yet (research/sectors.tsv
    # absent) — the channel had nothing to scan, not nothing to say.
    assert isinstance(v, CatalystVerdict)
    return v.scanned == 0


def _deep_noop(v: BaseModel) -> bool:
    # Preopen/postclose always write at least one document on a real pass;
    # `wrote == []` is the run finding nothing to do at all, e.g. a halted
    # day or a precondition that stopped it before its first write.
    assert isinstance(v, DeepVerdict)
    return v.wrote == []


# research and sector_tag have no `noop_when`: zero HOT and zero new tags are
# ordinary results of a pass that did its work, not a pass that found nothing
# to do — see the brief's step 3 rationale, restated at each job below.

JOB_SPECS: dict[str, JobSpec] = {
    "scout": JobSpec(
        name="scout",
        agent="scout",
        command="/scout",
        prompt=(
            "Follow .claude/commands/scout.md §B through §D exactly for one "
            "information-edge scout pass over the active earnings cohort. "
            "Escalate only evidence that clears the §C corroboration bar — "
            "gather and corroborate, never form a thesis or trade. Return a "
            "single JSON object matching the ScoutVerdict schema and nothing "
            "else."
        ),
        allowed_tools=tools_for(
            "get_datetime", "market_hours", "quotes", "instruments", "option_chain",
            "cohort", "evidence_read", "evidence_append", "escalation_raise",
            "sector_write", "status_latest", "alert_read",
        )
        + _WEB_AND_READ,
        verdict=ScoutVerdict,
        # ~2-3 names/pass, a handful of Schwab reads and web samples per name
        # (scout.md §B2-B4) plus the evidence/escalation writes — 30 turns is
        # comfortably above the observed shape with room for a retry.
        max_turns=30,
        timeout_s=2400.0,  # docker/crontab: scout 07:12 weekdays, job_timeout 2400
        window=(time(7, 0), time(8, 0)),
        role="research",
        noop_when=_scout_noop,
    ),
    "catalyst": JobSpec(
        name="catalyst",
        agent="catalyst",
        command="/catalyst",
        prompt=(
            "Follow .claude/commands/catalyst.md §B through §D exactly for "
            "one catalyst sweep of the three in-scope sectors for non-"
            "calendar stories. Escalate only evidence that clears the same "
            "§C corroboration bar as /scout — gather and corroborate, never "
            "form a thesis or trade. Return a single JSON object matching "
            "the CatalystVerdict schema and nothing else."
        ),
        allowed_tools=tools_for(
            "get_datetime", "market_hours", "quotes", "instruments",
            "evidence_read", "evidence_append", "escalation_raise",
            "sector_write", "sectors_read", "status_latest", "alert_read",
        )
        + _WEB_AND_READ,
        verdict=CatalystVerdict,
        # Same shape as scout, source-driven rather than cohort-driven.
        max_turns=30,
        timeout_s=2400.0,  # docker/crontab: catalyst 18:33 weekdays, job_timeout 2400
        window=(time(18, 0), time(19, 30)),
        role="research",
        noop_when=_catalyst_noop,
    ),
    "preopen": JobSpec(
        name="preopen",
        agent="deep-research",
        command="/deep-research preopen",
        prompt=(
            "Follow .claude/commands/deep-research.md §P exactly, in preopen "
            "mode. Write exactly one file — the pre-open brief — and never "
            "ping, promote a candidate, or touch research/candidates.md. "
            "Return a single JSON object matching the DeepVerdict schema "
            "(kind=\"preopen\") and nothing else."
        ),
        # Identical to `postclose`'s allowlist, not just preopen's own needs:
        # both jobs share the `deep-research` agent, and an agent's `tools:`
        # frontmatter is the SDK-enforced ceiling (0c-sdk-facts §1.5/§5), not
        # a per-job overlay -- one frontmatter list has to work for whichever
        # of the two modes actually runs, so it is the union of what either
        # needs, and each job spec must itself grant that union or the
        # subset check (`test_every_job_spec_agent_exists_and_its_tools_are_a_subset`)
        # fails. preopen never calls the postclose-only tools in practice
        # (§P is file-only, per deep-research.md), but it must be GRANTED
        # them for the shared agent file to be honest about what it can
        # reach in either mode.
        allowed_tools=tools_for(
            "get_datetime", "market_hours", "quotes", "instruments", "option_chain",
            "expiration_chain", "price_history", "universe_symbols",
            "ledger_append", "ledger_read", "tombstone", "doc_read", "doc_write",
            "alert_read", "status_latest", "rules",
        )
        + _WEB_AND_READ,
        verdict=DeepVerdict,
        # §P budget ~6 Schwab + ~8 API/web calls (writers-contract §2.4).
        max_turns=40,
        timeout_s=3600.0,  # docker/crontab: preopen 08:17 weekdays, job_timeout 3600
        window=(time(8, 0), time(9, 15)),
        role="research",
        noop_when=_deep_noop,
    ),
    "postclose": JobSpec(
        name="postclose",
        agent="deep-research",
        command="/deep-research postclose",
        prompt=(
            "Follow .claude/commands/deep-research.md §D exactly, in "
            "postclose mode, in its stated priority order — scorecard, "
            "universe/drift screens, roster chain-checks, ETF track, deeper "
            "vetting, IV series, standing refresh — logging whatever the "
            "budget never reached. Return a single JSON object matching the "
            "DeepVerdict schema (kind=\"postclose\") and nothing else."
        ),
        # Kept textually identical to `preopen`'s allowlist (see the comment
        # there) -- the two jobs share one agent file, whose frontmatter is
        # the ceiling both must fit under.
        allowed_tools=tools_for(
            "get_datetime", "market_hours", "quotes", "instruments", "option_chain",
            "expiration_chain", "price_history", "universe_symbols",
            "ledger_append", "ledger_read", "tombstone", "doc_read", "doc_write",
            "alert_read", "status_latest", "rules",
        )
        + _WEB_AND_READ,
        verdict=DeepVerdict,
        # §D budget ~15 Schwab + ~15 API/web calls, the largest job here.
        max_turns=80,
        timeout_s=3600.0,  # docker/crontab: postclose 16:22 weekdays, job_timeout 3600
        window=(time(16, 15), time(18, 0)),
        role="research",
        noop_when=_deep_noop,
    ),
    "research": JobSpec(
        name="research",
        agent="research-scout",
        command="/research",
        prompt=(
            "Follow .claude/commands/research.md §B through §D exactly for "
            "one research pass maintaining research/candidates.md. This is "
            "read-only against the broker — never place, preview, replace, "
            "or cancel an order, and never open the entry workflow. Return "
            "a single JSON object matching the ResearchVerdict schema and "
            "nothing else."
        ),
        allowed_tools=tools_for(
            "get_datetime", "market_hours", "quotes", "instruments", "option_chain",
            "expiration_chain", "price_history", "movers", "doc_read", "doc_write",
            "ledger_read", "alert_read", "status_latest", "rules",
        )
        + _WEB_AND_READ,
        verdict=ResearchVerdict,
        # §B budget ~8 Schwab calls + ~4 web fetches (research.md §B).
        max_turns=40,
        timeout_s=1500.0,  # docker/crontab: research hourly :57, job_timeout 1500
        window=(time(9, 45), time(15, 15)),
        role="research",
        noop_when=None,  # zero HOT / zero new is an ordinary result, not an empty pass
    ),
    "sector_tag": JobSpec(
        name="sector_tag",
        agent="sector-tagger",
        command="/sector-tag",
        prompt=(
            "Follow .claude/commands/sector-tag.md §B through §D exactly to "
            "classify the weekly sweep's qualified universe into the three "
            "scout sectors. Tag only names that clearly belong; leave a "
            "genuinely unsure name untagged rather than guessing. Return a "
            "single JSON object matching the SectorVerdict schema and "
            "nothing else."
        ),
        allowed_tools=tools_for(
            "get_datetime", "sector_write", "universe_names_page", "sectors_read", "cohort",
        )
        + _SEARCH_AND_READ,
        verdict=SectorVerdict,
        # Chunked reads over ~3,000 universe rows plus batched (<=200-line)
        # writes — the heaviest turn count of the six despite the lightest
        # tool grant.
        max_turns=60,
        timeout_s=3000.0,  # docker/crontab: sector_tag Sat 09:40, job_timeout 3000
        window=(time(9, 0), time(13, 0)),
        role="research",
        noop_when=None,  # zero new tags is an ordinary result, not an empty pass
    ),
}
