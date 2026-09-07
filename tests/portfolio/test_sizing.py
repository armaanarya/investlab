from decimal import Decimal

from investlab.contracts import Action, BlockedOrder, BlockReason, SizedOrder, SizingConstraints
from investlab.portfolio.sizing import BindingConstraint, Candidate, size_batch, size_order

D = Decimal


def constraints(**over):
    base = dict(
        equity=D("100000.00"),
        spendable_cash=D("100000.00"),
        risk_fraction=D("0.01"),
        position_ceiling_fraction=D("0.10"),
        min_shares=1,
        min_price=D("3.00"),
        commission_per_trade=D("0"),
        sell_fee_rate=D("0"),
    )
    base.update(over)
    return SizingConstraints(**base)


def test_worked_example_position_ceiling_binds_at_200_shares():
    """Spec section 8: risk alone allows floor(1000/4)=250, the 10% ceiling
    allows floor(10000/50)=200. The answer is 200. Planned loss $800 = 0.8%
    of equity, and the solver does NOT scale up to consume the full 1%."""
    order = size_order("AAA", D("50.00"), D("46.00"), constraints())
    assert isinstance(order, SizedOrder)
    assert order.quantity == 200
    assert order.estimated_notional == D("10000.00")
    assert order.planned_risk == D("800.00")
    assert order.planned_risk / D("100000.00") == D("0.008")
    assert order.binding_constraint == BindingConstraint.POSITION_CEILING.value
    assert order.gap_stress_loss == D("2000.00")
    assert order.action is Action.BUY
    assert order.protective_reference == D("46.00")


def test_worked_example_with_commissions_cash_binds_at_199_shares():
    """$5 entry + $5 exit: 200 shares would cost $10,005 and risk $810.
    With exactly $10,000 spendable, q falls to 199 — never rounded up."""
    con = constraints(spendable_cash=D("10000.00"), commission_per_trade=D("5.00"))
    order = size_order("AAA", D("50.00"), D("46.00"), con)
    assert isinstance(order, SizedOrder)
    assert order.quantity == 199
    assert order.estimated_commission == D("5.00")
    assert order.planned_risk == D("806.00")  # 199*4 + 5 + 5
    assert order.binding_constraint == BindingConstraint.SPENDABLE_CASH.value


def test_two_hundred_shares_would_have_cost_10005_and_risked_810():
    """The arithmetic the previous test turns on, asserted directly."""
    from investlab.portfolio.sizing import planned_loss

    con = constraints(commission_per_trade=D("5.00"))
    assert planned_loss(200, D("50.00"), D("46.00"), con) == D("810.00")
    assert D("200") * D("50.00") + D("5.00") == D("10005.00")


def test_risk_binds_when_the_stop_is_wide():
    con = constraints(position_ceiling_fraction=D("1.0"))
    order = size_order("AAA", D("50.00"), D("46.00"), con)
    assert order.quantity == 250
    assert order.binding_constraint == BindingConstraint.RISK_BUDGET.value


def test_price_below_minimum_is_blocked():
    order = size_order("AAA", D("2.50"), D("2.00"), constraints())
    assert isinstance(order, BlockedOrder)
    assert order.reason is BlockReason.PRICE_BELOW_MINIMUM


def test_missing_protective_reference_is_blocked_not_guessed():
    order = size_order("AAA", D("50.00"), None, constraints())
    assert isinstance(order, BlockedOrder)
    assert order.reason is BlockReason.INSUFFICIENT_EVIDENCE


def test_stop_above_entry_is_blocked():
    order = size_order("AAA", D("50.00"), D("50.00"), constraints())
    assert isinstance(order, BlockedOrder)
    assert order.reason is BlockReason.INSUFFICIENT_EVIDENCE


def test_min_lot_exceeding_risk_blocks_rather_than_forcing_the_minimum():
    con = constraints(min_shares=10, risk_fraction=D("0.0001"))  # $10 budget, $4/share
    order = size_order("AAA", D("50.00"), D("46.00"), con)
    assert isinstance(order, BlockedOrder)
    assert order.reason is BlockReason.MIN_LOT_EXCEEDS_RISK


def test_min_lot_exceeding_cash_blocks():
    con = constraints(min_shares=10, spendable_cash=D("100.00"))
    order = size_order("AAA", D("50.00"), D("46.00"), con)
    assert isinstance(order, BlockedOrder)
    assert order.reason is BlockReason.MIN_LOT_EXCEEDS_CASH


def test_existing_position_reduces_the_ceiling_headroom():
    order = size_order(
        "AAA", D("50.00"), D("46.00"), constraints(), current_position_value=D("7500.00")
    )
    assert order.quantity == 50
    assert order.binding_constraint == BindingConstraint.POSITION_CEILING.value


def test_batch_reserves_cash_sequentially_so_no_dollar_is_spent_twice():
    con = constraints(spendable_cash=D("12000.00"), position_ceiling_fraction=D("1.0"))
    results = size_batch(
        (
            Candidate("BBB", D("50.00"), D("46.00"), rank=2),
            Candidate("AAA", D("50.00"), D("46.00"), rank=1),
        ),
        con,
    )
    assert [r.symbol for r in results] == ["AAA", "BBB"]  # rank order, deterministic
    spent = sum(
        (
            r.estimated_notional + r.estimated_commission
            for r in results
            if isinstance(r, SizedOrder)
        ),
        D("0"),
    )
    assert spent <= D("12000.00")
