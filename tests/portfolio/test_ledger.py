from datetime import date
from decimal import Decimal

import pytest

from investlab.contracts import Action, AssetClass, Fill
from investlab.portfolio.ledger import (
    InsufficientCashError,
    InsufficientSharesError,
    Ledger,
    UnknownSymbolError,
)

D = Decimal


def buy(symbol="AAA", qty=100, price="50.00", commission="5.00", fees="0", session=date(2026, 9, 8)):
    return Fill(symbol, Action.BUY, qty, D(price), D(commission), D(fees), session)


def sell(symbol="AAA", qty=100, price="55.00", commission="5.00", fees="0.02", session=date(2026, 9, 15)):
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
    assert led.realized_pnl == D("200.00")          # sold the $10 lot
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
