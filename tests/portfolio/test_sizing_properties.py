from decimal import Decimal

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from investlab.contracts import BlockedOrder, SizedOrder, SizingConstraints
from investlab.portfolio.sizing import Candidate, planned_loss, size_batch, size_order

D = Decimal

money = st.integers(min_value=1, max_value=10_000_000).map(lambda c: D(c) / 100)
fractions = st.integers(min_value=1, max_value=100).map(lambda b: D(b) / 1000)


@st.composite
def scenarios(draw):
    price = draw(st.integers(min_value=500, max_value=50_000).map(lambda c: D(c) / 100))
    stop_gap = draw(st.integers(min_value=1, max_value=int(price * 90)).map(lambda c: D(c) / 100))
    stop = price - stop_gap
    assume(stop > D("0"))
    con = SizingConstraints(
        equity=draw(money),
        spendable_cash=draw(money),
        risk_fraction=draw(fractions),
        position_ceiling_fraction=draw(st.sampled_from([D("0.05"), D("0.10"), D("0.30"), D("1")])),
        min_shares=draw(st.sampled_from([1, 10])),
        min_price=D("3.00"),
        commission_per_trade=draw(st.sampled_from([D("0"), D("5.00"), D("25.00")])),
        sell_fee_rate=draw(st.sampled_from([D("0"), D("0.0000278")])),
    )
    return price, stop, con


@settings(max_examples=300)
@given(scenarios())
def test_every_sized_order_satisfies_every_cap(scenario):
    price, stop, con = scenario
    result = size_order("AAA", price, stop, con)
    if isinstance(result, BlockedOrder):
        return
    q = result.quantity
    assert isinstance(q, int) and q >= max(con.min_shares, 1)
    assert planned_loss(q, price, stop, con) <= con.risk_fraction * con.equity
    assert D(q) * price + con.commission_per_trade <= con.spendable_cash
    assert D(q) * price <= con.position_ceiling_fraction * con.equity


@settings(max_examples=300)
@given(scenarios())
def test_the_solver_is_maximal_one_more_share_would_breach(scenario):
    price, stop, con = scenario
    result = size_order("AAA", price, stop, con)
    if isinstance(result, BlockedOrder):
        return
    q = result.quantity + 1
    breaches = (
        planned_loss(q, price, stop, con) > con.risk_fraction * con.equity
        or D(q) * price + con.commission_per_trade > con.spendable_cash
        or D(q) * price > con.position_ceiling_fraction * con.equity
    )
    assert breaches


@settings(max_examples=200)
@given(scenarios(), st.integers(min_value=2, max_value=6))
def test_a_batch_never_spends_more_than_available_cash(scenario, n):
    price, stop, con = scenario
    candidates = tuple(Candidate(f"S{i:02d}", price, stop, rank=i) for i in range(n))
    results = size_batch(candidates, con)
    spent = sum(
        (
            r.estimated_notional + r.estimated_commission
            for r in results
            if isinstance(r, SizedOrder)
        ),
        D("0"),
    )
    assert spent <= con.spendable_cash


@settings(max_examples=200)
@given(scenarios())
def test_sizing_is_deterministic(scenario):
    price, stop, con = scenario
    a = size_order("AAA", price, stop, con)
    b = size_order("AAA", price, stop, con)
    assert a == b
