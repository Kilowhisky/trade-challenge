from pathlib import Path

import pytest
from pydantic import ValidationError

from tc.config import Settings, load_settings

CONFIG = """
engine:
  timezone: America/New_York
  data_dir: /tmp/tc-test-data
  http_bind: 127.0.0.1:8080
  reserve_usd: "900.00"
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
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
    assert s.engine.data_dir == Path("/tmp/tc-test-data")
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
    assert s.engine.data_dir == Path("/tmp/tc-test-data")
    assert s.schwab_app_key == "k"


def test_reauth_must_precede_hard_expiry(tmp_path: Path) -> None:
    c, e = _write(tmp_path, cfg=CONFIG.replace("reauth_after_days: 5", "reauth_after_days: 8"))
    with pytest.raises(ValidationError):
        load_settings(c, e)
