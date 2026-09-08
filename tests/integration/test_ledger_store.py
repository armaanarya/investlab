"""The per-competition ledger, tracked in git.

The separation is the point: DECA and Wharton have different capital, rules and
asset-class definitions, so a shared book could let a Wharton holding appear to
satisfy a DECA diversification requirement.
"""

from datetime import date
from decimal import Decimal

import pytest

from investlab.contracts import AssetClass
from investlab.store import LedgerStore, TradeRecord


@pytest.fixture
def root(tmp_path):
    return tmp_path / "ledger"


def _record(symbol="AAPL", qty=30, price="100", klass="stock", cash="97000"):
    return TradeRecord(
        session=date(2026, 9, 8),
        recorded_at="2026-09-08T09:30:00-04:00",
        symbol=symbol,
        action="buy",
        quantity=qty,
        price=Decimal(price),
        commission=Decimal("5"),
        fees=Decimal("0"),
        asset_class=klass,
        cash_after=Decimal(cash),
    )


def test_profiles_get_separate_directories(root):
    deca = LedgerStore("deca", root)
    wharton = LedgerStore("wharton", root)
    assert deca.dir != wharton.dir
    assert deca.portfolio_path.parent.name == "deca"
    assert wharton.portfolio_path.parent.name == "wharton"


def test_an_unknown_profile_is_rejected(root):
    with pytest.raises(ValueError, match="unknown profile"):
        LedgerStore("both", root)


def test_a_trade_in_one_ledger_does_not_appear_in_the_other(root):
    deca, wharton = LedgerStore("deca", root), LedgerStore("wharton", root)
    deca.init(Decimal("100000"), date(2026, 9, 8))
    wharton.init(Decimal("500000"), date(2026, 9, 8))

    deca.append_trade(_record())

    assert len(deca.trades()) == 1
    assert wharton.trades() == []


def test_init_records_starting_capital_separately(root):
    deca, wharton = LedgerStore("deca", root), LedgerStore("wharton", root)
    deca.init(Decimal("100000"), date(2026, 9, 8))
    wharton.init(Decimal("500000"), date(2026, 9, 8))
    assert Decimal(deca.load_book()["starting_cash"]) == Decimal("100000")
    assert Decimal(wharton.load_book()["starting_cash"]) == Decimal("500000")


def test_trades_are_append_only_and_ordered(root):
    store = LedgerStore("deca", root)
    store.init(Decimal("100000"), date(2026, 9, 8))
    store.append_trade(_record(symbol="AAPL"))
    store.append_trade(_record(symbol="MSFT"))
    rows = store.trades()
    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]


def test_summary_reports_bond_leg_unmet_when_only_a_bond_etf_is_held(root):
    """DECA classifies every ETF as a stock, bond ETFs included, so holding AGG
    must not show the bond requirement as satisfied."""
    store = LedgerStore("deca", root)
    store.init(Decimal("100000"), date(2026, 9, 8))
    book = store.load_book()
    book["positions"] = [
        {
            "symbol": "AGG",
            "asset_class": AssetClass.ETF.value,
            "lots": [
                {"quantity": 200, "price": "99.00", "commission": "5", "opened": "2026-09-08"}
            ],
        }
    ]
    book["cash"] = "80195.00"
    store.save_book(book)

    text = store.render_summary(store.account())
    bond_row = next(line for line in text.splitlines() if line.startswith("| bonds "))
    assert "NOT MET" in bond_row
    etf_row = next(line for line in text.splitlines() if line.startswith("| ETFs "))
    assert "counts toward stocks" in etf_row


def test_summary_is_regenerated_not_appended(root):
    store = LedgerStore("deca", root)
    store.init(Decimal("100000"), date(2026, 9, 8))
    first = store.render_summary(store.account())
    second = store.render_summary(store.account())
    assert first == second
    assert store.summary_path.read_text().count("# DECA Stock Market Game ledger") == 1


def test_account_round_trips_through_the_book(root):
    store = LedgerStore("deca", root)
    store.init(Decimal("100000"), date(2026, 9, 8))
    book = store.load_book()
    book["positions"] = [
        {
            "symbol": "AAPL",
            "asset_class": "stock",
            "lots": [
                {"quantity": 30, "price": "100.00", "commission": "5", "opened": "2026-09-08"}
            ],
        }
    ]
    book["cash"] = "96995.00"
    store.save_book(book)

    account = store.account(marks={"AAPL": Decimal("110")})
    assert account.cash == Decimal("96995.00")
    assert account.positions[0].quantity == 30
    assert account.equity == Decimal("96995.00") + Decimal("3300")
