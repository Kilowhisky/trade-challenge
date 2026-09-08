"""The active earnings cohort: the right-hand side of the scout's daily work.

A port of cohort.sh, whose awk implementation carried Howard Hinnant's civil
date algorithms because `date -d` is GNU-only. Here it is `datetime.timedelta`,
which is the whole reason this belongs in Python.

Three of that script's decisions are kept exactly:

* **Empty is a correct answer, and never an error.** Between earnings seasons,
  and before the first weekly sweep has ever run, the honest cohort is zero
  rows. A raised exception here would make the deadman cry wolf every day.
* **An unparseable last-earnings date is skipped, not guessed.** The awk version
  returned a BAD sentinel rather than a wrong number, because a wrong number
  silently puts a name in or out of the window.
* **`other` is out of scope, and so is untagged.** `other` exists only to retire
  a previously in-scope tag.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from tc.rules.model import Rules
from tc.store.db import IN_SCOPE_SECTORS, Store

# A calendar fact, not a risk parameter: rules.yml carries no key for it, and
# inventing one would put "a quarter is 91 days" in the file that holds caps.
QUARTER_DAYS = 91


class CohortRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    sector: str
    est_next_earnings: date
    days_out: int


async def cohort(store: Store, rules: Rules, d: date) -> list[CohortRow]:
    win_min = int(rules.get("strategy", "scout_entry_window_min_days"))
    win_max = int(rules.get("strategy", "scout_entry_window_max_days"))
    tags = {r["symbol"]: r["sector"] for r in await store.sectors()}
    out: list[CohortRow] = []
    for row in await store.universe_rows(qualified_only=True):
        sector = tags.get(row["symbol"])
        if sector is None or sector not in IN_SCOPE_SECTORS:
            continue
        try:
            last = date.fromisoformat(str(row["last_earnings"])[:10])
        except ValueError:
            continue
        est_next = last + timedelta(days=QUARTER_DAYS)
        days_out = (est_next - d).days
        if win_min <= days_out <= win_max:
            out.append(
                CohortRow(
                    symbol=row["symbol"], sector=sector,
                    est_next_earnings=est_next, days_out=days_out,
                )
            )
    out.sort(key=lambda r: (r.days_out, r.symbol))
    return out
