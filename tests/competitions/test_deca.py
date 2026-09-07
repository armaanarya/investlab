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
