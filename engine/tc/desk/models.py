"""Names every desk module shares, stated once."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args

Analyst = Literal["technical", "earnings", "news", "macro"]
ANALYSTS: tuple[Analyst, ...] = get_args(Analyst)
Direction = Literal["up", "down"]
Funding = Literal["none", "shares", "call", "put"]
Instrument = Literal["shares", "call", "put"]

# Spec §5: a pitch's benchmark is SPY or one of the 11 sector SPDRs.
BENCHMARKS: tuple[str, ...] = (
    "SPY", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)

# Who is calling a desk tool is decided by the ENGINE from the running job,
# never supplied by the model (spec §5, `analyst`: "Set by the engine").
JOB_ANALYST: dict[str, Analyst] = {
    "analyst_technical": "technical",
    "analyst_earnings": "earnings",
    "analyst_news": "news",
    "analyst_macro": "macro",
    "preopen_news": "news",
    "preopen_earnings": "earnings",
}
PREOPEN_JOBS: frozenset[str] = frozenset({"preopen_news", "preopen_earnings"})
PM_JOBS: frozenset[str] = frozenset({"pm", "pm_midday"})


class DeskRefused(Exception):  # noqa: N818
    """A desk rule refused the model's input. The message is written for the
    model to read and act on; tc/mcp/tools_desk.py delivers it as a ToolError."""


@dataclass
class ActiveJob:
    """Which Claude job is running, set by the engine around every dispatch.

    The runner is one job at a time, so this is well-defined. A desk tool
    reached with no active job (someone poking the MCP mount by hand) is
    refused rather than attributed to anyone."""

    name: str | None = None


def utc_iso(dt: datetime) -> str:
    """One spelling for every stored timestamp, so text order is time order."""
    return dt.astimezone(UTC).isoformat()
