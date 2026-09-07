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
from enum import Enum

from investlab.contracts import (
    AccountState,
    Action,
    AssetClass,
    Bar,
    BlockedOrder,
    BlockReason,
    Fill,
    Instrument,
    RuleCheck,
    RuleStatus,
    SizingConstraints,
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


def _pct(fraction: Decimal) -> str:
    """Format a fraction as a one-decimal percentage, e.g. Decimal("0.31")
    -> "31.0%"."""
    return f"{(fraction * Decimal(100)).quantize(Decimal('0.1'))}%"


# ---------------------------------------------------------------------------
# Task 4: diversification classes
# ---------------------------------------------------------------------------


class AssetBucket(str, Enum):
    """The three classes DECA's diversification rule counts. An ETF
    (including a bond ETF) is a STOCK; a bond mutual fund is a MUTUAL_FUND.
    See `docs/rules/deca-verified.md`."""

    STOCKS = "stocks"
    MUTUAL_FUNDS = "mutual_funds"
    BONDS = "bonds"


_ASSET_CLASS_TO_BUCKET: dict[AssetClass, AssetBucket] = {
    AssetClass.STOCK: AssetBucket.STOCKS,
    AssetClass.ETF: AssetBucket.STOCKS,
    AssetClass.MUTUAL_FUND: AssetBucket.MUTUAL_FUNDS,
    AssetClass.BOND: AssetBucket.BONDS,
    # AssetClass.CASH is intentionally absent -> maps to None.
}


def bucket_for(asset_class: AssetClass) -> AssetBucket | None:
    """Map a contract-level `AssetClass` to the diversification bucket it
    counts toward. Cash counts toward none of the three."""
    return _ASSET_CLASS_TO_BUCKET.get(asset_class)


@dataclass(frozen=True, slots=True)
class ClassifiedFill:
    """A `Fill` paired with the asset class it was classified under at the
    time of the trade, used to reconstruct which bucket a SELL emptied."""

    fill: Fill
    asset_class: AssetClass


_BUCKET_LABEL = {
    AssetBucket.STOCKS: "the stocks class",
    AssetBucket.MUTUAL_FUNDS: "the mutual funds class",
    AssetBucket.BONDS: "the bonds class",
}


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

    # -----------------------------------------------------------------
    # Task 3: sizing constraints and the position ceiling
    # -----------------------------------------------------------------

    def sizing_constraints(self, account: AccountState) -> SizingConstraints:
        """Everything the integer share solver (owned by CORE) needs to size
        an order under DECA's rules."""
        return SizingConstraints(
            equity=account.equity,
            spendable_cash=self.spendable_cash(account),
            risk_fraction=self.risk_fraction,
            position_ceiling_fraction=POSITION_CEILING_FRACTION,
            min_shares=MIN_SHARES_PER_BUY,
            min_price=MIN_SHARE_PRICE,
            commission_per_trade=self.commission_per_trade,
            sell_fee_rate=self.sec_fee_rate,
        )

    def spendable_cash(self, account: AccountState) -> Decimal:
        """Cash available to spend on new buys. Without margin, only
        positive cash counts. With margin, buying power extends to half of
        total equity minus whatever is already borrowed (negative cash)."""
        if not self.allows_margin:
            return max(account.cash, Decimal("0"))
        borrowed = max(-account.cash, Decimal("0"))
        headroom = account.equity * MARGIN_MAX_EQUITY_FRACTION - borrowed
        return max(account.cash + headroom, Decimal("0"))

    def position_weight(self, account: AccountState, symbol: str) -> Decimal:
        """`symbol`'s marked value as a fraction of total equity. Zero when
        the account holds no position in `symbol` or equity is zero."""
        equity = account.equity
        if equity == 0:
            return Decimal("0")
        position = next((p for p in account.positions if p.symbol == symbol), None)
        if position is None:
            return Decimal("0")
        mark = account.marks.get(symbol)
        if mark is None:
            return Decimal("0")
        return (mark * Decimal(position.quantity)) / equity

    def can_add_to(self, account: AccountState, symbol: str) -> tuple[bool, str]:
        """Whether a BUY may add to an existing position in `symbol`. At or
        above the 30% ceiling, buys are blocked -- the position is held, not
        force-sold; this method never recommends a sale."""
        weight = self.position_weight(account, symbol)
        if weight >= POSITION_CEILING_FRACTION:
            reason = (
                f"{symbol} is {_pct(weight)} of equity, at or above the "
                f"{_pct(POSITION_CEILING_FRACTION)} position ceiling; no "
                f"further buys are allowed. The existing position is held, "
                f"not sold."
            )
            return False, reason
        return True, ""

    def check_buy_quantity(self, quantity: int) -> tuple[bool, str]:
        """DECA requires a minimum 10-share lot on buys."""
        if quantity < MIN_SHARES_PER_BUY:
            reason = (
                f"buy quantity {quantity} is below the "
                f"{MIN_SHARES_PER_BUY}-share minimum required on buys."
            )
            return False, reason
        return True, ""

    def check_sell_quantity(self, quantity: int) -> tuple[bool, str]:
        """Sells and covers may be fewer than the 10-share buy minimum; no
        floor is verified for them."""
        return True, ""

    # -----------------------------------------------------------------
    # Task 4: the diversification engine
    # -----------------------------------------------------------------

    def class_net_cost(self, account: AccountState, bucket: AssetBucket) -> Decimal:
        """Total net cost (purchase price x quantity, excluding commission)
        held in `bucket`. Only LONG positions count -- a short position
        (negative quantity) contributes nothing, which is how "only long
        stock positions count toward the stock bucket" is enforced."""
        total = Decimal("0")
        for position in account.positions:
            if bucket_for(position.asset_class) != bucket:
                continue
            if position.quantity <= 0:
                continue
            total += position.net_cost
        return total

    def _latest_sell_session(
        self, bucket: AssetBucket, sell_history: tuple[ClassifiedFill, ...]
    ) -> date | None:
        sessions = [
            cf.fill.session
            for cf in sell_history
            if cf.fill.action == Action.SELL and bucket_for(cf.asset_class) == bucket
        ]
        return max(sessions) if sessions else None

    def _bucket_check(
        self,
        account: AccountState,
        bucket: AssetBucket,
        sell_history: tuple[ClassifiedFill, ...],
    ) -> RuleCheck:
        name = f"diversification_{bucket.value}"
        net_cost = self.class_net_cost(account, bucket)
        label = _BUCKET_LABEL[bucket]

        if net_cost >= DIVERSIFICATION_MINIMUM:
            detail = (
                f"{label} holds {_money(net_cost)} net cost, at or above the "
                f"{_money(DIVERSIFICATION_MINIMUM)} minimum; requirement "
                f"satisfied. A later market-value decline requires no action. "
                f"Hold through {DIVERSIFICATION_HOLD_THROUGH.isoformat()}."
            )
            return RuleCheck(
                name=name,
                status=RuleStatus.VERIFIED,
                satisfied=True,
                detail=detail,
                deadline=DIVERSIFICATION_HOLD_THROUGH,
            )

        latest_sell = self._latest_sell_session(bucket, sell_history)
        if latest_sell is not None:
            deadline = max(DIVERSIFICATION_DEADLINE, next_business_day(latest_sell))
            clock_note = (
                f" A sale in {label} on {latest_sell.isoformat()} starts a one "
                f"business day clock to restore the minimum, due "
                f"{deadline.isoformat()} ({deadline.strftime('%A')})."
            )
        else:
            deadline = DIVERSIFICATION_DEADLINE
            clock_note = ""

        shortfall = DIVERSIFICATION_MINIMUM - net_cost
        detail = (
            f"{label} holds {_money(net_cost)} net cost, {_money(shortfall)} "
            f"short of the {_money(DIVERSIFICATION_MINIMUM)} minimum. Budget "
            f"{_money(REQUIRED_GROSS_OUTLAY)} gross to absorb the "
            f"{_money(COMMISSION_PER_TRADE)} commission. Due by "
            f"{deadline.isoformat()} ({deadline.strftime('%A')})."
        ) + clock_note

        return RuleCheck(
            name=name, status=RuleStatus.VERIFIED, satisfied=False, detail=detail, deadline=deadline
        )

    def diversification_checks(
        self,
        account: AccountState,
        today: date,
        sell_history: tuple[ClassifiedFill, ...] = (),
    ) -> list[RuleCheck]:
        """One `RuleCheck` per asset bucket: `diversification_stocks`,
        `diversification_mutual_funds`, `diversification_bonds`. `today` is
        accepted for interface symmetry with `check_rules`; the deadline
        arithmetic depends only on `sell_history` and today's account
        holdings, not on `today` itself."""
        del today  # not needed by the per-bucket calculation itself
        return [
            self._bucket_check(account, bucket, sell_history)
            for bucket in (AssetBucket.STOCKS, AssetBucket.MUTUAL_FUNDS, AssetBucket.BONDS)
        ]

    def check_rules(
        self,
        account: AccountState,
        today: date,
        sell_history: tuple[ClassifiedFill, ...] = (),
    ) -> list[RuleCheck]:
        """Every DECA rule evaluated against the current portfolio. `today`
        and `sell_history` are extra optional parameters beyond the
        `CompetitionProfile` protocol's `check_rules(account, today)` --
        see the contract-defect note in the implementation report."""
        return self.diversification_checks(account, today, sell_history)
