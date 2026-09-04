"""tick.md §B/§C as code: one sweep, one ledger row, seven watches in priority
order. The tick is the only loop that runs unattended ~78 times a session, so
its failure modes are asserted here rather than discovered in production —
BLIND is a state it writes, never an exception it raises.

Fixture timing: every tick fixture is pinned to 2026-09-04 (a Friday, so the
Monday-only `C` reminder stays out of the clean-tick flags), and
``fixtures/broker/quotes.json`` carries a quoteTime of ``NOW - 1 minute`` so a
clean RTH tick reads a fresh quote. ``quotes-stale.json`` is the same payload
with a quoteTime weeks earlier, which is what STALE is asserted against.
"""

import json
import shutil
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D  # noqa: N817 -- brevity in a Decimal-heavy fixture table
from pathlib import Path

import pytest

from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, MarketWindow, Position, Quote
from tc.loops.clocks import ClockAlert
from tc.loops.reconcile import BookView
from tc.loops.session import seed_hwm
from tc.loops.tick import (
    STALE_QUOTE_AGE,
    TickResult,
    Trip,
    evaluate_watches,
    run_tick,
)
from tc.rules.model import Rules
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")
RESERVE = D("900.00")
ORDERS_FROM = date(2026, 8, 14)

TODAY = date(2026, 9, 4)  # Friday
NOW = datetime(2026, 9, 4, 17, 31, tzinfo=UTC)  # 13:31 ET -> RTH
PRE_NOW = datetime(2026, 9, 4, 13, 0, tzinfo=UTC)  # 09:00 ET -> PRE


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


def _weekdays_between(a: date, b: date) -> int:
    n = 0
    d = a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


class CountingBroker(FakeBroker):
    """Counts quote reads: tick.md §B4 skips B4 entirely outside RTH and
    re-fetches exactly once when a quote is stale. Both are call-count facts,
    invisible in the returned row."""

    def __init__(self, fixture_dir: Path, frozen_now: datetime) -> None:
        super().__init__(fixture_dir, frozen_now)
        self.quote_calls = 0

    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        self.quote_calls += 1
        return await super().quotes(symbols)


def _fx(tmp_path: Path, edits: dict[str, str] | None = None) -> Path:
    d = tmp_path / "fx"
    if not d.exists():
        shutil.copytree(FIX, d)
    for name, text in (edits or {}).items():
        (d / name).write_text(text)
    return d


def _account_edit(d: Path, **balances: float) -> None:
    payload = json.loads((d / "account.json").read_text())
    payload["securitiesAccount"]["currentBalances"].update(balances)
    (d / "account.json").write_text(json.dumps(payload))


def _restrict(d: Path) -> None:
    payload = json.loads((d / "account.json").read_text())
    payload["securitiesAccount"]["isClosingOnlyRestricted"] = True
    (d / "account.json").write_text(json.dumps(payload))


async def _window(on: date = TODAY) -> MarketWindow:
    """Always read the hours fixture from the pristine FIX directory — a test
    that edits its tmp copy (or drops an `unauthorized` marker in it) must
    still be able to state what the market window was."""
    return await FakeBroker(FIX, NOW).market_window(on)


async def _tick(
    store: Store,
    broker: FakeBroker,
    *,
    now: datetime = NOW,
    window: MarketWindow | None = None,
    leveraged: set[str] | None = None,
) -> TickResult:
    return await run_tick(
        broker=broker,
        store=store,
        rules=RULES,
        reserve=RESERVE,
        account_hash="HASH_REDACTED",
        now=now,
        window=window,
        orders_from=ORDERS_FROM,
        leveraged=leveraged or set(),
        trading_days_between=_weekdays_between,
    )


async def _seed(store: Store, hwm: str = "4000.00") -> None:
    await seed_hwm(store, D(hwm), date(2026, 9, 3), RESERVE)


# --- the clean tick ---------------------------------------------------------

async def test_clean_rth_tick_writes_exactly_one_row(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert res.trips == []
    row = res.row
    assert row.state == "RTH" and row.flags == "-" and row.level == "OK"
    assert row.positions == 1 and row.stops == 1 and row.orders == 0
    assert row.account_value == D("3781.06")
    assert row.comp_capital == D("3781.06") - RESERVE
    assert row.hwm == D("4000.00") and row.drawdown_pct == D("-5.47")
    assert row.reserve == D("2393.57") and row.settled == D("2393.57")
    assert row.at_et == "2026-09-04 13:31"
    assert [r.at_et for r in await store.ticks_for("2026-09-04")] == ["2026-09-04 13:31"]
    assert res.view is not None and res.view.naked == []


# --- BLIND ------------------------------------------------------------------

async def test_blind_when_the_token_is_dead(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    (d / "unauthorized").touch()
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert res.view is None
    assert res.trips == [Trip(watch=0, name="blind", detail="token dead or absent")]
    assert res.row.state == "BLIND" and res.row.flags == "B"
    assert "BLIND: token dead or absent" in res.row.note
    assert res.row.account_value == D("0") and res.row.hwm == D("0")
    assert len(await store.ticks_for("2026-09-04")) == 1


# --- watches ----------------------------------------------------------------

async def test_naked_position_trips_watch_4(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path, {"orders.json": "[]"})
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [4]
    assert "AMH" in res.trips[0].detail
    assert res.row.flags == "N" and res.row.stops == 0


async def test_partial_fill_trips_watch_5(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path, {"orders.json": (FIX / "orders-partial.json").read_text()})
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [5]
    assert res.row.flags == "P"


async def test_restriction_is_evaluated_first(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path, {"orders.json": "[]"})
    _restrict(d)
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [1, 4]
    assert res.row.flags == "RN"


async def test_reserve_breach_trips_watch_2(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    _account_edit(d, cashBalance=100.0)
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [2]
    assert res.row.flags == "V" and res.row.reserve == D("100.00")


async def test_drawdown_halt_trips_watch_3(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    await _seed(store, "10000.00")
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [3]
    assert res.row.flags == "H" and res.row.level == "HALT"


async def test_stop_fill_trips_watch_6(store: Store, tmp_path: Path) -> None:
    """A symbol in the prior snapshot and absent now. The prior read must
    happen BEFORE reconcile writes this tick's snapshot, or the comparison is
    the new snapshot against itself and watch 6 can never trip."""
    d = _fx(tmp_path)
    await _seed(store)
    await store.record_account(
        AccountSnapshot(
            account_hash="HASH_REDACTED", read_at=NOW - timedelta(minutes=15),
            liquidation_value=D("3781.06"), cash_available_for_trading=D("2393.57"),
            unsettled_cash=D("0"), cash_balance=D("2393.57"), cash_call=D("0"),
            is_closing_only_restricted=False,
            positions=[
                Position(symbol="CSX", asset_type="EQUITY", quantity=10,
                         average_price=D("30.00"), market_value=D("300.00"),
                         day_pl=D("0"), settled_quantity=10),
            ],
        )
    )
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [6]
    assert "CSX" in res.trips[0].detail and res.row.flags == "X"


async def test_clock_alert_trips_watch_7(store: Store, tmp_path: Path) -> None:
    """§3.5's leveraged hold clock, reached through run_clocks: the fixture
    position is renamed to a symbol the caller declares leveraged."""
    payload = json.loads((FIX / "account.json").read_text())
    payload["securitiesAccount"]["positions"][0]["instrument"]["symbol"] = "TQQQ"
    quotes = json.loads((FIX / "quotes.json").read_text())
    quotes["TQQQ"] = quotes.pop("AMH")
    d = _fx(tmp_path, {"account.json": json.dumps(payload), "orders.json": "[]",
                       "quotes.json": json.dumps(quotes)})
    await _seed(store)
    await store.record_account(
        AccountSnapshot(
            account_hash="HASH_REDACTED",
            read_at=datetime(2026, 8, 24, 16, 0, tzinfo=UTC),
            liquidation_value=D("3781.06"), cash_available_for_trading=D("2393.57"),
            unsettled_cash=D("0"), cash_balance=D("2393.57"), cash_call=D("0"),
            is_closing_only_restricted=False,
            positions=[
                Position(symbol="TQQQ", asset_type="EQUITY", quantity=29,
                         average_price=D("30.00"), market_value=D("900.00"),
                         day_pl=D("0"), settled_quantity=29),
            ],
        )
    )
    res = await _tick(store, FakeBroker(d, NOW), window=await _window(), leveraged={"TQQQ"})
    assert [t.watch for t in res.trips] == [4, 7]
    assert res.row.flags == "NK"
    assert "TQQQ" in res.trips[1].detail


# --- phase, quotes, staleness ------------------------------------------------

async def test_pre_phase_reads_no_quotes(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    await _seed(store)
    b = CountingBroker(d, PRE_NOW)
    res = await _tick(store, b, now=PRE_NOW, window=await _window())
    assert res.row.state == "PRE" and res.row.flags == "-"
    assert b.quote_calls == 0


async def test_stale_quote_is_refetched_once_then_marks_the_tick(
    store: Store, tmp_path: Path
) -> None:
    d = _fx(tmp_path, {"quotes.json": (FIX / "quotes-stale.json").read_text()})
    await _seed(store)
    b = CountingBroker(d, NOW)
    res = await _tick(store, b, window=await _window())
    assert b.quote_calls == 2
    assert res.row.state == "STALE" and "S" in res.row.flags
    assert "STALE" in res.row.note
    # Watch 3 is account-value arithmetic and still runs on a STALE tick.
    assert res.row.level == "OK" and res.row.drawdown_pct == D("-5.47")


async def test_stale_threshold_is_the_documented_few_minutes() -> None:
    assert STALE_QUOTE_AGE == timedelta(minutes=3)


async def test_closed_day_is_not_an_rth_tick(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    await _seed(store)
    b = CountingBroker(d, NOW)
    res = await _tick(store, b, window=await _window(date(2026, 9, 7)))
    assert res.row.state == "POST" and b.quote_calls == 0


# --- flags that are not watches ---------------------------------------------

async def test_missing_window_falls_back_and_flags_f_and_monday_flags_c(
    store: Store, tmp_path: Path
) -> None:
    """No market-hours window: fall back to the weekday calendar (`F`) rather
    than skipping the sweep. `C` is the deferred watch-8 reminder, emitted on
    the first trading day of the week. Taken PRE so no quote read (and so no
    STALE letter) is involved in the flag string under test."""
    d = _fx(tmp_path)
    await _seed(store)
    monday = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)  # 09:00 ET, a Monday
    res = await _tick(store, FakeBroker(d, monday), now=monday, window=None)
    assert res.row.state == "PRE"
    assert res.row.flags == "FC"


# --- the unseeded mark -------------------------------------------------------

async def test_no_session_status_notes_and_trips_zero(store: Store, tmp_path: Path) -> None:
    d = _fx(tmp_path)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert [t.watch for t in res.trips] == [0]
    assert "seed" in res.row.note
    assert res.row.hwm == D("0") and res.row.drawdown_pct == D("0")
    assert res.row.level == "OK" and res.row.state == "RTH"


# --- open entries ------------------------------------------------------------

async def test_pending_activation_buy_counts_as_an_open_entry(
    store: Store, tmp_path: Path
) -> None:
    """reconcile's open_entries tests the shared RESTING set, not a narrower
    literal — a PENDING_ACTIVATION buy is a resting entry that will fill
    unattended, which is exactly what §4.2 exists to notice."""
    orders = json.loads((FIX / "orders.json").read_text())
    orders.append(
        {
            "orderId": 1000000000002,
            "status": "PENDING_ACTIVATION",
            "orderType": "LIMIT",
            "duration": "DAY",
            "enteredTime": "2026-09-04T13:30:00+0000",
            "quantity": 5.0,
            "filledQuantity": 0.0,
            "price": 30.0,
            "orderLegCollection": [
                {
                    "instruction": "BUY",
                    "quantity": 5.0,
                    "instrument": {"symbol": "AMH", "assetType": "EQUITY"},
                }
            ],
        }
    )
    d = _fx(tmp_path, {"orders.json": json.dumps(orders)})
    await _seed(store)
    res = await _tick(store, FakeBroker(d, NOW), window=await _window())
    assert res.row.orders == 1 and res.row.flags == "-"


# --- evaluate_watches, with no I/O at all ------------------------------------

def _view(*, restricted: bool = False, cash: str = "2393.57", naked: list[str] | None = None) -> BookView:
    account = AccountSnapshot(
        account_hash="HASH_REDACTED", read_at=NOW, liquidation_value=D("3781.06"),
        cash_available_for_trading=D(cash), unsettled_cash=D("0"), cash_balance=D(cash),
        cash_call=D("0"), is_closing_only_restricted=restricted,
        positions=[
            Position(symbol="AMH", asset_type="EQUITY", quantity=29, average_price=D("34.79"),
                     market_value=D("990.64"), day_pl=D("0"), settled_quantity=29),
        ],
    )
    return BookView(
        account=account, orders=[], resting_stops={}, naked=naked or [], partial=[],
        orphaned_stops=[], open_entries=[], restricted=restricted, read_at=NOW,
    )


def test_evaluate_watches_is_pure_and_ordered() -> None:
    """Every watch tripping at once: the returned order IS the §C priority
    order, which is what the escalation in tick.md §E reads top-down."""
    trips = evaluate_watches(
        _view(restricted=True, cash="10.00", naked=["AMH"]),
        {"CSX": 10},
        D("10000.00"),
        RULES,
        RESERVE,
        [ClockAlert(symbol="AMH   261016P00030000", kind="option_close", detail="5 DTE")],
    )
    assert [t.watch for t in trips] == [1, 2, 3, 4, 6, 7]


def test_evaluate_watches_skips_drawdown_when_the_mark_is_unseeded() -> None:
    """hwm 0 is "not recorded", not "a mark of zero" — testing against it
    would halt every tick on an account that has never closed a session."""
    assert evaluate_watches(_view(), {}, D("0"), RULES, RESERVE, []) == []
