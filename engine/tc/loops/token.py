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
* **The daily post is deduped; the `token_dead` alert is checked on every
  call regardless.** Chris does not need the same Discord message every five
  minutes for eight hours -- but the alert (`open_alerts`) tracks a
  *standing* condition, not a daily event: if he acks it mid-day while the
  token is still dead, the very next check reopens it rather than waiting
  for tomorrow's post. Coupling the alert to the post dedupe would leave the
  account un-alerted and blind for the rest of the day the moment he acks.
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


def _dud_clause(left: float | None, *, fallback: str) -> str:
    # `left` comes from the same `status()` snapshot as `state`, so a
    # reauth_due/dead call always carries a finite age in practice -- but
    # this stays a fallback string, never an assert, because "never raises"
    # must hold even if that invariant is ever wrong.
    return f"{left:.1f} days until dead" if left is not None else fallback


async def token_check(
    token: TokenStore, store: Store, notifier: Notifier, now: datetime
) -> TokenReport:
    # One read, one consistent snapshot -- state/age/left can never disagree
    # with each other the way three independent state()/age_days()/
    # days_until_dead() calls could if the token file changed mid-check
    # (a re-auth completing concurrently, say).
    state, age, left = token.status()
    action = action_for(state)
    et_date = now.astimezone(ET).date().isoformat()

    if state == "fresh":
        await store.record_token_event("checked", et_date)
        # A working token answers the standing `token_dead` alert. Nothing
        # else closes it, and an alert that can only ever open is one a human
        # learns to scroll past — which is the alert we most need read.
        await store.ack_alerts_of_kind("token_dead")
        return TokenReport(
            state=state, age_days=age, days_until_dead=left, action=action, auth_url=None
        )

    # The token_dead alert is a *standing* condition, not a once-a-day event:
    # if Chris acks it mid-day while the token is still dead/absent, the very
    # next check must reopen it rather than silently waiting for tomorrow's
    # Discord post. So this runs on every dead/absent call, independent of
    # the post dedupe below.
    if state in ("dead", "absent"):
        open_alerts = await store.open_alerts()
        if not any(a.kind == "token_dead" for a in open_alerts):
            msg = (
                f"Schwab token {state}: the engine is BLIND. "
                f"{_dud_clause(left, fallback='token absent')}. Run `tc auth-url`."
            )
            await store.open_alert("token_dead", msg)

    # reauth_due, dead and absent all carry a re-auth URL and a once-per-day
    # Discord post -- but dead/absent get their own dedupe key so a
    # reauth_due post earlier the same day never silently swallows a later
    # dead-state post.
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
        left_str = _dud_clause(left, fallback="n/a")
        text = (
            f"🔑 Schwab re-auth due — {left_str}. "
            f"Open on your phone (Tailscale on): {url_text}"
        )
    else:  # dead / absent
        dud_clause = _dud_clause(left, fallback="token absent")
        text = (
            f"⛔ Schwab token {state}: the engine is BLIND — no reconciliation, no watches, "
            f"resting stops only. {dud_clause}. Re-auth: {url_text}"
        )

    await notifier.post(text)
    if url is not None:
        # The ET date and the state word, and nothing else. The authorization
        # URL carries the app key as a query parameter, and token_events is
        # read by /api, dumped in support, and copied into handoffs -- the
        # dedupe only ever matches on the `{et_date}%` prefix, so the URL was
        # storing a credential for no functional gain.
        await store.record_token_event(dedupe_kind, f"{et_date} {state}")

    return TokenReport(state=state, age_days=age, days_until_dead=left, action=action, auth_url=url)
