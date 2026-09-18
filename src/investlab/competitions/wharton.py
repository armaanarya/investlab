"""Wharton Investment Simulator (WInS) competition profile, 2026-27 season.

Every rule below comes from the 2026-27 materials published 2026-09-15 on the
competition's SurveyMonkey Apply site (Trading Details, Deliverables, FAQs,
and the Investment Competition Guide). Sources and quotes are in
`docs/rules/wharton-verified.md`. What changed from 2025-26:

- Starting capital is $300,000 (was $500,000).
- Any ETF available on WInS is eligible. The Approved ETF List and the
  hold-at-least-one-ETF rule are gone.
- Trading runs Sept 28 to Nov 6, not to December. The portfolio is frozen
  when the Investment Policy Statement is due.
- A Trading Notes Analysis of three executed trades is due Oct 23.
- An order may not exceed twice the security's current daily volume.

AI-authorship constraint: trading notes are audited against WInS ("Yes, we
will verify this"), and the judged deliverables must be the team's own work.
This module emits only facts, numbers, constraint results, and rule status.
It never generates an investment thesis, a trading note, policy-statement
prose, or any other text a student would submit.

Money is always `decimal.Decimal`. Imports: `investlab.contracts` and the
standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import ClassVar

from investlab.contracts import (
    AccountState,
    AssetClass,
    Bar,
    BlockedOrder,
    BlockReason,
    DailyPlan,
    Instrument,
    RuleCheck,
    RuleStatus,
    SizedOrder,
    SizingConstraints,
)

# --------------------------------------------------------------------------
# 2026-27 rules (published 2026-09-15)
# --------------------------------------------------------------------------

STARTING_CAPITAL = Decimal("300000")
# Last season's figure, and the StockTrak boilerplate figure. Neither applies.
PRIOR_SEASON_STARTING_CAPITAL = Decimal("500000")
MISREPORTED_STARTING_CAPITAL = Decimal("100000")

STOCK_COMMISSION = Decimal("25")
TREASURY_COMMISSION = Decimal("10")
# Applies to stocks ("or the equivalent of $5 in its local currency"). The
# ETF rule is "any ETF available on WInS", with no price floor.
MIN_SHARE_PRICE = Decimal("5")
HARD_TRADE_CAP = 200
# "no more than twice a security's current daily trading volume"
VOLUME_MULTIPLE = 2

# Government bonds on WInS come from these issuers only.
GOVERNMENT_BOND_ISSUERS = ("US", "UK", "DE", "FR", "IT", "NL")

MATERIALS_RELEASE = date(2026, 9, 15)
TRADING_BEGINS = date(2026, 9, 28)
ROSTER_DUE = date(2026, 10, 9)
# The Trading Notes Analysis. Named without the fragment the prose self-check
# below forbids in public names.
NOTES_ANALYSIS_DUE = date(2026, 10, 23)
NOTES_ANALYSIS_TRADES_REQUIRED = 3
INVESTMENT_POLICY_STATEMENT_DUE = date(2026, 11, 6)
# Same day: trading ends and the portfolio is frozen.
TRADING_ENDS = date(2026, 11, 6)
FINAL_REPORT_INSTRUCTIONS = date(2026, 11, 9)
FINAL_REPORT_DUE = date(2026, 12, 4)
DELIVERABLE_CUTOFF = "5:00 p.m. ET"

# Team policy, not rules. Wharton says the competition "does not require
# frequent or same-day trading" and judges the strategy, so turnover stays low.
SELF_IMPOSED_TRADE_BUDGET = 40
# Broad index funds and Treasury ladders can reasonably be a third of the
# book; the per-sleeve targets in configs/wharton_strategy.json are the real
# limit. This ceiling only stops a fat-finger order.
SELF_IMPOSED_POSITION_CEILING = Decimal("0.40")
SELF_IMPOSED_RISK_FRACTION = Decimal("0.01")


class WhartonConfigError(ValueError):
    """A season configuration failed validation."""


@dataclass(frozen=True, slots=True)
class WhartonSeasonConfig:
    """The season parameters. `SEASON_2026_27` is the published season; the
    type exists so a test or a mid-season rule change can substitute one."""

    starting_capital: Decimal
    trading_start: date
    trading_end: date
    position_ceiling_fraction: Decimal = SELF_IMPOSED_POSITION_CEILING
    source: str = "2026-27 Trading Details, published 2026-09-15"

    def __post_init__(self) -> None:
        if self.starting_capital <= 0:
            raise WhartonConfigError("starting_capital must be a positive amount")
        if self.trading_end < self.trading_start:
            raise WhartonConfigError("trading_end is before trading_start")
        if not (Decimal("0") < self.position_ceiling_fraction <= Decimal("1")):
            raise WhartonConfigError(
                "position_ceiling_fraction must be between 0 (exclusive) and 1 (inclusive)"
            )


SEASON_2026_27 = WhartonSeasonConfig(
    starting_capital=STARTING_CAPITAL,
    trading_start=TRADING_BEGINS,
    trading_end=TRADING_ENDS,
)


class TradingWindow(str, Enum):
    NOT_OPEN = "not_open"
    OPEN = "open"
    FROZEN = "frozen"


def trading_window(today: date, season: WhartonSeasonConfig = SEASON_2026_27) -> TradingWindow:
    if today < season.trading_start:
        return TradingWindow.NOT_OPEN
    if today > season.trading_end:
        return TradingWindow.FROZEN
    return TradingWindow.OPEN


def max_quantity_by_volume(volume: int) -> int:
    """The largest order WInS accepts against a day's volume.

    The rule is measured against the *current* day's volume. Before the open
    that is unknown, so callers pass the last completed session's volume and
    say so. Zero volume (bonds, no data) returns 0, meaning "cannot check".
    """
    return max(0, volume) * VOLUME_MULTIPLE


# --------------------------------------------------------------------------
# Trade budget
# --------------------------------------------------------------------------


class BudgetSeverity(str, Enum):
    OK = "ok"
    NOTICE = "notice"
    WARNING = "warning"
    CRITICAL = "critical"
    BLOCKED = "blocked"


_DRAG_FRACTION_QUANT = Decimal("1e-8")
_DRAG_PCT_QUANT = Decimal("1e-6")
_CRITICAL_WINDOW = 10  # trades before hard_cap where severity becomes CRITICAL


@dataclass(frozen=True, slots=True)
class TradeBudget:
    """Season trade usage against a self-imposed budget and the 200-trade cap.
    Every buy and every sell counts as one trade.

    Severity ladder: OK under 30, NOTICE 30-39, WARNING from the self-imposed
    budget (40) through 189, CRITICAL in the last 10 trades before the cap,
    BLOCKED at the cap. Only BLOCKED produces `satisfied=False`; a warning
    must stay satisfied or it would silently kill the plan through
    `DailyPlan.is_actionable`.
    """

    trades_used: int = 0
    self_imposed_budget: int = SELF_IMPOSED_TRADE_BUDGET
    hard_cap: int = HARD_TRADE_CAP
    commission_per_trade: Decimal = STOCK_COMMISSION

    def __post_init__(self) -> None:
        if self.trades_used < 0:
            raise ValueError("trades_used cannot be negative")

    @property
    def remaining_self_imposed(self) -> int:
        return max(0, self.self_imposed_budget - self.trades_used)

    @property
    def remaining_hard_cap(self) -> int:
        return max(0, self.hard_cap - self.trades_used)

    @property
    def commission_spent(self) -> Decimal:
        return self.commission_per_trade * Decimal(self.trades_used)

    @property
    def commission_at_self_imposed_budget(self) -> Decimal:
        return self.commission_per_trade * Decimal(self.self_imposed_budget)

    @property
    def commission_at_hard_cap(self) -> Decimal:
        return self.commission_per_trade * Decimal(self.hard_cap)

    @property
    def severity(self) -> BudgetSeverity:
        if self.trades_used >= self.hard_cap:
            return BudgetSeverity.BLOCKED
        if self.trades_used >= self.hard_cap - _CRITICAL_WINDOW:
            return BudgetSeverity.CRITICAL
        if self.trades_used >= self.self_imposed_budget:
            return BudgetSeverity.WARNING
        if self.trades_used >= 30:
            return BudgetSeverity.NOTICE
        return BudgetSeverity.OK

    @property
    def is_exhausted(self) -> bool:
        return self.trades_used >= self.hard_cap

    def can_trade(self, n: int = 1) -> bool:
        return self.trades_used + n <= self.hard_cap

    def with_trades(self, n: int) -> TradeBudget:
        return replace(self, trades_used=self.trades_used + n)

    def commission_drag_fraction(self, equity: Decimal) -> Decimal:
        if equity <= 0:
            raise ValueError("equity must be positive to compute commission drag")
        return (self.commission_spent / equity).quantize(
            _DRAG_FRACTION_QUANT, rounding=ROUND_HALF_UP
        )

    def commission_drag_pct(self, equity: Decimal) -> Decimal:
        return (self.commission_drag_fraction(equity) * Decimal(100)).quantize(
            _DRAG_PCT_QUANT, rounding=ROUND_HALF_UP
        )

    def to_rule_check(self, equity: Decimal) -> RuleCheck:
        sev = self.severity
        if sev is BudgetSeverity.BLOCKED:
            detail = (
                f"BLOCKED: {self.trades_used} trades used meets the {self.hard_cap}-trade "
                "cap. No further trades may be entered this season."
            )
            return RuleCheck("trade_budget", RuleStatus.VERIFIED, False, detail)
        if sev is BudgetSeverity.CRITICAL:
            detail = (
                f"CRITICAL: {self.trades_used} of {self.hard_cap} trades used; "
                f"only {self.remaining_hard_cap} remain this season."
            )
        elif sev is BudgetSeverity.WARNING:
            detail = (
                f"WARNING: {self.trades_used} trades used, past the self-imposed "
                f"budget of {self.self_imposed_budget} (cap {self.hard_cap}). "
                "Wharton does not reward activity; the strategy is what is judged."
            )
        elif sev is BudgetSeverity.NOTICE:
            detail = (
                f"NOTICE: {self.trades_used} trades used, approaching the "
                f"self-imposed budget of {self.self_imposed_budget} (cap {self.hard_cap})."
            )
        else:
            detail = (
                f"OK: {self.trades_used} of {self.self_imposed_budget} self-imposed "
                f"trade budget used (cap {self.hard_cap})."
            )
        if equity > 0:
            detail += f" Commission spent so far: ${self.commission_spent:,.2f}."
        return RuleCheck("trade_budget", RuleStatus.VERIFIED, True, detail)

    def blocked_order(self, symbol: str) -> BlockedOrder:
        return BlockedOrder(
            symbol,
            BlockReason.TRADE_BUDGET_EXHAUSTED,
            f"{symbol}: trade cap reached ({self.trades_used}/{self.hard_cap} trades used).",
        )


# --------------------------------------------------------------------------
# WhartonProfile
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WhartonProfile:
    """A `CompetitionProfile` for WInS, loaded with the 2026-27 rules."""

    season: WhartonSeasonConfig = SEASON_2026_27
    budget: TradeBudget = field(default_factory=TradeBudget)

    name: ClassVar[str] = "wharton"
    display_name: ClassVar[str] = "Wharton Investment Simulator (WInS)"
    allows_margin: ClassVar[bool] = False
    allows_shorting: ClassVar[bool] = False
    commission_per_trade: ClassVar[Decimal] = STOCK_COMMISSION
    treasury_commission_per_trade: ClassVar[Decimal] = TREASURY_COMMISSION
    min_share_price: ClassVar[Decimal] = MIN_SHARE_PRICE

    @property
    def starting_cash(self) -> Decimal:
        return self.season.starting_capital

    @property
    def position_ceiling_fraction(self) -> Decimal:
        return self.season.position_ceiling_fraction

    def with_season(self, season: WhartonSeasonConfig) -> WhartonProfile:
        return replace(self, season=season)

    def with_trade_budget(self, budget: TradeBudget) -> WhartonProfile:
        return replace(self, budget=budget)

    def commission_for(self, asset_class: AssetClass) -> Decimal:
        if asset_class is AssetClass.BOND:
            return self.treasury_commission_per_trade
        return self.commission_per_trade

    # -- eligibility ----------------------------------------------------------

    def evaluate(self, instrument: Instrument, bar: Bar) -> BlockedOrder | None:
        """First match wins: crypto -> leveraged/inverse -> asset class ->
        exchange -> stock price floor."""
        symbol = instrument.symbol

        if instrument.is_spot_bitcoin_etf:
            return BlockedOrder(
                symbol,
                BlockReason.PROHIBITED_SECURITY,
                f"{symbol} holds crypto. Wharton bans crypto outright; being an "
                "ETF does not change that.",
            )

        if instrument.is_leveraged:
            return BlockedOrder(
                symbol,
                BlockReason.PROHIBITED_SECURITY,
                f"{symbol} is a leveraged or inverse fund. The rules allow any ETF "
                "on WInS but ban derivatives, and these funds are built from swaps "
                "and futures, so the tool reads them as banned. Ask Wharton before "
                "relying on the other reading.",
            )

        if instrument.asset_class not in (AssetClass.STOCK, AssetClass.ETF, AssetClass.BOND):
            return BlockedOrder(
                symbol,
                BlockReason.PROHIBITED_SECURITY,
                f"{symbol} is a {instrument.asset_class.value}. Wharton allows cash, "
                "stocks, ETFs and government bonds only.",
            )

        if instrument.is_commodity_or_crypto_trust and instrument.asset_class is not AssetClass.ETF:
            return BlockedOrder(
                symbol,
                BlockReason.PROHIBITED_SECURITY,
                f"{symbol} is a commodity or crypto trust that is not an ETF.",
            )

        if not instrument.exchange:
            return BlockedOrder(
                symbol,
                BlockReason.INELIGIBLE_EXCHANGE,
                f"{symbol} has no listed exchange; WInS lists every eligible security.",
            )

        if instrument.asset_class is AssetClass.STOCK and bar.close < self.min_share_price:
            local = "" if instrument.currency == "USD" else f" ({instrument.currency}; convert)"
            return BlockedOrder(
                symbol,
                BlockReason.PRICE_BELOW_MINIMUM,
                f"{symbol} at {bar.close}{local} is below the $5 minimum stock price.",
            )

        return None

    def is_eligible(self, instrument: Instrument, bar: Bar) -> tuple[bool, str]:
        blocked = self.evaluate(instrument, bar)
        if blocked is None:
            return True, ""
        return False, blocked.detail

    def volume_block(self, symbol: str, quantity: int, volume: int) -> BlockedOrder | None:
        """Block an order larger than twice the reference day's volume.
        Volume 0 means no data (bonds), which is reported, not blocked."""
        limit = max_quantity_by_volume(volume)
        if volume > 0 and quantity > limit:
            return BlockedOrder(
                symbol,
                BlockReason.RULE_CONFLICT,
                f"{symbol}: {quantity:,} shares exceeds twice the last session's "
                f"volume ({volume:,}), so WInS would reject it. Split it across "
                f"days or cap it at {limit:,}.",
            )
        return None

    # -- sizing -----------------------------------------------------------

    def sizing_constraints(self, account: AccountState) -> SizingConstraints:
        return SizingConstraints(
            equity=account.equity,
            spendable_cash=account.cash,  # margin banned: cash only
            risk_fraction=SELF_IMPOSED_RISK_FRACTION,
            position_ceiling_fraction=self.position_ceiling_fraction,
            min_shares=1,
            min_price=Decimal("0"),
            commission_per_trade=self.commission_per_trade,
            sell_fee_rate=Decimal("0"),
        )

    # -- rule checks --------------------------------------------------------

    def check_rules(self, account: AccountState, today: date) -> list[RuleCheck]:
        checks = [
            RuleCheck(
                "season_rules_loaded",
                RuleStatus.VERIFIED,
                True,
                f"2026-27 rules loaded ({self.season.source}). Starting capital "
                f"${self.starting_cash:,}, not last season's "
                f"${PRIOR_SEASON_STARTING_CAPITAL:,} and not the "
                f"${MISREPORTED_STARTING_CAPITAL:,} StockTrak boilerplate.",
            ),
            self._trading_window_check(today),
            self.budget.to_rule_check(account.equity),
            self._notes_analysis_check(today),
            self._commission_drag_check(account),
            self._margin_and_shorting_check(account),
            RuleCheck(
                "student_authorship",
                RuleStatus.VERIFIED,
                True,
                "Trading notes are checked against WInS and the judged deliverables "
                "must be in the team's own voice. This tool supplies numbers and "
                "rule results only; any AI help used must be cited the way the "
                "competition requires.",
            ),
        ]
        return checks

    def _trading_window_check(self, today: date) -> RuleCheck:
        window = trading_window(today, self.season)
        if window is TradingWindow.NOT_OPEN:
            detail = (
                f"Trading opens {self.season.trading_start.isoformat()}. Until then "
                "WInS is a practice account; trades there do not count."
            )
        elif window is TradingWindow.OPEN:
            days_left = (self.season.trading_end - today).days
            detail = (
                f"Trading is open until {self.season.trading_end.isoformat()} "
                f"({days_left} calendar days). The portfolio is then frozen and "
                "cannot change."
            )
        else:
            detail = (
                f"Trading ended {self.season.trading_end.isoformat()}. The portfolio "
                "is frozen; no order can be placed."
            )
        return RuleCheck(
            "trading_window", RuleStatus.VERIFIED, True, detail, deadline=self.season.trading_end
        )

    def _notes_analysis_check(self, today: date) -> RuleCheck:
        need = NOTES_ANALYSIS_TRADES_REQUIRED
        have = self.budget.trades_used
        # A progress item until the deadline: failing it earlier would make
        # every plan non-actionable before the trades that satisfy it exist.
        satisfied = have >= need or today <= NOTES_ANALYSIS_DUE
        detail = (
            f"The Trading Notes Analysis ({NOTES_ANALYSIS_DUE.isoformat()}, "
            f"{DELIVERABLE_CUTOFF}) reflects on {need} trades executed in WInS, each "
            f"with its Trading Note. Trades recorded: {have}."
        )
        if not satisfied and today > NOTES_ANALYSIS_DUE:
            detail += " The deadline has passed."
        return RuleCheck(
            "notes_analysis_trades",
            RuleStatus.VERIFIED,
            satisfied,
            detail,
            deadline=NOTES_ANALYSIS_DUE,
        )

    def _commission_drag_check(self, account: AccountState) -> RuleCheck:
        equity = account.equity
        spent = self.budget.commission_spent
        if equity <= 0:
            return RuleCheck(
                "commission_drag",
                RuleStatus.VERIFIED,
                True,
                f"Commission spent so far: ${spent:,.2f} "
                f"({self.budget.trades_used} trades at ${self.budget.commission_per_trade}).",
            )
        pct = self.budget.commission_drag_pct(equity)
        return RuleCheck(
            "commission_drag",
            RuleStatus.VERIFIED,
            True,
            f"Commission spent so far: ${spent:,.2f} on {self.budget.trades_used} trades "
            f"(${self.budget.commission_per_trade} per stock or ETF trade, "
            f"${self.treasury_commission_per_trade} per Treasury), {pct:.2f}% of "
            f"${equity:,.2f} equity.",
        )

    def _margin_and_shorting_check(self, account: AccountState) -> RuleCheck:
        problems = []
        if account.liabilities > 0:
            problems.append(f"margin liability of ${account.liabilities:,.2f}; Wharton bans margin")
        shorts = sorted(pos.symbol for pos in account.positions if pos.quantity < 0)
        if shorts:
            problems.append(f"short position(s) in {', '.join(shorts)}; Wharton bans shorting")
        if problems:
            return RuleCheck(
                "margin_and_shorting_ban",
                RuleStatus.VERIFIED,
                False,
                "VIOLATION: " + "; ".join(problems) + ".",
            )
        return RuleCheck(
            "margin_and_shorting_ban",
            RuleStatus.VERIFIED,
            True,
            "No margin and no short positions; both are banned.",
        )

    # -- execution ----------------------------------------------------------

    def execution_note(self, today: date) -> str:
        return " ".join(self.execution_note_lines(today))

    def execution_note_lines(self, today: date) -> tuple[str, ...]:
        lines = [
            "U.S. orders fill at real-time prices, 9:30 a.m. to 4:00 p.m. ET.",
            "Held-position prices on WInS lag 10 to 15 minutes; bid and ask are live.",
            "Orders entered while the market is closed fill at the next open.",
            "International stocks settle at the end of the next trading day, in "
            "local currency converted automatically.",
            "Bonds price once a day at the U.S. open and pay coupons every six months.",
            "Unlike DECA, which fills every order at the day's close.",
        ]
        window = trading_window(today, self.season)
        if window is TradingWindow.NOT_OPEN:
            lines.append(f"Trading opens {self.season.trading_start.isoformat()}.")
        elif window is TradingWindow.FROZEN:
            lines.append("Trading has ended; the portfolio is frozen.")
        else:
            lines.append(
                f"Trading ends {self.season.trading_end.isoformat()}; the portfolio then freezes."
            )
        return tuple(lines)


def build_daily_plan(
    profile: WhartonProfile,
    account: AccountState,
    today: date,
    orders: tuple[SizedOrder, ...] = (),
    blocked: tuple[BlockedOrder, ...] = (),
    data_is_stale: bool = False,
    notes: tuple[str, ...] = (),
) -> DailyPlan:
    """Assemble the plan. Outside the trading window every order is withheld,
    and at the trade cap every order is blocked."""
    rule_checks = profile.check_rules(account, today)
    window = trading_window(today, profile.season)
    extra: tuple[BlockedOrder, ...] = ()
    emitted = orders

    if window is not TradingWindow.OPEN:
        reason = (
            f"trading opens {profile.season.trading_start.isoformat()}"
            if window is TradingWindow.NOT_OPEN
            else f"the portfolio froze {profile.season.trading_end.isoformat()}"
        )
        extra = tuple(
            BlockedOrder(o.symbol, BlockReason.RULE_CONFLICT, f"{o.symbol}: withheld, {reason}.")
            for o in orders
        )
        emitted = ()
    elif profile.budget.is_exhausted:
        extra = tuple(profile.budget.blocked_order(o.symbol) for o in orders)
        emitted = ()

    return DailyPlan(
        profile=profile.name,
        as_of=today,
        data_is_stale=data_is_stale,
        account=account,
        orders=emitted,
        blocked=blocked + extra,
        rule_checks=tuple(rule_checks),
        notes=notes,
    )
