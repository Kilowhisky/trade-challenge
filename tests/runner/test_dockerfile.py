"""Dockerfile.runner and its compose block hold no data mount, no Schwab
credential, and no route to the engine's own secrets file -- spec §3's memory
fence is worthless if the container that gets OOM-killed for research also
happens to hold the account.
"""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile.runner"
COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"


def _runner_block() -> str:
    body = COMPOSE.read_text()
    assert "  runner:" in body
    runner = body[body.index("  runner:") :]
    # The runner's own `volumes:` key is indented; only the file's trailing,
    # unindented `volumes:` section (the named-volume declarations) starts a
    # line with no leading spaces, so this is where the runner block ends.
    return runner[: runner.index("\nvolumes:")] if "\nvolumes:" in runner else runner


def test_runner_gets_no_data_mount_and_no_schwab_credential() -> None:
    runner = _runner_block()
    assert "/data" not in runner  # no database, no store
    assert "SCHWAB" not in runner  # no broker credential, ever
    assert "..:/app/repo:ro" in runner  # the repo, read-only, and only that


def test_runner_uses_its_own_env_file_not_the_engines() -> None:
    # A text grep for "SCHWAB" or "/data" in the runner block does NOT catch
    # this: env_file: [/srv/tc/.env] would load the engine's whole secrets
    # file -- Schwab credential, Discord webhooks, MCP role bearers -- into
    # this container with neither of those strings appearing anywhere in the
    # compose file itself. The only real check is the env_file path.
    runner = _runner_block()
    assert "env_file: [/srv/tc/runner.env]" in runner
    assert "/srv/tc/.env" not in runner


def test_runner_is_built_on_the_pinned_toolchain_image() -> None:
    body = DOCKERFILE.read_text()
    assert body.splitlines()[0].startswith("#")
    assert "FROM ghcr.io/kilowhisky/trade-challenge:latest" in body
    assert "TC_CLAUDE_CLI=/usr/local/bin/claude" in body


def test_runner_reseeds_trust_for_its_own_cwd() -> None:
    # The base image's baked /home/trader/.claude.json trusts only "/app" --
    # this image's cwd is /app/repo, a different path, and without a re-seed
    # here the CLI treats it as untrusted and ignores .claude/settings.json.
    body = DOCKERFILE.read_text()
    assert '"/app/repo":{"hasTrustDialogAccepted":true}' in body


def test_runner_binds_loopback_only() -> None:
    assert '"--host", "127.0.0.1"' in DOCKERFILE.read_text()
