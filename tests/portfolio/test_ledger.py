from datetime import date
from decimal import Decimal

import pytest

from investlab.contracts import Action, Fill
from investlab.portfolio.ledger import (
    DuplicateDividendError,
    InsufficientCashError,
    InsufficientSharesError,
    Ledger,
    UnknownSymbolError,
)

D = Decimal


def buy(
    symbol="AAA", qty=100, price="50.00", commission="5.00", fees="0", session=date(2026, 9, 8)
):
    return Fill(symbol, Action.BUY, qty, D(price), D(commission), D(fees), session)


def sell(
    symbol="AAA", qty=100, price="55.00", commission="5.00", fees="0.02", session=date(2026, 9, 15)
):
    return Fill(symbol, Action.SELL, qty, D(price), D(commission), D(fees), session)


def test_buy_reduces_cash_by_notional_plus_costs():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy())
    assert led.cash == D("94995.00")
    assert led.positions[0].quantity == 100
    assert led.positions[0].net_cost == D("5000.00")


def test_buy_beyond_cash_is_refused_when_margin_disabled():
    led = Ledger(D("1000.00"))
    with pytest.raises(InsufficientCashError):
        led.apply_fill(buy(qty=100))
    assert led.cash == D("1000.00")
    assert led.positions == ()
    assert led.fills == ()


def test_sell_more_than_held_is_refused():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=10))
    with pytest.raises(InsufficientSharesError):
        led.apply_fill(sell(qty=11))


def test_fifo_sell_consumes_the_oldest_lot_first():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=10, price="10.00", commission="0", session=date(2026, 9, 8)))
    led.apply_fill(buy(qty=10, price="20.00", commission="0", session=date(2026, 9, 9)))
    led.apply_fill(sell(qty=10, price="30.00", commission="0", fees="0", session=date(2026, 9, 10)))
    assert led.realized_pnl == D("200.00")  # sold the $10 lot
    assert led.positions[0].quantity == 10
    assert led.positions[0].net_cost == D("200.00")  # the $20 lot survives


def test_partial_lot_consumption_amortizes_entry_commission():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=100, price="10.00", commission="5.00"))
    led.apply_fill(sell(qty=40, price="10.00", commission="0", fees="0"))
    # 40% of the $5 entry commission is expensed with the sale.
    assert led.realized_pnl == D("-2.00")
    assert led.positions[0].lots[0].commission == D("3.00")


def test_snapshot_requires_a_mark_for_every_held_symbol():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy())
    with pytest.raises(UnknownSymbolError):
        led.snapshot({}, date(2026, 9, 8))


def test_snapshot_equity_matches_the_contract_identity():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy())
    state = led.snapshot({"AAA": D("52.00")}, date(2026, 9, 8))
    assert state.cash == D("94995.00")
    assert state.marked_positions == D("5200.00")
    assert state.equity == D("100195.00")


def test_plan_verbs_are_not_transactions():
    led = Ledger(D("100000.00"))
    with pytest.raises(ValueError):
        led.apply_fill(Fill("AAA", Action.HOLD, 1, D("1.00"), D("0"), D("0"), date(2026, 9, 8)))


def test_split_preserves_total_basis_and_scales_quantity():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=100, price="50.00", commission="0"))
    before = led.positions[0].net_cost
    led.apply_split("AAA", D("2"), date(2026, 10, 1))
    pos = led.positions[0]
    assert pos.quantity == 200
    assert pos.net_cost == before == D("5000.00")
    assert pos.lots[0].price == D("25.00000000")


def test_reverse_split_scales_the_other_way():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=100, price="5.00", commission="0"))
    led.apply_split("AAA", D("0.1"), date(2026, 10, 1))
    pos = led.positions[0]
    assert pos.quantity == 10
    assert pos.net_cost == D("500.00")


def test_split_fraction_becomes_cash_in_lieu_at_basis_not_pnl():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=5, price="30.00", commission="0"))
    cash_before, basis_before = led.cash, led.positions[0].net_cost
    record = led.apply_split("AAA", D("1.5"), date(2026, 10, 1))  # 7.5 -> 7 shares
    pos = led.positions[0]
    assert pos.quantity == 7
    assert record.cash_in_lieu > D("0")
    assert led.cash == cash_before + record.cash_in_lieu
    assert pos.net_cost + record.cash_in_lieu == basis_before
    assert led.realized_pnl == D("0")


def test_dividend_is_a_receivable_on_ex_date_and_cash_on_pay_date():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=100, price="50.00", commission="0"))
    led.apply_dividend("AAA", D("0.25"), date(2026, 10, 1), date(2026, 10, 15))
    assert led.receivables == D("25.00")
    equity_at_ex = led.snapshot({"AAA": D("50.00")}, date(2026, 10, 1)).equity

    assert led.settle_dividends(date(2026, 10, 10)) == ()  # before pay date
    assert led.receivables == D("25.00")

    paid = led.settle_dividends(date(2026, 10, 15))
    assert len(paid) == 1
    assert led.receivables == D("0.00")
    assert led.cash == D("95025.00")
    assert led.snapshot({"AAA": D("50.00")}, date(2026, 10, 15)).equity == equity_at_ex


def test_dividend_cannot_be_credited_twice():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=100, price="50.00", commission="0"))
    led.apply_dividend("AAA", D("0.25"), date(2026, 10, 1), date(2026, 10, 15))
    with pytest.raises(DuplicateDividendError):
        led.apply_dividend("AAA", D("0.25"), date(2026, 10, 1), date(2026, 10, 15))
    led.settle_dividends(date(2026, 10, 15))
    led.settle_dividends(date(2026, 10, 16))
    assert led.cash == D("95025.00")


def test_reconcile_agrees_when_histories_match():
    led = Ledger(D("100000.00"))
    f = buy()
    led.apply_fill(f)
    rec = led.reconcile([f])
    assert rec.in_agreement
    assert len(rec.matched) == 1


def test_reconcile_reports_a_price_discrepancy_without_rewriting_history():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(price="50.00"))
    rec = led.reconcile([buy(price="50.10")])
    assert not rec.in_agreement
    assert len(rec.discrepancies) == 1
    assert "price" in rec.discrepancies[0].fields
    assert led.fills[0].price == D("50.00")  # our history is untouched


def test_reconcile_flags_a_fill_only_the_platform_has():
    led = Ledger(D("100000.00"))
    rec = led.reconcile([buy()])
    assert len(rec.only_in_statement) == 1
    assert rec.only_in_ledger == ()


def test_reconcile_flags_a_fill_only_we_have():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy())
    rec = led.reconcile([])
    assert len(rec.only_in_ledger) == 1


def test_reconcile_pairs_duplicates_as_a_multiset():
    led = Ledger(D("100000.00"))
    led.apply_fill(buy(qty=10))
    led.apply_fill(buy(qty=10))
    rec = led.reconcile([buy(qty=10)])
    assert len(rec.matched) == 1
    assert len(rec.only_in_ledger) == 1
