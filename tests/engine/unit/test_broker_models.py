from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tc.broker.models import AccountSnapshot, MarketWindow, OrderRow, Quote

ACCOUNT = {
    "securitiesAccount": {
        "type": "CASH",
        "isClosingOnlyRestricted": False,
        "positions": [
            {
                "instrument": {"symbol": "AMH", "assetType": "EQUITY"},
                "longQuantity": 29.0, "shortQuantity": 0.0, "settledLongQuantity": 29.0,
                "averagePrice": 34.79, "marketValue": 990.64, "currentDayProfitLoss": -11.6,
            }
        ],
        "currentBalances": {
            "liquidationValue": 3781.06, "cashAvailableForTrading": 2393.57,
            "unsettledCash": 0.0, "cashBalance": 2393.57, "cashCall": 0.0,
        },
        "initialBalances": {"liquidationValue": 999999.0},
    }
}

ORDER = {
    "orderId": 1000000000001, "status": "WORKING", "orderType": "STOP_LIMIT", "duration": "GOOD_TILL_CANCEL",
    "enteredTime": "2026-08-24T14:32:11+0000", "quantity": 29.0, "filledQuantity": 0.0,
    "price": 30.40, "stopPrice": 32.01,
    "orderLegCollection": [{"instruction": "SELL", "quantity": 29.0,
                            "instrument": {"symbol": "AMH", "assetType": "EQUITY"}}],
}

QUOTE = {"AMH": {"quote": {"lastPrice": 34.16, "bidPrice": 34.15, "askPrice": 34.17,
                           "quoteTime": 1756838400000},
                 "reference": {"description": "AMERICAN HOMES 4 RENT"}}}

HOURS_OPEN = {"equity": {"EQ": {"date": "2026-09-02", "isOpen": True, "sessionHours": {
    "regularMarket": [{"start": "2026-09-02T09:30:00-04:00", "end": "2026-09-02T16:00:00-04:00"}]}}}}
HOURS_CLOSED = {"equity": {"equity": {"date": "2026-09-07", "isOpen": False}}}


def test_account_reads_current_balances_only() -> None:
    now = datetime(2026, 9, 2, 16, 5, tzinfo=UTC)
    a = AccountSnapshot.from_payload("HASH", ACCOUNT, now)
    assert a.liquidation_value == Decimal("3781.06")          # not 999999 from initialBalances
    assert a.reserve_cash == Decimal("2393.57")
    assert a.positions[0].quantity == 29
    assert a.positions[0].lifetime_pl == Decimal("-18.27")
    assert a.positions[0].day_pl == Decimal("-11.60")
    assert a.is_closing_only_restricted is False


def test_order_row_resting_stop() -> None:
    o = OrderRow.from_payload(ORDER)
    assert o.symbol == "AMH" and o.is_resting_stop
    assert o.stop_price == Decimal("32.01") and o.price == Decimal("30.40")
    assert o.entered_at.tzinfo is not None


def test_quote_time_is_datetime() -> None:
    q = Quote.from_payload("AMH", QUOTE["AMH"])
    assert q.last == Decimal("34.16")
    assert q.quote_time == datetime.fromtimestamp(1756838400, tz=UTC)
    assert "AMERICAN HOMES" in q.description


def test_market_window_open_and_closed_shapes() -> None:
    w = MarketWindow.from_payload(date(2026, 9, 2), HOURS_OPEN)
    assert w.is_trading_day and w.rth_start is not None and w.rth_start.hour == 9
    c = MarketWindow.from_payload(date(2026, 9, 7), HOURS_CLOSED)
    assert not c.is_trading_day and c.rth_start is None


def test_null_average_price_is_unknown() -> None:
    now = datetime(2026, 9, 2, 16, 5, tzinfo=UTC)
    payload = {
        "securitiesAccount": {
            "type": "CASH",
            "isClosingOnlyRestricted": False,
            "positions": [
                {
                    "instrument": {"symbol": "AMH", "assetType": "EQUITY"},
                    "longQuantity": 29.0, "shortQuantity": 0.0, "settledLongQuantity": 29.0,
                    "averagePrice": None, "marketValue": 990.64, "currentDayProfitLoss": -11.6,
                }
            ],
            "currentBalances": {
                "liquidationValue": 3781.06, "cashAvailableForTrading": 2393.57,
                "unsettledCash": 0.0, "cashBalance": 2393.57, "cashCall": 0.0,
            },
        }
    }
    # A missing cost basis is unknown, not zero: zero would report the whole
    # market value as lifetime gain, which is a fabricated number in exactly
    # the field a §7.3 honest report reads from.
    a = AccountSnapshot.from_payload("HASH", payload, now)
    assert a.positions[0].average_price is None
    assert a.positions[0].lifetime_pl is None


def test_quote_without_quote_block_raises() -> None:
    with pytest.raises(ValueError, match="no quote payload for BAD"):
        Quote.from_payload("BAD", {"reference": {"description": "no data"}})
