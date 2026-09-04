"""The token loop (spec §8, CLAUDE.md "Standing operating constraint — token
expiry"). A dead or expiring token is the one failure mode that takes the
whole engine blind at once (§4.5: no reconciliation, no §3.6 check, no
ability to close) -- and CLAUDE.md's "absence of action is never evidence
that nothing needed doing" makes silence about it the one thing this loop
must never do.

Three properties this module holds, deliberately mirroring tick.py:

* **`token_check` never raises.** `begin_auth()` reaches the network; a
  network failure here must degrade to "post without a URL", never take the
  scheduler down with it (ruling 6).
* **The daily post is deduped, the alert is not re-opened.** Chris does not
  need the same Discord message every five minutes for eight hours -- but he
  does need it again the next calendar day if the token is still dead. The
  `token_dead` alert (open_alerts) is a *standing* condition and stays open
  across days until he acks it or re-auths.
* **The word "healthy" never appears.** `fresh` is silent by design (no
  post, no alert) -- there is no affirmative "all is well" message for a
  human to over-trust between checks.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from tc.broker.token import TokenState, TokenStore, action_for
from tc.clock import ET
from tc.notify import Notifier
from tc.store.db import Store

log = logging.getLogger(__name__)

_URL_UNAVAILABLE = "(auth-url unavailable — see logs)"


class TokenReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: TokenState
    age_days: float | None
    days_until_dead: float | None
    action: str
    auth_url: str | None


def _try_begin_auth(token: TokenStore) -> str | None:
    try:
        return token.begin_auth()
    except Exception as e:
        # Network/schwab-py failure. token_check must not raise out of this --
        # a blind engine that also crashes its own "you are blind" report is
        # strictly worse than one that posts the bad news without a link.
        log.warning("begin_auth failed while reporting a %s token: %s", token.state(), e)
        return None


async def _already_posted_today(store: Store, kind: str, et_date: str) -> bool:
    # token_events.at is UTC (Store._now()), so its calendar date can differ
    # from the ET calendar date near midnight -- we cannot dedupe on `at`.
    # Instead the ET date is encoded into `detail` itself and matched as a
    # prefix; see ruling 3 in the task brief.
    rows = await store.fetchall(
        "SELECT 1 FROM token_events WHERE kind=? AND detail LIKE ? LIMIT 1",
        (kind, f"{et_date}%"),
    )
    return len(rows) > 0


async def token_check(
    token: TokenStore, store: Store, notifier: Notifier, now: datetime
) -> TokenReport:
    state = token.state()
    age = token.age_days()
    left = token.days_until_dead()
    action = action_for(state)
    et_date = now.astimezone(ET).date().isoformat()

    if state == "fresh":
        await store.record_token_event("checked", et_date)
        return TokenReport(
            state=state, age_days=age, days_until_dead=left, action=action, auth_url=None
        )

    # reauth_due, dead and absent all carry a re-auth URL and a once-per-day
    # post -- but dead/absent get their own dedupe key so a reauth_due post
    # earlier the same day never silently swallows a later dead-state alert.
    dedupe_kind = "auth_url_posted" if state == "reauth_due" else "blind_posted"
    already = await _already_posted_today(store, dedupe_kind, et_date)
    if already:
        return TokenReport(
            state=state, age_days=age, days_until_dead=left, action=action, auth_url=None
        )

    url = _try_begin_auth(token)
    if url is None:
        await store.record_token_event("auth_url_failed", et_date)
    url_text = url if url is not None else _URL_UNAVAILABLE

    if state == "reauth_due":
        assert left is not None  # reauth_due implies a readable token, hence a finite age
        text = (
            f"🔑 Schwab re-auth due — {left:.1f} days until dead. "
            f"Open on your phone (Tailscale on): {url_text}"
        )
    else:  # dead / absent
        dud_clause = f"{left:.1f} days until dead" if left is not None else "token absent"
        text = (
            f"⛔ Schwab token {state}: the engine is BLIND — no reconciliation, no watches, "
            f"resting stops only. {dud_clause}. Re-auth: {url_text}"
        )

    await notifier.post(text)
    if url is not None:
        await store.record_token_event(dedupe_kind, f"{et_date} {url}")

    if state in ("dead", "absent"):
        open_alerts = await store.open_alerts()
        if not any(a.kind == "token_dead" for a in open_alerts):
            await store.open_alert("token_dead", text)

    return TokenReport(state=state, age_days=age, days_until_dead=left, action=action, auth_url=url)
