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
