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
* **An empty cohort is `noop`, not `done`.** `done` on sixty consecutive empty
  passes is "every job green, nothing ever happens".
* **A failure names its class and nothing else.** `ConnectError`'s message
  carries the host and port it could not reach, and this string reaches the
  ledger and Discord.
* **A job outside its window never reaches the runner at all.** A pre-open
  brief written at noon is worse than no brief.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tc.jobs.dispatch import JobRunner, RunnerClient, classify
from tc.jobs.spec import JOB_SPECS
from tc.notify import Notifier

# 07:15 ET on a Monday — inside scout's 07:00-08:00 window.
IN_WINDOW = datetime(2026, 9, 7, 11, 15, tzinfo=UTC)
OUT_WINDOW = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)  # 16:00 ET
SLACK_S = 120.0
TOKEN = "runner-token-not-real"  # noqa: S105 -- test fixture, not a credential

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


def _runner(handler: Handler, token: str | None = TOKEN) -> RunnerClient:
    return RunnerClient(
        "http://runner",
        token,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        SLACK_S,
    )


def _ok(payload: dict[str, Any]) -> Handler:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return h


GOOD: dict[str, Any] = {
    "verdict_raw": {"cohort": 4, "observed": 3, "escalations": [], "summary": "SCOUT ok"},
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


def _jr(handler: Handler, notifier: RecordingNotifier, **kw: Any) -> JobRunner:
    return JobRunner(_runner(handler, **kw), notifier, lambda: IN_WINDOW)


# --- classification ---------------------------------------------------------

async def test_done_with_a_valid_verdict(notifier: RecordingNotifier) -> None:
    verdict, detail = await _jr(_ok(GOOD), notifier).execute("scout", IN_WINDOW)
    assert verdict == "done"
    assert detail["observed"] == 3


async def test_outside_the_window_is_a_noop_and_never_calls_the_runner(
    notifier: RecordingNotifier,
) -> None:
    called: list[httpx.Request] = []

    def h(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=GOOD)

    verdict, detail = await _jr(h, notifier).execute("scout", OUT_WINDOW)
    assert verdict == "noop"
    assert detail["skipped"] == "outside window"
    assert called == []


async def test_empty_cohort_is_noop_not_done(notifier: RecordingNotifier) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {"cohort": 0, "observed": 0, "escalations": [], "summary": "cohort 0"},
    }
    verdict, _ = await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
    assert verdict == "noop"


async def test_no_structured_output_is_content_failed(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "verdict_raw": None, "result_text": "I ran out of turns"}
    verdict, detail = await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
    assert verdict == "content_failed"
    assert "ran out of turns" in detail["text"]


async def test_a_verdict_with_the_wrong_shape_is_content_failed(
    notifier: RecordingNotifier,
) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {"cohort": "four", "observed": 3, "escalations": [], "summary": "x"},
    }
    verdict, detail = await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
    assert verdict == "content_failed"
    assert detail["reason"] == "verdict did not validate"
    # Location and type only: the rejected value never reaches the ledger.
    assert detail["errors"] == ["cohort: int_parsing"]


async def test_a_verdict_with_an_extra_key_is_content_failed(
    notifier: RecordingNotifier,
) -> None:
    """`extra="forbid"` on the verdict models, enforced end to end: a field the
    engine does not know about is a claim nothing downstream will read."""
    payload = {**GOOD, "verdict_raw": {**GOOD["verdict_raw"], "orders_placed": 1}}
    verdict, _ = await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
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
        verdict, _ = await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
        assert verdict == want


async def test_busy_runner_is_a_noop(notifier: RecordingNotifier) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "runner busy"})

    verdict, detail = await _jr(h, notifier).execute("scout", IN_WINDOW)
    assert verdict == "noop"
    assert detail["skipped"] == "runner busy"


async def test_a_non_2xx_answer_is_failed_and_names_only_the_status(
    notifier: RecordingNotifier,
) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Traceback: /app/tc_runner/app.py line 1")

    verdict, detail = await _jr(h, notifier).execute("scout", IN_WINDOW)
    assert verdict == "failed"
    assert detail["error"] == "HTTP 500"


async def test_transport_failure_is_failed_and_names_only_the_class(
    notifier: RecordingNotifier,
) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.5:8090")

    verdict, detail = await _jr(h, notifier).execute("scout", IN_WINDOW)
    assert verdict == "failed"
    assert detail["error"] == "ConnectError"
    assert "10.0.0.5" not in repr(detail)


async def test_a_body_that_is_not_a_run_result_is_failed_not_a_crash(
    notifier: RecordingNotifier,
) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    verdict, detail = await _jr(h, notifier).execute("scout", IN_WINDOW)
    assert verdict == "failed"
    assert "error" in detail


async def test_no_runner_configured_is_a_noop_not_a_failure(
    notifier: RecordingNotifier,
) -> None:
    jr = _jr(_ok(GOOD), notifier, token=None)
    verdict, detail = await jr.execute("scout", IN_WINDOW)
    assert verdict == "noop"
    assert detail["skipped"] == "no runner configured"


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

    await _jr(h, notifier).execute("scout", IN_WINDOW)
    assert seen["timeout"] == JOB_SPECS["scout"].timeout_s + SLACK_S


async def test_the_request_carries_the_spec_and_the_et_date(
    notifier: RecordingNotifier,
) -> None:
    seen: dict[str, Any] = {}

    def h(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=GOOD)

    await _jr(h, notifier).execute("scout", IN_WINDOW)
    spec = JOB_SPECS["scout"]
    assert seen["job"] == "scout"
    assert seen["agent"] == spec.agent
    assert seen["mcp_role"] == "research"
    assert seen["allowed_tools"] == list(spec.allowed_tools)
    assert seen["max_turns"] == spec.max_turns
    assert seen["auth"] == f"Bearer {TOKEN}"
    # additionalProperties:false travels with the schema, so the runner's own
    # structured-output mode refuses a stray field before the engine sees it.
    assert seen["output_schema"]["additionalProperties"] is False
    # The container clock is UTC and every deadline in a command file is ET.
    assert "2026-09-07" in seen["prompt"]


async def test_the_deep_runs_are_told_which_half_to_execute(
    notifier: RecordingNotifier,
) -> None:
    """preopen and postclose share one command file and one agent."""
    seen: dict[str, Any] = {}

    def h(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={**GOOD, "verdict_raw": None})

    at = datetime(2026, 9, 7, 12, 30, tzinfo=UTC)  # 08:30 ET, inside preopen
    await _jr(h, notifier).execute("preopen", at)
    assert '"preopen" mode' in seen["prompt"]


# --- relays -----------------------------------------------------------------

async def test_escalations_are_relayed_one_line_each(notifier: RecordingNotifier) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {
            "cohort": 4,
            "observed": 3,
            "summary": "SCOUT 2 escalations",
            "escalations": [
                {"symbol": "CSX", "claim": "queue times doubled", "evidence_ids": ["e1"]},
                {"symbol": "NSC", "claim": "second yard idled", "evidence_ids": ["e2"]},
            ],
        },
    }
    await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
    assert any("ESCALATE: CSX — queue times doubled" in m for m in notifier.posted)
    assert any("ESCALATE: NSC" in m for m in notifier.posted)


async def test_hot_fresh_is_relayed_with_its_reference_price(
    notifier: RecordingNotifier,
) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {
            "hot": 1,
            "watch": 3,
            "tomb": 0,
            "standing_stale": False,
            "summary": "PASS | HOT 1",
            "hot_fresh": [
                {
                    "symbol": "CSX",
                    "sleeve": "core",
                    "ref": "48.99@2026-09-07T14:03:11Z",
                    "thesis": "volumes recovering",
                }
            ],
        },
    }
    at = datetime(2026, 9, 7, 17, 57, tzinfo=UTC)  # 13:57 ET, inside research
    await _jr(_ok(payload), notifier).execute("research", at)
    line = next(m for m in notifier.posted if "HOT-FRESH" in m)
    assert "CSX" in line and "48.99@2026-09-07T14:03:11Z" in line
    # `research` has no `noop_when`: zero new is an ordinary result of a pass
    # that did its work, and the summary is already carried per candidate.
    assert not any(m.startswith("✅") for m in notifier.posted)


async def test_the_deep_runs_relay_their_summary_line(notifier: RecordingNotifier) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {
            "kind": "postclose",
            "wrote": ["scorecard.md"],
            "hot_fresh": [],
            "notes": "",
            "summary": "CLOSE | scorecard written",
        },
    }
    at = datetime(2026, 9, 7, 20, 30, tzinfo=UTC)  # 16:30 ET, inside postclose
    verdict, _ = await _jr(_ok(payload), notifier).execute("postclose", at)
    assert verdict == "done"
    assert any("✅ CLOSE | scorecard written" in m for m in notifier.posted)


async def test_a_deep_run_that_wrote_nothing_is_a_noop_and_says_nothing(
    notifier: RecordingNotifier,
) -> None:
    payload = {
        **GOOD,
        "verdict_raw": {
            "kind": "postclose",
            "wrote": [],
            "hot_fresh": [],
            "notes": "halted",
            "summary": "CLOSE | nothing",
        },
    }
    at = datetime(2026, 9, 7, 20, 30, tzinfo=UTC)
    verdict, _ = await _jr(_ok(payload), notifier).execute("postclose", at)
    assert verdict == "noop"
    assert notifier.posted == []


async def test_a_failed_run_posts_exactly_one_warning(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "verdict_raw": None, "result_text": "I ran out of turns"}
    await _jr(_ok(payload), notifier).execute("scout", IN_WINDOW)
    assert len(notifier.posted) == 1
    assert notifier.posted[0].startswith("⚠️ scout content_failed:")


async def test_a_quiet_pass_says_nothing(notifier: RecordingNotifier) -> None:
    await _jr(_ok(GOOD), notifier).execute("scout", IN_WINDOW)
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


# --- classify is pure -------------------------------------------------------

def test_classify_reads_the_reply_and_nothing_else() -> None:
    """No clock, no store, no network — so the whole truth table is decidable
    without either end of the wire."""
    from tc.jobs.dispatch import RunnerReply, RunResultView

    spec = JOB_SPECS["scout"]
    assert classify(spec, RunnerReply(busy=True))[0] == "noop"
    assert classify(spec, RunnerReply(transport_error="ReadTimeout"))[0] == "failed"
    assert classify(spec, RunnerReply())[0] == "failed"
    # A body missing `is_error` is not evidence the run succeeded.
    verdict, model, _ = classify(spec, RunnerReply(result=RunResultView()))
    assert verdict == "failed" and model is None
