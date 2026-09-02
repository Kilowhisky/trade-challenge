import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tc.broker.client import BrokerUnauthorized
from tc.broker.fake import FakeBroker

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)


async def test_reads_all_fixtures() -> None:
    b = FakeBroker(FIX, NOW)
    hashes = await b.account_hashes()
    assert hashes == ["HASH_REDACTED"]
    a = await b.account(hashes[0])
    assert a.liquidation_value == Decimal("3781.06") and a.read_at == NOW
    orders = await b.orders(hashes[0], NOW, NOW)
    assert orders[0].is_resting_stop
    q = await b.quotes(["AMH"])
    assert q["AMH"].last == Decimal("34.16")
    w = await b.market_window(date(2026, 9, 2))
    assert w.is_trading_day
    bars = await b.daily_bars("AMH", 3)
    assert [x.close for x in bars][-1] == Decimal("34.20")
    assert b.now() == NOW


async def test_unauthorized_marker(tmp_path: Path) -> None:
    (tmp_path / "unauthorized").touch()
    b = FakeBroker(tmp_path, NOW)
    with pytest.raises(BrokerUnauthorized):
        await b.account_hashes()


async def test_daily_bars_rejects_non_positive_days() -> None:
    b = FakeBroker(FIX, NOW)
    with pytest.raises(ValueError, match="days must be positive"):
        await b.daily_bars("AMH", 0)
    with pytest.raises(ValueError, match="days must be positive"):
        await b.daily_bars("AMH", -1)


async def test_quotes_skips_entries_without_a_quote_block(tmp_path: Path) -> None:
    """Schwab returns an entry with no "quote" for an unknown or halted symbol.

    The real client filters those out; the fake used to hand them to
    Quote.from_payload and raise, so a fixture recorded on a day with a halted
    name would fail replay where production would not.
    """
    (tmp_path / "quotes.json").write_text(
        json.dumps(
            {
                "AMH": {"quote": {"lastPrice": 34.16}, "reference": {"description": "AMH"}},
                "HALTED": {"invalidSymbols": ["HALTED"]},
            }
        )
    )
    b = FakeBroker(tmp_path, NOW)
    q = await b.quotes(["AMH", "HALTED", "MISSING"])
    assert list(q) == ["AMH"]
    assert q["AMH"].last == Decimal("34.16")
