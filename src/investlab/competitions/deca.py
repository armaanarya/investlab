"""DECA Stock Market Game (2026-27 HS season) competition profile.

Every constant here is copied verbatim from `docs/rules/deca-verified.md`,
which records the primary source for each value. Where a rule is not
verified (the SEC fee rate, NYSE American eligibility, the ~30% maintenance
margin), this module says so explicitly rather than guessing.

This module imports ONLY `investlab.contracts` and the standard library. It
must not import `investlab.calendar`, `investlab.portfolio`, `investlab.data`,
`investlab.features`, or `investlab.competitions.wharton` -- those are owned
and being written by other agents concurrently (see
`docs/superpowers/plans/AGENT-FILE-OWNERSHIP.md`). Business-day arithmetic is
therefore implemented locally, scoped to calendar year 2026 only.

AI-authorship note (design spec Sec 3): every string this module emits is a
number, a date, or the name of a constraint -- never a drafted argument or
thesis a student could submit as their own analysis. "position is 31.0% of
equity, ceiling is 30.0%" is correct output. Editorializing about a stock's
prospects is not this module's job and must never appear here.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

# ---------------------------------------------------------------------------
# Task 1: verified constants and 2026 NYSE business-day arithmetic
# ---------------------------------------------------------------------------

STARTING_CASH = Decimal("100000")
COMMISSION_PER_TRADE = Decimal("5")

# UNVERIFIED. docs/rules/deca-verified.md: "SEC fee on sells (rate
# UNVERIFIED)". Encoded as a configurable default on DecaProfile, and
# surfaced by check_rules() as RuleStatus.INCOMPLETE so a student never
# mistakes it for a confirmed figure.
DEFAULT_SEC_FEE_RATE = Decimal("0.0000278")

# strategy risk fraction is not verified against a primary source either; it
# is a configurable knob, not a DECA rule. Kept conservative by default.
DEFAULT_RISK_FRACTION = Decimal("0.02")

MARGIN_MAX_EQUITY_FRACTION = Decimal("0.50")
MARGIN_INTEREST_RATE = Decimal("0.0700")
CASH_CREDIT_RATE = Decimal("0.0075")
# SECONDARY source only, per docs/rules/deca-verified.md. Not used to force
# any liquidation in this module -- only surfaced as informational.
MARGIN_MAINTENANCE_FRACTION = Decimal("0.30")

MIN_SHARE_PRICE = Decimal("3.00")
MIN_MARKET_CAP = Decimal("25000000")
MIN_SHARES_PER_BUY = 10

BASE_POSITION_LIMIT_FRACTION = Decimal("0.20")
POSITION_LIMIT_MULTIPLIER = Decimal("1.5")
POSITION_CEILING_FRACTION = Decimal("0.30")

DIVERSIFICATION_MINIMUM = Decimal("10000")
# The $5 commission does not count toward the $10,000 net-cost minimum, so
# the minimum *gross* outlay a student must budget is $10,005.00.
REQUIRED_GROSS_OUTLAY = DIVERSIFICATION_MINIMUM + COMMISSION_PER_TRADE

GAME_START = date(2026, 9, 8)
GAME_END = date(2026, 12, 4)
DIVERSIFICATION_DEADLINE = date(2026, 10, 23)
DIVERSIFICATION_HOLD_THROUGH = date(2026, 12, 4)
# Margin cannot usefully be modeled/enabled before this date in v1's simple
# "before Nov 1" guard (see Task 5's cash_and_margin check).
MARGIN_EARLIEST_ENABLE = date(2026, 11, 1)

ELIGIBLE_EXCHANGES = frozenset({"NYSE", "NASDAQ"})
# Normalized (upper-cased, punctuation stripped) forms that must be REJECTED
# as unverified rather than silently allowed or silently banned.
UNVERIFIED_EXCHANGES = frozenset({"NYSEAMERICAN", "AMEX", "NYSEMKT"})

# The ten 2026 NYSE holidays, each verified by weekday (see plan Task 1,
# Step 3). Sep 7 (Labor Day) falls the day before GAME_START; Dec 25
# (Christmas) falls after GAME_END. Both are included for completeness of
# the calendar even though they are outside the game window.
NYSE_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day (Thu)
        date(2026, 1, 19),  # MLK Day (Mon)
        date(2026, 2, 16),  # Washington's Birthday (Mon)
        date(2026, 4, 3),  # Good Friday (Fri)
        date(2026, 5, 25),  # Memorial Day (Mon)
        date(2026, 6, 19),  # Juneteenth (Fri)
        date(2026, 7, 3),  # Independence Day observed (Fri; Jul 4 is a Sat)
        date(2026, 9, 7),  # Labor Day (Mon)
        date(2026, 11, 26),  # Thanksgiving (Thu)
        date(2026, 12, 25),  # Christmas (Fri)
    }
)

_COVERED_YEAR = 2026


def _assert_covered(d: date) -> None:
    """Business-day arithmetic in this module is verified for 2026 only.
    Refuse silently computing a wrong answer for any other year."""
    if d.year != _COVERED_YEAR:
        raise ValueError(
            f"business-day arithmetic in investlab.competitions.deca is only "
            f"verified for {_COVERED_YEAR}; got {d.isoformat()}"
        )


def is_business_day(d: date) -> bool:
    """True for a NYSE trading session: not a weekend, not a 2026 holiday."""
    _assert_covered(d)
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS_2026


def next_business_day(d: date) -> date:
    """The next NYSE trading session strictly after `d`."""
    _assert_covered(d)
    candidate = d + timedelta(days=1)
    while not is_business_day(candidate):
        candidate += timedelta(days=1)
        _assert_covered(candidate)
    return candidate


def trading_days_until(start: date, end: date) -> int:
    """Count of NYSE trading sessions strictly after `start` up to and
    including `end`. `start` itself is not counted."""
    _assert_covered(start)
    _assert_covered(end)
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_business_day(current):
            count += 1
    return count


def calendar_days_until(start: date, end: date) -> int:
    """Plain calendar days between `start` and `end`, inclusive of neither
    endpoint's own day (i.e. `(end - start).days`)."""
    _assert_covered(start)
    _assert_covered(end)
    return (end - start).days
