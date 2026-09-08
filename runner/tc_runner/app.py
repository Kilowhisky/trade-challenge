"""The Claude runner: one job at a time, and nothing else in the container.

It holds CLAUDE_CODE_OAUTH_TOKEN and a per-role MCP bearer handed to it by the
engine. It has no Schwab credential, no database, and no Bash. If it is OOM-
killed mid-research it takes a research run with it and nothing else -- which is
the whole reason it is a second cgroup (spec §3).

Read-only is enforced TWICE, both times in Python (spec §4):

1. `allowed_tools` on the options, which is what the CLI auto-approves.
2. A PreToolUse hook that denies anything outside that list and denies Bash,
   Write, Edit, NotebookEdit and the agent-spawning tools unconditionally.

Two is not redundant. A hook outranks BOTH `allowed_tools` and
`bypassPermissions` -- verified by execution -- while `can_use_tool` does not,
because the CLI only consults it when its own rules evaluate to "ask", so an
allowlist silently shadows it. The hook is the gate; the allowlist is the
convenience.

There is no bash script path for Claude to be refused on, so the v2 failure
where the container's permission gate silently refused every `scripts/*.sh`
call with no approver present has nothing left to refuse.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, cast

from claude_agent_sdk import ClaudeAgentOptions, HookCallback, HookMatcher, query
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# The SDK bundles its own CLI (2.1.259 in 0.2.152) and `_find_cli()` prefers it
# over PATH. Without an explicit cli_path the Dockerfile's 2.1.234 pin and its
# DISABLE_AUTOUPDATER buy exactly nothing for SDK-driven runs.
CLI_PATH = os.environ.get("TC_CLAUDE_CLI", "/usr/local/bin/claude")
REPO_DIR = os.environ.get("TC_REPO_DIR", "/app/repo")
ENGINE_URL = os.environ.get("TC_ENGINE_URL", "http://127.0.0.1:8080").rstrip("/")
RUNNER_TOKEN = os.environ.get("TC_RUNNER_TOKEN", "")
OAUTH_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")

# Denied for every job, whatever a job spec says. Bash/Write/Edit/NotebookEdit
# are the write surface the design removes; Task/Agent would let a job spawn a
# child whose allowlist this hook never sees.
DENY_ALWAYS: tuple[str, ...] = (
    "Bash",
    "BashOutput",
    "KillShell",
    "Write",
    "Edit",
    "NotebookEdit",
    "Task",
    "Agent",
)

# The test seam. Nothing in the suite launches a real CLI.
QUERY: Any = query

GateCallback = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]

_lock = asyncio.Lock()


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job: str
    prompt: str
    allowed_tools: list[str]
    mcp_role: Literal["research", "decide"]
    mcp_role_token: str
    output_schema: dict[str, Any]
    max_turns: int = Field(default=40, ge=1, le=200)
    timeout_s: float = Field(default=1500.0, gt=0, le=7200)
    agent: str | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict_raw: dict[str, Any] | None
    result_text: str | None
    is_error: bool
    subtype: str | None
    num_turns: int
    permission_denials: list[dict[str, Any]]
    usage: dict[str, Any]
    duration_s: float
    timed_out: bool


def tool_allowed(name: str, allowed: Sequence[str]) -> bool:
    """Exact match, or a trailing-`*` prefix match, with DENY_ALWAYS winning first.

    The deny list is checked before the allowlist so no job spec -- and no
    wildcard broad enough to cover it -- can reach the write surface.
    """
    if name in DENY_ALWAYS:
        return False
    for entry in allowed:
        if entry == name:
            return True
        if entry.endswith("*") and name.startswith(entry[:-1]):
            return True
    return False


def make_gate(allowed: Sequence[str]) -> GateCallback:
    """The deny shape is exact and was verified live: permissionDecision "deny"
    inside hookSpecificOutput, with a reason the model reads. An empty dict is
    "no opinion" and falls through -- returning "allow" here would override
    decisions this gate has no business making."""

    async def gate(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        name = str(input_data.get("tool_name", ""))
        if name in DENY_ALWAYS:
            reason = f"{name} is denied for every engine job: this runner has no write surface."
        elif not tool_allowed(name, allowed):
            reason = f"{name} is not on this job's allowlist. Use one of: {', '.join(allowed)}."
        else:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return gate


def build_options(req: RunRequest) -> ClaudeAgentOptions:
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": OAUTH_TOKEN,
        "DISABLE_AUTOUPDATER": "1",
    }
    return ClaudeAgentOptions(
        cli_path=CLI_PATH,
        env=env,
        cwd=REPO_DIR,
        # "project" is REQUIRED: it is what loads CLAUDE.md and discovers
        # .claude/agents/*.md. The default (None) resolves to
        # ["user","project"], which would also pull in the host account's
        # settings -- not something an unattended run should inherit.
        setting_sources=["project"],
        allowed_tools=list(req.allowed_tools),
        disallowed_tools=list(DENY_ALWAYS),
        # matcher=None is every tool, not a subset: a matcher that named the
        # denied tools would leave everything it did not name ungated.
        # The SDK types a hook's first argument as a union of per-event
        # TypedDicts while the wire delivers a plain dict (0c-sdk-facts §1.3),
        # which is what the gate is written and tested against; the cast
        # reconciles the two without weakening the gate's own signature.
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=None,
                    hooks=[cast(HookCallback, make_gate(req.allowed_tools))],
                )
            ]
        },
        mcp_servers={
            "engine": {
                "type": "http",
                # Trailing slash on purpose: /mcp/<role> without it answers a
                # 307 on every message.
                "url": f"{ENGINE_URL}/mcp/{req.mcp_role}/",
                "headers": {"Authorization": f"Bearer {req.mcp_role_token}"},
            }
        },
        # Ignore .mcp.json, user settings and plugin servers: the engine's
        # server list must be exactly this one.
        strict_mcp_config=True,
        output_format={"type": "json_schema", "schema": req.output_schema},
        permission_mode="default",
        max_turns=req.max_turns,
        extra_args={"agent": req.agent} if req.agent else {},
    )


async def _consume(req: RunRequest) -> tuple[Any | None, bool]:
    """Drain the message stream, keeping the LAST ResultMessage.

    An error result is yielded and THEN query() raises, so a bare `async for`
    without this guard loses the very message that says what went wrong.
    """
    last: Any | None = None
    raised = False
    try:
        async for msg in QUERY(prompt=req.prompt, options=build_options(req)):
            if hasattr(msg, "is_error"):
                last = msg
    except Exception:  # a failed run is reported as a result, never raised at the caller
        raised = True
    return last, raised


async def run(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"error": "missing bearer"}, status_code=401)
    if not RUNNER_TOKEN or auth.split(" ", 1)[1].strip() != RUNNER_TOKEN:
        return JSONResponse({"error": "bad token"}, status_code=403)
    try:
        req = RunRequest.model_validate(await request.json())
    except Exception as e:
        return JSONResponse({"error": f"bad request: {type(e).__name__}"}, status_code=422)

    if _lock.locked():
        # One run at a time is the memory contract (spec §3). A queue would
        # turn a slow job into a backlog of jobs whose windows have passed.
        return JSONResponse({"error": "runner busy"}, status_code=409)

    async with _lock:
        started = time.monotonic()
        timed_out = False
        try:
            last, raised = await asyncio.wait_for(_consume(req), timeout=req.timeout_s)
        except TimeoutError:
            last, raised, timed_out = None, True, True
        duration = time.monotonic() - started

    structured = getattr(last, "structured_output", None) if last is not None else None
    result = RunResult(
        verdict_raw=structured if isinstance(structured, dict) else None,
        result_text=getattr(last, "result", None) if last is not None else None,
        # is_error is the test, never subtype: is_error=True coexists with
        # subtype="success" for an API-level failure.
        is_error=bool(getattr(last, "is_error", True)) or raised or last is None,
        subtype=getattr(last, "subtype", None) if last is not None else None,
        num_turns=int(getattr(last, "num_turns", 0) or 0) if last is not None else 0,
        permission_denials=list(getattr(last, "permission_denials", None) or []),
        usage=dict(getattr(last, "usage", None) or {}),
        duration_s=duration,
        timed_out=timed_out,
    )
    return JSONResponse(result.model_dump())


async def health(request: Request) -> Response:
    return JSONResponse(
        {
            "ok": bool(OAUTH_TOKEN) and bool(RUNNER_TOKEN),
            "busy": _lock.locked(),
            "cli_path": CLI_PATH,
            "engine_url": ENGINE_URL,
        }
    )


app = Starlette(routes=[Route("/run", run, methods=["POST"]), Route("/health", health)])
