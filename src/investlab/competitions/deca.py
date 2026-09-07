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

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from investlab.contracts import (
    Bar,
    BlockedOrder,
    BlockReason,
    Instrument,
)

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


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _money(value: Decimal) -> str:
    """Format a Decimal as US currency with thousands separators, e.g.
    Decimal("10005") -> "$10,005.00". Used in every rule-check detail string
    so numbers are unmissable and consistently formatted."""
    quantized = value.quantize(Decimal("0.01"))
    return f"${quantized:,.2f}"


def _normalize_exchange(exchange: str) -> str:
    """Upper-case and strip all non-alphanumeric characters, so 'NYSE
    American', 'NYSE AMERICAN', 'NYSEAMERICAN', 'AMEX' and 'NYSE MKT' all
    land on the same normalized token."""
    return "".join(ch for ch in exchange.upper() if ch.isalnum())


# ---------------------------------------------------------------------------
# Task 2: DecaProfile and eligibility
# ---------------------------------------------------------------------------

_PROHIBITION_REASON = (
    "{symbol} is a commodity- or crypto-backed trust and is prohibited under "
    "three independent grounds: (1) bitcoin is named in the DECA SMG "
    "prohibited-instrument list; (2) commodities are named in the same "
    "prohibited-instrument list; (3) DECA SMG rule 3 defines the eligible "
    "universe as stocks and mutual funds, and a commodity trust is neither. "
    "IBIT and GLD are both prohibited under this same reasoning."
)


def _eligibility_reason(
    instrument: Instrument, bar: Bar, prior_bar: Bar | None
) -> tuple[bool, str, BlockReason | None]:
    """The single source of truth for eligibility. Check order is
    load-bearing: prohibition first (so a banned trust never reports a price
    or exchange problem instead of the prohibition), then exchange, then
    price (today, then the day before), then market cap."""
    if instrument.is_commodity_or_crypto_trust:
        return False, _PROHIBITION_REASON.format(symbol=instrument.symbol), BlockReason.PROHIBITED_SECURITY

    normalized = _normalize_exchange(instrument.exchange)
    if normalized in UNVERIFIED_EXCHANGES:
        reason = (
            f"{instrument.exchange} eligibility is UNVERIFIED against a primary "
            f"DECA SMG source; treated as ineligible until confirmed, not guessed."
        )
        return False, reason, BlockReason.UNVERIFIED_INSTRUMENT
    if normalized not in ELIGIBLE_EXCHANGES:
        reason = (
            f"{instrument.symbol} trades on {instrument.exchange}, which is not "
            f"eligible; DECA SMG restricts the universe to NYSE and NASDAQ only."
        )
        return False, reason, BlockReason.INELIGIBLE_EXCHANGE

    if bar.close < MIN_SHARE_PRICE:
        reason = (
            f"{instrument.symbol} closed at {_money(bar.close)} on "
            f"{bar.session.isoformat()}, below the {_money(MIN_SHARE_PRICE)} minimum."
        )
        return False, reason, BlockReason.PRICE_BELOW_MINIMUM
    if prior_bar is not None and prior_bar.close < MIN_SHARE_PRICE:
        reason = (
            f"{instrument.symbol} closed at {_money(prior_bar.close)} the day "
            f"before ({prior_bar.session.isoformat()}), below the "
            f"{_money(MIN_SHARE_PRICE)} minimum required day-before AND day-of."
        )
        return False, reason, BlockReason.PRICE_BELOW_MINIMUM

    if instrument.market_cap is None:
        reason = (
            f"{instrument.symbol} has an unknown market cap; DECA SMG requires "
            f">= {_money(MIN_MARKET_CAP)} and an unknown value is rejected, not "
            f"assumed eligible."
        )
        return False, reason, BlockReason.INSUFFICIENT_EVIDENCE
    if instrument.market_cap < MIN_MARKET_CAP:
        reason = (
            f"{instrument.symbol} has a market cap of {_money(instrument.market_cap)}, "
            f"below the {_money(MIN_MARKET_CAP)} minimum."
        )
        return False, reason, BlockReason.MARKET_CAP_BELOW_MINIMUM

    return True, "", None


@dataclass(frozen=True, slots=True)
class DecaProfile:
    """`CompetitionProfile` implementation for the DECA Stock Market Game.

    `allows_margin` and `allows_shorting` default to False even though DECA's
    rules permit both -- a deliberate v1 posture (design spec Sec 5), not a
    reflection of the rules themselves. `sec_fee_rate` is UNVERIFIED and
    exists as a configurable knob for exactly that reason.
    """

    sec_fee_rate: Decimal = DEFAULT_SEC_FEE_RATE
    risk_fraction: Decimal = DEFAULT_RISK_FRACTION
    allows_margin: bool = False
    allows_shorting: bool = False
    name: str = "deca"
    starting_cash: Decimal = STARTING_CASH
    commission_per_trade: Decimal = COMMISSION_PER_TRADE

    def is_eligible(
        self, instrument: Instrument, bar: Bar, prior_bar: Bar | None = None
    ) -> tuple[bool, str]:
        """Return (eligible, reason). `prior_bar` is an additional optional
        parameter beyond the `CompetitionProfile` protocol signature, used to
        enforce the day-before >= $3.00 leg of the price rule when the caller
        supplies it. See the contract-defect note in the implementation
        report: the protocol's `is_eligible(instrument, bar)` cannot express
        a two-session rule with a single bar."""
        ok, reason, _ = _eligibility_reason(instrument, bar, prior_bar)
        return ok, reason

    def eligibility_block(
        self, instrument: Instrument, bar: Bar, prior_bar: Bar | None = None
    ) -> BlockedOrder | None:
        """Same evaluation as `is_eligible`, packaged as a `BlockedOrder` for
        callers that need the structured `BlockReason` rather than just a
        boolean and a string. Returns None when eligible."""
        ok, reason, block_reason = _eligibility_reason(instrument, bar, prior_bar)
        if ok:
            return None
        assert block_reason is not None  # every False branch sets one
        return BlockedOrder(symbol=instrument.symbol, reason=block_reason, detail=reason)
