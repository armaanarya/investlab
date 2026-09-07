"""Frozen cross-module contracts.

Every type that crosses a module boundary lives here. The core, the DECA
module, and the Wharton module are built concurrently against this file, so
it is the integration surface: changing a signature here breaks other people's
work. Add new types freely; do not alter existing ones without saying so.

Money is always Decimal. Share quantities are always int. Dates are always
`datetime.date` in US/Eastern trading-session terms. Timestamps that need a
clock are timezone-aware and stored in UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Protocol, runtime_checkable

# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class AssetClass(str, Enum):
    """Competition-facing asset classification.

    Note the DECA quirk this exists to encode: an ETF is classified as a
    STOCK for diversification purposes, including a bond ETF. A bond mutual
    fund is classified as a MUTUAL_FUND. Do not infer class from the ticker.
    """

    STOCK = "stock"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"
    BOND = "bond"
    CASH = "cash"


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"
    REVIEW = "review"
    NO_TRADE = "no_trade"


class BlockReason(str, Enum):
    """Why a candidate order was not emitted. Every NO_TRADE carries one."""

    STALE_DATA = "stale_data"
    INSUFFICIENT_CASH = "insufficient_cash"
    MIN_LOT_EXCEEDS_RISK = "min_lot_exceeds_risk"
    MIN_LOT_EXCEEDS_CASH = "min_lot_exceeds_cash"
    POSITION_CEILING = "position_ceiling"
    PRICE_BELOW_MINIMUM = "price_below_minimum"
    MARKET_CAP_BELOW_MINIMUM = "market_cap_below_minimum"
    INELIGIBLE_EXCHANGE = "ineligible_exchange"
    PROHIBITED_SECURITY = "prohibited_security"
    RULE_CONFLICT = "rule_conflict"
    UNVERIFIED_INSTRUMENT = "unverified_instrument"
    TRADE_BUDGET_EXHAUSTED = "trade_budget_exhausted"
    DIVERSIFICATION_LOCK = "diversification_lock"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RuleStatus(str, Enum):
    VERIFIED = "verified"
    CONFLICTING = "conflicting"
    INCOMPLETE = "incomplete"


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bar:
    """One completed daily session. Prices are raw, not back-adjusted.

    `adj_close` carries the provider's adjusted close for return computation.
    Never mix `adj_close` with raw `high`/`low` in the same calculation: ATR
    and breakout levels use raw OHLC, return series use adj_close.
    """

    symbol: str
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int
    source: str

    def __post_init__(self) -> None:
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"{self.symbol} {self.session}: open outside low/high")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"{self.symbol} {self.session}: close outside low/high")
        if self.volume < 0:
            raise ValueError(f"{self.symbol} {self.session}: negative volume")


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradeable thing, with the metadata the rules engines need."""

    symbol: str
    name: str
    asset_class: AssetClass
    exchange: str
    currency: str = "USD"
    market_cap: Decimal | None = None
    is_leveraged: bool = False
    # True for spot-crypto and physically-backed commodity trusts (IBIT, GLD,
    # SLV). Both competitions prohibit these. Set explicitly, never inferred.
    is_commodity_or_crypto_trust: bool = False


@runtime_checkable
class PriceProvider(Protocol):
    """A source of daily bars. Implementations must not raise on a missing
    symbol; they return an empty list so one bad ticker cannot fail a pull."""

    name: str

    def fetch(self, symbols: list[str], start: date, end: date) -> list[Bar]: ...

    def available(self) -> bool:
        """False when credentials or network are missing. The caller falls
        through to the next provider rather than failing the run."""
        ...


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lot:
    """One purchase. Diversification tests measure NET COST at purchase, so
    the original cost basis must survive every later mark-to-market."""

    symbol: str
    quantity: int
    price: Decimal
    commission: Decimal
    opened: date

    @property
    def net_cost(self) -> Decimal:
        """Cost basis excluding commission.

        DECA measures the $10,000-per-asset-class minimum on net cost 'minus
        the $5 transaction fee', i.e. the commission does NOT count toward
        the minimum. Budget above $10,000 to absorb it.
        """
        return self.price * Decimal(self.quantity)

    @property
    def gross_cost(self) -> Decimal:
        return self.net_cost + self.commission


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    asset_class: AssetClass
    lots: tuple[Lot, ...]

    @property
    def quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)

    @property
    def net_cost(self) -> Decimal:
        return sum((lot.net_cost for lot in self.lots), Decimal("0"))


@dataclass(frozen=True, slots=True)
class Fill:
    """A completed transaction as recorded by the official platform statement.
    The platform is the authoritative record; our ledger mirrors it."""

    symbol: str
    action: Action
    quantity: int
    price: Decimal
    commission: Decimal
    fees: Decimal
    session: date
    order_id: str | None = None


@dataclass(frozen=True, slots=True)
class AccountState:
    """A frozen snapshot. Sizing reserves cash against this, never against a
    live mutable balance, so two candidates cannot spend the same dollar."""

    as_of: date
    cash: Decimal
    positions: tuple[Position, ...]
    marks: dict[str, Decimal] = field(default_factory=dict)
    receivables: Decimal = Decimal("0")
    liabilities: Decimal = Decimal("0")

    @property
    def marked_positions(self) -> Decimal:
        total = Decimal("0")
        for pos in self.positions:
            mark = self.marks.get(pos.symbol)
            if mark is None:
                raise ValueError(f"no mark for held position {pos.symbol}")
            total += mark * Decimal(pos.quantity)
        return total

    @property
    def equity(self) -> Decimal:
        """The one definition of equity. Risk budgets use this, never
        buying power, and never cash alone."""
        return self.cash + self.marked_positions + self.receivables - self.liabilities


# --------------------------------------------------------------------------
# Orders and plans
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SizedOrder:
    """A concrete instruction the student types into the platform."""

    symbol: str
    action: Action
    quantity: int
    price_bound: Decimal
    estimated_notional: Decimal
    estimated_commission: Decimal
    protective_reference: Decimal | None
    planned_risk: Decimal
    gap_stress_loss: Decimal
    binding_constraint: str
    rationale: str


@dataclass(frozen=True, slots=True)
class BlockedOrder:
    symbol: str
    reason: BlockReason
    detail: str


@dataclass(frozen=True, slots=True)
class SizingConstraints:
    """Everything the integer share solver needs. Assembled by a Profile."""

    equity: Decimal
    spendable_cash: Decimal
    risk_fraction: Decimal
    position_ceiling_fraction: Decimal
    min_shares: int
    min_price: Decimal
    commission_per_trade: Decimal
    sell_fee_rate: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class RuleCheck:
    """One competition rule evaluated against the current portfolio."""

    name: str
    status: RuleStatus
    satisfied: bool
    detail: str
    deadline: date | None = None


@dataclass(frozen=True, slots=True)
class DailyPlan:
    """The output of `investlab daily`. Everything the student needs."""

    profile: str
    as_of: date
    data_is_stale: bool
    account: AccountState
    orders: tuple[SizedOrder, ...]
    blocked: tuple[BlockedOrder, ...]
    rule_checks: tuple[RuleCheck, ...]
    notes: tuple[str, ...] = ()

    @property
    def is_actionable(self) -> bool:
        """False when any rule check is unsatisfied and hard-blocking, or the
        data is stale. A non-actionable plan still prints, clearly labeled."""
        return not self.data_is_stale and all(
            c.satisfied for c in self.rule_checks if c.status is RuleStatus.VERIFIED
        )


# --------------------------------------------------------------------------
# Competition profiles
# --------------------------------------------------------------------------


@runtime_checkable
class CompetitionProfile(Protocol):
    """What a competition module must implement.

    Implementations live in `competitions/deca.py` and
    `competitions/wharton.py` and must not import each other.
    """

    name: str
    starting_cash: Decimal
    commission_per_trade: Decimal
    allows_margin: bool
    allows_shorting: bool

    def is_eligible(self, instrument: Instrument, bar: Bar) -> tuple[bool, str]:
        """Return (eligible, reason). Reason is '' when eligible."""
        ...

    def sizing_constraints(self, account: AccountState) -> SizingConstraints: ...

    def check_rules(self, account: AccountState, today: date) -> list[RuleCheck]:
        """Every competition rule evaluated. Empty list is never correct."""
        ...

    def execution_note(self, today: date) -> str:
        """One line telling the student when and how these orders will fill.
        DECA and Wharton differ fundamentally here, and the student must see
        which regime they are in on every order sheet."""
        ...
