from datetime import date, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from investlab.contracts import Action, Fill
from investlab.portfolio.ledger import InsufficientCashError, InsufficientSharesError, Ledger

D = Decimal
SYMBOLS = ["AAA", "BBB", "CCC"]
START = date(2026, 9, 8)

prices = st.integers(min_value=100, max_value=50_000).map(lambda c: D(c) / 100)
quantities = st.integers(min_value=1, max_value=500)
commissions = st.sampled_from([D("0"), D("5.00"), D("25.00")])


@st.composite
def fills(draw):
    return Fill(
        symbol=draw(st.sampled_from(SYMBOLS)),
        action=draw(st.sampled_from([Action.BUY, Action.SELL])),
        quantity=draw(quantities),
        price=draw(prices),
        commission=draw(commissions),
        fees=draw(st.sampled_from([D("0"), D("0.02")])),
        session=START + timedelta(days=draw(st.integers(min_value=0, max_value=60))),
    )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(fills(), min_size=0, max_size=25))
def test_equity_identity_holds_after_any_sequence_of_fills(seq):
    led = Ledger(D("100000.00"))
    for f in seq:
        try:
            led.apply_fill(f)
        except (InsufficientCashError, InsufficientSharesError):
            continue
        marks = {s: D("40.00") for s in SYMBOLS}
        state = led.snapshot(marks, f.session)
        assert state.equity == (
            state.cash + state.marked_positions + state.receivables - state.liabilities
        )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(fills(), min_size=0, max_size=25))
def test_cash_never_negative_and_quantities_are_non_negative_ints(seq):
    led = Ledger(D("100000.00"), allow_margin=False)
    for f in seq:
        try:
            led.apply_fill(f)
        except (InsufficientCashError, InsufficientSharesError):
            pass
        assert led.cash >= D("0")
        for pos in led.positions:
            assert isinstance(pos.quantity, int)
            assert pos.quantity > 0
            for lot in pos.lots:
                assert isinstance(lot.quantity, int)


@given(quantities, prices, st.sampled_from([D("2"), D("3"), D("4"), D("0.5"), D("0.2")]))
def test_split_preserves_economic_value(qty, price, ratio):
    led = Ledger(D("10000000.00"))
    led.apply_fill(Fill("AAA", Action.BUY, qty, price, D("0"), D("0"), START))
    before = led.positions[0].net_cost + led.cash
    record = led.apply_split("AAA", ratio, START)
    after = (led.positions[0].net_cost if led.positions else D("0")) + led.cash
    assert abs(after - before) <= D("0.01")
    assert record.cash_in_lieu >= D("0")


@given(
    st.integers(min_value=1, max_value=1000),
    st.integers(min_value=1, max_value=500).map(lambda c: D(c) / 100),
    st.integers(min_value=0, max_value=5),
)
def test_dividend_credited_exactly_once_regardless_of_settle_calls(qty, per_share, extra_settles):
    led = Ledger(D("10000000.00"))
    led.apply_fill(Fill("AAA", Action.BUY, qty, D("10.00"), D("0"), D("0"), START))
    cash_before = led.cash
    led.apply_dividend("AAA", per_share, START, START + timedelta(days=14))
    equity_marks = {"AAA": D("10.00")}
    equity_at_ex = led.snapshot(equity_marks, START).equity
    for i in range(extra_settles + 3):
        led.settle_dividends(START + timedelta(days=14 + i))
    expected = (per_share * D(qty)).quantize(D("0.01"))
    assert led.cash - cash_before == expected
    assert led.receivables == D("0.00")
    assert led.snapshot(equity_marks, START + timedelta(days=30)).equity == equity_at_ex
