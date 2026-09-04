"""The engine's HTTP surface: `/health` (spec §7 layer 2's probe body),
`/oauth/callback` (the phone re-auth landing page), and `/api` (the read-only
dashboard feed).

`/health` always answers 200 -- the probe reads the JSON body, including when
`ok` is false, so a 5xx here would hide the very signal the probe exists to
read. Only a handler-level exception should ever produce something other than
200, and none of the paths tested here raise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D  # noqa: N817 -- brevity in a Decimal-heavy fixture table
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl

from tc.broker.models import AccountSnapshot
from tc.broker.token import NoAuthInProgress, TokenStore
from tc.config import TokenConfig
from tc.http.app import EngineState, build_app
from tc.loops.reconcile import BookView
from tc.loops.tick import TickResult
from tc.store.db import SessionStatusRow, Store, TickRow

CFG = TokenConfig(
    reauth_after_days=5, hard_expiry_days=7,
    callback_url=AnyHttpUrl("https://pi.example.ts.net/oauth/callback"),
)
DAY = 86400.0
NOW = datetime(2026, 9, 4, 17, 31, tzinfo=UTC)


def _token(tmp_path: Path, age_days: float | None) -> TokenStore:
    now = 1_000_000.0
    ts = TokenStore(tmp_path / "token.json", CFG, "key", "secret", clock=lambda: now)
    if age_days is not None:
        ts.write({"creation_timestamp": int(now - age_days * DAY), "token": {"a": "b"}})
    return ts


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        account_hash="H", read_at=NOW, liquidation_value=D("3781.06"),
        cash_available_for_trading=D("2393.57"), unsettled_cash=D("0"),
        cash_balance=D("2393.57"), cash_call=D("0"), is_closing_only_restricted=False,
        positions=[],
    )


def _view(naked: list[str]) -> BookView:
    return BookView(
        account=_account(), orders=[], resting_stops={}, naked=naked, partial=[],
        orphaned_stops=[], open_entries=[], restricted=False, read_at=NOW,
    )


def _tick_row(**overrides: Any) -> TickRow:
    base: dict[str, Any] = dict(
        at_et="2026-09-04 13:31", state="RTH", account_value=D("3781.06"),
        comp_capital=D("2881.06"), hwm=D("3800"), drawdown_pct=D("-0.50"), level="OK",
        positions=1, stops=1, orders=0, settled=D("2393.57"), unsettled=D("0"),
        reserve=D("2393.57"), flags="-", note="",
    )
    base.update(overrides)
    return TickRow(**base)


def _tick_result(naked: list[str]) -> TickResult:
    return TickResult(row=_tick_row(), trips=[], view=_view(naked))


def _state(tmp_path: Path, store: Store, **overrides: Any) -> EngineState:
    # A caller-supplied `token=` must win outright -- calling `_token` for the
    # default unconditionally would write over the same `tmp_path/token.json`
    # the override already wrote, clobbering it before `EngineState` is ever
    # built.
    token = overrides.pop("token", None) or _token(tmp_path, age_days=1.0)
    base: dict[str, Any] = dict(
        token=token,
        store=store,
        started_at=NOW,
        now=lambda: NOW,
        version="0.2.0",
        shadow=True,
    )
    base.update(overrides)
    return EngineState(**base)


async def _get(state: EngineState, path: str) -> httpx.Response:
    app = build_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


# --- /health -----------------------------------------------------------


async def test_health_shape_and_values(tmp_path: Path, store: Store) -> None:
    state = _state(
        tmp_path, store,
        last_broker_read_ok_at=NOW - timedelta(seconds=41),
        last_tick=_tick_result([]),
    )
    r = await _get(state, "/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "ok", "version", "shadow", "blind", "token_state", "token_days_until_dead",
        "last_broker_read_ok_at", "last_broker_read_age_s", "positions_without_stop",
        "pending_approval_age_s", "runner_ok", "db_ok", "in_flight_proposal", "action",
        "open_alerts", "consecutive_tick_failures",
    }
    assert body["ok"] is True
    assert body["version"] == "0.2.0"
    assert body["shadow"] is True
    assert body["blind"] is False
    assert body["token_state"] == "fresh"  # noqa: S105 -- a TokenState value, not a password
    assert body["token_days_until_dead"] == pytest.approx(6.0, abs=0.01)
    assert body["last_broker_read_age_s"] == 41
    assert body["positions_without_stop"] == 0
    assert body["pending_approval_age_s"] is None
    assert body["runner_ok"] is None
    assert body["db_ok"] is True
    assert body["open_alerts"] == 0
    assert body["consecutive_tick_failures"] == 0
    assert body["in_flight_proposal"] is False
    assert body["action"] == "none"


async def test_health_no_tick_yet_reports_none_naked(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store)
    r = await _get(state, "/health")
    body = r.json()
    assert body["positions_without_stop"] is None
    assert body["last_broker_read_ok_at"] is None
    assert body["last_broker_read_age_s"] is None


async def test_health_ok_false_when_blind(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store, blind=True)
    r = await _get(state, "/health")
    assert r.status_code == 200
    body = r.json()
    assert body["blind"] is True
    assert body["ok"] is False


async def test_health_naked_count_reflects_view(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store, last_tick=_tick_result(["AMH", "BMH"]))
    r = await _get(state, "/health")
    assert r.json()["positions_without_stop"] == 2


async def test_health_db_ok_false_when_store_closed(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store)
    await store.close()
    r = await _get(state, "/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db_ok"] is False
    assert body["ok"] is False


async def test_health_token_dead_action_mirrors_token_wording(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store, token=_token(tmp_path, age_days=8.0))
    r = await _get(state, "/health")
    body = r.json()
    assert body["token_state"] == "dead"  # noqa: S105 -- a TokenState value, not a password
    assert body["action"] == "DEAD — account is blind until re-auth"


async def test_health_counts_open_alerts(tmp_path: Path, store: Store) -> None:
    """An alert nobody has acked is a standing condition the probe must see:
    it is the only channel-independent evidence that something was reported."""
    await store.open_alert("token_dead", "the engine is BLIND")
    await store.open_alert("naked_position", "AMH has no stop")
    state = _state(tmp_path, store)
    assert (await _get(state, "/health")).json()["open_alerts"] == 2

    acked = await store.ack_alerts_of_kind("token_dead")
    assert acked == 1
    assert (await _get(state, "/health")).json()["open_alerts"] == 1


async def test_health_ok_false_after_two_consecutive_tick_failures(
    tmp_path: Path, store: Store
) -> None:
    """One failed sweep is a transient upstream; two in a row is a loop that
    is not working — and until now nothing about repeated job failure could
    make `ok` false, so the probe read a green light over a dead loop."""
    state = _state(tmp_path, store)
    state.consecutive_failures["tick"] = 1
    body = (await _get(state, "/health")).json()
    assert body["consecutive_tick_failures"] == 1 and body["ok"] is True

    state.consecutive_failures["tick"] = 2
    body = (await _get(state, "/health")).json()
    assert body["consecutive_tick_failures"] == 2 and body["ok"] is False


# --- /oauth/callback -----------------------------------------------------


async def test_oauth_callback_success(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, store, blind=True)
    seen: dict[str, str] = {}

    def fake_complete_auth(received_url: str) -> None:
        seen["url"] = received_url

    monkeypatch.setattr(state.token, "complete_auth", fake_complete_auth)
    r = await _get(state, "/oauth/callback?code=abc&state=S")
    assert r.status_code == 200
    assert r.text == "Token installed — you can close this tab."
    assert seen["url"].endswith("/oauth/callback?code=abc&state=S")
    # The route does NOT clear `blind` on its own: a token file on disk is
    # not a broker that answers. With no engine hook registered there is no
    # successful read to point at, so the engine stays blind.
    assert state.blind is True
    rows = await store.fetchall("SELECT kind, detail FROM token_events")
    assert [tuple(row) for row in rows] == [("installed", "callback")]


async def test_oauth_callback_awaits_the_engine_hook_and_acks_token_dead(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install is the engine's cue to re-open the broker. `blind` is
    whatever that hook's read proves — the route never decides it."""
    await store.open_alert("token_dead", "the engine is BLIND")
    state = _state(tmp_path, store, blind=True)
    monkeypatch.setattr(state.token, "complete_auth", lambda url: None)
    called: list[str] = []

    async def hook() -> None:
        called.append("hook")
        state.blind = False

    state.on_token_installed = hook
    r = await _get(state, "/oauth/callback?code=abc&state=S")

    assert r.status_code == 200
    assert called == ["hook"]
    assert state.blind is False
    assert [a.kind for a in await store.open_alerts()] == []


async def test_oauth_callback_stays_blind_when_the_hook_read_fails(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token that installs but does not authenticate (wrong app, revoked
    grant) must not turn /health green. `ok` follows the read, not the file."""
    state = _state(tmp_path, store, blind=True)
    monkeypatch.setattr(state.token, "complete_auth", lambda url: None)

    async def hook() -> None:
        state.blind = True  # the engine's read failed; still BLIND

    state.on_token_installed = hook
    assert (await _get(state, "/oauth/callback?code=abc&state=S")).status_code == 200
    assert state.blind is True
    assert (await _get(state, "/health")).json()["ok"] is False


@pytest.mark.parametrize(
    ("exc", "name"),
    [
        (OSError("disk"), "OSError"),
        (KeyError("callback_url"), "KeyError"),
        (httpx.ConnectError("schwab unreachable"), "ConnectError"),
    ],
)
async def test_oauth_callback_maps_io_failures_to_install_failed(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch, exc: Exception, name: str
) -> None:
    """A torn auth-context file, a missing key in it, and a network failure
    talking to Schwab are all "the install did not happen" — a 400 naming the
    class, not a 500 traceback that could carry the one-time code."""
    state = _state(tmp_path, store)

    def raise_it(received_url: str) -> None:
        raise exc

    monkeypatch.setattr(state.token, "complete_auth", raise_it)
    r = await _get(state, "/oauth/callback?code=abc&state=S")
    assert r.status_code == 400 and r.text == name
    rows = await store.fetchall("SELECT kind, detail FROM token_events")
    assert [tuple(row) for row in rows] == [("install_failed", name)]


async def test_oauth_callback_no_auth_in_progress(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, store, blind=True)

    def raise_no_auth(received_url: str) -> None:
        raise NoAuthInProgress("no context")

    monkeypatch.setattr(state.token, "complete_auth", raise_no_auth)
    r = await _get(state, "/oauth/callback?code=abc&state=S")
    assert r.status_code == 400
    assert r.text == "NoAuthInProgress"
    assert state.blind is True
    rows = await store.fetchall("SELECT kind, detail FROM token_events")
    assert [tuple(row) for row in rows] == [("install_failed", "NoAuthInProgress")]


async def test_oauth_callback_value_error(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, store)

    def raise_value_error(received_url: str) -> None:
        raise ValueError("state mismatch")

    monkeypatch.setattr(state.token, "complete_auth", raise_value_error)
    r = await _get(state, "/oauth/callback?code=abc&state=S")
    assert r.status_code == 400
    assert r.text == "ValueError"


async def test_oauth_callback_error_body_never_echoes_url_or_code(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, store)

    def raise_value_error(received_url: str) -> None:
        raise ValueError("secret-looking-code-xyz")

    monkeypatch.setattr(state.token, "complete_auth", raise_value_error)
    r = await _get(state, "/oauth/callback?code=secret-looking-code-xyz&state=S")
    assert "secret-looking-code-xyz" not in r.text


async def test_oauth_callback_rejects_post(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store)
    app = build_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/oauth/callback?code=abc&state=S")
    assert r.status_code in (404, 405)


# --- /api ------------------------------------------------------------


async def test_api_status_with_no_rows(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store)
    r = await _get(state, "/api/status")
    assert r.status_code == 200
    assert r.json() == {"session": None, "tick": None}


async def test_api_status_with_rows(tmp_path: Path, store: Store) -> None:
    await store.write_session_status(
        SessionStatusRow(
            date=NOW.date(), close_value=D("3758"), hwm=D("3800"), halt=D("3040"),
            drawdown_pct=D("-1.11"), level="OK", prior_hwm=D("3800"), ratcheted=False,
            intraday_high=None,
        )
    )
    state = _state(tmp_path, store, last_tick=_tick_result([]))
    r = await _get(state, "/api/status")
    body = r.json()
    assert body["session"]["hwm"] == "3800"
    assert isinstance(body["session"]["hwm"], str)
    assert body["tick"]["account_value"] == "3781.06"
    assert isinstance(body["tick"]["account_value"], str)


async def test_api_ticks_returns_seeded_row_with_decimal_strings(
    tmp_path: Path, store: Store
) -> None:
    await store.append_tick(_tick_row(at_et="2026-09-04 13:31"))
    await store.append_tick(_tick_row(at_et="2026-09-03 13:31"))
    state = _state(tmp_path, store)
    r = await _get(state, "/api/ticks?date=2026-09-04")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["at_et"] == "2026-09-04 13:31"
    assert row["account_value"] == "3781.06"
    assert isinstance(row["account_value"], str)


async def test_api_ticks_missing_date_is_400(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store)
    r = await _get(state, "/api/ticks")
    assert r.status_code == 400


async def test_api_ticks_malformed_date_is_400(tmp_path: Path, store: Store) -> None:
    state = _state(tmp_path, store)
    r = await _get(state, "/api/ticks?date=not-a-date")
    assert r.status_code == 400
    r2 = await _get(state, "/api/ticks?date=2026-13-40")
    assert r2.status_code == 400
