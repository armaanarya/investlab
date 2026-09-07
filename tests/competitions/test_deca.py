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
from investlab.contracts import AssetClass, Bar, BlockReason, Instrument

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

PROFILE = deca.DecaProfile()


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
