"""The runner service: auth, the one-run lock, and a timeout that is a result."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

import httpx
import pytest
from conftest import AUTH, BODY, FakeResult, RecordingGen, fake_query, gen_query

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
    assert o.mcp_servers["engine"]["type"] == "http"
    assert o.mcp_servers["engine"]["url"] == "http://127.0.0.1:8080/mcp/research/"
    assert o.mcp_servers["engine"]["headers"]["Authorization"] == "Bearer test-token-not-real"


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
    captured: dict[str, Any] = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    await client.post("/run", json={**BODY, "mcp_role": "decide"}, headers=AUTH)
    assert captured["options"].mcp_servers["engine"]["url"].endswith("/mcp/decide/")


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
