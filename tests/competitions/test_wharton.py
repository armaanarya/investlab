"""Wharton WInS competition module tests, 2026-27 rules (published 2026-09-15).

Starting capital is $300,000: not last season's $500,000 and not StockTrak's
$100,000 boilerplate. Money is Decimal everywhere. A float here is a bug.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investlab.competitions import wharton
from investlab.competitions.wharton import (
    MISREPORTED_STARTING_CAPITAL,
    NOTES_ANALYSIS_DUE,
    PRIOR_SEASON_STARTING_CAPITAL,
    SEASON_2026_27,
    TRADING_ENDS,
    BudgetSeverity,
    TradeBudget,
    TradingWindow,
    WhartonConfigError,
    WhartonProfile,
    WhartonSeasonConfig,
    build_daily_plan,
    max_quantity_by_volume,
    trading_window,
)
from investlab.contracts import (
    AccountState,
    Action,
    AssetClass,
    Bar,
    BlockReason,
    CompetitionProfile,
    Instrument,
    Lot,
    Position,
    RuleCheck,
    SizedOrder,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def stock(symbol: str, exchange: str = "NASDAQ") -> Instrument:
    return Instrument(symbol, symbol, AssetClass.STOCK, exchange)


def etf(symbol: str, exchange: str = "NYSEARCA") -> Instrument:
    return Instrument(symbol, symbol, AssetClass.ETF, exchange)


def bar(symbol: str, close: str, session: date = date(2026, 9, 4), volume: int = 1_000_000) -> Bar:
    price = Decimal(close)
    return Bar(symbol, session, price, price, price, price, price, volume, "test")


def empty_account(as_of: date = date(2026, 9, 6)) -> AccountState:
    return AccountState(as_of, Decimal("0"), (), {}, Decimal("0"), Decimal("0"))


def account_worth_300k(as_of: date = date(2026, 10, 1)) -> AccountState:
    return AccountState(as_of, Decimal("300000"), (), {}, Decimal("0"), Decimal("0"))


def account_holding(
    symbol: str,
    asset_class: AssetClass,
    qty: int,
    mark: str,
    as_of: date = date(2026, 10, 1),
    cash: str = "1000",
) -> AccountState:
    lot = Lot(symbol, qty, Decimal(mark), Decimal("25"), date(2026, 9, 29))
    position = Position(symbol, asset_class, (lot,))
    return AccountState(
        as_of, Decimal(cash), (position,), {symbol: Decimal(mark)}, Decimal("0"), Decimal("0")
    )


def named(checks: list[RuleCheck], name: str) -> RuleCheck:
    for c in checks:
        if c.name == name:
            return c
    raise AssertionError(f"no rule check named {name!r} in {[c.name for c in checks]}")


def a_sized_order(symbol: str) -> SizedOrder:
    return SizedOrder(
        symbol=symbol,
        action=Action.BUY,
        quantity=1,
        price_bound=Decimal("100"),
        estimated_notional=Decimal("100"),
        estimated_commission=Decimal("25"),
        protective_reference=None,
        planned_risk=Decimal("0"),
        gap_stress_loss=Decimal("0"),
        binding_constraint="test fixture",
        rationale="test fixture order",
    )


OPEN_DAY = date(2026, 10, 1)


def profile_with_trades(n: int) -> WhartonProfile:
    return WhartonProfile().with_trade_budget(TradeBudget(trades_used=n))


# ---------------------------------------------------------------------------
# Identity and season
# ---------------------------------------------------------------------------


def test_profile_satisfies_the_frozen_protocol():
    assert isinstance(WhartonProfile(), CompetitionProfile)


def test_starting_capital_is_300k():
    p = WhartonProfile()
    assert p.starting_cash == Decimal("300000")
    assert p.starting_cash not in (PRIOR_SEASON_STARTING_CAPITAL, MISREPORTED_STARTING_CAPITAL)


def test_margin_and_shorting_are_banned():
    assert WhartonProfile.allows_margin is False
    assert WhartonProfile.allows_shorting is False


def test_commissions_by_asset_class():
    p = WhartonProfile()
    assert p.commission_for(AssetClass.STOCK) == Decimal("25")
    assert p.commission_for(AssetClass.ETF) == Decimal("25")
    assert p.commission_for(AssetClass.BOND) == Decimal("10")


def test_season_config_validates():
    with pytest.raises(WhartonConfigError):
        WhartonSeasonConfig(Decimal("0"), date(2026, 9, 28), date(2026, 11, 6))
    with pytest.raises(WhartonConfigError):
        WhartonSeasonConfig(Decimal("1"), date(2026, 11, 6), date(2026, 9, 28))


@pytest.mark.parametrize(
    "today, window",
    [
        (date(2026, 9, 27), TradingWindow.NOT_OPEN),
        (date(2026, 9, 28), TradingWindow.OPEN),
        (date(2026, 11, 6), TradingWindow.OPEN),
        (date(2026, 11, 7), TradingWindow.FROZEN),
    ],
)
def test_trading_window_runs_sept_28_to_nov_6(today, window):
    assert trading_window(today) is window


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def test_plain_stock_at_five_dollars_is_eligible():
    assert WhartonProfile().is_eligible(stock("ABC"), bar("ABC", "5.00")) == (True, "")


def test_stock_at_four_ninety_nine_is_rejected():
    blocked = WhartonProfile().evaluate(stock("ABC"), bar("ABC", "4.99"))
    assert blocked is not None and blocked.reason is BlockReason.PRICE_BELOW_MINIMUM


def test_price_floor_does_not_apply_to_etfs():
    assert WhartonProfile().evaluate(etf("CHEAP"), bar("CHEAP", "3.00")) is None


def test_any_etf_is_eligible_without_an_approved_list():
    for sym in ("VTI", "VXUS", "IBTO", "SGOV"):
        assert WhartonProfile().evaluate(etf(sym), bar(sym, "50")) is None


def test_gold_etf_is_eligible_at_wharton():
    gld = Instrument(
        "GLD", "SPDR Gold", AssetClass.ETF, "NYSEARCA", is_commodity_or_crypto_trust=True
    )
    assert WhartonProfile().evaluate(gld, bar("GLD", "300")) is None


def test_bitcoin_etf_is_rejected_as_crypto():
    ibit = Instrument(
        "IBIT",
        "iShares Bitcoin Trust",
        AssetClass.ETF,
        "NASDAQ",
        is_commodity_or_crypto_trust=True,
        is_spot_bitcoin_etf=True,
    )
    blocked = WhartonProfile().evaluate(ibit, bar("IBIT", "60"))
    assert blocked is not None and "crypto" in blocked.detail


def test_foreign_adr_is_eligible():
    adr = Instrument("TSM", "TSMC ADR", AssetClass.STOCK, "NYSE")
    assert WhartonProfile().evaluate(adr, bar("TSM", "150")) is None


def test_leveraged_product_rejected_as_derivative():
    lev = Instrument("TQQQ", "3x", AssetClass.ETF, "NASDAQ", is_leveraged=True)
    blocked = WhartonProfile().evaluate(lev, bar("TQQQ", "80"))
    assert blocked is not None and blocked.reason is BlockReason.PROHIBITED_SECURITY


def test_mutual_fund_is_not_an_eligible_wharton_asset_class():
    mf = Instrument("VFIAX", "Admiral", AssetClass.MUTUAL_FUND, "NASDAQ")
    assert WhartonProfile().evaluate(mf, bar("VFIAX", "500")) is not None


def test_bond_is_eligible():
    bond = Instrument("UST2033", "US Treasury 2033", AssetClass.BOND, "OTC")
    assert WhartonProfile().evaluate(bond, bar("UST2033", "98")) is None


def test_missing_exchange_is_ineligible():
    blocked = WhartonProfile().evaluate(stock("ABC", exchange=""), bar("ABC", "10"))
    assert blocked is not None and blocked.reason is BlockReason.INELIGIBLE_EXCHANGE


def test_volume_rule_blocks_more_than_twice_daily_volume():
    p = WhartonProfile()
    assert max_quantity_by_volume(1_000) == 2_000
    assert p.volume_block("IBTR", 2_000, 1_000) is None
    assert p.volume_block("IBTR", 2_001, 1_000) is not None
    assert p.volume_block("BOND", 10_000, 0) is None  # no volume data: not blocked


# ---------------------------------------------------------------------------
# Trade budget
# ---------------------------------------------------------------------------


def test_budget_severity_ladder():
    assert TradeBudget(trades_used=0).severity is BudgetSeverity.OK
    assert TradeBudget(trades_used=30).severity is BudgetSeverity.NOTICE
    assert TradeBudget(trades_used=40).severity is BudgetSeverity.WARNING
    assert TradeBudget(trades_used=190).severity is BudgetSeverity.CRITICAL
    assert TradeBudget(trades_used=200).severity is BudgetSeverity.BLOCKED


def test_trade_forty_warns_but_does_not_block():
    check = TradeBudget(trades_used=40).to_rule_check(Decimal("300000"))
    assert check.satisfied is True and "WARNING" in check.detail


def test_trade_two_hundred_hard_blocks():
    check = TradeBudget(trades_used=200).to_rule_check(Decimal("300000"))
    assert check.satisfied is False


def test_full_hard_cap_costs_five_thousand():
    assert TradeBudget().commission_at_hard_cap == Decimal("5000")


def test_commission_drag_on_300k_at_40_trades():
    assert TradeBudget(trades_used=40).commission_drag_pct(Decimal("300000")) == Decimal("0.333333")


def test_drag_on_non_positive_equity_raises():
    with pytest.raises(ValueError):
        TradeBudget(trades_used=1).commission_drag_fraction(Decimal("0"))


def test_with_trades_is_immutable_and_accumulates():
    b = TradeBudget(trades_used=3)
    assert b.with_trades(2).trades_used == 5 and b.trades_used == 3


# ---------------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------------


def test_check_rules_names_the_season_and_the_window():
    checks = WhartonProfile().check_rules(account_worth_300k(), OPEN_DAY)
    assert named(checks, "season_rules_loaded").satisfied
    window = named(checks, "trading_window")
    assert window.deadline == TRADING_ENDS and "frozen" in window.detail


def test_notes_analysis_is_progress_until_due_then_fails():
    early = named(
        WhartonProfile().check_rules(account_worth_300k(), OPEN_DAY), "notes_analysis_trades"
    )
    assert early.satisfied and early.deadline == NOTES_ANALYSIS_DUE
    late = named(
        WhartonProfile().check_rules(account_worth_300k(), date(2026, 10, 24)),
        "notes_analysis_trades",
    )
    assert late.satisfied is False
    done = named(
        profile_with_trades(3).check_rules(account_worth_300k(), date(2026, 10, 24)),
        "notes_analysis_trades",
    )
    assert done.satisfied


def test_margin_liability_is_an_error_not_a_warning():
    acct = AccountState(OPEN_DAY, Decimal("1000"), (), {}, Decimal("0"), Decimal("500"))
    assert (
        named(WhartonProfile().check_rules(acct, OPEN_DAY), "margin_and_shorting_ban").satisfied
        is False
    )


def test_short_position_is_an_error():
    acct = account_holding("ABC", AssetClass.STOCK, -10, "20")
    assert (
        named(WhartonProfile().check_rules(acct, OPEN_DAY), "margin_and_shorting_ban").satisfied
        is False
    )


def test_commission_drag_check_reports_dollars_and_percent():
    check = named(
        profile_with_trades(4).check_rules(account_worth_300k(), OPEN_DAY), "commission_drag"
    )
    assert "$100.00" in check.detail and "%" in check.detail


def test_sizing_constraints_are_cash_only():
    sc = WhartonProfile().sizing_constraints(account_worth_300k())
    assert sc.spendable_cash == account_worth_300k().cash
    assert sc.commission_per_trade == Decimal("25")


def test_execution_note_says_real_time_and_contrasts_deca():
    note = WhartonProfile().execution_note(OPEN_DAY)
    assert "real-time" in note and "DECA" in note and "next open" in note


def test_execution_note_flags_the_opening_and_the_freeze():
    assert "opens" in WhartonProfile().execution_note(date(2026, 9, 20))
    assert "frozen" in WhartonProfile().execution_note(date(2026, 11, 9))


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_orders_are_withheld_before_trading_opens():
    plan = build_daily_plan(
        WhartonProfile(), account_worth_300k(), date(2026, 9, 21), (a_sized_order("VTI"),)
    )
    assert plan.orders == () and plan.blocked[0].reason is BlockReason.RULE_CONFLICT


def test_orders_are_withheld_after_the_freeze():
    plan = build_daily_plan(
        WhartonProfile(), account_worth_300k(), date(2026, 11, 9), (a_sized_order("VTI"),)
    )
    assert plan.orders == ()


def test_open_window_plan_with_a_clean_book_is_actionable():
    plan = build_daily_plan(
        WhartonProfile(), account_worth_300k(), OPEN_DAY, (a_sized_order("VTI"),)
    )
    assert plan.is_actionable and len(plan.orders) == 1


def test_exhausted_budget_blocks_every_order():
    plan = build_daily_plan(
        profile_with_trades(200), account_worth_300k(), OPEN_DAY, (a_sized_order("VTI"),)
    )
    assert plan.orders == () and plan.is_actionable is False


def test_default_season_is_the_published_one():
    assert WhartonProfile().season == SEASON_2026_27


def test_module_emits_no_submittable_prose():
    banned = (
        "thesis",
        "trading_note",
        "write_note",
        "ips",
        "narrative",
        "draft",
        "summary_paragraph",
        "rationale_text",
    )
    public = [n for n in dir(wharton) if not n.startswith("_")]
    assert not [n for n in public if any(b in n.lower() for b in banned)]
