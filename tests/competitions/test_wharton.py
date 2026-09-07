"""Wharton WInS competition module tests.

The 2026-27 Wharton trading rules are not released until 2026-09-15 (eight
days after this file was written). Every prior-season number used here is
PROVISIONAL and is asserted to be labeled as such in module output. Starting
capital is $500,000, never the commonly-misreported $100,000 StockTrak FAQ
figure -- see `docs/rules/wharton-verified.md`.

Money is Decimal everywhere. A float in this file is a bug.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from investlab.competitions import wharton
from investlab.competitions.wharton import (
    MISREPORTED_STARTING_CAPITAL,
    BudgetSeverity,
    SeasonStatus,
    TradeBudget,
    WhartonConfigError,
    WhartonProfile,
    build_daily_plan,
    load_season_config,
    provisional_disclosures,
    season_config_template,
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
    RuleStatus,
    SizedOrder,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def stock(symbol: str, exchange: str = "NASDAQ") -> Instrument:
    return Instrument(symbol, symbol, AssetClass.STOCK, exchange)


def etf(symbol: str, exchange: str = "NYSEARCA") -> Instrument:
    return Instrument(symbol, symbol, AssetClass.ETF, exchange)


def bar(symbol: str, close: str, session: date = date(2026, 9, 4)) -> Bar:
    price = Decimal(close)
    return Bar(symbol, session, price, price, price, price, price, 1_000_000, "test")


def empty_account(as_of: date = date(2026, 9, 6)) -> AccountState:
    return AccountState(as_of, Decimal("0"), (), {}, Decimal("0"), Decimal("0"))


def account_worth_500k(as_of: date = date(2026, 10, 1)) -> AccountState:
    return AccountState(as_of, Decimal("500000"), (), {}, Decimal("0"), Decimal("0"))


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


def write_json(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "wharton-config.json"
    p.write_text(json.dumps(data))
    return p


CONFIG = {
    "starting_capital": "500000",
    "approved_etfs": ["SPY", "VTI", "AGG"],
    "position_ceiling_fraction": "0.15",
    "minimum_activity_deadline": "2026-10-09",
    "client_mandate_summary": "Alum client, $1.5M in 10 years, $10k/yr from year 3.",
}


def verified_profile(tmp_path: Path) -> WhartonProfile:
    cfg = load_season_config(write_json(tmp_path, dict(CONFIG)))
    return WhartonProfile().with_season(cfg)


# ---------------------------------------------------------------------------
# Task 1: identity, provisional constants
# ---------------------------------------------------------------------------


def test_profile_satisfies_the_frozen_protocol():
    assert isinstance(WhartonProfile(), CompetitionProfile)


def test_defaults_to_the_unverified_state():
    assert WhartonProfile().status is SeasonStatus.WHARTON_UNVERIFIED
    assert WhartonProfile().is_verified is False


def test_starting_capital_defaults_to_500k_never_100k():
    p = WhartonProfile()
    assert p.starting_cash == Decimal("500000")
    assert p.starting_cash != MISREPORTED_STARTING_CAPITAL
    assert p.starting_cash_is_provisional is True


def test_starting_capital_default_is_labeled_provisional():
    blob = " ".join(provisional_disclosures()).lower()
    assert "provisional" in blob and "500,000" in blob and "2025-26" in blob


def test_margin_and_shorting_are_banned_outright():
    p = WhartonProfile()
    assert p.allows_margin is False
    assert p.allows_shorting is False


# ---------------------------------------------------------------------------
# Task 2: eligibility
# ---------------------------------------------------------------------------


def test_plain_stock_at_five_dollars_is_eligible():
    ok, reason = WhartonProfile().is_eligible(stock("AAA"), bar("AAA", "5.00"))
    assert ok is True and reason == ""


def test_stock_at_four_ninety_nine_is_rejected():
    ok, reason = WhartonProfile().is_eligible(stock("AAA"), bar("AAA", "4.99"))
    assert ok is False
    blocked = WhartonProfile().evaluate(stock("AAA"), bar("AAA", "4.99"))
    assert blocked.reason is BlockReason.PRICE_BELOW_MINIMUM
    assert "$5" in blocked.detail and "PROVISIONAL" in blocked.detail


def test_foreign_adr_is_eligible():  # any platform exchange, ADRs included
    ok, _ = WhartonProfile().is_eligible(
        Instrument("BABA", "Alibaba ADR", AssetClass.STOCK, "NYSE"), bar("BABA", "90")
    )
    assert ok is True


def test_unverified_state_blocks_every_etf():
    for sym in ("SPY", "VTI", "AGG"):
        blocked = WhartonProfile().evaluate(etf(sym), bar(sym, "100"))
        assert blocked.reason is BlockReason.UNVERIFIED_INSTRUMENT
        assert "Approved ETF List" in blocked.detail
        assert "2026-09-15" in blocked.detail


def test_ibit_is_rejected_on_both_grounds():
    ibit = Instrument(
        "IBIT",
        "iShares Bitcoin Trust",
        AssetClass.ETF,
        "NASDAQ",
        is_commodity_or_crypto_trust=True,
    )
    blocked = WhartonProfile().evaluate(ibit, bar("IBIT", "60"))
    assert blocked.reason is BlockReason.PROHIBITED_SECURITY
    d = blocked.detail.lower()
    assert "crypto" in d  # ground 1
    assert "approved etf list" in d  # ground 2
    assert "two independent grounds" in d


def test_leveraged_product_rejected_as_derivative():
    lev = Instrument("TQQQ", "3x QQQ", AssetClass.ETF, "NASDAQ", is_leveraged=True)
    assert WhartonProfile().evaluate(lev, bar("TQQQ", "50")).reason is BlockReason.PROHIBITED_SECURITY


def test_mutual_fund_is_not_an_eligible_wharton_asset_class():
    mf = Instrument("VFIAX", "Vanguard 500", AssetClass.MUTUAL_FUND, "NASDAQ")
    assert WhartonProfile().evaluate(mf, bar("VFIAX", "500")).reason is BlockReason.PROHIBITED_SECURITY


def test_missing_exchange_is_ineligible():
    assert (
        WhartonProfile().evaluate(Instrument("XXX", "?", AssetClass.STOCK, ""), bar("XXX", "10")).reason
        is BlockReason.INELIGIBLE_EXCHANGE
    )


# ---------------------------------------------------------------------------
# Task 3: WhartonSeasonConfig, loader, validation, template
# ---------------------------------------------------------------------------


def test_loading_a_json_config_moves_the_profile_to_verified(tmp_path):
    p = tmp_path / "wharton-2026-27.json"
    p.write_text(json.dumps(CONFIG))
    prof = WhartonProfile().with_season(load_season_config(p))
    assert prof.status is SeasonStatus.WHARTON_VERIFIED
    assert prof.starting_cash == Decimal("500000")
    assert prof.starting_cash_is_provisional is False
    assert prof.position_ceiling_fraction == Decimal("0.15")


def test_verified_state_unblocks_approved_etfs_and_still_blocks_others(tmp_path):
    prof = WhartonProfile().with_season(load_season_config(write_json(tmp_path, CONFIG)))
    assert prof.is_eligible(etf("SPY"), bar("SPY", "600"))[0] is True
    blocked = prof.evaluate(etf("ARKK"), bar("ARKK", "60"))
    assert blocked.reason is BlockReason.PROHIBITED_SECURITY
    assert "Approved ETF List" in blocked.detail


def test_yaml_config_loads_with_comments_and_a_list(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "# 2026-27, transcribed from the Sept 15 materials\n"
        "starting_capital: 500000\n"
        "approved_etfs:\n  - SPY\n  - VTI\n"
        "position_ceiling_fraction: 0.15\n"
        "minimum_activity_deadline: 2026-10-09\n"
        'client_mandate_summary: "Growth to $1.5M over ten years."\n'
    )
    cfg = load_season_config(p)
    assert cfg.approved_etfs == frozenset({"SPY", "VTI"})
    assert cfg.starting_capital == Decimal("500000")


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda c: c.pop("approved_etfs"), "approved_etfs"),
        (lambda c: c.update(aproved_etfs=[]), "aproved_etfs"),  # typo -> unknown key
        (lambda c: c.update(approved_etfs=[]), "at least one"),
        (lambda c: c.update(starting_capital="0"), "starting_capital"),
        (lambda c: c.update(position_ceiling_fraction="1.5"), "position_ceiling_fraction"),
        (lambda c: c.update(minimum_activity_deadline="Oct 9"), "minimum_activity_deadline"),
        (lambda c: c.update(client_mandate_summary="  "), "client_mandate_summary"),
    ],
)
def test_invalid_configs_are_rejected_by_name(tmp_path, mutate, fragment):
    bad = dict(CONFIG)
    mutate(bad)
    with pytest.raises(WhartonConfigError) as e:
        load_season_config(write_json(tmp_path, bad))
    assert fragment in str(e.value)


def test_template_names_all_five_keys_and_the_release_date():
    t = season_config_template()
    for k in (
        "starting_capital",
        "approved_etfs",
        "position_ceiling_fraction",
        "minimum_activity_deadline",
        "client_mandate_summary",
    ):
        assert k in t
    assert "2026-09-15" in t


def test_config_money_never_passes_through_float(tmp_path):
    cfg = load_season_config(write_json(tmp_path, {**CONFIG, "starting_capital": 500000.10}))
    assert cfg.starting_capital == Decimal("500000.10")


def test_unknown_config_format_is_rejected(tmp_path):
    p = tmp_path / "config.txt"
    p.write_text("starting_capital: 500000\n")
    with pytest.raises(WhartonConfigError):
        load_season_config(p)


# ---------------------------------------------------------------------------
# Task 4: TradeBudget
# ---------------------------------------------------------------------------


def test_budget_severity_ladder():
    assert TradeBudget(0).severity is BudgetSeverity.OK
    assert TradeBudget(29).severity is BudgetSeverity.OK
    assert TradeBudget(30).severity is BudgetSeverity.NOTICE
    assert TradeBudget(40).severity is BudgetSeverity.WARNING
    assert TradeBudget(190).severity is BudgetSeverity.CRITICAL
    assert TradeBudget(200).severity is BudgetSeverity.BLOCKED


def test_trade_forty_warns_but_does_not_block():
    check = TradeBudget(40).to_rule_check(Decimal("500000"))
    assert check.satisfied is True  # still legal
    assert "WARNING" in check.detail
    assert "40" in check.detail and "200" in check.detail
    assert TradeBudget(40).can_trade() is True


def test_trade_two_hundred_hard_blocks():
    b = TradeBudget(200)
    assert b.is_exhausted is True
    assert b.can_trade() is False
    check = b.to_rule_check(Decimal("500000"))
    assert check.satisfied is False and check.status is RuleStatus.VERIFIED
    assert b.blocked_order("AAA").reason is BlockReason.TRADE_BUDGET_EXHAUSTED


def test_commission_drag_on_500k_at_40_trades():
    b = TradeBudget(40)
    assert b.commission_spent == Decimal("1000")
    assert b.commission_drag_fraction(Decimal("500000")) == Decimal("0.002")
    assert b.commission_drag_pct(Decimal("500000")) == Decimal("0.2")


def test_full_hard_cap_costs_five_thousand():
    assert TradeBudget(200).commission_spent == Decimal("5000")


def test_drag_on_non_positive_equity_raises():
    with pytest.raises(ValueError):
        TradeBudget(1).commission_drag_fraction(Decimal("0"))


def test_with_trades_is_immutable_and_accumulates():
    b = TradeBudget(0)
    assert b.with_trades(3).trades_used == 3
    assert b.trades_used == 0


# ---------------------------------------------------------------------------
# Task 5: check_rules and sizing_constraints
# ---------------------------------------------------------------------------


def test_check_rules_is_never_empty():
    assert WhartonProfile().check_rules(empty_account(), date(2026, 9, 6))


def test_unverified_emits_an_incomplete_check_naming_sept_15():
    checks = WhartonProfile().check_rules(empty_account(), date(2026, 9, 6))
    inc = [c for c in checks if c.status is RuleStatus.INCOMPLETE]
    assert inc
    joined = " ".join(c.detail for c in inc)
    assert "2026-09-15" in joined
    for missing in (
        "starting capital",
        "Approved ETF List",
        "client mandate",
        "position limit",
        "minimum trading activity",
    ):
        assert missing.lower() in joined.lower()
    assert "$100,000" in joined  # names the misreported figure as wrong


def test_unverified_has_a_verified_unsatisfied_gate():
    checks = WhartonProfile().check_rules(empty_account(), date(2026, 9, 6))
    assert any(c.status is RuleStatus.VERIFIED and not c.satisfied for c in checks)


def test_verified_season_lifts_the_gate(tmp_path):
    prof = verified_profile(tmp_path).with_trade_budget(TradeBudget(1))
    acct = account_holding("SPY", AssetClass.ETF, qty=10, mark="600")
    checks = prof.check_rules(acct, date(2026, 10, 1))
    assert all(c.satisfied for c in checks if c.status is RuleStatus.VERIFIED)


def test_approved_etf_minimum_unsatisfied_when_no_etf_held(tmp_path):
    prof = verified_profile(tmp_path)
    acct = account_holding("AAA", AssetClass.STOCK, qty=10, mark="100")
    c = named(prof.check_rules(acct, date(2026, 10, 1)), "approved_etf_minimum")
    assert c.satisfied is False


def test_minimum_trading_activity_deadline_is_reported_with_its_date(tmp_path):
    prof = verified_profile(tmp_path)
    c = named(prof.check_rules(empty_account(), date(2026, 10, 1)), "minimum_trading_activity")
    assert c.deadline == date(2026, 10, 9)
    assert c.satisfied is False  # nothing traded yet


def test_margin_liability_is_an_error_not_a_warning():
    acct = AccountState(date(2026, 10, 1), Decimal("100"), (), {}, Decimal("0"), Decimal("5000"))
    c = named(WhartonProfile().check_rules(acct, date(2026, 10, 1)), "margin_and_shorting_ban")
    assert c.status is RuleStatus.VERIFIED and c.satisfied is False
    assert "margin" in c.detail.lower()


def test_short_position_is_an_error():
    lot = Lot("AAA", -5, Decimal("50"), Decimal("25"), date(2026, 9, 29))
    position = Position("AAA", AssetClass.STOCK, (lot,))
    acct = AccountState(
        date(2026, 10, 1), Decimal("100"), (position,), {"AAA": Decimal("50")}, Decimal("0"), Decimal("0")
    )
    c = named(WhartonProfile().check_rules(acct, date(2026, 10, 1)), "margin_and_shorting_ban")
    assert c.status is RuleStatus.VERIFIED and c.satisfied is False
    assert "short" in c.detail.lower()


def test_commission_drag_check_reports_dollars_and_percent(tmp_path):
    prof = verified_profile(tmp_path).with_trade_budget(TradeBudget(40))
    c = named(prof.check_rules(account_worth_500k(), date(2026, 10, 1)), "commission_drag")
    assert "$1,000" in c.detail and "0.2" in c.detail


def test_sizing_constraints_are_cash_only_and_price_floored():
    sc = WhartonProfile().sizing_constraints(account_worth_500k())
    assert sc.min_price == Decimal("5")
    assert sc.min_shares == 1
    assert sc.commission_per_trade == Decimal("25")
    assert sc.sell_fee_rate == Decimal("0")
    assert sc.spendable_cash == account_worth_500k().cash  # margin banned: cash only
    assert sc.position_ceiling_fraction == Decimal("0.20")  # self-imposed default


# ---------------------------------------------------------------------------
# Task 6: execution_note and the order-sheet gate
# ---------------------------------------------------------------------------


def test_execution_note_says_real_time_and_contrasts_deca():
    note = WhartonProfile().execution_note(date(2026, 10, 1))
    low = note.lower()
    assert "real time" in low or "real-time" in low
    assert ("9:30" in note and "4:00" in note) or "16:00" in note
    assert "10" in note and "15" in note  # display lag minutes
    assert "next" in low and "open" in low  # after-hours -> next day's open
    assert "international" in low and "bond" in low
    assert "deca" in low  # the contrast is explicit
    assert "close" in low  # DECA fills at the close
    assert "\n" not in note  # contract says one line


def test_execution_note_flags_that_trading_has_not_opened_yet():
    assert "2026-09-28" in WhartonProfile().execution_note(date(2026, 9, 6))


def test_unverified_plan_is_not_actionable():
    prof = WhartonProfile()
    plan = build_daily_plan(prof, empty_account(), date(2026, 9, 6))
    assert plan.is_actionable is False


def test_unverified_plan_emits_no_orders_but_keeps_screening():
    plan = build_daily_plan(
        WhartonProfile(), empty_account(), date(2026, 9, 6), orders=(a_sized_order("AAA"),)
    )
    assert plan.orders == ()
    assert any(b.reason is BlockReason.RULE_CONFLICT for b in plan.blocked)
    # screening still runs:
    assert WhartonProfile().is_eligible(stock("AAA"), bar("AAA", "50"))[0] is True


def test_verified_plan_with_a_clean_book_is_actionable(tmp_path):
    prof = verified_profile(tmp_path).with_trade_budget(TradeBudget(1))
    plan = build_daily_plan(
        prof,
        account_holding("SPY", AssetClass.ETF, 10, "600"),
        date(2026, 10, 1),
        orders=(a_sized_order("SPY"),),
    )
    assert plan.is_actionable is True and len(plan.orders) == 1


def test_exhausted_budget_makes_a_verified_plan_non_actionable(tmp_path):
    prof = verified_profile(tmp_path).with_trade_budget(TradeBudget(200))
    plan = build_daily_plan(
        prof, account_holding("SPY", AssetClass.ETF, 10, "600"), date(2026, 10, 1)
    )
    assert plan.is_actionable is False


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
