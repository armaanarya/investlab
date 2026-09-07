"""DECA Stock Market Game rule engine tests.

Every expected value here is hand-computed from `docs/rules/deca-verified.md`.
Where a number is derived (position ceiling, net-cost-minus-fee arithmetic,
business-day deadlines) the derivation is written out in a comment so a
reviewer can check it without running the code.

Money is Decimal everywhere. A float in this file is a bug.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investlab.competitions import deca
from investlab.contracts import (
    AccountState,
    Action,
    AssetClass,
    Bar,
    BlockReason,
    Fill,
    Instrument,
    Lot,
    Position,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

PROFILE = deca.DecaProfile()


def check_named(checks, name):
    for c in checks:
        if c.name == name:
            return c
    raise AssertionError(f"no check named {name!r} in {[c.name for c in checks]}")


def make_account(cash="100000", positions=(), marks=None, as_of=date(2026, 9, 8)):
    return AccountState(
        as_of=as_of,
        cash=Decimal(cash),
        positions=tuple(positions),
        marks={symbol: Decimal(value) for symbol, value in (marks or {}).items()},
    )


def account_with_one_position_at(value: Decimal, cash: Decimal, symbol="AAA", as_of=date(2026, 9, 8)):
    """An account whose one 100-share position is marked to exactly `value`,
    so position_weight comes out to a clean fraction of equity."""
    per_share = (value / Decimal(100)).quantize(Decimal("0.01"))
    lot = Lot(symbol, 100, per_share, Decimal("5"), as_of)
    position = Position(symbol, AssetClass.STOCK, (lot,))
    return make_account(cash=str(cash), positions=(position,), marks={symbol: per_share}, as_of=as_of)


pos_aaa_80k = Position(
    "AAA", AssetClass.STOCK, (Lot("AAA", 100, Decimal("800.00"), Decimal("5"), date(2026, 9, 8)),)
)
pos_aaa_90k = Position(
    "AAA", AssetClass.STOCK, (Lot("AAA", 100, Decimal("900.00"), Decimal("5"), date(2026, 9, 8)),)
)


def make_instrument(
    symbol="AAA",
    exchange="NYSE",
    market_cap="50000000",
    asset_class=AssetClass.STOCK,
    is_commodity_or_crypto_trust=False,
):
    return Instrument(
        symbol=symbol,
        name=symbol,
        asset_class=asset_class,
        exchange=exchange,
        market_cap=Decimal(market_cap) if market_cap is not None else None,
        is_commodity_or_crypto_trust=is_commodity_or_crypto_trust,
    )


def make_bar(symbol="AAA", close="50.00", session=date(2026, 9, 9)):
    price = Decimal(close)
    return Bar(
        symbol=symbol,
        session=session,
        open=price,
        high=price,
        low=price,
        close=price,
        adj_close=price,
        volume=1000,
        source="test",
    )


# ---------------------------------------------------------------------------
# Task 1: verified constants and 2026 NYSE business-day arithmetic
# ---------------------------------------------------------------------------


def test_next_business_day_from_a_friday_is_the_following_monday():
    # 2026-11-20 is a Friday; the next session is Monday 2026-11-23.
    assert date(2026, 11, 20).weekday() == 4
    assert deca.next_business_day(date(2026, 11, 20)) == date(2026, 11, 23)


def test_next_business_day_skips_thanksgiving():
    # 2026-11-25 is a Wednesday. Thursday 2026-11-26 is Thanksgiving, so the
    # next session is Friday 2026-11-27.
    assert deca.next_business_day(date(2026, 11, 25)) == date(2026, 11, 27)
    assert not deca.is_business_day(date(2026, 11, 26))


def test_christmas_is_a_holiday_and_falls_after_the_game_ends():
    assert not deca.is_business_day(date(2026, 12, 25))
    assert date(2026, 12, 25) > deca.GAME_END


def test_business_day_arithmetic_refuses_dates_outside_2026():
    with pytest.raises(ValueError, match="2026"):
        deca.next_business_day(date(2027, 1, 4))
    with pytest.raises(ValueError, match="2026"):
        deca.is_business_day(date(2025, 12, 31))


def test_trading_and_calendar_days_from_game_start_to_the_deadline():
    # Sep 9-30: 22 days minus 6 weekend days = 16 sessions.
    # Oct 1-23:  23 days minus 6 weekend days = 17 sessions. No October holiday.
    # 16 + 17 = 33 sessions. Calendar: 22 + 23 = 45 days.
    assert deca.trading_days_until(deca.GAME_START, deca.DIVERSIFICATION_DEADLINE) == 33
    assert deca.calendar_days_until(deca.GAME_START, deca.DIVERSIFICATION_DEADLINE) == 45


def test_position_ceiling_is_twenty_percent_times_one_point_five():
    assert deca.BASE_POSITION_LIMIT_FRACTION == Decimal("0.20")
    assert deca.POSITION_LIMIT_MULTIPLIER == Decimal("1.5")
    assert deca.BASE_POSITION_LIMIT_FRACTION * deca.POSITION_LIMIT_MULTIPLIER == Decimal("0.300")
    assert deca.POSITION_CEILING_FRACTION == Decimal("0.30")


def test_verified_money_and_date_constants():
    assert deca.STARTING_CASH == Decimal("100000")
    assert deca.COMMISSION_PER_TRADE == Decimal("5")
    assert deca.MIN_SHARE_PRICE == Decimal("3.00")
    assert deca.MIN_MARKET_CAP == Decimal("25000000")
    assert deca.MIN_SHARES_PER_BUY == 10
    assert deca.DIVERSIFICATION_MINIMUM == Decimal("10000")
    assert deca.REQUIRED_GROSS_OUTLAY == Decimal("10005")
    assert deca.MARGIN_MAX_EQUITY_FRACTION == Decimal("0.50")
    assert deca.MARGIN_INTEREST_RATE == Decimal("0.0700")
    assert deca.CASH_CREDIT_RATE == Decimal("0.0075")
    assert deca.GAME_START == date(2026, 9, 8)
    assert deca.GAME_END == date(2026, 12, 4)
    assert deca.DIVERSIFICATION_DEADLINE == date(2026, 10, 23)
    assert deca.DIVERSIFICATION_HOLD_THROUGH == date(2026, 12, 4)


# ---------------------------------------------------------------------------
# Task 2: eligibility
# ---------------------------------------------------------------------------


def test_nyse_and_nasdaq_are_eligible():
    for exch in ("NYSE", "NASDAQ"):
        ok, reason = PROFILE.is_eligible(make_instrument(exchange=exch), make_bar(close="50.00"))
        assert ok is True
        assert reason == ""


def test_otc_and_pink_sheets_rejected():
    for exch in ("OTC", "Pink Sheets", "OTCQB"):
        ok, reason = PROFILE.is_eligible(make_instrument(exchange=exch), make_bar(close="50.00"))
        assert ok is False
        assert "NYSE and NASDAQ" in reason


def test_nyse_american_rejected_as_unverified():
    ok, reason = PROFILE.is_eligible(
        make_instrument(exchange="NYSE American"), make_bar(close="50.00")
    )
    assert ok is False
    assert "UNVERIFIED" in reason
    block = PROFILE.eligibility_block(
        make_instrument(exchange="NYSE American"), make_bar(close="50.00")
    )
    assert block.reason is BlockReason.UNVERIFIED_INSTRUMENT


def test_price_below_three_dollars_rejected_and_exactly_three_accepted():
    ok, reason = PROFILE.is_eligible(make_instrument(), make_bar(close="2.99"))
    assert ok is False
    assert "2.99" in reason
    assert "3.00" in reason
    ok, reason = PROFILE.is_eligible(make_instrument(), make_bar(close="3.00"))
    assert ok is True
    assert reason == ""


def test_prior_session_below_three_dollars_rejected():
    today = make_bar(close="3.50", session=date(2026, 9, 9))
    yesterday = make_bar(close="2.95", session=date(2026, 9, 8))
    ok, reason = PROFILE.is_eligible(make_instrument(), today, prior_bar=yesterday)
    assert ok is False
    assert "2.95" in reason
    assert "day before" in reason


def test_market_cap_floor_is_twenty_five_million():
    ok, _ = PROFILE.is_eligible(make_instrument(market_cap="24999999"), make_bar(close="50.00"))
    assert ok is False
    ok, _ = PROFILE.is_eligible(make_instrument(market_cap="25000000"), make_bar(close="50.00"))
    assert ok is True


def test_unknown_market_cap_is_rejected_not_assumed():
    ok, reason = PROFILE.is_eligible(make_instrument(market_cap=None), make_bar(close="50.00"))
    assert ok is False
    assert "unknown" in reason.lower()


def test_ibit_rejected_with_three_grounds():
    ibit = make_instrument(symbol="IBIT", exchange="NASDAQ", is_commodity_or_crypto_trust=True)
    ok, reason = PROFILE.is_eligible(ibit, make_bar(symbol="IBIT", close="60.00"))
    assert ok is False
    for token in ("IBIT", "GLD", "bitcoin", "commodit", "rule 3"):
        assert token.lower() in reason.lower()
    block = PROFILE.eligibility_block(ibit, make_bar(symbol="IBIT", close="60.00"))
    assert block.reason is BlockReason.PROHIBITED_SECURITY


def test_gld_rejected():
    gld = make_instrument(symbol="GLD", exchange="NYSE", is_commodity_or_crypto_trust=True)
    ok, reason = PROFILE.is_eligible(gld, make_bar(symbol="GLD", close="200.00"))
    assert ok is False
    assert "prohibit" in reason.lower()


def test_prohibition_outranks_every_other_check():
    # A prohibited trust that is ALSO cheap and on OTC still reports the prohibition.
    bad = make_instrument(
        symbol="IBIT",
        exchange="OTC",
        market_cap=None,
        is_commodity_or_crypto_trust=True,
    )
    _, reason = PROFILE.is_eligible(bad, make_bar(symbol="IBIT", close="1.00"))
    assert "bitcoin" in reason.lower()


# ---------------------------------------------------------------------------
# Task 3: sizing constraints and the position ceiling
# ---------------------------------------------------------------------------


def test_sizing_constraints_carry_verified_values():
    acct = make_account(cash="100000")
    c = PROFILE.sizing_constraints(acct)
    assert c.equity == Decimal("100000")
    assert c.min_shares == 10
    assert c.min_price == Decimal("3.00")
    assert c.position_ceiling_fraction == Decimal("0.30")
    assert c.commission_per_trade == Decimal("5")
    assert c.sell_fee_rate == Decimal("0.0000278")


def test_nine_share_buy_rejected_ten_accepted():
    ok, reason = PROFILE.check_buy_quantity(9)
    assert ok is False
    assert "10" in reason
    assert "9" in reason
    assert PROFILE.check_buy_quantity(10) == (True, "")


def test_sells_may_be_fewer_than_ten_shares():
    assert PROFILE.check_sell_quantity(3) == (True, "")


def test_spendable_cash_excludes_margin_by_default():
    acct = make_account(cash="20000", positions=(pos_aaa_80k,), marks={"AAA": "800.00"})
    assert PROFILE.spendable_cash(acct) == Decimal("20000")


def test_spendable_cash_with_margin_enabled_adds_half_of_equity():
    profile = deca.DecaProfile(allows_margin=True)
    acct = make_account(cash="10000", positions=(pos_aaa_90k,), marks={"AAA": "900.00"})
    assert acct.equity == Decimal("100000")
    assert profile.spendable_cash(acct) == Decimal("60000")


def test_position_at_31_percent_blocks_add_but_is_not_force_sold():
    acct = account_with_one_position_at(Decimal("31000"), cash=Decimal("69000"))
    assert acct.equity == Decimal("100000")
    assert PROFILE.position_weight(acct, "AAA") == Decimal("0.31")
    ok, reason = PROFILE.can_add_to(acct, "AAA")
    assert ok is False
    assert "31.0" in reason
    assert "30.0" in reason
    assert "sell" not in reason.lower()


def test_position_at_29_percent_allows_add():
    acct = account_with_one_position_at(Decimal("29000"), cash=Decimal("71000"))
    assert PROFILE.can_add_to(acct, "AAA") == (True, "")


# ---------------------------------------------------------------------------
# Task 4: the diversification engine
# ---------------------------------------------------------------------------


def test_ten_thousand_dollar_outlay_with_five_dollar_fee_falls_short():
    # 1999 shares x $5.00 = $9,995 net cost + $5.00 commission = $10,000.00 spent.
    lot = Lot("FXAIX", 1999, Decimal("5.00"), Decimal("5.00"), date(2026, 9, 9))
    assert lot.gross_cost == Decimal("10000.00")
    assert lot.net_cost == Decimal("9995.00")
    acct = make_account(
        cash="0",
        positions=(Position("FXAIX", AssetClass.MUTUAL_FUND, (lot,)),),
        marks={"FXAIX": "5.00"},
    )
    check = check_named(PROFILE.check_rules(acct, date(2026, 10, 1)), "diversification_mutual_funds")
    assert check.satisfied is False
    assert "9,995.00" in check.detail
    assert "10,005.00" in check.detail


def test_ten_thousand_one_hundred_dollar_outlay_qualifies():
    # 2019 shares x $5.00 = $10,095 net cost + $5.00 = $10,100.00 spent.
    lot = Lot("FXAIX", 2019, Decimal("5.00"), Decimal("5.00"), date(2026, 9, 9))
    assert lot.gross_cost == Decimal("10100.00")
    acct = make_account(
        cash="0",
        positions=(Position("FXAIX", AssetClass.MUTUAL_FUND, (lot,)),),
        marks={"FXAIX": "5.00"},
    )
    check = check_named(PROFILE.check_rules(acct, date(2026, 10, 1)), "diversification_mutual_funds")
    assert check.satisfied is True


def test_net_cost_of_exactly_ten_thousand_qualifies():
    lot = Lot("FXAIX", 2000, Decimal("5.00"), Decimal("5.00"), date(2026, 9, 9))
    assert lot.net_cost == Decimal("10000.00")
    acct = make_account(
        cash="0",
        positions=(Position("FXAIX", AssetClass.MUTUAL_FUND, (lot,)),),
        marks={"FXAIX": "5.00"},
    )
    check = check_named(PROFILE.check_rules(acct, date(2026, 10, 1)), "diversification_mutual_funds")
    assert check.satisfied is True


def test_market_value_falling_to_eight_thousand_requires_no_action():
    # 200 shares x $50.50 = $10,100 net cost, later marked at $40.00 = $8,000.
    lot = Lot("FXAIX", 200, Decimal("50.50"), Decimal("5.00"), date(2026, 9, 9))
    acct = make_account(
        cash="0",
        positions=(Position("FXAIX", AssetClass.MUTUAL_FUND, (lot,)),),
        marks={"FXAIX": "40.00"},
    )
    assert acct.marked_positions == Decimal("8000.00")
    check = check_named(PROFILE.check_rules(acct, date(2026, 11, 2)), "diversification_mutual_funds")
    assert check.satisfied is True
    assert "no action" in check.detail.lower()


def test_bond_etf_counts_as_a_stock_not_a_bond():
    lot = Lot("BND", 200, Decimal("70.00"), Decimal("5.00"), date(2026, 9, 9))  # $14,000
    acct = make_account(
        cash="0", positions=(Position("BND", AssetClass.ETF, (lot,)),), marks={"BND": "70.00"}
    )
    checks = PROFILE.check_rules(acct, date(2026, 10, 1))
    assert check_named(checks, "diversification_stocks").satisfied is True
    assert check_named(checks, "diversification_bonds").satisfied is False


def test_bond_mutual_fund_counts_as_a_mutual_fund_not_a_bond():
    lot = Lot("VBTLX", 1500, Decimal("10.00"), Decimal("5.00"), date(2026, 9, 9))  # $15,000
    acct = make_account(
        cash="0",
        positions=(Position("VBTLX", AssetClass.MUTUAL_FUND, (lot,)),),
        marks={"VBTLX": "10.00"},
    )
    checks = PROFILE.check_rules(acct, date(2026, 10, 1))
    assert check_named(checks, "diversification_mutual_funds").satisfied is True
    assert check_named(checks, "diversification_bonds").satisfied is False


def test_short_stock_position_does_not_count_toward_the_stock_bucket():
    lot = Lot("AAA", -200, Decimal("100.00"), Decimal("5.00"), date(2026, 9, 9))
    acct = make_account(
        cash="0", positions=(Position("AAA", AssetClass.STOCK, (lot,)),), marks={"AAA": "100.00"}
    )
    assert PROFILE.class_net_cost(acct, deca.AssetBucket.STOCKS) == Decimal("0")


def test_selling_out_of_bonds_on_a_friday_is_due_the_following_monday():
    sale = deca.ClassifiedFill(
        Fill("USTB", Action.SELL, 12, Decimal("1000.00"), Decimal("5.00"), Decimal("0.33"), date(2026, 11, 20)),
        AssetClass.BOND,
    )
    acct = make_account(cash="12000", positions=(), marks={})
    check = check_named(
        PROFILE.check_rules(acct, date(2026, 11, 20), sell_history=(sale,)), "diversification_bonds"
    )
    assert check.satisfied is False
    assert check.deadline == date(2026, 11, 23)  # Friday sale -> Monday
    assert "one business day" in check.detail.lower()
    assert "2026-11-23" in check.detail


def test_selling_the_day_before_thanksgiving_is_due_the_friday():
    sale = deca.ClassifiedFill(
        Fill("USTB", Action.SELL, 12, Decimal("1000.00"), Decimal("5.00"), Decimal("0.33"), date(2026, 11, 25)),
        AssetClass.BOND,
    )
    acct = make_account(cash="12000", positions=(), marks={})
    check = check_named(
        PROFILE.check_rules(acct, date(2026, 11, 25), sell_history=(sale,)), "diversification_bonds"
    )
    assert check.deadline == date(2026, 11, 27)  # skips Thanksgiving


def test_an_early_sale_does_not_manufacture_a_deadline_before_october_23():
    sale = deca.ClassifiedFill(
        Fill("USTB", Action.SELL, 12, Decimal("1000.00"), Decimal("5.00"), Decimal("0.33"), date(2026, 9, 18)),
        AssetClass.BOND,
    )
    acct = make_account(cash="12000", positions=(), marks={})
    check = check_named(
        PROFILE.check_rules(acct, date(2026, 9, 21), sell_history=(sale,)), "diversification_bonds"
    )
    assert check.deadline == deca.DIVERSIFICATION_DEADLINE


def test_a_qualifying_class_reports_the_hold_through_date():
    lot = Lot("AAA", 300, Decimal("40.00"), Decimal("5.00"), date(2026, 9, 9))  # $12,000
    acct = make_account(
        cash="0", positions=(Position("AAA", AssetClass.STOCK, (lot,)),), marks={"AAA": "40.00"}
    )
    check = check_named(PROFILE.check_rules(acct, date(2026, 10, 1)), "diversification_stocks")
    assert check.satisfied is True
    assert check.deadline == deca.DIVERSIFICATION_HOLD_THROUGH
    assert "2026-12-04" in check.detail
