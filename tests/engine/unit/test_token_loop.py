"""The token loop (spec §8): a dead token is a state the engine reports
daily with the URL that fixes it, never a crash loop. See CLAUDE.md
"Operating limitation #1" -- the machinery being down is silent from the
inside, so this loop is what tells Chris."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl

from tc.broker.token import TokenStore
from tc.config import TokenConfig
from tc.loops.token import token_check
from tc.notify import Notifier
from tc.store.db import Store

CFG = TokenConfig(
    reauth_after_days=5, hard_expiry_days=7,
    callback_url=AnyHttpUrl("https://pi.example.ts.net/oauth/callback"),
)
DAY = 86400.0
NOW = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)
NEXT_DAY = NOW + timedelta(days=1)
AUTH_URL = "https://auth.test/x"


def _wrapped(created: float) -> dict[str, Any]:
    return {"creation_timestamp": int(created), "token": {"access_token": "x"}}


def _token(tmp_path: Path, age_days: float, epoch_now: float) -> TokenStore:
    s = TokenStore(tmp_path / "token.json", CFG, "key", "secret", clock=lambda: epoch_now)
    s.write(_wrapped(epoch_now - age_days * DAY))
    return s


def _absent_token(tmp_path: Path, epoch_now: float) -> TokenStore:
    return TokenStore(tmp_path / "token.json", CFG, "key", "secret", clock=lambda: epoch_now)


class _Posts:
    """Captures every Discord post body's `content` field, in order."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def notifier(self) -> Notifier:
        def handler(req: httpx.Request) -> httpx.Response:
            self.texts.append(json.loads(req.read().decode())["content"])
            return httpx.Response(204)

        return Notifier("https://discord.test/hook", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def _events(store: Store, kind: str) -> list[str]:
    rows = await store.fetchall("SELECT detail FROM token_events WHERE kind=? ORDER BY id", (kind,))
    return [r["detail"] for r in rows]


async def test_fresh_records_checked_event_and_posts_nothing(store: Store, tmp_path: Path) -> None:
    token = _token(tmp_path, 1.0, NOW.timestamp())
    posts = _Posts()
    report = await token_check(token, store, posts.notifier(), NOW)

    assert report.state == "fresh"
    assert report.action == "none"
    assert report.auth_url is None
    assert round(report.age_days or 0, 2) == 1.0
    assert round(report.days_until_dead or 0, 2) == 6.0
    assert posts.texts == []
    assert await _events(store, "checked")


async def test_reauth_due_posts_url_dedupes_same_day_then_posts_next_day(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token(tmp_path, 5.5, NOW.timestamp())
    monkeypatch.setattr(TokenStore, "begin_auth", lambda self: AUTH_URL)
    posts = _Posts()

    first = await token_check(token, store, posts.notifier(), NOW)
    assert first.state == "reauth_due"
    assert first.auth_url == AUTH_URL
    assert len(posts.texts) == 1
    text = posts.texts[0]
    assert "🔑" in text
    assert AUTH_URL in text
    assert "1.5" in text  # days_until_dead
    assert "healthy" not in text.lower()

    # Same calendar day (ET): dedupe suppresses a second post.
    second = await token_check(token, store, posts.notifier(), NOW + timedelta(hours=2))
    assert len(posts.texts) == 1
    assert second.auth_url is None

    # A new ET calendar day: posts again.
    third = await token_check(token, store, posts.notifier(), NEXT_DAY)
    assert len(posts.texts) == 2
    assert third.auth_url == AUTH_URL
    assert "healthy" not in posts.texts[1].lower()


async def test_dead_state_posts_and_opens_alert_once(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _token(tmp_path, 8.0, NOW.timestamp())
    monkeypatch.setattr(TokenStore, "begin_auth", lambda self: AUTH_URL)
    posts = _Posts()

    report = await token_check(token, store, posts.notifier(), NOW)
    assert report.state == "dead"
    assert report.action == "DEAD — account is blind until re-auth"
    assert len(posts.texts) == 1
    text = posts.texts[0]
    assert "⛔" in text
    assert "BLIND" in text
    assert AUTH_URL in text
    assert "healthy" not in text.lower()

    alerts = await store.open_alerts()
    dead_alerts = [a for a in alerts if a.kind == "token_dead"]
    assert len(dead_alerts) == 1

    # Same day, second check: no new post, no second alert (the first is
    # still open and unacked).
    await token_check(token, store, posts.notifier(), NOW + timedelta(hours=1))
    assert len(posts.texts) == 1
    alerts_again = await store.open_alerts()
    assert len([a for a in alerts_again if a.kind == "token_dead"]) == 1


async def test_dead_alert_reopens_same_day_after_ack_even_without_a_new_post(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token_dead alert is a standing condition, not a once-a-day event:
    if Chris acks it mid-day while the token is still dead, the very next
    check must reopen it -- it must not wait for tomorrow's Discord post,
    or the account sits alert-less and blind for the rest of the day."""
    token = _token(tmp_path, 8.0, NOW.timestamp())
    monkeypatch.setattr(TokenStore, "begin_auth", lambda self: AUTH_URL)
    posts = _Posts()

    await token_check(token, store, posts.notifier(), NOW)
    first_open = [a for a in await store.open_alerts() if a.kind == "token_dead"]
    assert len(first_open) == 1
    await store.ack_alert(first_open[0].id)
    assert not [a for a in await store.open_alerts() if a.kind == "token_dead"]

    # Same ET calendar day: no new Discord post (still deduped)...
    await token_check(token, store, posts.notifier(), NOW + timedelta(hours=1))
    assert len(posts.texts) == 1

    # ...but a fresh alert, because the condition is still live.
    second_open = [a for a in await store.open_alerts() if a.kind == "token_dead"]
    assert len(second_open) == 1
    assert second_open[0].id != first_open[0].id


async def test_absent_state_writes_token_absent_not_a_number(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _absent_token(tmp_path, NOW.timestamp())
    monkeypatch.setattr(TokenStore, "begin_auth", lambda self: AUTH_URL)
    posts = _Posts()

    report = await token_check(token, store, posts.notifier(), NOW)
    assert report.state == "absent"
    assert report.age_days is None
    assert report.days_until_dead is None
    assert len(posts.texts) == 1
    text = posts.texts[0]
    assert "token absent" in text
    assert "None" not in text
    assert "healthy" not in text.lower()

    alerts = await store.open_alerts()
    assert any(a.kind == "token_dead" for a in alerts)


async def test_begin_auth_failure_posts_without_url_and_records_auth_url_failed(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: TokenStore) -> str:
        raise RuntimeError("network is down")

    token = _token(tmp_path, 5.5, NOW.timestamp())
    monkeypatch.setattr(TokenStore, "begin_auth", _boom)
    posts = _Posts()

    report = await token_check(token, store, posts.notifier(), NOW)  # must not raise
    assert report.state == "reauth_due"
    assert report.auth_url is None
    assert len(posts.texts) == 1
    assert "healthy" not in posts.texts[0].lower()
    assert await _events(store, "auth_url_failed")


async def test_a_fresh_token_acks_the_standing_dead_alert(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`token_dead` is opened on every dead check and nothing ever closed it:
    a re-auth left the alert standing forever, `/health` kept counting it, and
    an alert that can only open is one a human learns to scroll past."""
    monkeypatch.setattr(TokenStore, "begin_auth", lambda self: AUTH_URL)
    dead = _token(tmp_path, 8.0, NOW.timestamp())
    await token_check(dead, store, _Posts().notifier(), NOW)
    assert [a.kind for a in await store.open_alerts()] == ["token_dead"]

    fresh = _token(tmp_path, 0.0, NOW.timestamp())  # the phone re-auth landed
    report = await token_check(fresh, store, _Posts().notifier(), NEXT_DAY)

    assert report.state == "fresh"
    assert await store.open_alerts() == []


async def test_the_dedupe_detail_never_carries_the_authorization_url(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authorization URL carries the app key as a query parameter, and
    `token_events` is read by /api, dumped into support threads and copied
    into handoffs. The dedupe only ever matches on the `{et_date}%` prefix, so
    storing the URL bought nothing and leaked a credential."""
    url = "https://api.schwabapi.com/v1/oauth/authorize?client_id=SECRETAPPKEY&state=S"
    monkeypatch.setattr(TokenStore, "begin_auth", lambda self: url)
    token = _token(tmp_path, 5.5, NOW.timestamp())
    posts = _Posts()

    await token_check(token, store, posts.notifier(), NOW)

    details = await _events(store, "auth_url_posted")
    assert details == ["2026-09-02 reauth_due"]
    assert "SECRETAPPKEY" not in " ".join(details)
    # ...and the URL still reaches the human, which is the point of the post.
    assert url in posts.texts[0]

    # The dedupe still works off that detail: same ET day, no second post.
    await token_check(token, store, posts.notifier(), NOW + timedelta(hours=2))
    assert len(posts.texts) == 1
