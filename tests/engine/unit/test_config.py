from pathlib import Path

import pytest
from pydantic import ValidationError

from tc.config import Settings, load_settings

CONFIG = """
engine:
  timezone: America/New_York
  data_dir: /opt/tc/data
  repo_dir: /opt/tc/repo
  research_dir: /opt/tc/data/research
  http_bind: 127.0.0.1:8080
  reserve_usd: "900.00"
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""

ENV = """
TC_SCHWAB_APP_KEY=k
TC_SCHWAB_APP_SECRET=s
TC_DISCORD_WEBHOOK_URL=https://discord.example/hook
"""


def _write(tmp_path: Path, cfg: str = CONFIG, env: str = ENV) -> tuple[Path, Path]:
    c = tmp_path / "config.yml"
    e = tmp_path / ".env"
    c.write_text(cfg)
    e.write_text(env)
    return c, e


def test_loads_yaml_and_env(tmp_path: Path) -> None:
    c, e = _write(tmp_path)
    s = load_settings(c, e)
    assert isinstance(s, Settings)
    assert s.engine.data_dir == Path("/opt/tc/data")
    assert s.token.reauth_after_days == 5
    assert s.schwab_app_key == "k"
    assert str(s.discord_webhook_url).startswith("https://discord.example")


def test_missing_secret_fails_fast(tmp_path: Path) -> None:
    c, e = _write(tmp_path, env="TC_SCHWAB_APP_KEY=k\n")
    with pytest.raises(ValidationError):
        load_settings(c, e)


def test_unknown_yaml_key_rejected(tmp_path: Path) -> None:
    # Zero indent: a top-level key, so this exercises FileConfig's own forbid.
    c, e = _write(tmp_path, cfg=CONFIG + "bogus: 1\n")
    with pytest.raises(ValidationError):
        load_settings(c, e)


def test_unknown_nested_yaml_key_rejected(tmp_path: Path) -> None:
    # Two-space indent: a key inside token:, so this exercises TokenConfig's.
    c, e = _write(tmp_path, cfg=CONFIG + "  bogus: 1\n")
    with pytest.raises(ValidationError):
        load_settings(c, e)


def test_unrelated_tc_env_var_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC_TOKEN / TC_ENGINE in the environment must not reach config parsing.

    pydantic-settings parses every DECLARED field from the environment even
    when an init kwarg overrides it, so declaring engine/token on a
    BaseSettings under env_prefix="TC_" made any TC_TOKEN=... in the operator's
    shell raise SettingsError (not ValidationError) before the yaml was ever
    consulted. Secrets and file config are separate models for this reason.
    """
    monkeypatch.setenv("TC_TOKEN", "abc")
    monkeypatch.setenv("TC_ENGINE", "x")
    c, e = _write(tmp_path)
    s = load_settings(c, e)
    assert s.token.reauth_after_days == 5
    assert s.engine.data_dir == Path("/opt/tc/data")
    assert s.schwab_app_key == "k"


def test_reauth_must_precede_hard_expiry(tmp_path: Path) -> None:
    c, e = _write(tmp_path, cfg=CONFIG.replace("reauth_after_days: 5", "reauth_after_days: 8"))
    with pytest.raises(ValidationError):
        load_settings(c, e)


def test_schedule_and_shadow_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
        "schedule: {tick: 'every 15m 09:32-15:47 weekdays', session_close: 'at 16:04 weekdays'}\n"
        "shadow: {enabled: true}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    monkeypatch.setenv("TC_DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setenv("TC_DISCORD_SHADOW_WEBHOOK_URL", "https://discord.test/shadow")
    from tc.config import load_settings
    s = load_settings(cfg)
    assert s.schedule["tick"] == "every 15m 09:32-15:47 weekdays"
    assert s.shadow.enabled is True
    assert s.engine.repo_dir == Path("/r")
    assert str(s.discord_shadow_webhook_url) == "https://discord.test/shadow"


def test_engine_loads_without_any_discord_webhook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No Discord configured is a valid (quiet) engine, not a startup failure."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
    )
    for k in ("TC_DISCORD_WEBHOOK_URL", "TC_DISCORD_SHADOW_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    from tc.config import load_settings
    s = load_settings(cfg, env_file=tmp_path / "no-such-env")
    assert s.discord_webhook_url is None and s.discord_shadow_webhook_url is None


def test_runner_and_mcp_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
        "schedule: {scout: 'at 07:12 weekdays'}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    monkeypatch.setenv("TC_RUNNER_TOKEN", "runner-secret")
    monkeypatch.setenv("TC_MCP_RESEARCH_TOKEN", "research-secret")
    monkeypatch.setenv("TC_MCP_DECIDE_TOKEN", "decide-secret")
    from tc.config import load_settings

    s = load_settings(cfg)
    assert s.engine.research_dir == Path("/d/research")
    assert str(s.runner.url).rstrip("/") == "http://127.0.0.1:8090"
    assert s.runner.slack_s == 120.0
    assert s.runner_token == "runner-secret"  # noqa: S105 -- a fixture bearer, not a real secret
    assert s.mcp_tokens() == {"research-secret": "research", "decide-secret": "decide"}


def test_mcp_tokens_empty_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    for k in ("TC_RUNNER_TOKEN", "TC_MCP_RESEARCH_TOKEN", "TC_MCP_DECIDE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from tc.config import load_settings

    s = load_settings(cfg)
    assert s.mcp_tokens() == {}
    assert s.runner_token is None


def test_mcp_tokens_rejects_equal_research_and_decide_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy-paste in .env that gives both roles the same bearer must not
    silently collapse the map onto whichever role sorts last -- that would
    hand the research runner the decide role with no warning."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    monkeypatch.setenv("TC_MCP_RESEARCH_TOKEN", "same-secret")
    monkeypatch.setenv("TC_MCP_DECIDE_TOKEN", "same-secret")
    from tc.config import load_settings

    with pytest.raises(ValueError, match="must differ"):
        load_settings(cfg)


def test_mcp_tokens_one_set_is_one_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    monkeypatch.setenv("TC_MCP_RESEARCH_TOKEN", "research-secret")
    monkeypatch.delenv("TC_MCP_DECIDE_TOKEN", raising=False)
    from tc.config import load_settings

    s = load_settings(cfg)
    assert s.mcp_tokens() == {"research-secret": "research"}
