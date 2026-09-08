"""Shared fixtures for the runner suite.

Nothing here launches a real Claude CLI: `tc_runner.app.QUERY` is the seam and
every test replaces it with an async generator of fake messages. The tokens in
this suite are literal strings that are not credentials anywhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

import tc_runner.app as appmod

RUNNER_TOKEN = "runner-token-test"  # noqa: S105 - a test literal, not a credential
ROLE_TOKEN = "test-token-not-real"  # noqa: S105 - a test literal, not a credential

BODY: dict[str, Any] = {
    "job": "scout",
    "prompt": "run the scout pass",
    "allowed_tools": ["mcp__engine__cohort", "WebSearch"],
    "mcp_role": "research",
    "mcp_role_token": ROLE_TOKEN,
    "output_schema": {
        "type": "object",
        "properties": {"cohort": {"type": "integer"}},
        "required": ["cohort"],
        "additionalProperties": False,
    },
    "max_turns": 5,
    "timeout_s": 5.0,
    "agent": "scout",
}
AUTH = {"Authorization": f"Bearer {RUNNER_TOKEN}"}


@dataclass
class FakeResult:
    """The shape of `ResultMessage` the runner actually reads (0c-sdk-facts §1.7)."""

    subtype: str = "success"
    is_error: bool = False
    num_turns: int = 3
    result: str | None = '{"cohort": 4}'
    structured_output: Any = field(default_factory=lambda: {"cohort": 4})
    permission_denials: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = field(default_factory=lambda: {"input_tokens": 10})
    duration_ms: int = 1200


def fake_query(
    seq: Sequence[Any],
    *,
    delay: float = 0.0,
    captured: dict[str, Any] | None = None,
) -> Callable[..., AsyncIterator[Any]]:
    """Build a stand-in for `claude_agent_sdk.query` over a fixed message sequence."""

    async def _q(*, prompt: str, options: Any, **kw: Any) -> AsyncIterator[Any]:
        if captured is not None:
            captured["prompt"] = prompt
            captured["options"] = options
        for msg in seq:
            if delay:
                await asyncio.sleep(delay)
            yield msg

    return _q


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setattr(appmod, "RUNNER_TOKEN", RUNNER_TOKEN)
    monkeypatch.setattr(appmod, "OAUTH_TOKEN", ROLE_TOKEN)
    monkeypatch.setattr(appmod, "ENGINE_URL", "http://127.0.0.1:8080")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=appmod.app), base_url="http://runner"
    )
