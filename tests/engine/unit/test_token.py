import json
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import AnyHttpUrl

from tc.broker import token as tok
from tc.config import TokenConfig

CFG = TokenConfig(reauth_after_days=5, hard_expiry_days=7,
                  callback_url=AnyHttpUrl("https://pi.example.ts.net/oauth/callback"))
DAY = 86400.0


def _store(tmp_path: Path, now: float) -> tok.TokenStore:
    return tok.TokenStore(tmp_path / "token.json", CFG, "key", "secret", clock=lambda: now)


def _wrapped(created: float) -> dict[str, Any]:
    return {"creation_timestamp": int(created), "token": {"access_token": "a", "refresh_token": "r"}}


def test_absent_state(tmp_path: Path) -> None:
    s = _store(tmp_path, 1_000_000.0)
    assert s.read() is None and s.state() == "absent" and s.days_until_dead() is None


def test_states_by_age(tmp_path: Path) -> None:
    now = 1_000_000.0
    s = _store(tmp_path, now)
    s.write(_wrapped(now - 1 * DAY))
    assert s.state() == "fresh" and round(s.days_until_dead() or 0, 2) == 6.0
    s.write(_wrapped(now - 5 * DAY))
    assert s.state() == "reauth_due"
    s.write(_wrapped(now - 7 * DAY))
    dud = s.days_until_dead()
    assert s.state() == "dead" and dud is not None and dud <= 0


def test_write_is_atomic_and_private(tmp_path: Path) -> None:
    s = _store(tmp_path, 1.0)
    s.write(_wrapped(1.0))
    p = tmp_path / "token.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(p.read_text())["creation_timestamp"] == 1


def test_corrupt_file_reads_as_absent_and_is_quarantined(tmp_path: Path) -> None:
    p = tmp_path / "token.json"
    p.write_text("{not json")
    s = _store(tmp_path, 1.0)
    assert s.read() is None and s.state() == "absent"
    assert (tmp_path / "token.json.corrupt").exists()


def test_begin_auth_persists_context_and_returns_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Ctx:
        callback_url = str(CFG.callback_url)
        authorization_url = "https://api.schwabapi.com/v1/oauth/authorize?x=1&state=S"
        state = "S"

    monkeypatch.setattr(tok.schwab_auth, "get_auth_context", lambda api_key, callback_url: Ctx())
    s = _store(tmp_path, 1.0)
    url = s.begin_auth()
    assert url.startswith("https://api.schwabapi.com")
    ctx = json.loads((tmp_path / "auth-context.json").read_text())
    assert ctx == {"callback_url": str(CFG.callback_url), "state": "S"}


def test_complete_auth_writes_token_via_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "auth-context.json").write_text(json.dumps({"callback_url": "https://x/cb", "state": "S"}))
    seen: dict[str, Any] = {}

    def fake_client_from_received_url(api_key: str, app_secret: str, auth_context: Any, received_url: str,
                                      token_write_func: Any, asyncio: bool = False, **kw: Any) -> object:
        seen["state"] = auth_context.state
        seen["url"] = received_url
        token_write_func({"creation_timestamp": 42, "token": {"access_token": "new"}})
        return object()

    monkeypatch.setattr(tok.schwab_auth, "client_from_received_url", fake_client_from_received_url)
    s = _store(tmp_path, 1.0)
    s.complete_auth("https://x/cb?code=abc&state=S")
    assert seen == {"state": "S", "url": "https://x/cb?code=abc&state=S"}
    assert (s.read() or {})["creation_timestamp"] == 42
    assert not (tmp_path / "auth-context.json").exists()


def test_complete_auth_without_context_raises(tmp_path: Path) -> None:
    s = _store(tmp_path, 1.0)
    with pytest.raises(tok.NoAuthInProgress):
        s.complete_auth("https://x/cb?code=abc")
