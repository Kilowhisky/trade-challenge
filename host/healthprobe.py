#!/usr/bin/env python3
"""Host-side probe -- the layer of spec §7 layer 2 that runs OUTSIDE docker.

This is the point of it: if the engine container is wedged, the token has
died, or the whole box has lost docker, nothing running *inside* a container
can report that. This script runs on the Pi's system Python via a systemd
timer (`host/tc-healthprobe.service` / `.timer`), reads `/health` over plain
loopback HTTP, and posts to a Discord webhook the engine itself does not
own -- so an engine that can no longer speak for itself still gets spoken
for. CLAUDE.md's operating limitation #1: "absence of action is never
evidence that nothing needed doing" applies to the probe's own supervision
loop too, which is why it is deliberately as small and dependency-free as
possible: stdlib only, no `subprocess`, no venv, nothing to break in the
python3 already on the Pi.

`evaluate()` is the pure rule set, and the only thing `tests/engine/unit/
test_healthprobe.py` exercises (imported by path -- this file is not part of
the `tc` package and never runs inside the engine's venv). `main()` composes
it with the two side effects the rules exist to trigger: an HTTP GET of
`/health` and, when there is something to say, a webhook POST -- deduped so
a wedged engine does not spam the same line every two minutes forever.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

HEALTH_URL = "http://127.0.0.1:8080/health"
FETCH_TIMEOUT_S = 5

# --- spec §7 layer 2 thresholds ---------------------------------------------
# These three numbers belong to the probe, not to rules.yml: they are about
# how fast a human finds out the engine has gone quiet, not about position
# sizing or risk caps. scripts/check-consistency.sh has no opinion on them.
TOKEN_DAYS_CRITICAL = 2  # token_days_until_dead <= this triggers an alert
BROKER_READ_STALE_S = 1200  # last_broker_read_age_s > this, RTH-only, alerts
APPROVAL_PENDING_S = 600  # pending_approval_age_s > this triggers an alert

RTH_START = dtime(9, 30)
RTH_END = dtime(16, 0)

STATE_DIR_ENV = "PROBE_STATE_DIR"  # override for tests; unit's default is /var/lib/tc
DEFAULT_STATE_DIR = "/var/lib/tc"
STATE_FILENAME = "probe.last"
DEDUPE_WINDOW_S = 30 * 60

WEBHOOK_ENV = "PROBE_WEBHOOK"


def _in_regular_hours(now_et: datetime) -> bool:
    """Mon-Fri, 09:30 <= t < 16:00 ET -- the same half-open RTH window
    `tc.clock.phase_for` uses inside the engine."""
    return now_et.weekday() < 5 and RTH_START <= now_et.time() < RTH_END


def evaluate(health: dict[str, object] | None, now_et: datetime) -> list[str]:
    """Given a `/health` body (or `None` when the fetch failed) and the
    current ET time, return zero or more alert messages. Pure: no I/O, no
    clock reads other than the one passed in. Missing keys are treated the
    same as an explicit `None` -- silent -- except `ok`, whose absence reads
    as `False` (an engine that cannot even report is not "ok" by default)."""
    if health is None:
        return ["engine unreachable: /health did not answer"]

    msgs: list[str] = []
    action = health.get("action")

    if health.get("ok", False) is not True:
        msgs.append(f"engine reports ok=false (action: {action})")

    days = health.get("token_days_until_dead")
    if isinstance(days, (int, float)) and days <= TOKEN_DAYS_CRITICAL:
        msgs.append(f"token dies in {days:.1f} days — {action}")

    age_s = health.get("last_broker_read_age_s")
    if (
        isinstance(age_s, (int, float))
        and age_s > BROKER_READ_STALE_S
        and _in_regular_hours(now_et)
    ):
        msgs.append(f"no broker read for {int(age_s)}s during regular hours")

    naked = health.get("positions_without_stop")
    if isinstance(naked, (int, float)) and naked > 0:
        msgs.append(f"{int(naked)} position(s) without a stop")

    pending = health.get("pending_approval_age_s")
    if isinstance(pending, (int, float)) and pending > APPROVAL_PENDING_S:
        msgs.append(f"approval pending {int(pending)}s")

    if health.get("runner_ok") is False:
        msgs.append("runner not ok")

    if health.get("db_ok") is False:
        msgs.append("db not ok")

    return msgs


def _fetch_health() -> dict[str, object] | None:
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310 -- fixed loopback URL
            if resp.status != 200:
                return None
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def _state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV, DEFAULT_STATE_DIR))


def _should_post(joined: str, now_epoch: float) -> bool:
    """Dedupe on the sha256 of the joined message text: an identical message
    inside the 30-minute window is skipped. Any read/parse trouble with the
    state file is treated as "no prior post" -- a re-alert is safe, silently
    losing an alert is not."""
    state_path = _state_dir() / STATE_FILENAME
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    try:
        prior = json.loads(state_path.read_text())
        prior_digest = prior.get("digest")
        prior_epoch = prior.get("epoch")
        if (
            prior_digest == digest
            and isinstance(prior_epoch, (int, float))
            and now_epoch - prior_epoch < DEDUPE_WINDOW_S
        ):
            return False
    except (FileNotFoundError, ValueError, OSError):
        pass

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"digest": digest, "epoch": now_epoch}))
    except OSError:
        pass
    return True


def _post_webhook(joined: str) -> None:
    webhook = os.environ.get(WEBHOOK_ENV)
    if not webhook:
        print(
            f"tc-healthprobe: {WEBHOOK_ENV} not set; would have posted: {joined}",
            file=sys.stderr,
        )
        return
    payload = json.dumps({"content": "🛰 host probe — " + joined}).encode("utf-8")
    # webhook comes from EnvironmentFile (/etc/tc/probe.env), not user input.
    req = urllib.request.Request(  # noqa: S310
        webhook, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S):  # noqa: S310
            pass
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"tc-healthprobe: webhook POST failed: {e}", file=sys.stderr)


def main() -> int:
    try:
        now_et = datetime.now(ET)
        health = _fetch_health()
        msgs = evaluate(health, now_et)
        if msgs:
            joined = "; ".join(msgs)
            if _should_post(joined, now_et.timestamp()):
                _post_webhook(joined)
    except Exception as e:  # this probe must never raise (main()'s one contract)
        print(f"tc-healthprobe: unexpected error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
