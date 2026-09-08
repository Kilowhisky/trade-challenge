"""The runner service: auth, the one-run lock, and a timeout that is a result."""

from __future__ import annotations

import asyncio
import json
import secrets
import stat
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import (
    AUTH,
    BODY,
    ROLE_TOKEN,
    RUNNER_TOKEN,
    FakeResult,
    RecordingGen,
    fake_query,
    gen_query,
)

import tc_runner.app as appmod


async def test_health_is_open(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["busy"] is False


async def test_health_reports_ok_only_when_both_tokens_are_present(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.get("/health")).json()["ok"] is True
    monkeypatch.setattr(appmod, "OAUTH_TOKEN", "")
    assert (await client.get("/health")).json()["ok"] is False


async def test_run_requires_the_bearer(client: httpx.AsyncClient) -> None:
    assert (await client.post("/run", json=BODY)).status_code == 401
    r = await client.post("/run", json=BODY, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


async def test_an_unset_runner_token_refuses_everything(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty configured token must never mean "any bearer will do".
    monkeypatch.setattr(appmod, "RUNNER_TOKEN", "")
    r = await client.post("/run", json=BODY, headers={"Authorization": "Bearer "})
    assert r.status_code == 403


async def test_run_returns_the_structured_verdict(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    r = await client.post("/run", json=BODY, headers=AUTH)
    assert r.status_code == 200
    out = r.json()
    assert out["verdict_raw"] == {"cohort": 4}
    assert out["is_error"] is False
    assert out["timed_out"] is False
    assert out["duration_s"] > 0
    assert out["num_turns"] == 3
    assert out["usage"] == {"input_tokens": 10}
    assert captured["prompt"] == BODY["prompt"]


async def test_options_carry_the_pins_the_gate_depends_on(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    await client.post("/run", json=BODY, headers=AUTH)
    o = captured["options"]
    assert o.cli_path == appmod.CLI_PATH  # never the wheel's bundled CLI
    assert o.setting_sources == ["project"]  # CLAUDE.md and .claude/agents
    assert o.cwd == appmod.REPO_DIR
    assert o.permission_mode == "default"  # bypassPermissions is never used
    assert o.strict_mcp_config is True
    assert set(appmod.DENY_ALWAYS) <= set(o.disallowed_tools)
    assert o.allowed_tools == BODY["allowed_tools"]
    assert o.output_format == {"type": "json_schema", "schema": BODY["output_schema"]}
    assert o.extra_args == {"agent": "scout"}
    assert o.max_turns == BODY["max_turns"]
    assert o.env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-token-not-real"  # noqa: S105
    assert o.env["DISABLE_AUTOUPDATER"] == "1"
    # A PATH, never the dict: the dict is rendered by the SDK into
    # `--mcp-config <json>` on the CLI child's argv, which publishes the
    # engine's role bearer to every `ps` on the host.
    assert isinstance(o.mcp_servers, str)
    assert not isinstance(o.mcp_servers, dict)


async def test_the_mcp_bearer_is_written_to_a_private_file_not_the_child_s_argv(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    seen: dict[str, Any] = {}

    def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        # Read the config DURING the run: it is deleted the moment the run ends.
        path = Path(options.mcp_servers)
        seen["mode"] = stat.S_IMODE(path.stat().st_mode)
        seen["dir_mode"] = stat.S_IMODE(path.parent.stat().st_mode)
        seen["body"] = json.loads(path.read_text())
        seen["path"] = path
        return fake_query([FakeResult()], captured=captured)(prompt=prompt, options=options)

    monkeypatch.setattr(appmod, "QUERY", _q)
    assert (await client.post("/run", json=BODY, headers=AUTH)).status_code == 200
    engine = seen["body"]["mcpServers"]["engine"]
    assert engine["type"] == "http"
    assert engine["url"] == "http://127.0.0.1:8080/mcp/research/"
    assert engine["headers"]["Authorization"] == f"Bearer {ROLE_TOKEN}"
    assert seen["mode"] == 0o600, oct(seen["mode"])  # never world-readable, not even briefly
    assert seen["dir_mode"] == 0o700, oct(seen["dir_mode"])
    # And it does not outlive the run: the whole private directory is gone.
    assert not seen["path"].exists()
    assert not seen["path"].parent.exists()


async def test_the_mcp_config_is_removed_even_when_the_run_raises(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        seen["path"] = Path(options.mcp_servers)
        raise RuntimeError("CLINotFoundError")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(appmod, "QUERY", _q)
    assert (await client.post("/run", json=BODY, headers=AUTH)).json()["is_error"] is True
    assert not seen["path"].exists()
    assert not seen["path"].parent.exists()


async def test_the_mcp_config_is_removed_after_a_timeout(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    gen = RecordingGen([FakeResult()], delay=5.0)

    def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        seen["path"] = Path(options.mcp_servers)
        return gen

    monkeypatch.setattr(appmod, "QUERY", _q)
    out = (await client.post("/run", json={**BODY, "timeout_s": 0.05}, headers=AUTH)).json()
    assert out["timed_out"] is True
    assert not seen["path"].parent.exists()


async def test_the_hook_on_the_options_is_the_job_s_own_gate(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    await client.post("/run", json=BODY, headers=AUTH)
    matchers = captured["options"].hooks["PreToolUse"]
    assert len(matchers) == 1
    assert matchers[0].matcher is None  # every tool, not a subset
    gate = matchers[0].hooks[0]
    assert await gate({"tool_name": "WebSearch", "tool_input": {}}, "tu_1", None) == {}
    denied = await gate({"tool_name": "Read", "tool_input": {}}, "tu_1", None)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_no_agent_means_no_agent_flag(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    await client.post("/run", json={**BODY, "agent": None}, headers=AUTH)
    assert captured["options"].extra_args == {}


async def test_the_decide_role_gets_the_decide_mount(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        seen["body"] = json.loads(Path(options.mcp_servers).read_text())
        return fake_query([FakeResult()])(prompt=prompt, options=options)

    monkeypatch.setattr(appmod, "QUERY", _q)
    await client.post("/run", json={**BODY, "mcp_role": "decide"}, headers=AUTH)
    assert seen["body"]["mcpServers"]["engine"]["url"].endswith("/mcp/decide/")


async def test_a_missing_structured_output_is_reported_not_invented(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        appmod,
        "QUERY",
        fake_query([FakeResult(structured_output=None, result="I could not finish")]),
    )
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["verdict_raw"] is None
    assert out["result_text"] == "I could not finish"


async def test_is_error_with_subtype_success_is_still_an_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        appmod, "QUERY", fake_query([FakeResult(is_error=True, subtype="success")])
    )
    assert (await client.post("/run", json=BODY, headers=AUTH)).json()["is_error"] is True


async def test_a_query_that_raises_after_yielding_is_still_reported(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        yield FakeResult(is_error=True, subtype="error_max_turns", structured_output=None)
        raise RuntimeError("ResultError: exit code 1")

    monkeypatch.setattr(appmod, "QUERY", _q)
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["is_error"] is True
    assert out["subtype"] == "error_max_turns"


async def test_a_query_that_raises_before_any_result_is_an_error_result(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        raise RuntimeError("CLINotFoundError")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(appmod, "QUERY", _q)
    r = await client.post("/run", json=BODY, headers=AUTH)
    assert r.status_code == 200  # a failure is a result, never a 500
    out = r.json()
    assert out["is_error"] is True
    assert out["verdict_raw"] is None
    assert out["timed_out"] is False


async def test_timeout_is_a_result_not_an_exception(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], delay=0.5))
    out = (await client.post("/run", json={**BODY, "timeout_s": 0.05}, headers=AUTH)).json()
    assert out["timed_out"] is True
    assert out["is_error"] is True
    assert out["verdict_raw"] is None


async def test_only_one_run_at_a_time(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], delay=0.3))
    first = asyncio.create_task(client.post("/run", json=BODY, headers=AUTH))
    await asyncio.sleep(0.05)
    second = await client.post("/run", json=BODY, headers=AUTH)
    assert second.status_code == 409
    assert (await first).status_code == 200


async def test_the_lock_is_released_after_a_failed_run(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], delay=0.5))
    assert (await client.post("/run", json={**BODY, "timeout_s": 0.05}, headers=AUTH)).json()[
        "timed_out"
    ] is True
    assert (await client.get("/health")).json()["busy"] is False
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()]))
    assert (await client.post("/run", json=BODY, headers=AUTH)).status_code == 200


async def test_permission_denials_are_relayed_verbatim(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    denial = {"tool_name": "Bash", "tool_use_id": "toolu_1", "tool_input": {"command": "ls"}}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult(permission_denials=[denial])]))
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["permission_denials"] == [denial]


async def test_body_rejects_unknown_fields(client: httpx.AsyncClient) -> None:
    r = await client.post("/run", json={**BODY, "sudo": True}, headers=AUTH)
    assert r.status_code == 422


async def test_body_rejects_an_unknown_mcp_role(client: httpx.AsyncClient) -> None:
    r = await client.post("/run", json={**BODY, "mcp_role": "admin"}, headers=AUTH)
    assert r.status_code == 422


async def test_the_last_result_message_wins(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NotAResult:
        text = "assistant chatter"

    monkeypatch.setattr(
        appmod,
        "QUERY",
        fake_query([NotAResult(), FakeResult(structured_output={"cohort": 9})]),
    )
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["verdict_raw"] == {"cohort": 9}


# --- review round 1: teardown, error text, timing-safe auth, wildcards --------


async def test_a_timeout_closes_the_generator_before_the_lock_drops(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen = RecordingGen([FakeResult()], delay=5.0)
    monkeypatch.setattr(appmod, "QUERY", gen_query(gen))
    out = (await client.post("/run", json={**BODY, "timeout_s": 0.05}, headers=AUTH)).json()
    assert out["timed_out"] is True
    assert out["is_error"] is True
    assert out["verdict_raw"] is None
    # The reap happened, and it happened before the response -- i.e. inside the
    # lock, not after it.
    assert gen.closed is True
    assert out["teardown_s"] >= 0.0
    assert out["result_text"] is None  # a clean reap says nothing
    assert (await client.get("/health")).json()["busy"] is False


async def test_a_wedged_teardown_does_not_hold_the_lock_forever(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(appmod, "TEARDOWN_S", 0.1)
    gen = RecordingGen([FakeResult()], delay=5.0, close_delay=5.0)
    monkeypatch.setattr(appmod, "QUERY", gen_query(gen))
    out = (await client.post("/run", json={**BODY, "timeout_s": 0.05}, headers=AUTH)).json()
    assert gen.close_started is True
    assert gen.closed is False  # gave up on the reap rather than wedge the runner
    assert out["timed_out"] is True
    assert out["subtype"] is None  # a timeout is `timed_out`, not a runner throw
    assert out["result_text"] is not None
    assert "teardown did not complete" in out["result_text"]
    assert 0.05 < out["teardown_s"] < 2.0
    assert (await client.get("/health")).json()["busy"] is False


async def test_a_completed_run_reports_no_teardown(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()]))
    assert (await client.post("/run", json=BODY, headers=AUTH)).json()["teardown_s"] == 0.0


async def test_a_raise_before_any_result_names_the_exception(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        raise RuntimeError("no such file: /usr/local/bin/claude")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(appmod, "QUERY", _q)
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["is_error"] is True
    assert out["subtype"] == "error_runner_exception"
    assert out["result_text"] is not None
    assert "RuntimeError" in out["result_text"]
    assert "no such file" in out["result_text"]


async def test_a_result_message_wins_over_the_later_raise_s_text(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        yield FakeResult(is_error=True, subtype="error_max_turns", result="hit the turn cap")
        raise RuntimeError("ResultError: exit code 1")

    monkeypatch.setattr(appmod, "QUERY", _q)
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["subtype"] == "error_max_turns"
    assert out["result_text"] == "hit the turn cap"


async def test_the_bearer_is_compared_with_compare_digest(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = secrets.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(secrets, "compare_digest", spy)
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()]))
    assert (await client.post("/run", json=BODY, headers=AUTH)).status_code == 200
    assert calls  # the compare went through the constant-time path, not `==`
    # A prefix of the real token must still be a 403.
    r = await client.post("/run", json=BODY, headers={"Authorization": "Bearer runner-token"})
    assert r.status_code == 403


@pytest.mark.parametrize("bad", ["*", "mcp__*", "Web*", "Bash*", "mcp__engine__doc_*", "**"])
async def test_a_wildcard_wider_than_one_mcp_server_is_a_422(
    client: httpx.AsyncClient, bad: str
) -> None:
    body = {**BODY, "allowed_tools": ["mcp__engine__cohort", bad]}
    assert (await client.post("/run", json=body, headers=AUTH)).status_code == 422


async def test_a_server_scoped_wildcard_is_accepted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    body = {**BODY, "allowed_tools": ["mcp__engine__*", "WebSearch"]}
    assert (await client.post("/run", json=body, headers=AUTH)).status_code == 200
    assert captured["options"].allowed_tools == ["mcp__engine__*", "WebSearch"]


async def test_a_malformed_sdk_field_degrades_to_an_error_result(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # permission_denials is list[Any] on the SDK side; a non-dict entry must not
    # become a 500 -- the engine's contract is that a run always answers.
    monkeypatch.setattr(
        appmod, "QUERY", fake_query([FakeResult(permission_denials=["not-a-dict"])])  # type: ignore[list-item]
    )
    r = await client.post("/run", json=BODY, headers=AUTH)
    assert r.status_code == 200
    out = r.json()
    assert out["is_error"] is True
    assert out["subtype"] == "error_runner_result"
    assert out["result_text"] is not None
    assert "could not render" in out["result_text"]


# --- review round 2: the bearer never leaves, and busy answers now -----------


async def test_an_exception_carrying_the_bearer_comes_back_redacted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`result_text` is written to the engine's job ledger and posted to
    Discord. An SDK exception renders whatever it was handed, and what it was
    handed includes the engine's role bearer."""

    async def _q(*, prompt: str, options: Any, **kw: Any) -> Any:
        raise RuntimeError(
            f"connect failed with Authorization: Bearer {ROLE_TOKEN} "
            f"(runner {RUNNER_TOKEN})"
        )
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(appmod, "QUERY", _q)
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    text = out["result_text"]
    assert text is not None
    assert ROLE_TOKEN not in text
    assert RUNNER_TOKEN not in text
    assert text.count(appmod.REDACTED) == 2  # both bearers, both gone
    assert "RuntimeError" in text  # the diagnosis survives the scrub


async def test_a_result_message_carrying_the_bearer_comes_back_redacted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        appmod,
        "QUERY",
        fake_query([FakeResult(result=f"401 from Bearer {ROLE_TOKEN}", subtype="success")]),
    )
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert ROLE_TOKEN not in out["result_text"]
    assert appmod.REDACTED in out["result_text"]


async def test_a_subtype_carrying_the_bearer_comes_back_redacted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        appmod, "QUERY", fake_query([FakeResult(subtype=f"error_{ROLE_TOKEN}")])
    )
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["subtype"] == f"error_{appmod.REDACTED}"


async def test_the_render_failure_path_is_redacted_too(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed SDK field reaches the "could not render" fallback, which
    # interpolates the exception -- and a pydantic error quotes its input.
    monkeypatch.setattr(
        appmod,
        "QUERY",
        fake_query([FakeResult(permission_denials=[ROLE_TOKEN])]),  # type: ignore[list-item]
    )
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["subtype"] == "error_runner_result"
    assert ROLE_TOKEN not in out["result_text"]


def test_redact_leaves_ordinary_text_alone() -> None:
    assert appmod.redact("nothing secret here", ("abc",)) == "nothing secret here"
    assert appmod.redact(None, ("abc",)) is None
    # An empty secret must never turn every character into a redaction.
    assert appmod.redact("abc", appmod.secrets_of(_req(mcp_role_token="x"))) == "abc"  # noqa: S106


def _req(**kw: Any) -> appmod.RunRequest:
    return appmod.RunRequest.model_validate({**BODY, **kw})


def test_secrets_of_drops_the_empty_ones_and_orders_longest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(appmod, "RUNNER_TOKEN", "")
    monkeypatch.setattr(appmod, "OAUTH_TOKEN", "tok")
    values = appmod.secrets_of(_req(mcp_role_token="tok-and-more"))  # noqa: S106
    assert values == ("tok-and-more", "tok")
    # Longest first is what stops a shorter secret shredding a longer one into
    # "[REDACTED]-and-more", which leaks the tail.
    assert appmod.redact("tok-and-more", values) == appmod.REDACTED


def test_the_run_slot_is_taken_without_waiting_for_it() -> None:
    slot = appmod.OneAtATime()
    assert slot.busy is False
    assert slot.try_acquire() is True
    assert slot.busy is True
    assert slot.try_acquire() is False  # no waiting, no queue: just "no"
    slot.release()
    assert slot.try_acquire() is True


async def test_a_concurrent_post_is_refused_immediately_not_queued(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409 must arrive while the first job is still running, not after it.

    The engine drops a `busy` reply rather than retrying, so a second request
    that QUEUES on the slot is a job whose window expires inside a socket.
    """
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], delay=0.6))
    first = asyncio.create_task(client.post("/run", json=BODY, headers=AUTH))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    second = await client.post("/run", json=BODY, headers=AUTH)
    elapsed = time.monotonic() - started
    assert second.status_code == 409
    assert elapsed < 0.3, elapsed  # answered now, not after the 0.6s run
    assert (await client.get("/health")).json()["busy"] is True
    assert (await first).status_code == 200
    assert (await client.get("/health")).json()["busy"] is False
