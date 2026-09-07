from datetime import date
from decimal import Decimal

import pytest

from investlab.contracts import AccountState, AssetClass, BlockedOrder, Lot, Position, SizingConstraints
from investlab.portfolio.risk import (
    DrawdownMonitor,
    DrawdownStage,
    MissingReferencePolicy,
    RiskLimits,
    aggregate_open_risk,
    cash_floor_check,
    concentration,
)

D = Decimal
TODAY = date(2026, 9, 8)


def account(cash="50000.00", holdings=(("AAA", 100, "50.00"), ("BBB", 200, "25.00"))):
    positions, marks = [], {}
    for sym, qty, px in holdings:
        positions.append(
            Position(sym, AssetClass.STOCK, (Lot(sym, qty, D(px), D("0"), TODAY),))
        )
        marks[sym] = D(px)
    return AccountState(TODAY, D(cash), tuple(positions), marks)


def test_open_risk_sums_planned_loss_to_each_protective_reference():
    state = account()
    report = aggregate_open_risk(state, {"AAA": D("46.00"), "BBB": D("23.00")})
    assert report.total_planned_risk == D("800.00")     # 100*4 + 200*2
    assert report.unreferenced == ()
    assert not report.blocks_new_risk


def test_a_position_without_a_reference_is_stressed_never_zero():
    state = account()
    report = aggregate_open_risk(state, {"AAA": D("46.00")})
    stressed = next(p for p in report.positions if p.symbol == "BBB")
    assert stressed.is_stressed
    assert stressed.planned_risk == D("1000.00")        # 200 * 25 * 0.20
    assert report.unreferenced == ("BBB",)
    assert report.total_planned_risk == D("1400.00")


def test_block_policy_stops_new_risk_when_a_reference_is_missing():
    report = aggregate_open_risk(
        account(), {"AAA": D("46.00")}, policy=MissingReferencePolicy.BLOCK
    )
    assert report.blocks_new_risk


def test_a_reference_above_the_mark_is_flagged_as_breached():
    report = aggregate_open_risk(account(), {"AAA": D("60.00"), "BBB": D("23.00")})
    assert "AAA" in report.breached_references
    assert report.total_planned_risk == D("400.00")


def test_concentration_reports_position_and_sector_weights():
    state = account()                                   # equity 50000 + 5000 + 5000 = 60000
    rep = concentration(state, {"AAA": "Tech", "BBB": "Tech"}, limits=RiskLimits(max_sector_weight=D("0.10")))
    assert rep.position_weights["AAA"] == D("5000.00") / D("60000.00")
    assert rep.sector_weights["Tech"] == D("10000.00") / D("60000.00")
    assert not rep.satisfied
    assert any(b.scope == "sector" for b in rep.breaches)


def test_unclassified_sector_is_its_own_bucket_not_ignored():
    rep = concentration(account(), {"AAA": "Tech"})
    assert "UNCLASSIFIED" in rep.sector_weights


def test_cash_floor_check():
    limits = RiskLimits(cash_floor_fraction=D("0.10"))
    assert cash_floor_check(account(), limits=limits).satisfied
    assert not cash_floor_check(account(cash="100.00"), limits=limits).satisfied


def test_drawdown_halves_risk_at_five_percent():
    mon = DrawdownMonitor(D("100000.00"))
    st_ = mon.observe(D("94000.00"), TODAY)
    assert st_.stage is DrawdownStage.REDUCED
    assert st_.risk_multiplier == D("0.5")
    assert st_.entries_allowed


def test_drawdown_halts_entries_at_ten_percent():
    mon = DrawdownMonitor(D("100000.00"))
    st_ = mon.observe(D("89000.00"), TODAY)
    assert st_.stage is DrawdownStage.HALTED
    assert not st_.entries_allowed
    assert st_.risk_multiplier == D("0")
    assert isinstance(mon.gate_new_entry("AAA"), BlockedOrder)


def test_a_halt_never_lifts_itself_on_recovery():
    mon = DrawdownMonitor(D("100000.00"))
    mon.observe(D("85000.00"), TODAY)
    recovered = mon.observe(D("101000.00"), date(2026, 10, 1))
    assert recovered.stage is DrawdownStage.HALTED
    assert recovered.high_water_mark == D("101000.00")


def test_resumption_is_an_explicit_logged_transition():
    mon = DrawdownMonitor(D("100000.00"))
    mon.observe(D("85000.00"), TODAY)
    resumed = mon.record_review(date(2026, 10, 2), note="Reviewed: thesis intact, cut position size.")
    assert resumed.stage is DrawdownStage.NORMAL
    transitions = [(e.from_stage, e.to_stage) for e in mon.events]
    assert (DrawdownStage.HALTED, DrawdownStage.NORMAL) in transitions
    assert mon.events[-1].note.startswith("Reviewed")


def test_a_review_without_the_students_own_note_is_refused():
    mon = DrawdownMonitor(D("100000.00"))
    mon.observe(D("85000.00"), TODAY)
    with pytest.raises(ValueError):
        mon.record_review(date(2026, 10, 2), note="   ")


def test_throttle_halves_the_risk_fraction_and_zeroes_it_when_halted():
    con = SizingConstraints(
        equity=D("100000.00"), spendable_cash=D("100000.00"), risk_fraction=D("0.01"),
        position_ceiling_fraction=D("0.10"), min_shares=1, min_price=D("3.00"),
        commission_per_trade=D("0"),
    )
    mon = DrawdownMonitor(D("100000.00"))
    mon.observe(D("94000.00"), TODAY)
    assert mon.throttle(con).risk_fraction == D("0.005")
    mon.observe(D("85000.00"), TODAY)
    assert mon.throttle(con).risk_fraction == D("0")
