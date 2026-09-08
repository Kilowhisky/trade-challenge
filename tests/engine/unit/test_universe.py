"""The weekly sweep: the directory filter, the five gates, the rank, the write.

Every gate assertion below is *isolated*: the symbol it names fails exactly one
gate and passes the other four, so deleting that gate's line in
`filter_universe` makes this file fail and nothing else does. That property is
inherited from scripts/test-universe-filter.sh, where it was established by an
actual mutation run, and it is the only reason a five-line chain of `continue`s
is testable at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal as D  # noqa: N817 -- brevity in a Decimal-heavy fixture table
from pathlib import Path
from typing import Any

import httpx
import pytest

from tc.broker.models import VerboseQuote
from tc.loops.universe import (
    CHUNK,
    SANITY_FLOOR,
    Counts,
    DirectoryUnavailable,
    EmptyUniverse,
    PartialSweep,
    UniverseUnavailable,
    fetch_symbols,
    filter_universe,
    parse_directory,
    render_universe_md,
    run_weekly_universe,
)
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nasdaq"
RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")
NOW = datetime(2026, 9, 12, 11, 56, tzinfo=UTC)

DIRECTORY_HEADER = (
    "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF"
    "|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares"
)


# --- the quote fixture -----------------------------------------------------
# Built through `VerboseQuote.from_payload`, not by hand, so the payload SHAPE
# stays part of what these tests pin: `extended` is emitted before `quote` in a
# real response, and reading the after-hours print instead of the regular close
# is the defect v2 shipped for a week (CSX below is the regression).
def _payload(
    *,
    regular: float | None = None,
    quote_last: float | None = None,
    extended: float | None = None,
    adv: float = 0.0,
    leverage: float = 0.0,
    high: float | None = None,
    low: float | None = None,
    high52: float = 0.0,
    optionable: bool = True,
    description: str = "",
    earnings: str = "",
    etf: bool = False,
) -> dict[str, Any]:
    q: dict[str, Any] = {"52WeekHigh": high52, "netPercentChange": 0.0}
    if quote_last is not None:
        q["lastPrice"] = quote_last
    if high is not None:
        q["highPrice"] = high
    if low is not None:
        q["lowPrice"] = low
    body: dict[str, Any] = {
        "assetSubType": "ETF" if etf else "",
        "quote": q,
        "fundamental": {
            "avg10DaysVolume": adv,
            "fundLeverageFactor": leverage,
            "lastEarningsDate": earnings,
        },
        "reference": {"optionable": optionable, "description": description},
        "regular": {} if regular is None else {"regularMarketLastPrice": regular},
    }
    if extended is not None:
        body["extended"] = {"lastPrice": extended}
    return body


PAYLOADS: dict[str, dict[str, Any]] = {
    # Clean stock, and the price-precedence regression: three different prices
    # in one block. The regular close (50.58) is the screening price; the
    # consolidated quote (50.89) and the after-hours tick (50.67) are not.
    "CSX": _payload(regular=50.58, quote_last=50.89, extended=50.67, adv=12_000_000,
                    high=51.00, low=50.10, high52=51.00, description="CSX CORP",
                    earnings="2026-07-22T00:00:00.000Z"),
    # A 1x fund reports fundLeverageFactor 100, NOT 0 -- and high/low arrive as
    # 0/0 alongside 34.4M shares of volume. Both facts are load-bearing: a
    # `!= 0` leverage gate discards every ETF in the market, and reading 0/0 as
    # a 0.00% range false-rejects the most liquid ETF there is.
    "SPY": _payload(regular=640.0, quote_last=640.0, adv=34_400_000, leverage=100.0,
                    high=0.0, low=0.0, high52=650.0, etf=True,
                    description="SPDR S&P 500 ETF TRUST"),
    "MPC": _payload(regular=150.0, quote_last=150.0, adv=3_000_000, high=152.0, low=148.0,
                    high52=160.0, description="MARATHON PETROLEUM CORP",
                    earnings="2026-08-04T00:00:00.000Z"),
    # Identical gap, different dollar volume: the tie-break is -dollar_vol, so
    # TIEB must rank ahead of TIEA.
    "TIEA": _payload(regular=97.0, quote_last=97.0, adv=1_000_000, high=99.0, low=96.0,
                     high52=100.0, description="TIE A CORP"),
    "TIEB": _payload(regular=97.0, quote_last=97.0, adv=2_000_000, high=99.0, low=96.0,
                     high52=100.0, description="TIE B CORP"),
    # No 52-week high at all -> the 999.0 sentinel, which sorts last rather
    # than first (a 0.0 gap would put an unknown name at the top of the file).
    "NOHI": _payload(regular=20.0, quote_last=20.0, adv=1_000_000, high=20.50, low=19.80,
                     high52=0.0, description="NO FIFTYTWO CORP"),
    # Wide-ranging live name: the stub gate must not catch it. 1.00/40.50 = 2.47%.
    "WIDE": _payload(regular=40.50, quote_last=40.50, adv=500_000, high=41.00, low=40.00,
                     high52=45.00, description="WIDE RANGE CORP", optionable=False),
    # --- one gate each, and only that gate ---------------------------------
    "TQQQ": _payload(regular=90.0, quote_last=90.0, adv=30_000_000, leverage=300.0,
                     high=91.0, low=88.0, high52=100.0, etf=True, description="PROSHARES ULTRAPRO QQQ"),
    "SHIN": _payload(regular=25.0, quote_last=25.0, adv=4_000_000, leverage=-100.0,
                     high=25.5, low=24.5, high52=30.0, etf=True, description="INVERSE FUND"),
    "PENNY": _payload(regular=3.00, quote_last=3.00, adv=10_000_000, high=3.20, low=2.90,
                      high52=4.00, optionable=False, description="PENNY CO"),
    # 200_000 shares clears the share floor; 200_000 x $6 = $1.2M does not clear
    # the $5M dollar floor.
    "THIN": _payload(regular=6.00, quote_last=6.00, adv=200_000, high=6.20, low=5.90,
                     high52=7.00, description="THIN DOLLAR CO"),
    # 50_000 x $200 = $10M clears the dollar floor; 50_000 shares does not clear
    # the 100_000-share sanity floor.
    "TSHR": _payload(regular=200.0, quote_last=200.0, adv=50_000, high=204.0, low=198.0,
                     high52=210.0, description="THIN SHARE CO"),
    # An announced all-cash deal target: pinned 0.33% under its deal price,
    # which is by construction its 52-week high.
    "STUB": _payload(regular=30.05, quote_last=30.05, adv=5_000_000, high=30.10, low=30.00,
                     high52=30.10, description="STUB CO"),
    # No price anywhere: skipped by name, never zeroed.
    "NOPRICE": _payload(adv=1_000_000, high52=10.0, description="UNQUOTABLE CO"),
}

QUALIFIED_ORDER = ["CSX", "SPY", "TIEB", "TIEA", "MPC", "WIDE", "NOHI"]


def _rules_with(**overrides: D) -> Rules:
    """`rules.yml` with one strategy value changed -- still the only source of
    the number, just a different file's worth of it."""
    return Rules(manual=RULES.manual, strategy={**RULES.strategy, **overrides},
                 source=RULES.source)


@pytest.fixture
def verbose_quotes() -> dict[str, VerboseQuote]:
    return {s: VerboseQuote.from_payload(s, b) for s, b in PAYLOADS.items()}


# --- the directory ---------------------------------------------------------
def test_directory_filter_drops_every_non_tradeable_class() -> None:
    kept = parse_directory((FIX / "nasdaqtraded-sample.txt").read_text())
    assert kept == sorted(kept)
    assert kept.count("CSX") == 1             # the duplicate CSX row collapses
    assert "MPC" in kept and "CSX" in kept
    assert "SPY" in kept and "AAL" in kept and "AAAU" in kept   # P, Z and A are major
    for bad in ("TESTQ", "WARRW", "UNITU", "RGHTR", "RIGHTR", "PREFA",
                "NOTEA", "BONDA", "DEPOA", "WHENA", "TOOLONGX", "BRK$A", "IEXCO"):
        assert bad not in kept


def test_the_file_creation_time_trailer_is_not_a_symbol() -> None:
    text = (FIX / "nasdaqtraded-sample.txt").read_text()
    assert "File Creation Time" in text
    assert not any("File Creation" in s for s in parse_directory(text))


def test_a_decoy_page_parses_to_nothing() -> None:
    assert parse_directory((FIX / "nasdaqtraded-decoy.html").read_text()) == []


def _directory(symbols: Iterable[str]) -> str:
    rows = [f"Y|{s}|{s} Test Co - Common Stock|Q|Q|N|100|N|N|{s}|{s}|N" for s in symbols]
    trailer = "File Creation Time: 09052026 22:15|||||||||||"
    return "\n".join([DIRECTORY_HEADER, *rows, trailer]) + "\n"


def _filler(n: int) -> list[str]:
    """`n` distinct three-letter symbols, AAA upward. None collide with the
    quote fixture's names (all of which sort past BZZ or are four+ characters)."""
    return [f"{chr(65 + i // 676)}{chr(65 + (i // 26) % 26)}{chr(65 + i % 26)}" for i in range(n)]


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_fetch_below_the_sanity_floor_raises_and_writes_nothing() -> None:
    body = (FIX / "nasdaqtraded-sample.txt").read_text()
    async with _client(httpx.MockTransport(lambda r: httpx.Response(200, text=body))) as client:
        with pytest.raises(DirectoryUnavailable) as e:
            await fetch_symbols(client)
    assert str(SANITY_FLOOR) in str(e.value)


async def test_a_decoy_page_that_returns_200_is_still_a_failure() -> None:
    """The WAF case: HTTP 200, a body that parses to zero rows. Without the
    floor this installs an empty universe and exits clean."""
    body = (FIX / "nasdaqtraded-decoy.html").read_text()
    async with _client(httpx.MockTransport(lambda r: httpx.Response(200, text=body))) as client:
        with pytest.raises(DirectoryUnavailable):
            await fetch_symbols(client)


async def test_fetch_propagates_an_http_failure_as_directory_unavailable() -> None:
    async with _client(httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        with pytest.raises(DirectoryUnavailable):
            await fetch_symbols(client)


async def test_an_empty_body_is_a_failure_not_an_empty_universe() -> None:
    async with _client(httpx.MockTransport(lambda r: httpx.Response(200, text="   \n"))) as client:
        with pytest.raises(DirectoryUnavailable):
            await fetch_symbols(client)


async def test_a_transport_error_is_a_directory_failure_not_a_traceback() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    async with _client(httpx.MockTransport(boom)) as client:
        with pytest.raises(DirectoryUnavailable):
            await fetch_symbols(client)


async def test_a_plausible_directory_is_returned_sorted_and_deduplicated() -> None:
    body = _directory([*_filler(1200), "CSX", "CSX"])
    async with _client(httpx.MockTransport(lambda r: httpx.Response(200, text=body))) as client:
        syms = await fetch_symbols(client)
    assert len(syms) == 1201 and syms == sorted(set(syms))


# --- the five gates --------------------------------------------------------
def test_gates_in_order(verbose_quotes: dict[str, VerboseQuote]) -> None:
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=500)
    kept = {r.symbol for r in rows}
    assert "CSX" in kept                      # clean stock
    assert "MPC" in kept                      # clean stock
    assert "SPY" in kept                      # 1x fund, and 0/0 high/low is NO DATA
    assert "WIDE" in kept                     # a real range is not a pinned one
    assert "TQQQ" not in kept                 # leverage 300 -> dropped
    assert "SHIN" not in kept                 # leverage -100 (inverse) -> dropped
    assert "PENNY" not in kept                # under the price floor
    assert "THIN" not in kept                 # under the dollar-volume floor
    assert "TSHR" not in kept                 # under the share sanity floor
    assert "STUB" not in kept                 # session range below the stub threshold
    assert counts.skipped == ["NOPRICE"]      # unquotable, named, never zeroed
    assert counts.nodata == 1                 # SPY: kept, and counted
    assert counts.stub_filtered == 1          # STUB, and only STUB
    assert counts.quoted == len(PAYLOADS)


def test_every_gate_threshold_comes_from_the_rules_file() -> None:
    """Not a hard-coded 5.00 / 5_000_000 / 100_000 / 0.75 anywhere: raise each
    floor past its fixture and the name it was passing must fall out."""
    assert RULES.get("manual", "min_share_price_usd") == D("5.00")
    assert RULES.get("manual", "min_avg_daily_dollar_volume") == D("5000000")
    assert RULES.get("manual", "min_avg_daily_volume") == D("100000")
    assert RULES.get("strategy", "min_session_range_pct") == D("0.75")


def test_a_one_times_fund_survives_and_a_leveraged_one_does_not(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    rows, _ = filter_universe(verbose_quotes, RULES, rank_top=500)
    spy = next(r for r in rows if r.symbol == "SPY")
    assert spy.leverage == D("1.0")           # the MULTIPLE in the row, 100 in the payload
    assert spy.session_range_pct is None      # "-" in the rendered table
    assert spy.is_etf is True
    csx = next(r for r in rows if r.symbol == "CSX")
    assert csx.leverage == D("0")             # a single stock, raw 0


def test_the_row_carries_the_regular_close_not_the_after_hours_tick(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    rows, _ = filter_universe(verbose_quotes, RULES, rank_top=500)
    csx = next(r for r in rows if r.symbol == "CSX")
    assert csx.price == D("50.58")            # not 50.89 (quote), not 50.67 (extended)
    assert csx.dollar_vol == D("50.58") * D("12000000")
    assert csx.last_earnings == "2026-07-22"  # the cohort builder's only input
    assert csx.description == "CSX CORP"      # the sector tagger's only input
    assert csx.qualified is True


def test_the_stub_gates_working_is_published_in_the_row(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    """A name that survives just over the threshold must be visible in the
    universe file, not only in hindsight."""
    rows, _ = filter_universe(verbose_quotes, RULES, rank_top=500)
    wide = next(r for r in rows if r.symbol == "WIDE")
    assert wide.session_range_pct is not None
    assert round(wide.session_range_pct, 2) == D("2.47")


def test_ranking_is_gap_then_dollar_volume(verbose_quotes: dict[str, VerboseQuote]) -> None:
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=0)
    assert [r.symbol for r in rows] == QUALIFIED_ORDER
    # TIEA and TIEB share a gap to four decimal places; the tie-break is the
    # larger dollar volume, which is TIEB.
    tie_a = next(r for r in rows if r.symbol == "TIEA")
    tie_b = next(r for r in rows if r.symbol == "TIEB")
    assert tie_a.pct_from_52wk_high == tie_b.pct_from_52wk_high
    assert tie_b.dollar_vol > tie_a.dollar_vol
    # No 52-week high sorts LAST, not first.
    assert rows[-1].symbol == "NOHI" and rows[-1].pct_from_52wk_high == D("999.0")
    assert counts.ranked == counts.qualified and counts.dropped == 0


def test_truncation_keeps_the_nearest_to_the_high_and_counts_the_rest(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=2)
    assert [r.symbol for r in rows] == QUALIFIED_ORDER[:2]
    assert counts.ranked == 2
    assert counts.qualified == len(QUALIFIED_ORDER)
    assert counts.dropped == counts.qualified - counts.ranked


def test_rank_top_zero_means_no_truncation(verbose_quotes: dict[str, VerboseQuote]) -> None:
    _, counts = filter_universe(verbose_quotes, RULES, rank_top=0)
    assert counts.ranked == counts.qualified and counts.dropped == 0


def test_rank_top_defaults_to_the_rules_value(verbose_quotes: dict[str, VerboseQuote]) -> None:
    """The 2026-08-29 sweep typed the working-universe size by hand because the
    rule was unreachable from where it stood. It is a default here."""
    explicit, _ = filter_universe(
        verbose_quotes, RULES, rank_top=int(RULES.get("strategy", "working_universe_size"))
    )
    default, _ = filter_universe(verbose_quotes, RULES)
    assert [r.symbol for r in default] == [r.symbol for r in explicit]


# --- the document ----------------------------------------------------------
def test_rendered_document_passes_the_universe_validator(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=500)
    body = render_universe_md(rows, counts, NOW)
    assert body.splitlines()[0] == "# Fallback universe"
    assert "never a source for order parameters" in body
    assert "the §4 tilts are not applied here" in body
    assert "symbol\tprice\tadv10" in body
    assert body.count("```") == 2
    assert "2026-09-12T11:56:00Z" in body


def test_the_table_carries_the_ten_columns_in_order(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=500)
    lines = render_universe_md(rows, counts, NOW).splitlines()
    fence = lines.index("```")
    header = lines[fence + 1]
    assert header.split("\t") == [
        "symbol", "price", "adv10", "dollar_vol", "pct_from_52wk_high",
        "optionable", "leverage", "last_earnings", "is_etf", "session_range_pct",
    ]
    table = {ln.split("\t")[0]: ln.split("\t") for ln in lines[fence + 2:] if ln != "```"}
    assert len(table) == len(QUALIFIED_ORDER)
    assert all(len(cols) == 10 for cols in table.values())
    assert table["CSX"][1] == "50.5800"                 # price, four places
    assert table["CSX"][7] == "2026-07-22"              # last_earnings
    assert table["SPY"][6] == "1.0"                     # leverage, the MULTIPLE
    assert table["SPY"][8] == "true"                    # is_etf
    assert table["SPY"][9] == "-"                       # no-data range, never 0.00
    assert table["WIDE"][5] == "false"                  # optionable is recorded, not required
    assert table["WIDE"][9] == "2.47"
    assert table["NOHI"][4] == "999.00"


def test_the_counts_line_states_what_was_dropped(
    verbose_quotes: dict[str, VerboseQuote],
) -> None:
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=2)
    body = render_universe_md(rows, counts.model_copy(update={"fetched": 11227}), NOW)
    assert "fetched 11227" in body
    assert f"qualified {counts.qualified}" in body
    assert f"ranked {counts.ranked}" in body
    assert f"dropped {counts.dropped}" in body
    assert "NOPRICE" in body                            # the skipped name, not just a count


# --- the sweep -------------------------------------------------------------
class _StubBroker:
    """Only `quotes_verbose`, which is all `run_weekly_universe` is given."""

    def __init__(
        self,
        quotes: dict[str, VerboseQuote],
        fail_on: int | None = None,
        fail_first: int = 0,
    ) -> None:
        self._quotes = quotes
        self._fail_on = fail_on
        self._fail_first = fail_first
        self.chunks: list[list[str]] = []

    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]:
        self.chunks.append(list(symbols))
        if self._fail_on is not None and len(self.chunks) == self._fail_on:
            raise RuntimeError("schwab said no")
        if len(self.chunks) <= self._fail_first:
            raise RuntimeError("schwab said no")
        return {s: self._quotes[s] for s in symbols if s in self._quotes}


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "engine.db")
    await s.open()
    yield s
    await s.close()


@pytest.fixture
def docs(tmp_path: Path, store: Store) -> DocStore:
    return DocStore(tmp_path / "research", store)


DIRECTORY_SYMBOLS = [s for s in PAYLOADS if len(s) <= 5]   # NOPRICE is 7 chars: not listable


def _old_row() -> dict[str, Any]:
    """Last week's universe, as one stored row."""
    return {"symbol": "OLD", "price": D("10"), "adv10": D("1000000"),
            "dollar_vol": D("10000000"), "pct_from_52wk_high": D("1.0"), "optionable": True,
            "leverage": D("0"), "last_earnings": "2026-08-01", "is_etf": False,
            "session_range_pct": D("1.2"), "description": "OLD CO", "qualified": True}


@pytest.fixture
def directory_body() -> str:
    return _directory([*_filler(1200), *DIRECTORY_SYMBOLS])


@pytest.fixture
async def sweep_ctx(
    store: Store, docs: DocStore, verbose_quotes: dict[str, VerboseQuote], directory_body: str
) -> AsyncIterator[dict[str, Any]]:
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, text=directory_body)))
    async with client:
        yield {
            "broker": _StubBroker(verbose_quotes), "store": store, "docs": docs,
            "rules": RULES, "client": client, "now": NOW,
        }


@pytest.fixture
async def sweep_ctx_unreachable(
    store: Store, docs: DocStore, verbose_quotes: dict[str, VerboseQuote]
) -> AsyncIterator[dict[str, Any]]:
    client = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    async with client:
        yield {
            "broker": _StubBroker(verbose_quotes), "store": store, "docs": docs,
            "rules": RULES, "client": client, "now": NOW,
        }


async def test_the_sweep_writes_the_table_and_the_document(sweep_ctx: dict[str, Any]) -> None:
    counts = await run_weekly_universe(**sweep_ctx)
    rows = await sweep_ctx["store"].universe_rows()
    assert {r["symbol"] for r in rows} and counts.qualified == len(rows)
    assert (await sweep_ctx["docs"].read("universe")).exists is True
    assert await sweep_ctx["store"].universe_asof() == date(2026, 9, 12)
    # Every stored row is a qualified one; the gates ran before the write.
    assert {r["symbol"] for r in rows} == set(QUALIFIED_ORDER)
    assert all(r["qualified"] for r in rows)


async def test_the_sweep_quotes_in_chunks_of_one_fifty(sweep_ctx: dict[str, Any]) -> None:
    counts = await run_weekly_universe(**sweep_ctx)
    broker: _StubBroker = sweep_ctx["broker"]
    # The number itself is the contract -- 150 is the batch Schwab answers
    # reliably -- so it is asserted, not merely derived from itself below.
    assert CHUNK == 150
    assert counts.fetched == 1200 + len(DIRECTORY_SYMBOLS)
    assert counts.chunks == len(broker.chunks) == -(-counts.fetched // CHUNK)
    assert all(len(c) <= CHUNK for c in broker.chunks)
    assert all(len(c) == CHUNK for c in broker.chunks[:-1])
    assert counts.chunks_failed == 0


async def test_a_failed_chunk_is_counted_and_the_sweep_continues(
    sweep_ctx: dict[str, Any],
) -> None:
    """One bad chunk out of nine must not cost the week's universe."""
    sweep_ctx["broker"] = _StubBroker(sweep_ctx["broker"]._quotes, fail_on=2)
    counts = await run_weekly_universe(**sweep_ctx)
    assert counts.chunks_failed == 1
    assert counts.qualified > 0
    assert (await sweep_ctx["docs"].read("universe")).exists is True


async def test_the_document_holds_the_ranked_tier_and_the_table_the_whole_set(
    sweep_ctx: dict[str, Any],
) -> None:
    """The v2 `--emit-qualified-set` flag existed so the ranked 500 could not
    overwrite the scout's 3,196 names. Here the store holds every qualifying
    name and the document holds the ranked head of it."""
    sweep_ctx["rules"] = _rules_with(working_universe_size=D(2))
    counts = await run_weekly_universe(**sweep_ctx)
    rows = await sweep_ctx["store"].universe_rows()
    assert counts.ranked == 2 and counts.dropped == counts.qualified - 2
    assert len(rows) == counts.qualified > 2
    body = (await sweep_ctx["docs"].read("universe")).body
    assert f"\n{QUALIFIED_ORDER[0]}\t" in body
    assert f"\n{QUALIFIED_ORDER[-1]}\t" not in body


async def test_an_unreachable_directory_keeps_last_weeks_universe(
    sweep_ctx_unreachable: dict[str, Any],
) -> None:
    store: Store = sweep_ctx_unreachable["store"]
    await store.replace_universe(date(2026, 9, 5), [_old_row()])
    before = await store.universe_asof()
    with pytest.raises(DirectoryUnavailable):
        await run_weekly_universe(**sweep_ctx_unreachable)
    assert await store.universe_asof() == before
    assert [r["symbol"] for r in await store.universe_rows()] == ["OLD"]
    # And no half-written document either.
    assert (await sweep_ctx_unreachable["docs"].read("universe")).exists is False


async def test_every_chunk_failing_keeps_last_weeks_universe(sweep_ctx: dict[str, Any]) -> None:
    """A token that lapses between the fetch and the first chunk quotes nothing.
    Writing that as a universe stamps today's date over the newest rows and
    hides last week's -- the 2026-08-29 outage through a different door."""
    store: Store = sweep_ctx["store"]
    await store.replace_universe(date(2026, 9, 5), [_old_row()])
    sweep_ctx["broker"] = _StubBroker({})
    with pytest.raises(EmptyUniverse):
        await run_weekly_universe(**sweep_ctx)
    assert await store.universe_asof() == date(2026, 9, 5)
    assert [r["symbol"] for r in await store.universe_rows()] == ["OLD"]
    assert (await sweep_ctx["docs"].read("universe")).exists is False


async def test_too_many_failed_chunks_keeps_last_weeks_universe(
    sweep_ctx: dict[str, Any],
) -> None:
    """A sweep that lost a quarter of the market did not produce a narrower
    universe, it produced an unknown one. Downstream cannot tell the
    difference: the scout's cohort is a join against the universe table, so a
    name that was never quoted reads as a name that does not qualify."""
    store: Store = sweep_ctx["store"]
    await store.replace_universe(date(2026, 9, 5), [_old_row()])
    # 9 chunks; 3 failed is 33% against the 20% ceiling.
    sweep_ctx["broker"] = _StubBroker(sweep_ctx["broker"]._quotes, fail_first=3)
    with pytest.raises(PartialSweep) as exc:
        await run_weekly_universe(**sweep_ctx)
    assert "3 of 9 chunks failed" in str(exc.value)
    assert "33.3%" in str(exc.value)
    # Refused BEFORE any write: last week's rows and asof both stand.
    assert await store.universe_asof() == date(2026, 9, 5)
    assert [r["symbol"] for r in await store.universe_rows()] == ["OLD"]
    assert (await sweep_ctx["docs"].read("universe")).exists is False


async def test_a_sweep_at_the_ceiling_still_installs(sweep_ctx: dict[str, Any]) -> None:
    """The ceiling is a `>` test, not `>=`: exactly at the threshold the sweep
    still counts as a sweep, and the counts line says how partial it was."""
    sweep_ctx["rules"] = _rules_with(universe_max_failed_chunk_pct=D("33.4"))
    sweep_ctx["broker"] = _StubBroker(sweep_ctx["broker"]._quotes, fail_first=3)
    counts = await run_weekly_universe(**sweep_ctx)
    assert counts.chunks_failed == 3 and counts.chunks == 9
    assert (await sweep_ctx["docs"].read("universe")).exists is True


async def test_the_ceiling_comes_from_the_rules_file_not_the_code(
    sweep_ctx: dict[str, Any],
) -> None:
    """Tightening it in rules.yml must refuse a sweep the default allows."""
    sweep_ctx["rules"] = _rules_with(universe_max_failed_chunk_pct=D(5))
    sweep_ctx["broker"] = _StubBroker(sweep_ctx["broker"]._quotes, fail_on=2)  # 1 of 9 = 11%
    with pytest.raises(PartialSweep):
        await run_weekly_universe(**sweep_ctx)


def test_both_abort_reasons_share_the_base_the_caller_catches() -> None:
    """`main.py` catches one type. A new reason to abort must not be able to
    escape it."""
    assert issubclass(DirectoryUnavailable, UniverseUnavailable)
    assert issubclass(EmptyUniverse, UniverseUnavailable)
    assert issubclass(PartialSweep, UniverseUnavailable)


async def test_a_rerun_of_the_same_week_is_idempotent(sweep_ctx: dict[str, Any]) -> None:
    first = await run_weekly_universe(**sweep_ctx)
    sweep_ctx["broker"] = _StubBroker(sweep_ctx["broker"]._quotes)
    second = await run_weekly_universe(**sweep_ctx)
    assert first.qualified == second.qualified
    assert len(await sweep_ctx["store"].universe_rows()) == second.qualified


def test_counts_defaults_are_zero_not_absent() -> None:
    """`filter_universe` cannot know what the fetch did; the sweep fills those
    in. They must read as 0, never as missing keys in the job-run ledger."""
    c = Counts()
    assert c.model_dump() == {
        "fetched": 0, "quoted": 0, "qualified": 0, "stub_filtered": 0, "nodata": 0,
        "ranked": 0, "dropped": 0, "skipped": [], "chunks": 0, "chunks_failed": 0,
    }
