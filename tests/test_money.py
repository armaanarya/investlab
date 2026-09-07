from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from investlab.money import round_shares, usd, usd_ceil


def test_usd_quantizes_to_cents_half_up():
    assert usd(Decimal("1.005")) == Decimal("1.01")
    assert usd(Decimal("2.344")) == Decimal("2.34")
    assert usd("10") == Decimal("10.00")
    assert usd(7) == Decimal("7.00")


def test_usd_rejects_float():
    with pytest.raises(TypeError):
        usd(1.10)


def test_usd_rejects_bool():
    with pytest.raises(TypeError):
        usd(True)


def test_usd_ceil_never_understates_a_cost():
    assert usd_ceil(Decimal("1.001")) == Decimal("1.01")
    assert usd_ceil(Decimal("1.010")) == Decimal("1.01")


def test_round_shares_always_rounds_down():
    assert round_shares(Decimal("199.999")) == 199
    assert round_shares(Decimal("200.0")) == 200
    assert round_shares(Decimal("0.9")) == 0


def test_round_shares_floors_negatives_too():
    assert round_shares(Decimal("-0.5")) == -1
    assert round_shares(Decimal("-2.1")) == -3


def test_round_shares_returns_int():
    assert isinstance(round_shares(Decimal("5.5")), int)


@given(st.integers(min_value=-(10**9), max_value=10**9), st.integers(min_value=0, max_value=999))
def test_round_shares_never_exceeds_input(whole, frac):
    value = Decimal(whole) + Decimal(frac) / Decimal(1000)
    assert Decimal(round_shares(value)) <= value


@given(st.integers(min_value=-(10**9), max_value=10**9))
def test_usd_is_idempotent(cents):
    once = usd(Decimal(cents) / 100)
    assert usd(once) == once
    assert once.as_tuple().exponent == -2
