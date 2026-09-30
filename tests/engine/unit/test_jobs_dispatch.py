"""Dispatching a Claude job: what the engine records, pings and says.

The runner is a `MockTransport`, so every one of its answer shapes — a good
verdict, an empty pass, no structured output, a malformed one, a timeout, a
busy 409, a refused connection — is exercised without a CLI, a container or a
second of wall clock.

What these tests exist to pin down:

* **A run that answers nothing is `content_failed`, not `done`.** The v2 runner
  recorded exit-0-with-no-content as `{"verdict":"ok"}`; a research pass that
  produced nothing pinged green for weeks. `content_failed` is the verdict that
  makes that visible, and the store expectation already watches for it.
* **An analyst that finds nothing is `done`, not `noop`.** No desk job has a
  `noop_when`: zero pitches is still a chain summary worth reporting, not a
  pass that never ran.
* **A failure names its class and nothing else.** `ConnectError`'s message
  carries the host and port it could not reach, and this string reaches the
  ledger and Discord.
* **A job outside its window never reaches the runner at all.** A pre-open
  brief written at noon is worse than no brief.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tc.jobs.dispatch import JobRunner, RunnerClient, classify
from tc.jobs.spec import JOB_SPECS
from tc.notify import Notifier

# 16:45 ET on a Tuesday -- inside the analysts' 16:25-20:00 window.
IN_WINDOW = datetime(2026, 9, 8, 20, 45, tzinfo=UTC)
OUT_WINDOW = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)  # 11:00 ET
JOB = "analyst_technical"
SLACK_S = 120.0
TOKEN = "runner-token-not-real"  # noqa: S105 -- test fixture, not a credential
ROLE_TOKEN = "research-token-not-real"  # noqa: S105 -- test fixture, not a credential

Handler = Any


class RecordingNotifier(Notifier):
    """Posts nowhere and remembers everything."""

    def __init__(self) -> None:
        super().__init__(None, httpx.AsyncClient())
        self.posted: list[str] = []

    async def post(self, text: str) -> bool:
        self.posted.append(text)
        return True


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


def _runner(
    handler: Handler, token: str | None = TOKEN, role_token: str | None = ROLE_TOKEN
) -> RunnerClient:
    return RunnerClient(
        "http://runner",
        token,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        SLACK_S,
        role_token=role_token,
    )


def _ok(payload: dict[str, Any]) -> Handler:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return h


GOOD: dict[str, Any] = {
    "verdict_raw": {"pitched": [1, 2], "withdrawn": [], "summary": "TECHNICAL 2 pitches"},
    "result_text": "{}",
    "is_error": False,
    "subtype": "success",
    "num_turns": 6,
    "permission_denials": [],
    "usage": {},
    "duration_s": 12.0,
    "timed_out": False,
    # The runner added this field after the engine's mirror was written. It
    # must be ignored, not rejected: the two services deploy separately.
    "teardown_s": 0.0,
}


async def _no_sleep(s: float) -> None:
    return None


def _jr(handler: Handler, notifier: RecordingNotifier, **kw: Any) -> JobRunner:
    return JobRunner(_runner(handler, **kw), notifier, lambda: IN_WINDOW, sleep=_no_sleep)


# --- classification ---------------------------------------------------------

async def test_done_with_a_valid_verdict(notifier: RecordingNotifier) -> None:
    verdict, detail = await _jr(_ok(GOOD), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done"
    assert detail["pitched"] == [1, 2]


async def test_outside_the_window_is_a_noop_and_never_calls_the_runner(
    notifier: RecordingNotifier,
) -> None:
    called: list[httpx.Request] = []

    def h(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=GOOD)

    verdict, detail = await _jr(h, notifier).execute(JOB, OUT_WINDOW)
    assert verdict == "noop"
    assert detail["skipped"] == "outside window"
    assert called == []


async def test_ignore_window_dispatches_a_job_whose_window_has_passed(
    notifier: RecordingNotifier,
) -> None:
    """The operator's `tc run --once JOB --ignore-window`: bootstrapping
    happens whenever it happens, and without this every seeding run outside
    business hours is a `noop` that looks like a broken chain."""
    called: list[httpx.Request] = []

    def h(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=GOOD)

    verdict, detail = await _jr(h, notifier).execute(
        JOB, OUT_WINDOW, ignore_window=True
    )
    assert verdict == "done"
    assert detail["pitched"] == [1, 2]
    assert len(called) == 1


async def test_ignore_window_does_not_bypass_the_other_refusals(
    notifier: RecordingNotifier,
) -> None:
    """It overrides the CLOCK and nothing else: an unconfigured runner and a
    missing MCP bearer are still noops, because neither is about the hour."""
    verdict, detail = await JobRunner(
        RunnerClient(None, None, httpx.AsyncClient(), SLACK_S), notifier, lambda: OUT_WINDOW
    ).execute(JOB, OUT_WINDOW, ignore_window=True)
    assert (verdict, detail["skipped"]) == ("noop", "no runner configured")
    verdict, detail = await _jr(_ok(GOOD), notifier, role_token=None).execute(
        JOB, OUT_WINDOW, ignore_window=True
    )
    assert (verdict, detail["skipped"]) == ("noop", "no mcp research token")


async def test_the_window_gate_is_still_the_default(notifier: RecordingNotifier) -> None:
    """No scheduled path passes `ignore_window`, so its default must be the
    gate: a fire dispatched hours late is a job whose premise has expired."""
    verdict, _ = await _jr(_ok(GOOD), notifier).execute(JOB, OUT_WINDOW)
    assert verdict == "noop"


async def test_zero_pitches_is_done_not_noop(notifier: RecordingNotifier) -> None:
    """An analyst that finds nothing has still done its job; the chain
    summary reports the count. No desk job has a noop predicate."""
    payload = {**GOOD, "verdict_raw": {"pitched": [], "withdrawn": [], "summary": "TECHNICAL 0"}}
    verdict, _ = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done"


async def test_no_structured_output_is_content_failed(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "verdict_raw": None, "result_text": "I ran out of turns"}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "content_failed"
    assert "ran out of turns" in detail["text"]


RECOVERED = '{"pitched": [3], "withdrawn": [], "summary": "TECHNICAL 1 pitch"}'


async def test_verdict_recovered_from_text_when_structured_output_missing(
    notifier: RecordingNotifier,
) -> None:
    payload = {**GOOD, "verdict_raw": None, "result_text": RECOVERED}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done"
    assert detail["verdict_source"] == "text" and detail["pitched"] == [3]


async def test_verdict_recovered_from_a_fenced_json_block(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "verdict_raw": None,
               "result_text": f"Here it is.\n```json\n{RECOVERED}\n```\n"}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done" and detail["verdict_source"] == "text"


async def test_text_with_a_failing_json_object_stays_content_failed(
    notifier: RecordingNotifier,
) -> None:
    text = '{"pitched": "3", "withdrawn": [], "summary": "x"}'  # pitched must be a list
    payload = {**GOOD, "verdict_raw": None, "result_text": text}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "content_failed"
    assert detail["reason"] == "no structured output" and "verdict_source" not in detail


async def test_structured_output_still_wins_over_text(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "result_text": RECOVERED}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done" and "verdict_source" not in detail
    assert detail["pitched"] == [1, 2]


async def test_a_verdict_with_the_wrong_shape_is_content_failed(
    notifier: RecordingNotifier,
) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {"pitched": "four", "withdrawn": [], "summary": "x"},
    }
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "content_failed"
    assert detail["reason"] == "verdict did not validate"
    # Location and type only: the rejected value never reaches the ledger.
    assert detail["errors"] == ["pitched: list_type"]


async def test_a_verdict_with_an_extra_key_is_content_failed(
    notifier: RecordingNotifier,
) -> None:
    """`extra="forbid"` on the verdict models, enforced end to end: a field the
    engine does not know about is a claim nothing downstream will read."""
    payload = {**GOOD, "verdict_raw": {**GOOD["verdict_raw"], "orders_placed": 1}}
    verdict, _ = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "content_failed"


async def test_timeout_and_is_error_are_distinct_verdicts(
    notifier: RecordingNotifier,
) -> None:
    """A timed-out run also carries `is_error=True`. Reporting it as `failed`
    would lose the only fact that separates "needs a bigger budget" from
    "broke"."""
    for payload, want in (
        ({**GOOD, "timed_out": True, "is_error": True, "verdict_raw": None}, "timeout"),
        ({**GOOD, "is_error": True, "verdict_raw": None}, "failed"),
    ):
        verdict, _ = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
        assert verdict == want


async def test_a_runner_busy_all_window_is_missed(notifier: RecordingNotifier) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "runner busy"})

    verdict, detail = await _jr(h, notifier).execute(JOB, IN_WINDOW)
    assert (verdict, detail["skipped"]) == ("missed", "runner busy past its window")


async def test_a_non_2xx_answer_is_failed_and_names_only_the_status(
    notifier: RecordingNotifier,
) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Traceback: /app/tc_runner/app.py line 1")

    verdict, detail = await _jr(h, notifier).execute(JOB, IN_WINDOW)
    assert verdict == "failed"
    assert detail["error"] == "HTTP 500"


async def test_transport_failure_is_failed_and_names_only_the_class(
    notifier: RecordingNotifier,
) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.5:8090")

    verdict, detail = await _jr(h, notifier).execute(JOB, IN_WINDOW)
    assert verdict == "failed"
    assert detail["error"] == "ConnectError"
    assert "10.0.0.5" not in repr(detail)


async def test_a_body_that_is_not_a_run_result_is_failed_not_a_crash(
    notifier: RecordingNotifier,
) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    verdict, detail = await _jr(h, notifier).execute(JOB, IN_WINDOW)
    assert verdict == "failed"
    assert "error" in detail


async def test_no_runner_configured_is_a_noop_not_a_failure(
    notifier: RecordingNotifier,
) -> None:
    jr = _jr(_ok(GOOD), notifier, token=None)
    verdict, detail = await jr.execute(JOB, IN_WINDOW)
    assert verdict == "noop"
    assert detail["skipped"] == "no runner configured"


async def test_a_runner_with_no_mcp_bearer_is_never_dispatched_to(
    notifier: RecordingNotifier,
) -> None:
    """Half-configured is worse than unconfigured: the runner would take the
    job, spend its whole budget, and be 401'd at its first engine tool."""
    called: list[httpx.Request] = []

    def h(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=GOOD)

    jr = _jr(h, notifier, role_token=None)
    verdict, detail = await jr.execute(JOB, IN_WINDOW)
    assert verdict == "noop"
    assert detail["skipped"] == "no mcp research token"
    assert called == []


# --- the request ------------------------------------------------------------

async def test_the_read_timeout_is_the_job_budget_plus_slack(
    notifier: RecordingNotifier,
) -> None:
    """The runner's own deadline must always fire first: it answers a timeout
    as a structured result, where the engine giving up answers a dead socket."""
    seen: dict[str, Any] = {}

    def h(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout", {}).get("read")
        return httpx.Response(200, json=GOOD)

    await _jr(h, notifier).execute(JOB, IN_WINDOW)
    assert seen["timeout"] == JOB_SPECS[JOB].timeout_s + SLACK_S


async def test_the_request_carries_the_spec_and_the_et_date(
    notifier: RecordingNotifier,
) -> None:
    seen: dict[str, Any] = {}

    def h(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=GOOD)

    await _jr(h, notifier).execute(JOB, IN_WINDOW)
    spec = JOB_SPECS[JOB]
    assert seen["job"] == JOB
    assert seen["agent"] == spec.agent
    assert seen["mcp_role"] == "research"
    assert seen["allowed_tools"] == list(spec.allowed_tools)
    assert seen["max_turns"] == spec.max_turns
    assert seen["auth"] == f"Bearer {TOKEN}"
    # The bearer the runner presents BACK to the engine's MCP mount.
    assert seen["mcp_role_token"] == ROLE_TOKEN
    # additionalProperties:false travels with the schema, so the runner's own
    # structured-output mode refuses a stray field before the engine sees it.
    assert seen["output_schema"]["additionalProperties"] is False
    # The container clock is UTC and every deadline in a command file is ET.
    assert "2026-09-08" in seen["prompt"]


async def test_preopen_jobs_run_in_preopen_mode(notifier: RecordingNotifier) -> None:
    seen: dict[str, Any] = {}

    def h(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=GOOD)

    at = datetime(2026, 9, 8, 12, 30, tzinfo=UTC)  # 08:30 ET, inside pre-open
    await _jr(h, notifier).execute("preopen_news", at)
    assert "PRE-OPEN MODE" in seen["prompt"]


# --- relays -----------------------------------------------------------------
# The escalation/hot-fresh/deep-run relays are gone with the jobs that
# produced them. The PM's summary relay is pinned by Task 17's
# `test_a_decide_job_presents_the_decide_bearer`.


async def test_a_runs_own_words_reach_the_ledger_on_one_line(
    notifier: RecordingNotifier,
) -> None:
    """A model that narrated its whole session would otherwise put a hundred
    lines into a detail column and break the channel's one-line-per-failure
    reading."""
    payload = {**GOOD, "verdict_raw": None, "result_text": "line one\nline two\n" + "x" * 500}
    _, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert detail["text"].startswith("line one line two ")
    assert "\n" not in detail["text"]
    assert len(detail["text"]) == 200
    assert "\n" not in notifier.posted[0]


async def test_a_failed_run_posts_exactly_one_warning(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "verdict_raw": None, "result_text": "I ran out of turns"}
    await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert len(notifier.posted) == 1
    assert notifier.posted[0].startswith("⚠️ analyst_technical content_failed:")


async def test_a_quiet_pass_says_nothing(notifier: RecordingNotifier) -> None:
    await _jr(_ok(GOOD), notifier).execute(JOB, IN_WINDOW)
    assert notifier.posted == []


# --- health -----------------------------------------------------------------

async def test_runner_health_is_the_runners_own_ok_flag(
    notifier: RecordingNotifier,
) -> None:
    def up(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "busy": False})

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "busy": False})

    def gone(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await _jr(up, notifier).health() is True
    assert await _jr(down, notifier).health() is False
    assert await _jr(gone, notifier).health() is False


# --- per-role bearers, busy wait, PM retry -----------------------------------

PM_AT = datetime(2026, 9, 8, 13, 50, tzinfo=UTC)          # 09:50 ET
PM_GOOD: dict[str, Any] = {**GOOD, "verdict_raw": {"held": [], "calls": [4], "summary": "PM 1 call"}}


def _pm_runner(handler: Handler, decide: str | None = "decide-token-not-real") -> RunnerClient:
    return RunnerClient("http://runner", TOKEN,
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)), SLACK_S,
                        role_token=ROLE_TOKEN,
                        role_tokens={"decide": decide} if decide else None)


async def test_a_decide_job_presents_the_decide_bearer(notifier: RecordingNotifier) -> None:
    seen: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.read()))
        return httpx.Response(200, json=PM_GOOD)

    jr = JobRunner(_pm_runner(h), notifier, lambda: PM_AT, sleep=_no_sleep)
    verdict, _ = await jr.execute("pm", PM_AT)
    assert verdict == "done"
    assert (seen["mcp_role"], seen["mcp_role_token"]) == ("decide", "decide-token-not-real")
    assert any("PM 1 call" in p for p in notifier.posted)


async def test_a_decide_job_without_a_decide_bearer_is_never_dispatched(
    notifier: RecordingNotifier,
) -> None:
    jr = JobRunner(_pm_runner(_ok(PM_GOOD), decide=None), notifier, lambda: PM_AT, sleep=_no_sleep)
    assert (await jr.execute("pm", PM_AT)) == ("noop", {"skipped": "no mcp decide token"})


async def test_a_busy_runner_is_waited_out_inside_the_window(notifier: RecordingNotifier) -> None:
    replies = [httpx.Response(409), httpx.Response(409), httpx.Response(200, json=GOOD)]
    jr = _jr(lambda r: replies.pop(0), notifier)
    verdict, _ = await jr.execute(JOB, IN_WINDOW)
    assert verdict == "done" and replies == []


async def test_the_pm_retries_a_failed_run_once(notifier: RecordingNotifier) -> None:
    failed = {**PM_GOOD, "is_error": True, "subtype": "error_during_execution",
              "result_text": "You've hit your session limit"}
    replies = [httpx.Response(200, json=failed), httpx.Response(200, json=PM_GOOD)]
    jr = JobRunner(_pm_runner(lambda r: replies.pop(0)), notifier, lambda: PM_AT, sleep=_no_sleep)
    verdict, detail = await jr.execute("pm", PM_AT)
    assert verdict == "done" and detail["retried"] is True


async def test_the_pm_retry_window_is_judged_from_the_clock_after_the_failure(
    notifier: RecordingNotifier,
) -> None:
    """`later` must be computed from the clock AFTER the failed run finished,
    never from the fire time captured before it started: a run that takes a
    while to fail must not still schedule a retry that lands past the
    window's close just because the FIRE was early enough."""
    failed = {**PM_GOOD, "is_error": True, "subtype": "error_during_execution",
              "result_text": "boom"}
    # 10:20 ET: 10 minutes from the 10:30 close, less than the 900s (15 min)
    # retry_failed_after_s -- so a retry scheduled from THIS clock reading
    # would land past the window and must not be attempted.
    after_failure = datetime(2026, 9, 8, 14, 20, tzinfo=UTC)
    jr = JobRunner(_pm_runner(_ok(failed)), notifier, lambda: after_failure, sleep=_no_sleep)
    verdict, detail = await jr.execute("pm", PM_AT)
    assert verdict == "failed"
    assert "retried" not in detail


async def test_the_pm_retry_meeting_a_busy_runner_keeps_the_first_failure(
    notifier: RecordingNotifier,
) -> None:
    """A retry that meets a busy runner must not read as `noop` (`classify`
    maps a busy reply to `noop`, which pings /ok and hides a day with no PM
    entries behind a green check). The original failed verdict survives,
    flagged as retried, and the retry itself is still queued through the
    same busy-wait every other dispatch gets."""
    failed = {**PM_GOOD, "is_error": True, "subtype": "error_during_execution",
              "result_text": "boom"}
    replies = [httpx.Response(200, json=failed), httpx.Response(409)]
    # A huge busy_retry_s means the retry's own busy-wait gives up
    # immediately (zero tries fit in the time left to the window's close),
    # so the still-busy reply comes straight back.
    jr = JobRunner(_pm_runner(lambda r: replies.pop(0)), notifier, lambda: PM_AT,
                   sleep=_no_sleep, busy_retry_s=3600.0)
    verdict, detail = await jr.execute("pm", PM_AT)
    assert verdict == "failed"
    assert detail["retried"] is True
    assert detail["retry_skipped"] == "runner busy past its window"
    assert detail["subtype"] == "error_during_execution"


# --- classify is pure -------------------------------------------------------

def test_classify_reads_the_reply_and_nothing_else() -> None:
    """No clock, no store, no network — so the whole truth table is decidable
    without either end of the wire."""
    from tc.jobs.dispatch import RunnerReply, RunResultView

    spec = JOB_SPECS[JOB]
    assert classify(spec, RunnerReply(busy=True))[0] == "noop"
    assert classify(spec, RunnerReply(transport_error="ReadTimeout"))[0] == "failed"
    assert classify(spec, RunnerReply())[0] == "failed"
    # A body missing `is_error` is not evidence the run succeeded.
    verdict, model, _ = classify(spec, RunnerReply(result=RunResultView()))
    assert verdict == "failed" and model is None
