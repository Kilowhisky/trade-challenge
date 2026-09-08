"""The weekly whole-market sweep, as code.

This was a Claude job -- `/weekly-universe` -- and it is the clearest case in
the system of a judgement call where none was needed (spec §2.2). Everything it
did was mechanical: fetch a directory, filter it by five numeric gates, quote
what survived in batches, rank by distance from the 52-week high, and write a
table. What made it a model's job was that the quote payloads were too large to
put in context, so the design put them in files the model was forbidden to read
and had it orchestrate scripts over paths. Here nothing is in context because
nothing is a model.

Two properties from that design are kept because they were right:

* **Degrade to stale, never to empty.** Below the sanity floor, or on any HTTP
  failure, this raises and writes NOTHING -- last week's universe stands. A WAF
  page parses to zero rows, and an empty universe leaves the scout with no
  cohort for a week (which is exactly what the 2026-08-29 sweep did).
* **The no-data branch of the stub gate runs first.** `session_range_pct is
  None` means the high/low fields were absent, not that the name is pinned at
  0.00%: SPY reports 0/0 high/low with 34M shares of volume.

And one is deleted: the two-tier `--emit-qualified-set` flag, whose entire job
was stopping the daily run from overwriting the scout's 3,196-name file with 500
of its own names. That is a `qualified` column now, and the daily run does not
write this table at all.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from tc.broker.models import VerboseQuote
from tc.clock import ET
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store

log = logging.getLogger(__name__)

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
# The real directory currently yields ~11,227 tradeable symbols. A moved file,
# a maintenance page or an edge/WAF block can come back as an HTTP success
# whose body is a short decoy page; parsed as pipe-delimited CSV that produces
# zero rows, which without this floor installs an empty universe and reports
# success. 1000 is far below the real count and far above zero. A false trip is
# SAFE by design: the caller keeps last week's universe.
SANITY_FLOOR = 1000
# No longer needed to force a payload out of context -- nothing here is in
# context -- but still the batch size Schwab answers reliably.
CHUNK = 150
MAJOR_EXCHANGES = frozenset("NQAPZ")
NAME_EXCLUSIONS: tuple[str, ...] = (
    " warrant", "% note", " right", " unit", "preferred",
    " depositary", "when issued", " due 20",
)
FETCH_TIMEOUT_S = 60.0
# A name with no 52-week high has no gap to rank on. The sentinel sorts it
# LAST; a 0.0 would put every unknown at the top of the working universe.
NO_52WK_HIGH_GAP = Decimal("999.0")

HEADER = (
    "symbol\tprice\tadv10\tdollar_vol\tpct_from_52wk_high\toptionable"
    "\tleverage\tlast_earnings\tis_etf\tsession_range_pct"
)


# N818 wants an "Error" suffix on all three. These names are the contract
# `main.py` catches by to turn a failed sweep into a `failed` verdict rather
# than a crash, and "DirectoryUnavailableError" reads as a failure to determine
# availability rather than what this is: the directory, unavailable.
class UniverseUnavailable(RuntimeError):  # noqa: N818
    """The sweep produced nothing installable, whatever the cause.

    Never a partial write: the caller keeps last week's universe, which is
    stale and says so, rather than this week's, which would be empty and would
    not. `main.py` catches this base, so a new reason to abort cannot be added
    without the caller already handling it.
    """


class DirectoryUnavailable(UniverseUnavailable):
    """The symbol directory could not be read, or read back as implausible."""


class PartialSweep(UniverseUnavailable):
    """Too much of the market went unquoted to call the result a universe.

    `EmptyUniverse` catches the total failure; this catches the one that still
    qualifies thousands of names and is therefore invisible downstream. The
    sweep absorbs a failed chunk on purpose -- 74 good chunks are a universe
    and an aborted sweep is none -- but every absorbed chunk is a slice of the
    market that reads as "did not qualify" rather than "was never asked", and
    the scout's cohort is a join against exactly that set. Above the
    `strategy.universe_max_failed_chunk_pct` ceiling the honest answer is last
    week's universe, which is stale and says so.
    """


class EmptyUniverse(UniverseUnavailable):
    """The directory was fine and the quotes were not.

    The sanity floor guards the fetch; this guards everything after it. A token
    that lapses between the fetch and the first chunk fails every chunk, quotes
    nothing, and qualifies nothing -- which without this check writes an
    `asof`-stamped empty table over the newest one, leaving the scout with no
    cohort for a week. That is the 2026-08-29 outage arriving through a
    different door.
    """


class QuoteSource(Protocol):
    """The only thing this loop asks of a broker. Narrower than `Broker` on
    purpose -- a whole-market screen has no business holding an order tool."""

    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]: ...


class UniverseRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    price: Decimal
    adv10: Decimal
    dollar_vol: Decimal
    pct_from_52wk_high: Decimal
    optionable: bool
    # The leverage MULTIPLE, not Schwab's `fundLeverageFactor`, which is a
    # PERCENTAGE: 0 a single stock, 100.0 a 1x fund, 300.0 a 3x fund, -100.0 an
    # inverse fund. §3.5 reasoning reads in multiples, so the row carries
    # raw/100 and the gate below reads the raw value.
    leverage: Decimal
    last_earnings: str
    is_etf: bool
    # `None` means the payload carried no usable high/low -- NOT a 0.00% range.
    # Rendered as `-`, and the difference is the whole stub gate.
    session_range_pct: Decimal | None
    description: str
    # Always True as written by this loop: a row exists because it passed the
    # five gates. The column is the scout tier's marker, and it is what the v2
    # `--emit-qualified-set` flag protected by convention.
    qualified: bool


class Counts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fetched: int = 0
    quoted: int = 0
    qualified: int = 0
    stub_filtered: int = 0
    nodata: int = 0
    ranked: int = 0
    dropped: int = 0
    skipped: list[str] = Field(default_factory=list)
    chunks: int = 0
    chunks_failed: int = 0


# --- the directory ---------------------------------------------------------
def parse_directory(text: str) -> list[str]:
    """Nasdaq Trader's pipe-delimited directory, reduced to tradeable symbols.

    A decoy page parses cleanly to zero rows here rather than raising: the
    sanity floor in `fetch_symbols`, not an exception, is what tells the two
    apart, because a real directory that legitimately shrank and an HTML block
    page are the same shape to a CSV reader.
    """
    keep: list[str] = []
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        sym = str(row.get("Symbol") or "").strip()
        if not sym or "$" in sym or len(sym) > 5:
            continue
        if row.get("Listing Exchange") not in MAJOR_EXCHANGES:
            continue
        if row.get("Test Issue") != "N":
            continue
        name = str(row.get("Security Name") or "").lower()
        if any(k in name for k in NAME_EXCLUSIONS):
            continue
        keep.append(sym)
    return sorted(set(keep))


async def fetch_symbols(client: httpx.AsyncClient, url: str = NASDAQ_URL) -> list[str]:
    try:
        resp = await client.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
    except httpx.HTTPError as e:
        # Redirects are followed rather than accepted as bodies, so a truly
        # moved file has a chance to surface as a real status below. That is
        # defense in depth, not a substitute for the floor: a redirect target
        # that itself returns 200 with a soft "not found" page slips past it.
        raise DirectoryUnavailable(f"symbol directory unreachable at {url}: {e}") from e
    if resp.status_code // 100 != 2:
        raise DirectoryUnavailable(f"symbol directory at {url} returned HTTP {resp.status_code}")
    if not resp.text.strip():
        raise DirectoryUnavailable(f"symbol directory at {url} came back empty")
    symbols = parse_directory(resp.text)
    if len(symbols) < SANITY_FLOOR:
        raise DirectoryUnavailable(
            f"symbol directory parsed to {len(symbols)} symbols, below the sanity floor of"
            f" {SANITY_FLOOR} — refusing to install an implausible universe;"
            " last week's stands"
        )
    return symbols


# --- the gates -------------------------------------------------------------
def _session_range_pct(q: VerboseQuote, price: Decimal) -> Decimal | None:
    hi, lo = q.high, q.low
    if hi is None or lo is None or hi <= 0 or lo <= 0 or price <= 0:
        return None
    return (hi - lo) / price * 100


def filter_universe(
    quotes: dict[str, VerboseQuote], rules: Rules, rank_top: int | None = None
) -> tuple[list[UniverseRow], Counts]:
    """The five gates, then the rank. `rank_top` defaults to the rule; 0 means
    no truncation at all.

    The gate order is not arbitrary and the stub gate's no-data branch runs
    first by construction -- see the module docstring.
    """
    min_price = rules.get("manual", "min_share_price_usd")
    min_dollar = rules.get("manual", "min_avg_daily_dollar_volume")
    min_shares = rules.get("manual", "min_avg_daily_volume")
    min_range = rules.get("strategy", "min_session_range_pct")
    if rank_top is None:
        rank_top = int(rules.get("strategy", "working_universe_size"))

    rows: list[UniverseRow] = []
    skipped: list[str] = []
    stub_filtered = 0
    nodata = 0
    for symbol, q in quotes.items():
        price = q.price
        if price is None:
            # Unquotable: skipped, and NAMED. Never treated as zero, which
            # would sail through the price floor as "free".
            skipped.append(symbol)
            continue
        adv = q.avg10_days_volume
        dollar_vol = adv * price
        if price < min_price:                       # §1.4 price floor
            continue
        if dollar_vol < min_dollar:                 # §1.4 dollar-volume floor
            continue
        if adv < min_shares:                        # §1.4 share sanity floor
            continue
        # §3.5, gated shut by default: keep only a single stock (raw 0) or a 1x
        # fund (raw 100). Rejecting on `!= 0` would discard every ETF in the
        # market -- roughly half the fetched directory -- not just the
        # leveraged and inverse ones.
        if q.fund_leverage_factor not in (Decimal(0), Decimal(100)):
            continue
        rng = _session_range_pct(q, price)
        # TAKEOVER-STUB GATE. An announced all-cash deal target trades pinned a
        # hair under its deal price, which is by construction its 52-week high,
        # so the proximity rank below acts as a merger detector: on 2026-08-19
        # nine of fifteen shortlisted names were deal stubs. A pinned name has
        # no daily range, so the range is the discriminator -- and `rng is
        # None` is "no high/low data", not "0.00% pinned".
        if rng is not None and rng < min_range:
            stub_filtered += 1
            continue
        if rng is None:
            nodata += 1
        gap = (
            (q.week52_high - price) / q.week52_high * 100
            if q.week52_high else NO_52WK_HIGH_GAP
        )
        rows.append(
            UniverseRow(
                symbol=symbol,
                price=price,
                adv10=adv,
                dollar_vol=dollar_vol,
                pct_from_52wk_high=gap,
                optionable=q.optionable,
                leverage=q.fund_leverage_factor / 100,
                last_earnings=q.last_earnings,
                is_etf=q.is_etf,
                session_range_pct=rng,
                description=q.description,
                qualified=True,
            )
        )

    rows.sort(key=lambda r: (r.pct_from_52wk_high, -r.dollar_vol))
    qualified = len(rows)
    if rank_top and qualified > rank_top:
        rows = rows[:rank_top]
    return rows, Counts(
        quoted=len(quotes), qualified=qualified, stub_filtered=stub_filtered, nodata=nodata,
        ranked=len(rows), dropped=qualified - len(rows), skipped=skipped,
    )


# --- the document ----------------------------------------------------------
def _q(d: Decimal, places: str) -> str:
    return str(d.quantize(Decimal(places), rounding=ROUND_HALF_EVEN))


def _tsv(r: UniverseRow) -> str:
    return "\t".join((
        r.symbol,
        _q(r.price, "0.0001"),
        _q(r.adv10, "1"),
        _q(r.dollar_vol, "1"),
        _q(r.pct_from_52wk_high, "0.01"),
        "true" if r.optionable else "false",
        _q(r.leverage, "0.1"),
        r.last_earnings or "-",
        "true" if r.is_etf else "false",
        "-" if r.session_range_pct is None else _q(r.session_range_pct, "0.01"),
    ))


def render_universe_md(rows: Sequence[UniverseRow], counts: Counts, asof: datetime) -> str:
    """The whole document, composed server-side.

    v2 built this as a `{ printf …; cat ranked.tsv; printf '```'; }` compound
    piped into a writer script, for one reason: keeping a 500-row table out of
    a model's context. The table never enters context here either, because no
    model is in this loop at all.
    """
    stamp = asof.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    skipped = ", ".join(counts.skipped) if counts.skipped else "-"
    head = [
        "# Fallback universe",
        "",
        "This file is never a source for order parameters — every candidate",
        "re-verifies live under §4.9/§4.10. Regenerated weekly by the engine's",
        "weekly_universe job; the daily research tier only reads it.",
        "",
        f"Assembled: {stamp} — fetched {counts.fetched} / quoted {counts.quoted} /",
        f"qualified {counts.qualified} / ranked {counts.ranked} / dropped {counts.dropped}.",
        f"Stub-filtered {counts.stub_filtered}; no usable high/low {counts.nodata};",
        f"chunks {counts.chunks} ({counts.chunks_failed} failed). Skipped: {skipped}.",
        "",
        "Tilts: the §4 tilts are not applied here (50-day SMA, 3- and 6-month",
        "returns) and belong to the daily tier — the verbose quote payload this",
        "sweep reads carries no price history to compute them from.",
        "",
        "Columns: symbol, price (regular-session close), 10-day ADV, dollar volume,",
        "% from 52-week high, optionable, leverage (a MULTIPLE: 0 single stock,",
        "1.0 a 1x fund; leveraged and inverse funds are gated out by §3.5 and do",
        "not appear), last earnings date, ETF flag, latest-session range as a",
        "% of price (`-` = the payload carried no usable high/low).",
        "",
        "```",
    ]
    return "\n".join([*head, HEADER, *(_tsv(r) for r in rows), "```"]) + "\n"


# --- the sweep -------------------------------------------------------------
async def run_weekly_universe(
    *,
    broker: QuoteSource,
    store: Store,
    docs: DocStore,
    rules: Rules,
    client: httpx.AsyncClient,
    now: datetime,
) -> Counts:
    """Fetch, quote in chunks, gate, rank, write. Raises `DirectoryUnavailable`
    before touching either the table or the document."""
    symbols = await fetch_symbols(client)
    quotes: dict[str, VerboseQuote] = {}
    chunks = 0
    chunks_failed = 0
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        chunks += 1
        try:
            quotes.update(await broker.quotes_verbose(chunk))
        except Exception as e:
            # One bad chunk must not cost the week's universe.
            # Deliberately broad: schwab-py, httpx and the token layer all
            # surface differently, and 74 good chunks are a universe while an
            # aborted sweep is none.
            chunks_failed += 1
            log.warning("weekly universe: chunk %d of %d failed: %s", chunks, len(symbols), e)

    # Before the gate, before the rank, before any write: a sweep that lost too
    # much of the market did not produce a narrower universe, it produced an
    # unknown one, and installing it would stamp today's date on it.
    max_failed_pct = rules.get("strategy", "universe_max_failed_chunk_pct")
    failed_pct = Decimal(chunks_failed) * 100 / Decimal(chunks) if chunks else Decimal(0)
    if failed_pct > max_failed_pct:
        raise PartialSweep(
            f"{chunks_failed} of {chunks} chunks failed ({failed_pct:.1f}% >"
            f" {max_failed_pct}% allowed) — refusing to install a universe missing"
            " that much of the market; last week's stands"
        )

    rows, counts = filter_universe(quotes, rules, rank_top=0)   # 0: the whole qualified set
    if not rows:
        # Before any write, and deliberately not a "wrote 0 rows" success:
        # `replace_universe` stamps today's date, so an empty write makes
        # `universe_asof()` point at nothing and hides last week's rows.
        raise EmptyUniverse(
            f"sweep qualified 0 of {len(quotes)} quoted symbols"
            f" ({chunks_failed} of {chunks} chunks failed) — refusing to install"
            " an empty universe; last week's stands"
        )
    # The store holds every qualifying name -- the scout tier, ~3,196 names --
    # and the document holds the ranked head of it. In v2 those were two files
    # and a flag; the rank discards precisely the low-coverage mid-caps the
    # information edge lives in, so the set the scout reads must not be the
    # truncated one. Rows are already rank-sorted, so the head IS the rank.
    rank_top = int(rules.get("strategy", "working_universe_size"))
    ranked = rows[:rank_top] if rank_top else list(rows)
    counts = counts.model_copy(update={
        "fetched": len(symbols), "chunks": chunks, "chunks_failed": chunks_failed,
        "ranked": len(ranked), "dropped": len(rows) - len(ranked),
    })
    await store.replace_universe(now.astimezone(ET).date(), [r.model_dump() for r in rows])
    await docs.replace("universe", render_universe_md(ranked, counts, now))
    return counts


def counts_detail(counts: Counts) -> dict[str, Any]:
    """The job-run ledger's detail blob. `skipped` can be thousands of names on
    a bad Schwab day, so the ledger carries the count and the first few."""
    d = counts.model_dump()
    d["skipped"] = counts.skipped[:20]
    d["skipped_total"] = len(counts.skipped)
    return d
