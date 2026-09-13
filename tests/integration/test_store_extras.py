from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investlab.contracts import AccountState, AssetClass, Lot, Position
from investlab.store import LedgerStore, TradeRecord


@pytest.fixture
def store():
    s = LedgerStore("deca")
    s.init(Decimal("100000"), date(2026, 9, 8))
    return s


def _trade(session, action, qty, price, cash_after):
    return TradeRecord(
        session=session,
        recorded_at="t",
        symbol="ORCL",
        action=action,
        quantity=qty,
        price=Decimal(price),
        commission=Decimal("5"),
        fees=Decimal("0"),
        asset_class="stock",
        cash_after=Decimal(cash_after),
    )


def test_stops_round_trip(store):
    book = store.load_book()
    book["positions"] = [
        {
            "symbol": "ORCL",
            "asset_class": "stock",
            "lots": [
                {"quantity": 142, "price": "150.28", "commission": "5", "opened": "2026-09-11"}
            ],
        }
    ]
    store.save_book(book)
    assert store.entry_stops() == {"ORCL": None}
    assert store.set_stop("ORCL", Decimal("147.68")) == 1
    assert store.entry_stops() == {"ORCL": Decimal("147.68")}
    assert store.set_stop("NOPE", Decimal("1")) == 0


def test_holdings_replay_from_the_trade_log(store):
    store.append_trade(_trade(date(2026, 9, 11), "buy", 142, "150.28", "78655.24"))
    store.append_trade(_trade(date(2026, 9, 15), "sell", 50, "160.00", "86650.24"))
    assert store.holdings_at(date(2026, 9, 10))[:2] == (Decimal("100000"), {})
    cash, shares, classes = store.holdings_at(date(2026, 9, 11))
    assert cash == Decimal("78655.24") and shares == {"ORCL": 142}
    assert classes["ORCL"] is AssetClass.STOCK
    cash, shares, _ = store.holdings_at(date(2026, 9, 15))
    assert cash == Decimal("86650.24") and shares == {"ORCL": 92}


def test_risk_reviews_need_a_note(store):
    with pytest.raises(ValueError):
        store.add_risk_review(date(2026, 9, 14), "  ", "t")
    store.add_risk_review(date(2026, 9, 14), "my own words", "t")
    assert store.risk_reviews() == [(date(2026, 9, 14), "my own words")]


def test_ledger_report_shows_entry_stops(store):
    book = store.load_book()
    book["cash"] = "78655.24"
    book["positions"] = [
        {
            "symbol": "ORCL",
            "asset_class": "stock",
            "lots": [
                {
                    "quantity": 142,
                    "price": "150.28",
                    "commission": "5",
                    "opened": "2026-09-11",
                    "stop": "147.68",
                }
            ],
        }
    ]
    store.save_book(book)
    account = AccountState(
        date(2026, 9, 11),
        Decimal("78655.24"),
        (
            Position(
                "ORCL",
                AssetClass.STOCK,
                (Lot("ORCL", 142, Decimal("150.28"), Decimal("5"), date(2026, 9, 11)),),
            ),
        ),
        {"ORCL": Decimal("150.28")},
    )
    text = store.render_summary(account)
    assert "Entry stop" in text and "$147.68" in text
