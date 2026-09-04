"""Expectations (spec §7 layer 3): declarative anomaly checks over the store.
Each check is a query, not a model call; every result is logged to
`expectations_log`, and the weekly digest always posts a first line even
when nothing breached."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl, ValidationError

from tc.broker.token import TokenStore
from tc.config import Expectation, TokenConfig, load_settings
from tc.loops.expectations import ExpectationResult, digest, run_expectations
from tc.store.db import SessionStatusRow, Store, TickRow

TOKEN_CFG = TokenConfig(
    reauth_after_days=5, hard_expiry_days=7,
    callback_url=AnyHttpUrl("https://pi.example.ts.net/oauth/callback"),
)
DAY = 86400.0
TODAY = date(2026, 9, 4)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


def _token(tmp_path: Path, age_days: float | None, epoch_now: float) -> TokenStore:
    t = TokenStore(tmp_path / "token.json", TOKEN_CFG, "key", "secret", clock=lambda: epoch_now)
    if age_days is not None:
        t.write({"creation_timestamp": int(epoch_now - age_days * DAY), "token": {"access_token": "x"}})
    return t


def _tick(at_et: str, state: str = "RTH") -> TickRow:
    return TickRow(
        at_et=at_et, state=state, account_value="3800.00", comp_capital="2900.00", hwm="3800.00",
        drawdown_pct="0", level="OK", positions=0, stops=0, orders=0,
        settled="1", unsettled="0", reserve="900.00", flags="-", note="-",
    )


async def _seed_ticks(store: Store, day: str, n: int, state: str = "RTH") -> None:
    for i in range(n):
        await store.append_tick(_tick(f"{day} {9 + i:02d}:32", state=state))


# --- ticks_per_session_min ------------------------------------------------

async def test_ticks_per_session_min_ok(store: Store, tmp_path: Path) -> None:
    await _seed_ticks(store, "2026-09-04", 20)
    spec = Expectation(name="tpm", check="ticks_per_session_min", arg=20)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is True
    assert "20" in results[0].detail


async def test_ticks_per_session_min_breach(store: Store, tmp_path: Path) -> None:
    await _seed_ticks(store, "2026-09-04", 5)
    spec = Expectation(name="tpm", check="ticks_per_session_min", arg=20)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is False
    assert "5" in results[0].detail


async def test_ticks_per_session_min_uses_latest_session_at_or_before_today(
    store: Store, tmp_path: Path
) -> None:
    # A thin session two days ago should not be masked by a fat session that
    # is still in the future relative to `today`.
    await _seed_ticks(store, "2026-09-02", 3)
    await _seed_ticks(store, "2026-09-10", 40)
    spec = Expectation(name="tpm", check="ticks_per_session_min", arg=20)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is False
    assert "2026-09-02" in results[0].detail


async def test_ticks_per_session_min_no_session_is_a_breach(store: Store, tmp_path: Path) -> None:
    spec = Expectation(name="tpm", check="ticks_per_session_min", arg=20)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is False
    assert "no session" in results[0].detail.lower()


# --- session_status_present -----------------------------------------------

async def test_session_status_present_ok(store: Store, tmp_path: Path) -> None:
    await _seed_ticks(store, "2026-09-04", 1)
    await store.write_session_status(
        SessionStatusRow(
            date=TODAY, close_value="3800.00", hwm="3800.00", halt="3040.00", drawdown_pct="0",
            level="OK", prior_hwm="3800.00", ratcheted=False, intraday_high=None,
        )
    )
    spec = Expectation(name="ssp", check="session_status_present")
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is True


async def test_session_status_present_breach(store: Store, tmp_path: Path) -> None:
    await _seed_ticks(store, "2026-09-04", 1)
    spec = Expectation(name="ssp", check="session_status_present")
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is False
    assert "2026-09-04" in results[0].detail


# --- job_verdict_not --------------------------------------------------------

async def test_job_verdict_not_breach_within_window(store: Store, tmp_path: Path) -> None:
    started = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    await store.record_job_run("tick", started, started + timedelta(minutes=1), "content_failed", {})
    spec = Expectation(name="jvn", check="job_verdict_not", arg="content_failed", window_sessions=5)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is False
    assert "content_failed" in results[0].detail


async def test_job_verdict_not_ok_when_outside_window(store: Store, tmp_path: Path) -> None:
    started = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)  # well before the 5-day window
    await store.record_job_run("tick", started, started + timedelta(minutes=1), "content_failed", {})
    spec = Expectation(name="jvn", check="job_verdict_not", arg="content_failed", window_sessions=5)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is True


async def test_job_verdict_not_ok_when_verdict_is_done(store: Store, tmp_path: Path) -> None:
    started = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    await store.record_job_run("tick", started, started + timedelta(minutes=1), "done", {})
    spec = Expectation(name="jvn", check="job_verdict_not", arg="content_failed", window_sessions=5)
    results = await run_expectations(store, _token(tmp_path, 1.0, 0.0), [spec], TODAY)
    assert results[0].ok is True


# --- token_days_until_dead_min ----------------------------------------------

async def test_token_days_until_dead_min_ok(store: Store, tmp_path: Path) -> None:
    token = _token(tmp_path, 1.0, 0.0)  # 6 days left of a 7-day life
    spec = Expectation(name="tdd", check="token_days_until_dead_min", arg=2)
    results = await run_expectations(store, token, [spec], TODAY)
    assert results[0].ok is True


async def test_token_days_until_dead_min_breach(store: Store, tmp_path: Path) -> None:
    token = _token(tmp_path, 6.0, 0.0)  # 1 day left
    spec = Expectation(name="tdd", check="token_days_until_dead_min", arg=2)
    results = await run_expectations(store, token, [spec], TODAY)
    assert results[0].ok is False


async def test_token_days_until_dead_min_absent_token_is_a_breach(
    store: Store, tmp_path: Path
) -> None:
    token = _token(tmp_path, None, 0.0)
    spec = Expectation(name="tdd", check="token_days_until_dead_min", arg=2)
    results = await run_expectations(store, token, [spec], TODAY)
    assert results[0].ok is False
    assert results[0].detail == "token absent"


# --- logging + digest -------------------------------------------------------

async def test_every_result_is_logged(store: Store, tmp_path: Path) -> None:
    await _seed_ticks(store, "2026-09-04", 20)
    specs = [
        Expectation(name="tpm", check="ticks_per_session_min", arg=20),
        Expectation(name="ssp", check="session_status_present"),
    ]
    await run_expectations(store, _token(tmp_path, 1.0, 0.0), specs, TODAY)
    rows = await store.fetchall("SELECT name, ok, detail FROM expectations_log ORDER BY id")
    assert [r["name"] for r in rows] == ["tpm", "ssp"]
    assert rows[0]["ok"] == 1
    assert rows[1]["ok"] == 0


def test_digest_nonzero_breach() -> None:
    results = [
        ExpectationResult(name="a", ok=True, detail="fine"),
        ExpectationResult(name="b", ok=False, detail="broke"),
    ]
    text = digest(results)
    assert text.startswith("expectations: 2 checks, 1 breached")
    assert "b: broke" in text


def test_digest_zero_breach_still_has_first_line() -> None:
    results = [ExpectationResult(name="a", ok=True, detail="fine")]
    text = digest(results)
    assert text == "expectations: 1 checks, 0 breached"
    assert text  # never empty


def test_digest_never_empty_with_no_results() -> None:
    text = digest([])
    assert text
    assert text.startswith("expectations: 0 checks, 0 breached")


# --- config ------------------------------------------------------------

def test_load_settings_parses_expectations_list(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "expectations:\n"
        "  - name: ticks_per_session_min\n"
        "    check: ticks_per_session_min\n"
        "    arg: 20\n"
        "  - name: session_status_present\n"
        "    check: session_status_present\n"
        "  - name: job_verdict_not\n"
        "    check: job_verdict_not\n"
        "    arg: content_failed\n"
        "    window_sessions: 5\n"
        "  - name: token_days_until_dead_min\n"
        "    check: token_days_until_dead_min\n"
        "    arg: 2\n"
    )
    env = tmp_path / ".env"
    env.write_text(
        "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\nTC_DISCORD_WEBHOOK_URL=https://discord.example/hook\n"
    )
    s = load_settings(cfg, env)
    assert [e.name for e in s.expectations] == [
        "ticks_per_session_min", "session_status_present", "job_verdict_not",
        "token_days_until_dead_min",
    ]
    jvn = next(e for e in s.expectations if e.name == "job_verdict_not")
    assert jvn.arg == "content_failed"
    assert jvn.window_sessions == 5


def test_expectations_defaults_to_empty_list(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
    )
    env = tmp_path / ".env"
    env.write_text(
        "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\nTC_DISCORD_WEBHOOK_URL=https://discord.example/hook\n"
    )
    s = load_settings(cfg, env)
    assert s.expectations == []


def test_unknown_check_literal_rejected() -> None:
    with pytest.raises(ValidationError):
        Expectation(name="bad", check="not_a_real_check")
