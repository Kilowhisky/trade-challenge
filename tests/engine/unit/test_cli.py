import json
import time
from pathlib import Path

import pytest

from tc import cli

REPO = Path(__file__).resolve().parents[3]

CONFIG = """
engine: {timezone: America/New_York, data_dir: "%s", http_bind: 127.0.0.1:8080, reserve_usd: "900.00"}
token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://pi.example.ts.net/oauth/callback}
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\nTC_DISCORD_WEBHOOK_URL=https://d.example/h\n"


def _cfg(tmp_path: Path) -> list[str]:
    (tmp_path / "config.yml").write_text(CONFIG % tmp_path)
    (tmp_path / ".env").write_text(ENV)
    return ["--config", str(tmp_path / "config.yml"), "--env", str(tmp_path / ".env")]


def test_check_consistency_on_real_repo(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["check-consistency", "--repo", str(REPO)])
    out = capsys.readouterr().out
    assert rc == 0 and out.strip().endswith("CONSISTENT")


def test_token_status_absent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main([*_cfg(tmp_path), "token-status"])
    out = capsys.readouterr().out
    assert rc == 3 and "state=absent" in out and "healthy" not in out.lower()


def test_auth_url_prints_url(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tc.broker import token as tok

    class Ctx:
        callback_url = "https://pi.example.ts.net/oauth/callback"
        authorization_url = "https://api.schwabapi.com/v1/oauth/authorize?state=S"
        state = "S"

    monkeypatch.setattr(tok.schwab_auth, "get_auth_context", lambda api_key, callback_url: Ctx())
    rc = cli.main([*_cfg(tmp_path), "auth-url"])
    out = capsys.readouterr().out
    assert rc == 0 and out.strip() == Ctx.authorization_url
    assert (tmp_path / "auth-context.json").exists()


def test_unknown_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["bogus"])
    assert e.value.code == 2


def test_missing_config_is_one_line_exit_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["--config", str(tmp_path / "nope.yml"), "token-status"])
    err = capsys.readouterr().err
    assert rc == 4
    assert err.startswith("tc: ")
    assert "Traceback" not in err


def test_missing_secret_is_one_line_exit_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.yml").write_text(CONFIG % tmp_path)
    (tmp_path / ".env").write_text("TC_SCHWAB_APP_KEY=k\nTC_DISCORD_WEBHOOK_URL=https://d.example/h\n")
    rc = cli.main([
        "--config", str(tmp_path / "config.yml"), "--env", str(tmp_path / ".env"), "token-status",
    ])
    err = capsys.readouterr().err
    assert rc == 4
    assert "schwab_app_secret" in err
    assert "k" not in err


def test_auth_complete_without_context_exit_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main([*_cfg(tmp_path), "auth-complete", "https://x/cb?code=abc"])
    err = capsys.readouterr().err
    assert rc == 4
    assert "auth-context" in err or "begin_auth" in err


def test_malformed_yaml_is_one_line_exit_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A truncated config.yml used to reach the operator as a YAMLError traceback."""
    (tmp_path / "config.yml").write_text("engine: [\n")
    rc = cli.main(["--config", str(tmp_path / "config.yml"), "token-status"])
    err = capsys.readouterr().err
    assert rc == 4
    assert err.startswith("tc: ")
    assert "Traceback" not in err
    assert err.count("\n") == 1


def test_non_mapping_yaml_is_one_line_exit_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.yml").write_text("- a\n")
    rc = cli.main(["--config", str(tmp_path / "config.yml"), "token-status"])
    err = capsys.readouterr().err
    assert rc == 4
    assert err.startswith("tc: ")
    assert "must be a mapping" in err
    assert "Traceback" not in err


def test_stray_tc_env_var_does_not_crash_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SettingsError is not a ValidationError; it used to escape as a traceback."""
    monkeypatch.setenv("TC_TOKEN", "abc")
    rc = cli.main([*_cfg(tmp_path), "token-status"])
    out, err = capsys.readouterr()
    assert rc == 3 and "state=absent" in out and err == ""


def _write_token(tmp_path: Path, age_days: float) -> None:
    """Age is relative to real time: _token_line reads TokenStore's default clock."""
    (tmp_path / "token.json").write_text(
        json.dumps(
            {
                "creation_timestamp": int(time.time() - age_days * 86400),
                "token": {"access_token": "a", "refresh_token": "r"},
            }
        )
    )


@pytest.mark.parametrize(
    ("age_days", "rc", "state"),
    [(1.0, 0, "fresh"), (5.5, 2, "reauth_due"), (8.0, 3, "dead")],
)
def test_token_status_branches(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    age_days: float,
    rc: int,
    state: str,
) -> None:
    args = _cfg(tmp_path)
    _write_token(tmp_path, age_days)
    assert cli.main([*args, "token-status"]) == rc
    assert f"state={state}" in capsys.readouterr().out


def test_default_env_is_resolved_next_to_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`tc --config /etc/tc/config.yml` from any CWD must find /etc/tc/.env."""
    cfgdir = tmp_path / "etc"
    cfgdir.mkdir()
    (cfgdir / "config.yml").write_text(CONFIG % tmp_path)
    (cfgdir / ".env").write_text(ENV)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc = cli.main(["--config", str(cfgdir / "config.yml"), "token-status"])
    assert rc == 3 and "state=absent" in capsys.readouterr().out
