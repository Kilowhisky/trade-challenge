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

`allowed_tools` is NOT the only thing the CLI auto-approves: the repo's
`.claude/settings.json` is loaded (`setting_sources=["project"]` is what makes
CLAUDE.md and `.claude/agents/*.md` visible at all), and every built-in it
allows is auto-approved for a job that never declared it. So for the built-in
surface the hook is not the second of two gates -- it is the ONLY gate. That
is why `DENY_ALWAYS` is unconditional rather than derived from the allowlist,
and why the project settings carry no entry the hook does not also refuse.

There is no bash script path for Claude to be refused on, so the v2 failure
where the container's permission gate silently refused every `scripts/*.sh`
call with no approver present has nothing left to refuse.

The MCP bearer never reaches the CLI child's argv. The SDK renders a
`mcp_servers` DICT into `--mcp-config <json>`, which puts the engine's role
token in `/proc/<pid>/cmdline` for anything that can run `ps`; the same option
accepts a PATH, so the config is written to a per-run 0600 file in a private
directory and deleted in a `finally`. For the same reason every string this
service hands back -- an exception's text especially -- is scrubbed of the
three secrets it holds before it leaves the process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from typing import Any, Literal, cast

from claude_agent_sdk import ClaudeAgentOptions, HookCallback, HookMatcher, query
from pydantic import BaseModel, ConfigDict, Field, field_validator
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
    "MultiEdit",
    "NotebookEdit",
    "Task",
    "Agent",
    # SlashCommand runs a project command file, which is an arbitrary
    # instruction sheet this gate never reviewed; TodoWrite writes to the repo.
    "SlashCommand",
    "TodoWrite",
)

# How long the runner will wait for the SDK generator to close -- i.e. for the
# CLI child to be reaped -- after a timeout, before it gives up and releases the
# lock anyway. 0c-sdk-facts §1.8 measured a 6 s deadline taking 11.5 s of wall
# clock end to end, so this is set above the observed reap, not at it.
TEARDOWN_S = 15.0

# What replaces a secret anywhere it would otherwise be echoed back.
REDACTED = "[REDACTED]"

# A wildcard allowlist entry may only widen a single MCP server's namespace.
# "*" would hand a job every built-in tool the deny list does not name, and
# "Web*" or "mcp__*" are the same mistake in smaller print: the allowlist is a
# job's declaration of what it needs, and a wildcard that spans servers or
# built-ins is a declaration of "whatever turns up".
WILDCARD_SHAPE = re.compile(r"^mcp__[^*]+__\*$")

# The test seam. Nothing in the suite launches a real CLI.
QUERY: Any = query

GateCallback = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


class OneAtATime:
    """The single run slot, acquired without ever waiting for it.

    `asyncio.Lock` has no try-acquire, and the shape it invites --
    `if _lock.locked(): return 409` followed by `async with _lock` -- is
    check-then-act. `locked()` is False in the window between a release and
    the handoff to a waiter, so a request can read "free", fall into the
    `async with`, and BLOCK there for the length of a job it was supposed to
    be told 409 about. The engine's contract is that a busy runner answers
    immediately (`RunnerReply(busy=True)` is dropped, never retried), so a
    request that queues silently is a job whose window expires inside a
    socket.

    A plain flag is enough and is what makes it correct: there is no `await`
    between the test and the set, and the event loop is single-threaded, so
    the pair is atomic in a way two lock operations are not.
    """

    def __init__(self) -> None:
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def try_acquire(self) -> bool:
        if self._busy:
            return False
        self._busy = True
        return True

    def release(self) -> None:
        self._busy = False


_slot = OneAtATime()


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

    @field_validator("allowed_tools")
    @classmethod
    def _wildcards_are_scoped_to_one_mcp_server(cls, v: list[str]) -> list[str]:
        """Reject a wildcard the gate cannot reason about.

        `tool_allowed` still refuses DENY_ALWAYS ahead of any allowlist entry,
        so a bare "*" was never a way to reach Bash -- but it WAS a way to
        reach every built-in the deny list does not name, silently, from a
        job spec typo. A malformed job spec is a 422, not a wide-open run.
        """
        for entry in v:
            if entry.endswith("*") and not WILDCARD_SHAPE.match(entry):
                raise ValueError(
                    f"allowed_tools entry {entry!r} is not a permitted wildcard: "
                    "only 'mcp__<server>__*' may end in '*'"
                )
        return v


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
    # Seconds spent closing the SDK generator after a timeout -- i.e. waiting
    # for the CLI child to be reaped. 0.0 on every run that was not timed out.
    teardown_s: float = 0.0


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

    def _deny(reason: str) -> dict[str, Any]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def gate(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        # The whole body is guarded. A hook that raises is a hook whose
        # decision the CLI never receives, and "no decision" falls through to
        # the allowlist -- which is exactly the layer this hook exists to
        # outrank. So every failure mode, including an input shape that is not
        # the dict the wire is documented to deliver, resolves to deny.
        try:
            name = input_data.get("tool_name", "")
            if not isinstance(name, str):
                return _deny(f"tool_name is a {type(name).__name__}, not a name: denied.")
            if name in DENY_ALWAYS:
                return _deny(
                    f"{name} is denied for every engine job: this runner has no write surface."
                )
            if not tool_allowed(name, allowed):
                return _deny(
                    f"{name} is not on this job's allowlist. "
                    f"Use one of: {', '.join(allowed)}."
                )
        except Exception as e:
            return _deny(f"gate error: {type(e).__name__}")
        return {}

    return gate


def secrets_of(req: RunRequest) -> tuple[str, ...]:
    """Every string this process holds that must never be echoed back.

    Read from the module globals at call time rather than captured at import,
    because that is what the tests (and a re-exec'd container) rebind.
    """
    return tuple(
        sorted(
            {s for s in (req.mcp_role_token, RUNNER_TOKEN, OAUTH_TOKEN) if s},
            key=len,
            reverse=True,  # longest first: a secret containing another still redacts whole
        )
    )


def redact(text: str | None, secret_values: Sequence[str]) -> str | None:
    """Replace every secret with `REDACTED`, leaving the rest of the text intact.

    An exception message is the one string in this service nobody wrote: the
    SDK renders whatever it was handed, and what it was handed includes the
    engine's role bearer and the OAuth token. `result_text` is written to the
    engine's job ledger and posted to Discord, so a leak there is a leak into
    a channel and a file, not just a log line.
    """
    if text is None:
        return None
    out = text
    for value in secret_values:
        out = out.replace(value, REDACTED)
    return out


@contextlib.contextmanager
def mcp_config_file(req: RunRequest) -> Iterator[str]:
    """Write the MCP config to a private 0600 file and remove it afterwards.

    `ClaudeAgentOptions.mcp_servers` accepts a dict OR a path. The dict is
    rendered by the SDK into `--mcp-config <json blob>` on the CLI child's
    argv, which publishes the engine's role bearer to every `ps` on the host
    and to `/proc/<pid>/cmdline`. The path form passes a filename instead, so
    the bearer lives only in a file this process owns.

    `mkdtemp` is 0700 and the file is opened `O_EXCL` at 0600, so the secret is
    never briefly world-readable between creation and chmod. The whole
    directory is removed in the caller's `finally`, timeout path included --
    the config outlives the run by nothing.
    """
    directory = tempfile.mkdtemp(prefix="tc-runner-mcp-")
    path = os.path.join(directory, "mcp.json")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"mcpServers": mcp_servers(req)}, fh)
        yield path
    finally:
        # ignore_errors: a config we could not delete is worth a leaked file,
        # never a failed run whose result the engine then cannot read.
        shutil.rmtree(directory, ignore_errors=True)


def mcp_servers(req: RunRequest) -> dict[str, Any]:
    return {
        "engine": {
            "type": "http",
            # Trailing slash on purpose: /mcp/<role> without it answers a
            # 307 on every message.
            "url": f"{ENGINE_URL}/mcp/{req.mcp_role}/",
            "headers": {"Authorization": f"Bearer {req.mcp_role_token}"},
        }
    }


def build_options(req: RunRequest, mcp_config_path: str) -> ClaudeAgentOptions:
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
        # A PATH, never the dict: the dict becomes `--mcp-config <json>` on the
        # child's argv and publishes the bearer to `ps` (see mcp_config_file).
        mcp_servers=mcp_config_path,
        # Ignore .mcp.json, user settings and plugin servers: the engine's
        # server list must be exactly this one.
        strict_mcp_config=True,
        output_format={"type": "json_schema", "schema": req.output_schema},
        permission_mode="default",
        max_turns=req.max_turns,
        extra_args={"agent": req.agent} if req.agent else {},
    )


async def _consume(gen: Any) -> tuple[Any | None, str | None]:
    """Drain the message stream, keeping the LAST ResultMessage.

    An error result is yielded and THEN query() raises, so a bare `async for`
    without this guard loses the very message that says what went wrong.

    Returns the last result message (or None) and, if the stream raised, the
    exception rendered as text. A raise with no result before it is the only
    case where the caller has nothing at all to report, so the text is what
    stops that becoming a blank error result the operator cannot diagnose.
    """
    last: Any | None = None
    err: str | None = None
    try:
        async for msg in gen:
            if hasattr(msg, "is_error"):
                last = msg
    except Exception as e:  # a failed run is reported as a result, never raised at the caller
        err = f"{type(e).__name__}: {e}"
    return last, err


async def _close(gen: Any) -> str | None:
    """Close the SDK generator so the CLI child is reaped before the lock drops.

    Shielded, because this runs on the timeout path where the surrounding task
    may itself be cancelled next -- and a cancel here is precisely how the
    child is orphaned. Bounded by TEARDOWN_S, because a wedged teardown must
    not hold the one-run lock forever; a missed reap is a leak, a held lock is
    an outage.

    Returns None on a clean close, or the failure as text. Teardown is
    best-effort -- the result is already decided by the time it runs -- but a
    reap that did not happen is the operator's problem later, so it is
    reported rather than swallowed.
    """
    aclose = getattr(gen, "aclose", None)
    if aclose is None:
        return "the SDK stream had no aclose(): the CLI child was not reaped"
    try:
        await asyncio.shield(asyncio.wait_for(aclose(), TEARDOWN_S))
    except Exception as e:
        return f"teardown did not complete within {TEARDOWN_S}s: {type(e).__name__}"
    return None


async def run(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"error": "missing bearer"}, status_code=401)
    presented = auth.split(" ", 1)[1].strip()
    # compare_digest, not ==: a short-circuiting compare leaks the shared
    # secret's prefix by timing to anything that can reach this port.
    if not RUNNER_TOKEN or not secrets.compare_digest(
        presented.encode("utf-8"), RUNNER_TOKEN.encode("utf-8")
    ):
        return JSONResponse({"error": "bad token"}, status_code=403)
    try:
        req = RunRequest.model_validate(await request.json())
    except Exception as e:
        return JSONResponse({"error": f"bad request: {type(e).__name__}"}, status_code=422)

    if not _slot.try_acquire():
        # One run at a time is the memory contract (spec §3). A queue would
        # turn a slow job into a backlog of jobs whose windows have passed --
        # so this answers now rather than waiting for the slot to free.
        return JSONResponse({"error": "runner busy"}, status_code=409)

    try:
        started = time.monotonic()
        timed_out = False
        teardown = 0.0
        with mcp_config_file(req) as mcp_path:
            # The generator is built here, not inside _consume, so the timeout
            # path still has a handle to close. Without it the slot frees while
            # the CLI child is still alive and the next job starts alongside a
            # dying one.
            gen = QUERY(prompt=req.prompt, options=build_options(req, mcp_path))
            try:
                last, err = await asyncio.wait_for(_consume(gen), timeout=req.timeout_s)
            except TimeoutError:
                last, timed_out = None, True
                teardown_started = time.monotonic()
                # `err` on this path is the teardown note, not a run failure: a
                # timeout is reported by `timed_out`, and a wedged reap is the
                # only thing left worth saying about it.
                err = await _close(gen)
                teardown = time.monotonic() - teardown_started
        duration = time.monotonic() - started
    finally:
        _slot.release()

    result = _to_result(last, err, duration, timed_out, teardown, secrets_of(req))
    return JSONResponse(result.model_dump())


def _to_result(
    last: Any | None,
    err: str | None,
    duration: float,
    timed_out: bool,
    teardown: float,
    secret_values: Sequence[str] = (),
) -> RunResult:
    """Render whatever came back as a RunResult, and never raise doing it.

    A malformed field from the SDK must not become a 500: the engine's contract
    with this service is that a run always answers with a result, and "the
    runner crashed formatting your failure" is the least useful failure there
    is.
    """
    structured = getattr(last, "structured_output", None) if last is not None else None
    def scrub(text: Any) -> str | None:
        """Scrubbed at the boundary, not at each call site: `result_text` is
        the SDK's or the exception's own words, and both have held the bearer.
        `str()` first, because a malformed SDK field is still a string once it
        reaches the ledger and must be redacted before it gets there."""
        return redact(text if text is None or isinstance(text, str) else str(text),
                      secret_values)

    fields: dict[str, Any] = {
        "verdict_raw": structured if isinstance(structured, dict) else None,
        # A raise with no result at all leaves result_text as the only place the
        # operator can learn what happened, so the exception goes there.
        "result_text": scrub(getattr(last, "result", None) if last is not None else err),
        # is_error is the test, never subtype: is_error=True coexists with
        # subtype="success" for an API-level failure.
        "is_error": bool(getattr(last, "is_error", True)) or err is not None or last is None,
        "subtype": scrub(
            getattr(last, "subtype", None)
            if last is not None
            # A timeout's `err` is a teardown note, not the run's failure mode:
            # `timed_out` already says what happened, so it keeps a null
            # subtype rather than claiming the runner threw.
            else ("error_runner_exception" if err is not None and not timed_out else None)
        ),
        "num_turns": int(getattr(last, "num_turns", 0) or 0) if last is not None else 0,
        "permission_denials": list(getattr(last, "permission_denials", None) or []),
        "usage": dict(getattr(last, "usage", None) or {}),
        "duration_s": duration,
        "timed_out": timed_out,
        "teardown_s": teardown,
    }
    try:
        return RunResult(**fields)
    except Exception as e:
        return RunResult(
            verdict_raw=None,
            result_text=redact(
                f"runner could not render the result: {type(e).__name__}: {e}", secret_values
            ),
            is_error=True,
            subtype="error_runner_result",
            num_turns=0,
            permission_denials=[],
            usage={},
            duration_s=duration,
            timed_out=timed_out,
            teardown_s=teardown,
        )


async def health(request: Request) -> Response:
    return JSONResponse(
        {
            "ok": bool(OAUTH_TOKEN) and bool(RUNNER_TOKEN),
            "busy": _slot.busy,
            "cli_path": CLI_PATH,
            "engine_url": ENGINE_URL,
        }
    )


app = Starlette(routes=[Route("/run", run, methods=["POST"]), Route("/health", health)])
