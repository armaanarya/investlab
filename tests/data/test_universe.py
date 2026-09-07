"""Fixed universe tests.

The universe is fixed and declared up front because a survivorship-free
universe is unobtainable at zero budget. These tests pin the bias-warning
contract, the trust-flagging the competition modules depend on to reject
IBIT/GLD/SLV, and the compliance sleeve (bond ETFs + mutual funds) other
agents assume exists.
"""

from __future__ import annotations

import pytest

from investlab.contracts import AssetClass
from investlab.data.universe import UnknownSymbolError, default_universe


def test_ibit_gld_slv_are_flagged_as_trusts():
    u = default_universe()
    for sym in ("IBIT", "GLD", "SLV"):
        assert u.get(sym).is_commodity_or_crypto_trust is True


def test_no_other_instrument_is_flagged():
    u = default_universe()
    flagged = {i.symbol for i in u.prohibited()}
    assert flagged == {"IBIT", "GLD", "SLV"}


def test_bias_warning_names_the_bias():
    w = default_universe().bias_warning()
    assert "fixed" in w.lower()
    assert "selection bias" in w.lower()
    assert "disclose" in w.lower()


def test_required_compliance_sleeve_present():
    u = default_universe()
    assert {"AGG", "BND", "TLT", "LQD", "SHY"} <= set(u.symbols())
    assert {"VFIAX", "FXAIX", "VTSAX", "VBTLX"} <= set(u.symbols())
    for f in ("VFIAX", "FXAIX", "VTSAX", "VBTLX"):
        assert u.get(f).asset_class is AssetClass.MUTUAL_FUND


def test_bond_etfs_are_asset_class_etf():
    # DECA classifies an ETF as a stock for diversification purposes, but
    # this field records what the instrument *is*, not how a competition
    # classifies it. That reclassification lives in competitions/, not here.
    u = default_universe()
    for sym in ("AGG", "BND", "TLT", "LQD", "SHY"):
        assert u.get(sym).asset_class is AssetClass.ETF


def test_symbols_are_unique_and_sorted_stable():
    syms = default_universe().symbols()
    assert len(syms) == len(set(syms))
    assert syms == sorted(syms)


def test_unknown_symbol_raises():
    with pytest.raises(UnknownSymbolError):
        default_universe().get("NOPE")


def test_brk_dot_b_is_present_for_symbol_translation_coverage():
    u = default_universe()
    assert "BRK.B" in u
    assert u.get("BRK.B").asset_class is AssetClass.STOCK


def test_universe_has_at_least_fifty_large_caps():
    u = default_universe()
    stocks = u.by_asset_class(AssetClass.STOCK)
    assert len(stocks) >= 50


def test_len_and_iter_agree_with_symbols():
    u = default_universe()
    assert len(u) == len(u.symbols())
    assert {i.symbol for i in u} == set(u.symbols())


def test_by_asset_class_partitions_the_universe():
    u = default_universe()
    total = sum(len(u.by_asset_class(ac)) for ac in AssetClass)
    assert total == len(u)


def test_contains_checks_symbol_membership():
    u = default_universe()
    assert "AAPL" in u
    assert "NOPE" not in u
