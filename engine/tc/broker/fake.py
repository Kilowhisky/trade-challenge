"""Fixture-backed Broker for tests and paper mode, plus the redacting recorder."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tc.broker.client import (
    Broker,
    BrokerError,
    BrokerUnauthorized,
    ContractType,
    MoverDirection,
    SchwabBroker,
    _raise_for,
)
from tc.broker.models import (
    AccountSnapshot,
    DailyBar,
    Expiration,
    Instrument,
    MarketWindow,
    Mover,
    OptionChainView,
    OrderRow,
    Quote,
    VerboseQuote,
)

HASH_RE = re.compile(r"\b[0-9A-F]{32,}\b")

# How much chain the recorder captures. Not a rule parameter -- a fixture
# size bound, wide enough to hold both sides of the money at the near
# expiries a caller screens.
CHAIN_STRIKES = 10
CHAIN_WINDOW_DAYS = 60


class FakeBroker:
    def __init__(self, fixture_dir: Path, frozen_now: datetime) -> None:
        self.dir = fixture_dir
        self._now = frozen_now

    def _load(self, name: str) -> Any:
        if (self.dir / "unauthorized").exists():
            raise BrokerUnauthorized("fixture: unauthorized")
        return json.loads((self.dir / name).read_text())

    def now(self) -> datetime:
        return self._now

    async def account_hashes(self) -> list[str]:
        self._load("account.json")
        return ["HASH_REDACTED"]

    async def account(self, account_hash: str) -> AccountSnapshot:
        return AccountSnapshot.from_payload(account_hash, self._load("account.json"), self._now)

    async def orders(
        self, account_hash: str, from_dt: datetime, to_dt: datetime
    ) -> list[OrderRow]:
        return [OrderRow.from_payload(o) for o in self._load("orders.json")]

    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        data = self._load("quotes.json")
        # Same filter as the real client: Schwab returns an entry with an
        # "invalidSymbols"-style body and no "quote" block for an unknown or
        # halted symbol, and the fake must not raise where the real one skips.
        return {
            s: Quote.from_payload(s, data[s])
            for s in symbols
            if s in data and "quote" in data[s]
        }

    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]:
        """One fixture holds the whole recorded sweep; a call takes its slice.

        Filtering rather than raising mirrors the real read: Schwab simply
        omits a symbol it does not know, and a fake that raised instead would
        make an unknown ticker a crash in tests and a skip in production.
        """
        data = self._load("quotes-verbose.json")
        return {s: VerboseQuote.from_payload(s, data[s]) for s in symbols if s in data}

    # Three reads below take arguments this fake does not use: `option_chain`
    # ignores from_date/to_date/strike_count/contract_type, `instruments`
    # ignores projection, `movers` ignores direction. That is deliberate and
    # it is the same reason each time -- a fixture is ONE already-served
    # response, recorded under one set of arguments. Re-deriving the answer
    # here would test this filter rather than the caller, and would diverge
    # from Schwab the first time its filtering differed from ours in any
    # detail. What the fixture holds is what a call under those arguments
    # returned; a test that needs a different slice records a different
    # fixture. The file name carries the only argument that selects between
    # recordings: the symbol, the query, the index.

    async def option_chain(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        strike_count: int,
        contract_type: ContractType,
    ) -> OptionChainView:
        return OptionChainView.from_payload(symbol, self._load(f"chain-{symbol}.json"))

    async def expiration_chain(self, symbol: str) -> list[Expiration]:
        data = self._load(f"expirations-{symbol}.json")
        return [Expiration.from_payload(e) for e in data.get("expirationList", [])]

    async def instruments(self, query: str, projection: str) -> list[Instrument]:
        data = self._load(f"instruments-{query}.json")
        return [Instrument.from_payload(i) for i in data.get("instruments", [])]

    async def movers(self, index: str, direction: MoverDirection) -> list[Mover]:
        data = self._load(f"movers-{index}.json")
        return [Mover.from_payload(m) for m in data.get("screeners", [])]

    async def market_window(self, d: date) -> MarketWindow:
        return MarketWindow.from_payload(d, self._load(f"hours-{d.isoformat()}.json"))

    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
        if days <= 0:
            raise ValueError("days must be positive")
        candles = self._load(f"bars-{symbol}.json").get("candles", [])
        return [DailyBar.from_payload(c) for c in candles][-days:]


def redact(obj: Any) -> Any:
    """Strip identifiers before a payload is written anywhere tracked."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in {"accountNumber", "accountId"}:
                out[k] = "REDACTED"
            elif k == "hashValue":
                out[k] = "HASH_REDACTED"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        return HASH_RE.sub("HASH_REDACTED", obj)
    return obj


def _check_shape(name: str, payload: dict[str, Any] | list[Any]) -> None:
    """Reject anything that is not the payload the fixture name promises."""
    ok: bool
    if name == "account.json":
        ok = isinstance(payload, dict) and "securitiesAccount" in payload
    elif name == "orders.json":
        ok = isinstance(payload, list)
    elif name == "quotes.json":
        ok = isinstance(payload, dict)
    elif name == "quotes-verbose.json":
        ok = isinstance(payload, dict)
    elif name.startswith("hours-"):
        ok = isinstance(payload, dict) and "equity" in payload
    elif name.startswith("chain-"):
        ok = isinstance(payload, dict) and "callExpDateMap" in payload
    elif name.startswith("expirations-"):
        ok = isinstance(payload, dict) and "expirationList" in payload
    elif name.startswith("instruments-"):
        ok = isinstance(payload, dict) and "instruments" in payload
    elif name.startswith("movers-"):
        ok = isinstance(payload, dict) and "screeners" in payload
    else:  # bars-<symbol>.json
        ok = isinstance(payload, dict) and "candles" in payload
    if not ok:
        raise BrokerError(f"unexpected {name} payload")


class Recorder:
    """Capture real payloads once, redacted, so tests replay reality."""

    def __init__(self, broker: SchwabBroker, out_dir: Path) -> None:
        self.broker = broker
        self.out = out_dir

    async def record(self, symbols: Sequence[str], d: date) -> None:
        c = self.broker._c()
        h = (await self.broker.account_hashes())[0]
        # Every response goes through _raise_for, not .json(): a 401 or a 500
        # body parses as perfectly good JSON, and writing it produces a fixture
        # that "replays reality" as an error page. Shapes are checked before
        # anything is written, and nothing is written unless all of them pass —
        # a half-recorded fixture directory is worse than none.
        raw = {
            "account.json": _raise_for(
                await c.get_account(h, fields=[c.Account.Fields.POSITIONS])
            ),
            "orders.json": _raise_for(await c.get_orders_for_account(h)),
            "quotes.json": _raise_for(await c.get_quotes(list(symbols))),
            f"hours-{d.isoformat()}.json": _raise_for(
                await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d)
            ),
            "quotes-verbose.json": _raise_for(
                await c.get_quotes(
                    list(symbols),
                    fields=[
                        c.Quote.Fields.QUOTE,
                        c.Quote.Fields.FUNDAMENTAL,
                        c.Quote.Fields.REFERENCE,
                        c.Quote.Fields.REGULAR,
                    ],
                )
            ),
            "movers-EQUITY_ALL.json": _raise_for(
                await c.get_movers(
                    c.Movers.Index.EQUITY_ALL,
                    sort_order=c.Movers.SortOrder.PERCENT_CHANGE_UP,
                )
            ),
        }
        for s in symbols:
            raw[f"bars-{s}.json"] = _raise_for(await c.get_price_history_every_day(s))
            # Bounded, not the whole chain: an unbounded chain for a liquid
            # name is megabytes of JSON, and these fixtures are committed to a
            # public repo. The window matches what callers actually ask for.
            raw[f"chain-{s}.json"] = _raise_for(
                await c.get_option_chain(
                    s,
                    strike_count=CHAIN_STRIKES,
                    from_date=d,
                    to_date=d + timedelta(days=CHAIN_WINDOW_DAYS),
                )
            )
            raw[f"expirations-{s}.json"] = _raise_for(
                await c.get_option_expiration_chain(s)
            )
            raw[f"instruments-{s}.json"] = _raise_for(
                await c.get_instruments(s, c.Instrument.Projection.SYMBOL_SEARCH)
            )
        for name, payload in raw.items():
            _check_shape(name, payload)
        self.out.mkdir(parents=True, exist_ok=True)
        for name, payload in raw.items():
            (self.out / name).write_text(json.dumps(redact(payload), indent=1))


if TYPE_CHECKING:
    # Same static conformance guard as client.py: the fake is only useful as a
    # stand-in if it still satisfies the protocol the real broker does.
    _fake_conforms: Broker = cast(FakeBroker, None)
