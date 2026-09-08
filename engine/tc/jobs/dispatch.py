"""Dispatching a Claude job: post it to the runner, classify what came back,
say the one thing a human needs to hear.

Three layers, deliberately separable:

* `RunnerClient` is transport and nothing else. It composes a `RunRequest`
  from a `JobSpec` (`jobs/spec.py`) and posts it to the runner's `POST /run`.
  It **never raises**: a refused connection, a 500, a body that will not parse
  — each becomes a `RunnerReply` carrying the failure's CLASS NAME only,
  because an exception message can carry a URL, a bearer fragment or an
  account number, and this string is written to the ledger and to Discord.
* `classify` is a pure function from a reply to a verdict. It has no clock, no
  store and no network, so the whole truth table is testable without either
  end of the wire.
* `JobRunner` is the seam the engine calls: window, dispatch, classify, relay.
  It returns `(verdict, detail)` for `Engine._dispatch` to record and ping —
  the ledger row and the healthchecks ping stay in one place for every job,
  Claude-driven or not.

**Why `content_failed` exists.** The v2 runner recorded exit-0-with-no-content
as `{"verdict":"ok"}`, so a research run that produced nothing pinged green and
the deadman saw a healthy day. Here a run that answers without a structured
verdict is its own verdict, it pings `/fail`, and the `job_verdict_not:
content_failed` expectation already watches for it. "The job ran" and "the job
answered" are different claims and the ledger now distinguishes them.

**Why an empty cohort is `noop` and not `done`.** `done` on sixty consecutive
empty passes is the "every job green, nothing ever happens" failure the design
exists to catch. Each spec carries its own `noop_when`, because only the job
knows what "there was nothing to do" looks like for it.

There is no line-matching whitelist here. v2 relayed by grepping stdout for a
first line that matched a known prefix, and the whitelist did not include
`PASS`, `SCOUT`, `CATALYST` or `CLOSE` — four of the six jobs relayed nothing
by construction, silently. What the relays read now are fields on a validated
model, so a job that reports a hot candidate cannot fail to have it said.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tc.clock import ET
from tc.jobs.spec import (
    JOB_SPECS,
    CatalystVerdict,
    DeepVerdict,
    JobSpec,
    ResearchVerdict,
    ScoutVerdict,
    output_schema,
)
from tc.notify import Notifier
from tc.store.db import Verdict

log = logging.getLogger(__name__)

# How much of a failed run's own words reach the ledger and the channel. Long
# enough to name the failure, short enough that a model which decided to
# narrate its whole session cannot fill a Discord message or a detail column.
MAX_TEXT = 200
MAX_ERRORS = 5
# The jobs whose one-line `summary` is worth a message of its own. The scout
# and catalyst passes already relay per-escalation, and `research` relays per
# hot-fresh candidate; posting their summary too would say the same pass twice.
SUMMARY_JOBS = frozenset({"preopen", "postclose", "sector_tag"})
# The verdicts that mean nobody got an answer: each gets one ⚠️ line here, and
# `Engine._dispatch` pings healthchecks `/fail` for them rather than `/ok`.
# One set, shared, because "the job did not work" must mean the same thing to
# the channel and to the deadman -- v2's whole failure was a run that produced
# nothing and pinged green.
FAILED_VERDICTS = frozenset({"content_failed", "failed", "timeout"})


class RunResultView(BaseModel):
    """The engine's mirror of the runner's `RunResult`.

    `extra="ignore"`, alone among the models in this file, and on purpose: the
    runner is a separate service on its own deploy cadence, and a field added
    there (`teardown_s` was, after this mirror was first written) must not turn
    every job in the engine into a `failed` row until both sides ship together.
    Every field is optional with a conservative default for the same reason —
    a truncated body is a run whose result we cannot read, which the caller
    already handles, not a validation crash inside the dispatcher.

    `is_error` defaults to True: a body that says nothing about whether the run
    failed is not evidence that it succeeded.
    """

    model_config = ConfigDict(extra="ignore")

    verdict_raw: dict[str, Any] | None = None
    result_text: str | None = None
    is_error: bool = True
    subtype: str | None = None
    num_turns: int = 0
    permission_denials: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    duration_s: float = 0.0
    timed_out: bool = False


class RunnerReply(BaseModel):
    """What the transport layer managed to obtain. Exactly one of the three
    states is meaningful at a time, and `classify` reads them in that order:
    busy, then transport error, then result."""

    model_config = ConfigDict(extra="forbid")

    result: RunResultView | None = None
    # The exception's CLASS NAME or `HTTP <status>`, never the message.
    transport_error: str | None = None
    busy: bool = False


class RunnerClient:
    """The engine's half of the runner contract.

    `base_url` and `token` are both required for a dispatch to be attempted:
    an engine deployed without a runner is a normal deployment (it still
    ticks, closes the session and answers /health), so "no runner configured"
    is a state to report, not a failure to raise.
    """

    def __init__(
        self,
        base_url: str | None,
        token: str | None,
        client: httpx.AsyncClient,
        slack_s: float,
        *,
        connect_timeout_s: float = 10.0,
        role_token: str | None = None,
    ) -> None:
        self._base = base_url.rstrip("/") if base_url else None
        self._token = token
        self._c = client
        self._slack_s = slack_s
        self._connect_timeout_s = connect_timeout_s
        self._role_token = role_token

    @property
    def configured(self) -> bool:
        return bool(self._base and self._token)

    @property
    def has_role_token(self) -> bool:
        """The bearer the runner presents BACK to the engine's MCP mount.

        Separate from `configured` because it fails differently: a runner with
        no MCP bearer would start the job, reach its first engine tool, and be
        refused -- burning the whole budget to arrive at a 401. Better to not
        dispatch, and to say which half of the configuration is missing.
        """
        return bool(self._role_token)

    async def health(self) -> bool:
        """The runner's own `/health`, for the engine's `/health` and the host
        probe. Never raises and never blocks startup for long: an unreachable
        runner is `False`, which is what the operator needs to see, not an
        engine that will not come up."""
        if self._base is None:
            return False
        try:
            r = await self._c.get(f"{self._base}/health", timeout=self._connect_timeout_s)
            if r.status_code != 200:
                return False
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("runner health check failed (%s)", type(e).__name__)
            return False
        return bool(isinstance(body, dict) and body.get("ok"))

    async def run(self, spec: JobSpec, *, prompt_extra: str = "") -> RunnerReply:
        """Post one job and come back with whatever there is to come back with.

        The read timeout is the job's OWN budget plus `runner.slack_s`, so the
        runner's deadline always fires first and a long job answers with a
        structured `timed_out` result instead of dying as a socket the engine
        gave up on. The SDK reaps its child process after the cancel, which is
        what the slack is for.
        """
        if self._base is None or self._token is None:
            # Guarded by `configured` at the caller; belt and braces, because a
            # bearer of "None" on the wire is worse than no request at all.
            return RunnerReply(transport_error="no runner configured")
        payload: dict[str, Any] = {
            "job": spec.name,
            "prompt": spec.prompt if not prompt_extra else f"{spec.prompt}\n\n{prompt_extra}",
            "allowed_tools": list(spec.allowed_tools),
            "mcp_role": spec.role,
            "mcp_role_token": self._role_token or "",
            "output_schema": output_schema(spec.verdict),
            "max_turns": spec.max_turns,
            "timeout_s": spec.timeout_s,
            "agent": spec.agent,
        }
        try:
            r = await self._c.post(
                f"{self._base}/run",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=httpx.Timeout(
                    spec.timeout_s + self._slack_s, connect=self._connect_timeout_s
                ),
            )
        except httpx.HTTPError as e:
            # The class name is the whole report. `ConnectError`'s message is
            # "connection refused to <host>:<port>" and this string is written
            # to a public-repo-adjacent ledger and posted to a channel.
            log.warning("runner dispatch of %s failed (%s)", spec.name, type(e).__name__)
            return RunnerReply(transport_error=type(e).__name__)
        if r.status_code == 409:
            # The runner is one run at a time by design. A retry inside the
            # slack window would be a second job queued behind a first whose
            # window has already moved on, so the fire is simply dropped.
            return RunnerReply(busy=True)
        if not 200 <= r.status_code < 300:
            return RunnerReply(transport_error=f"HTTP {r.status_code}")
        try:
            return RunnerReply(result=RunResultView.model_validate(r.json()))
        except (ValueError, ValidationError) as e:
            return RunnerReply(transport_error=type(e).__name__)


def _one_line(text: str | None) -> str:
    """Whitespace collapsed, then capped.

    A model that narrated its whole session would otherwise put a hundred lines
    into a ledger detail and a Discord message; and a newline inside a `⚠️`
    line breaks the one-line-per-failure reading the channel is scanned with.
    """
    return " ".join((text or "").split())[:MAX_TEXT]


def _recover_verdict_from_text(text: str, model: type[BaseModel]) -> BaseModel | None:
    """Best-effort recovery of a structured verdict from a run's raw text.

    Observed on the Pi: a 14-minute postclose run wrote its documents and
    ledgers through the MCP tools and its final text was exactly the verdict
    JSON object, but the CLI never populated `structured_output` — so
    `verdict_raw` came back `None` on a run that plainly answered. Refusing to
    look at `result_text` in that case turns a completed job into
    `content_failed` for a reason that has nothing to do with the job.

    Strips code-fence markers (a model that wraps its JSON in a ```json
    block), then walks every `{` in the text from the END toward the start,
    trying `json.JSONDecoder().raw_decode` at each position. The scan runs
    right-to-left and returns on the first candidate that both parses AND
    validates against `model`, so a nested object that happens to parse on
    its own (say, one entry of `hot_fresh`) but does not validate as the
    verdict itself is skipped in favour of the real, larger object that
    encloses it. Returns `None` — never raises — when nothing in the text
    both parses and validates; the caller's `content_failed` path is
    unchanged in that case.
    """
    cleaned = text.replace("```json", "").replace("```", "")
    decoder = json.JSONDecoder()
    positions = [i for i, ch in enumerate(cleaned) if ch == "{"]
    for idx in reversed(positions):
        try:
            obj, _ = decoder.raw_decode(cleaned, idx)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        try:
            return model.model_validate(obj)
        except ValidationError:
            continue
    return None


def _validation_errors(exc: ValidationError) -> list[str]:
    """Location and error type only — never `input`.

    A pydantic error carries the offending value, and the offending value here
    is model-authored text about a real position. The ledger wants to know
    which field was wrong and how; it does not want the payload back.
    """
    out = []
    for err in exc.errors()[:MAX_ERRORS]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
        out.append(f"{loc}: {err.get('type', 'invalid')}")
    return out


def classify(
    spec: JobSpec, reply: RunnerReply
) -> tuple[Verdict, BaseModel | None, dict[str, Any]]:
    """Reply -> (verdict, the parsed verdict model or None, ledger detail).

    Exhaustive and ordered. `timed_out` is tested BEFORE `is_error` because a
    timed-out run also carries `is_error=True` — reporting it as `failed` would
    lose the one fact that distinguishes "the job needs a bigger budget" from
    "the job broke".
    """
    if reply.busy:
        return "noop", None, {"skipped": "runner busy"}
    if reply.transport_error is not None:
        return "failed", None, {"error": reply.transport_error}
    res = reply.result
    if res is None:
        return "failed", None, {"error": "no result"}
    if res.timed_out:
        return "timeout", None, {"duration_s": res.duration_s, "budget_s": spec.timeout_s}
    if res.is_error:
        return "failed", None, {
            "subtype": res.subtype,
            "denials": len(res.permission_denials),
            "text": _one_line(res.result_text),
        }
    if res.verdict_raw is None:
        # Exit 0, turns spent, nothing structured on `structured_output` — but
        # the run may still have SAID the verdict as its final text (the CLI
        # simply failed to populate the field). Try to recover it before
        # calling this `content_failed`.
        recovered = (
            _recover_verdict_from_text(res.result_text, spec.verdict)
            if res.result_text
            else None
        )
        if recovered is not None:
            detail = recovered.model_dump(mode="json")
            detail["verdict_source"] = "text"
            if spec.noop_when is not None and spec.noop_when(recovered):
                return "noop", recovered, detail
            return "done", recovered, detail
        # No JSON object in the text parsed, or none validated. The v2 bug,
        # given a name.
        return "content_failed", None, {
            "reason": "no structured output",
            "text": _one_line(res.result_text),
        }
    try:
        verdict_model = spec.verdict.model_validate(res.verdict_raw)
    except ValidationError as e:
        return "content_failed", None, {
            "reason": "verdict did not validate",
            "errors": _validation_errors(e),
        }
    detail = verdict_model.model_dump(mode="json")
    if spec.noop_when is not None and spec.noop_when(verdict_model):
        return "noop", verdict_model, detail
    return "done", verdict_model, detail


class JobRunner:
    """One Claude job, end to end, for `Engine._execute`.

    Returns `(verdict, detail)` rather than recording anything itself: the
    `job_runs` row and the healthchecks ping belong to `Engine._dispatch`, so
    that every job in the engine — Claude-driven or plain Python — leaves the
    same evidence behind in the same place.
    """

    def __init__(
        self, runner: RunnerClient, notifier: Notifier, clock: Callable[[], datetime]
    ) -> None:
        self._runner = runner
        self._notifier = notifier
        self._clock = clock

    async def health(self) -> bool:
        """Is the other container up? Read once at start and reported by
        `/health` as `runner_ok`, because a runner that is down is invisible
        from inside the engine: every job simply becomes a `failed` row at its
        own scheduled hour, which is up to a day of silence."""
        return await self._runner.health()

    async def execute(
        self, job: str, now: datetime | None = None, *, ignore_window: bool = False
    ) -> tuple[Verdict, dict[str, Any]]:
        """`ignore_window` is the operator's `tc run --once --ignore-window`.

        Never the scheduler's: a fire dispatched hours late is a job whose
        premise expired (below), and the whole point of the gate is that
        nothing in the engine can decide to run one anyway. A human bootstrapping
        the chain at 21:00 is the opposite case -- they know the window has
        passed and are asking for the run regardless -- so the override is a
        keyword the CLI passes and no scheduled path can reach.
        """
        spec = JOB_SPECS[job]
        if not self._runner.configured:
            # Not a failure. An engine with no runner is a supported
            # deployment; a `failed` row every weekday at 07:12 for a service
            # nobody installed is how a deadman gets muted.
            return "noop", {"skipped": "no runner configured"}
        if not self._runner.has_role_token:
            # Half-configured, which is worse than unconfigured: the runner
            # would take the job, spend its budget, and be 401'd at its first
            # engine tool. Warned rather than silent -- unlike "no runner",
            # this state is nobody's intended deployment.
            log.warning("%s not dispatched: no MCP research token configured", job)
            return "noop", {"skipped": "no mcp research token"}
        et = (now or self._clock()).astimezone(ET)
        start, end = spec.window
        if not ignore_window and not start <= et.time() <= end:
            # A fire dispatched hours late (a restart, a long-held lock) is a
            # job whose whole premise has expired: a "pre-open" brief written
            # at noon is worse than no brief. The runner is never called.
            window = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} ET"
            return "noop", {
                "skipped": "outside window",
                "window": window,
                "at_et": et.strftime("%H:%M"),
            }
        reply = await self._runner.run(spec, prompt_extra=_prompt_extra(spec, et.date()))
        verdict, model, detail = classify(spec, reply)
        await self._relay(spec, verdict, model, detail, et.date())
        return verdict, detail

    async def _relay(
        self,
        spec: JobSpec,
        verdict: Verdict,
        model: BaseModel | None,
        detail: dict[str, Any],
        day: date,
    ) -> None:
        """One message per thing that happened, and nothing for a quiet pass.

        Deliberately field-driven: a candidate that cleared the bar is relayed
        because it is in `hot_fresh`, not because the model happened to open
        its answer with a string some grep recognised.
        """
        if verdict in FAILED_VERDICTS:
            await self._notifier.post(f"⚠️ {spec.name} {verdict}: {_reason(detail)}")
            return
        if model is None:
            return
        if isinstance(model, ResearchVerdict | DeepVerdict):
            for h in model.hot_fresh:
                await self._notifier.post(
                    f"🔥 HOT-FRESH: {h.symbol} sleeve={h.sleeve} ref={h.ref} — {h.thesis}"
                )
        if isinstance(model, ScoutVerdict | CatalystVerdict):
            for e in model.escalations:
                await self._notifier.post(f"📌 ESCALATE: {e.symbol} — {e.claim}")
        if verdict == "done" and spec.name in SUMMARY_JOBS:
            summary = str(detail.get("summary", "")).strip()
            if summary:
                await self._notifier.post(f"✅ {summary} ({day.isoformat()})")


def _reason(detail: dict[str, Any]) -> str:
    """The failure, in one clause, from whichever keys the classifier used."""
    parts = [
        str(detail[k])
        for k in ("error", "reason", "subtype", "errors", "text")
        if detail.get(k)
    ]
    if not parts and "duration_s" in detail:
        parts = [f"{detail['duration_s']:.0f}s of {detail.get('budget_s', 0):.0f}s"]
    return " — ".join(parts)[:MAX_TEXT] or "no detail"


def _prompt_extra(spec: JobSpec, day: date) -> str:
    """The two things a job cannot read off its own command file.

    The date, because the container clock is UTC, the laptop's is Pacific, and
    every deadline the command files describe is Eastern; and the mode, because
    preopen and postclose share one command file and one agent and differ only
    by which half of it they execute.
    """
    lines = [f"Today's date is {day.isoformat()} (Eastern)."]
    if spec.name in ("preopen", "postclose"):
        lines.append(
            f'Run in "{spec.name}" mode; the verdict\'s kind field must be "{spec.name}".'
        )
    return " ".join(lines)
