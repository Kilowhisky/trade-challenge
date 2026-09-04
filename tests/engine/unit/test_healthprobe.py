"""`host/healthprobe.py` -- the host-side layer of spec §7 layer 2.

It runs on the Pi's system Python outside docker (stdlib only), so it is
imported here by path via `importlib`, not as a package. `evaluate` is the
pure rule set this file pins down: given a `/health` body (or `None` when the
engine did not answer) and the current ET time, it returns zero or more
alert messages. `main()` -- the HTTP fetch, the dedupe file, the webhook POST
-- is deliberately not exercised here; it is a thin composition of `evaluate`
plus side effects that a unit test has no business reaching into.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HEALTHPROBE_PATH = Path(__file__).resolve().parents[3] / "host" / "healthprobe.py"


def _load_healthprobe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("healthprobe", HEALTHPROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


healthprobe = _load_healthprobe()
evaluate: Callable[[dict[str, object] | None, datetime], list[str]] = healthprobe.evaluate

# Tuesday, inside the 09:30-16:00 RTH window.
TUE_RTH = datetime(2026, 9, 8, 10, 0, tzinfo=ET)
# Same Tuesday, well outside RTH.
TUE_EVENING = datetime(2026, 9, 8, 20, 0, tzinfo=ET)
# Saturday, same clock time as TUE_RTH -- weekday gate, not just a time gate.
SAT_SAME_CLOCK = datetime(2026, 9, 5, 10, 0, tzinfo=ET)
# Tuesday, exactly at the RTH boundaries.
TUE_OPEN = datetime(2026, 9, 8, 9, 30, tzinfo=ET)
TUE_CLOSE = datetime(2026, 9, 8, 16, 0, tzinfo=ET)


def _health(**overrides: Any) -> dict[str, object]:
    base: dict[str, object] = {
        "ok": True,
        "version": "0.1.0",
        "shadow": True,
        "blind": False,
        "token_state": "fresh",
        "token_days_until_dead": 5.0,
        "last_broker_read_ok_at": "2026-09-08T14:00:00+00:00",
        "last_broker_read_age_s": 10,
        "positions_without_stop": 0,
        "open_alerts": 0,
        "consecutive_tick_failures": 0,
        "pending_approval_age_s": None,
        "runner_ok": True,
        "db_ok": True,
        "in_flight_proposal": False,
        "action": "none",
    }
    base.update(overrides)
    return base


def test_none_health_is_unreachable() -> None:
    assert evaluate(None, TUE_RTH) == ["engine unreachable: /health did not answer"]


def test_all_clear_health_has_no_messages() -> None:
    assert evaluate(_health(), TUE_RTH) == []


def test_ok_false_reports_action() -> None:
    assert evaluate(_health(ok=False, action="halt"), TUE_RTH) == [
        "engine reports ok=false (action: halt)"
    ]


def test_ok_missing_is_treated_as_false() -> None:
    health = _health()
    del health["ok"]
    assert evaluate(health, TUE_RTH) == ["engine reports ok=false (action: none)"]


def test_token_dying_at_threshold() -> None:
    assert evaluate(_health(token_days_until_dead=2.0, action="reauth"), TUE_RTH) == [
        "token dies in 2.0 days — reauth"
    ]


def test_token_dying_below_threshold() -> None:
    assert evaluate(_health(token_days_until_dead=0.3, action="reauth"), TUE_RTH) == [
        "token dies in 0.3 days — reauth"
    ]


def test_token_not_yet_dying_is_silent() -> None:
    assert evaluate(_health(token_days_until_dead=2.1), TUE_RTH) == []


def test_token_days_none_is_silent() -> None:
    assert evaluate(_health(token_days_until_dead=None), TUE_RTH) == []


def test_stale_broker_read_during_rth_on_a_weekday() -> None:
    assert evaluate(_health(last_broker_read_age_s=1201), TUE_RTH) == [
        "no broker read for 1201s during regular hours"
    ]


def test_stale_broker_read_outside_rth_is_silent_same_weekday() -> None:
    assert evaluate(_health(last_broker_read_age_s=99999), TUE_EVENING) == []


def test_stale_broker_read_on_a_weekend_is_silent_even_at_rth_clock_time() -> None:
    assert evaluate(_health(last_broker_read_age_s=99999), SAT_SAME_CLOCK) == []


def test_stale_broker_read_at_rth_open_boundary_counts() -> None:
    assert evaluate(_health(last_broker_read_age_s=1201), TUE_OPEN) == [
        "no broker read for 1201s during regular hours"
    ]


def test_stale_broker_read_at_rth_close_boundary_is_silent() -> None:
    # 16:00 ET is the close -- the RTH window is [09:30, 16:00).
    assert evaluate(_health(last_broker_read_age_s=99999), TUE_CLOSE) == []


def test_broker_read_age_at_threshold_is_silent() -> None:
    assert evaluate(_health(last_broker_read_age_s=1200), TUE_RTH) == []


def test_broker_read_age_none_alerts_during_regular_hours() -> None:
    """A missing age is not "no news": it is an engine that has never had a
    successful broker read, which during RTH is the token-dead / broker-never-
    reopened shape. Silence there hid the loudest failure the probe has."""
    assert evaluate(_health(last_broker_read_age_s=None), TUE_RTH) == [
        "no successful broker read yet during regular hours"
    ]


def test_broker_read_age_none_is_silent_outside_regular_hours() -> None:
    """Overnight there is nothing to read: the market is shut and the engine
    is not expected to have touched the broker."""
    assert evaluate(_health(last_broker_read_age_s=None), TUE_EVENING) == []
    assert evaluate(_health(last_broker_read_age_s=None), SAT_SAME_CLOCK) == []


def test_open_alerts_are_reported_and_zero_is_silent() -> None:
    assert evaluate(_health(open_alerts=2), TUE_RTH) == ["2 open alert(s)"]
    assert evaluate(_health(open_alerts=0), TUE_RTH) == []


def test_positions_without_stop() -> None:
    assert evaluate(_health(positions_without_stop=2), TUE_RTH) == [
        "2 position(s) without a stop"
    ]


def test_positions_without_stop_zero_is_silent() -> None:
    assert evaluate(_health(positions_without_stop=0), TUE_RTH) == []


def test_positions_without_stop_none_is_silent() -> None:
    assert evaluate(_health(positions_without_stop=None), TUE_RTH) == []


def test_pending_approval_over_threshold() -> None:
    assert evaluate(_health(pending_approval_age_s=601), TUE_RTH) == [
        "approval pending 601s"
    ]


def test_pending_approval_at_threshold_is_silent() -> None:
    assert evaluate(_health(pending_approval_age_s=600), TUE_RTH) == []


def test_pending_approval_none_is_silent() -> None:
    assert evaluate(_health(pending_approval_age_s=None), TUE_RTH) == []


def test_runner_not_ok() -> None:
    assert evaluate(_health(runner_ok=False), TUE_RTH) == ["runner not ok"]


def test_runner_ok_true_is_silent() -> None:
    assert evaluate(_health(runner_ok=True), TUE_RTH) == []


def test_runner_ok_none_is_silent() -> None:
    assert evaluate(_health(runner_ok=None), TUE_RTH) == []


def test_runner_ok_missing_is_silent() -> None:
    health = _health()
    del health["runner_ok"]
    assert evaluate(health, TUE_RTH) == []


def test_db_not_ok() -> None:
    assert evaluate(_health(db_ok=False), TUE_RTH) == ["db not ok"]


def test_db_ok_true_is_silent() -> None:
    assert evaluate(_health(db_ok=True), TUE_RTH) == []


def test_missing_keys_treated_as_none_no_messages() -> None:
    # A health body with almost nothing in it should not blow up and should
    # not alert on fields it does not carry -- only `ok` defaults to False,
    # and (during RTH only) a missing broker-read age, which is a claim about
    # the engine rather than a field it forgot to send.
    assert evaluate({"ok": True}, TUE_EVENING) == []
    assert evaluate({"ok": True}, TUE_RTH) == [
        "no successful broker read yet during regular hours"
    ]


def test_multiple_breaches_all_reported() -> None:
    health = _health(
        ok=False,
        action="halt",
        token_days_until_dead=1.0,
        positions_without_stop=3,
        runner_ok=False,
        db_ok=False,
    )
    msgs = evaluate(health, TUE_RTH)
    assert msgs == [
        "engine reports ok=false (action: halt)",
        "token dies in 1.0 days — halt",
        "3 position(s) without a stop",
        "runner not ok",
        "db not ok",
    ]
