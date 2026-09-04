"""Expectations (spec §7 layer 3): declarative anomaly checks over the store.

Each check is a query against ticks / session_status / job_runs / the token
--- never a model call, never a heuristic. `run_expectations` evaluates every
configured `Expectation` against the store as it stands `today`, writes one
`expectations_log` row per result (the append-only trail this loop exists to
produce), and returns the results so the caller (a daily job, per
`config.yml`'s `schedule.expectations`) can post `digest()` to Discord.

"Latest session" is defined once, here, and shared by every check that needs
it: the most recent tick date on or before `today` -- never the calendar date
itself, because a weekend or a blind stretch (CLAUDE.md "Operating limitation
#1") means the most recent *tick* can be several days behind `today`.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from tc.broker.token import TokenStore
from tc.config import Expectation
from tc.store.db import Store


class ExpectationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    ok: bool
    detail: str


async def _latest_session_date(store: Store, today: date) -> str | None:
    row = await store.fetchone(
        "SELECT MAX(substr(at_et,1,10)) AS d FROM ticks WHERE substr(at_et,1,10) <= ?",
        (today.isoformat(),),
    )
    d = row["d"] if row is not None else None
    return None if d is None else str(d)


async def _ticks_per_session_min(
    store: Store, spec: Expectation, today: date
) -> tuple[bool, str]:
    if not isinstance(spec.arg, int) or isinstance(spec.arg, bool):
        raise ValueError(
            f"{spec.name}: ticks_per_session_min requires an int arg, got {spec.arg!r}"
        )
    latest = await _latest_session_date(store, today)
    if latest is None:
        return False, "no session found on or before " + today.isoformat()
    row = await store.fetchone(
        "SELECT COUNT(*) AS c FROM ticks WHERE substr(at_et,1,10)=? AND state='RTH'",
        (latest,),
    )
    count = int(row["c"]) if row is not None else 0
    ok = count >= spec.arg
    return ok, f"{count} RTH ticks on {latest} (min {spec.arg})"


async def _session_status_present(store: Store, today: date) -> tuple[bool, str]:
    latest = await _latest_session_date(store, today)
    if latest is None:
        return False, "no session found on or before " + today.isoformat()
    row = await store.fetchone("SELECT 1 FROM session_status WHERE date=?", (latest,))
    if row is not None:
        return True, f"session_status present for {latest}"
    return False, f"no session_status row for {latest}"


async def _job_verdict_not(store: Store, spec: Expectation, today: date) -> tuple[bool, str]:
    if not isinstance(spec.arg, str):
        raise ValueError(f"{spec.name}: job_verdict_not requires a str arg, got {spec.arg!r}")
    # window_sessions is read as calendar days here, not trading sessions --
    # job_runs has no notion of a trading calendar and this check only needs
    # a coarse "recently" window, not session-exact precision.
    start = (today - timedelta(days=spec.window_sessions)).isoformat()
    row = await store.fetchone(
        "SELECT COUNT(*) AS c FROM job_runs WHERE verdict=? AND substr(started_at,1,10) >= ?",
        (spec.arg, start),
    )
    count = int(row["c"]) if row is not None else 0
    ok = count == 0
    return ok, f"{count} '{spec.arg}' job_runs since {start}"


async def _token_days_until_dead_min(token: TokenStore, spec: Expectation) -> tuple[bool, str]:
    if not isinstance(spec.arg, int) or isinstance(spec.arg, bool):
        raise ValueError(
            f"{spec.name}: token_days_until_dead_min requires an int arg, got {spec.arg!r}"
        )
    left = token.days_until_dead()
    if left is None:
        return False, "token absent"
    ok = left >= spec.arg
    return ok, f"{left:.2f} days until dead (min {spec.arg})"


async def _evaluate(
    store: Store, token: TokenStore, spec: Expectation, today: date
) -> tuple[bool, str]:
    if spec.check == "ticks_per_session_min":
        return await _ticks_per_session_min(store, spec, today)
    if spec.check == "session_status_present":
        return await _session_status_present(store, today)
    if spec.check == "job_verdict_not":
        return await _job_verdict_not(store, spec, today)
    if spec.check == "token_days_until_dead_min":
        return await _token_days_until_dead_min(token, spec)
    # Unreachable: Expectation.check is a Literal, so pydantic already
    # rejected anything else at config-load time.
    raise ValueError(f"unknown check: {spec.check!r}")  # pragma: no cover


async def run_expectations(
    store: Store, token: TokenStore, specs: list[Expectation], today: date
) -> list[ExpectationResult]:
    results: list[ExpectationResult] = []
    for spec in specs:
        ok, detail = await _evaluate(store, token, spec, today)
        result = ExpectationResult(name=spec.name, ok=ok, detail=detail)
        await store.record_expectation(result.name, result.ok, result.detail)
        results.append(result)
    return results


def digest(results: list[ExpectationResult]) -> str:
    """A one-line-or-more summary that is never empty, even with zero
    results and zero breaches -- the daily post always has something to say."""
    breaches = [r for r in results if not r.ok]
    lines = [f"expectations: {len(results)} checks, {len(breaches)} breached"]
    lines.extend(f" — {r.name}: {r.detail}" for r in breaches)
    return "\n".join(lines)
