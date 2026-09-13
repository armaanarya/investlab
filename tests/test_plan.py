"""The DECA order sheet, on a deterministic synthetic market.

Stocks S00..S23 drift from strongly down to strongly up, so the top of the
ranking is a real uptrend and the bottom a real downtrend.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from investlab.competitions.deca import DecaProfile
from investlab.config import DecaConfig
from investlab.contracts import AccountState, AssetClass, Lot, Position
from investlab.earnings import EarningsCalendar, EarningsEvent, Timing
from investlab.money import usd, usd_ceil
from investlab.performance import EquityPoint
from investlab.plan import SheetInputs, build_sheet
from investlab.portfolio.exits import trailing_stop
from investlab.screen import rank_features

AS_OF = date(2026, 9, 11)
CFG = DecaConfig()


def close_of(market, sym, d=AS_OF) -> Decimal:
    ff = market["features"][sym]
    return Decimal(str(round(float(ff.frame["close"].iloc[ff.count_upto(d) - 1]), 4)))


def account(cash="100000", positions=(), marks=None) -> AccountState:
    return AccountState(AS_OF, Decimal(cash), tuple(positions), dict(marks or {}))


def position(sym, qty, price, opened, klass=AssetClass.STOCK) -> Position:
    return Position(sym, klass, (Lot(sym, qty, Decimal(price), Decimal("5"), opened),))


def sheet_for(market, acct, **kw):
    return build_sheet(
        SheetInputs(
            as_of=kw.pop("as_of", AS_OF),
            account=acct,
            features=market["features"],
            universe=market["universe"],
            profile=kw.pop("profile", DecaProfile()),
            cfg=kw.pop("cfg", CFG),
            starting_capital=Decimal("100000"),
            **kw,
        )
    )


@pytest.fixture(scope="module")
def fresh(synth_market):
    return sheet_for(synth_market, account())


def test_fresh_book_fills_next_session_with_compliance_and_buys(synth_market, fresh):
    assert fresh.fill_session == date(2026, 9, 14)
    assert {c.bucket for c in fresh.compliance} == {"mutual_funds", "bonds"}
    mf = next(c for c in fresh.compliance if c.bucket == "mutual_funds")
    assert mf.symbol == "FXAIX"
    assert mf.quantity == max(math.ceil(Decimal("10200") / mf.reference_price), 10)
    assert fresh.buys and not fresh.sells
    assert len(fresh.buys) <= CFG.strategy.max_candidates


def test_buys_respect_entry_filters_and_minimums(synth_market, fresh):
    for b in fresh.buys:
        row = synth_market["features"][b.symbol].row_upto(AS_OF)
        assert b.score >= CFG.strategy.entry_min_score
        assert row["close"] > row["ema50"]
        assert b.quantity >= 10
        assert b.estimated_cost >= CFG.strategy.min_order_notional
        assert b.stop < b.reference_close


def test_cash_is_never_overcommitted(fresh):
    committed = sum((b.estimated_cost for b in fresh.buys), Decimal(0)) + sum(
        (c.estimated_cost for c in fresh.compliance), Decimal(0)
    )
    assert committed + CFG.risk.cash_floor_fraction * fresh.equity <= fresh.cash


def test_aggregate_risk_and_sector_caps_hold(fresh):
    assert sum((b.planned_risk for b in fresh.buys), Decimal(0)) <= (
        CFG.risk.aggregate_open_risk_fraction * fresh.equity
    )
    by_sector: dict[str, Decimal] = {}
    for b in fresh.buys:
        by_sector[b.sector] = by_sector.get(b.sector, Decimal(0)) + b.reference_close * b.quantity
    assert all(v <= CFG.strategy.max_sector_weight * fresh.equity for v in by_sector.values())


def test_position_below_its_stop_is_sold_and_proceeds_are_not_spent(synth_market):
    close = close_of(synth_market, "S00")
    acct = account(
        positions=[position("S00", 100, close + 5, date(2026, 8, 3))], marks={"S00": close}
    )
    sheet = sheet_for(synth_market, acct, entry_stops={"S00": close + 1})
    assert [s.symbol for s in sheet.sells] == ["S00"]
    sell = sheet.sells[0]
    assert sell.reason in ("stop", "trailing_stop")
    assert sell.quantity == 100
    fee = usd_ceil(DecaProfile().sec_fee_rate * 100 * sell.reference_close)
    assert sell.estimated_proceeds == usd(sell.reference_close * 100) - Decimal("5") - fee
    assert "S00" not in {b.symbol for b in sheet.buys}
    spent = sum((b.estimated_cost for b in sheet.buys), Decimal(0)) + sum(
        (c.estimated_cost for c in sheet.compliance), Decimal(0)
    )
    assert spent <= acct.cash
    assert sheet.alerts[0].startswith("SELL:")


def test_compliance_holdings_are_never_sold(synth_market):
    close = close_of(synth_market, "FXAIX")
    acct = account(
        cash="50000",
        positions=[position("FXAIX", 50, "1000", date(2026, 8, 3), AssetClass.MUTUAL_FUND)],
        marks={"FXAIX": close},
    )
    sheet = sheet_for(synth_market, acct)
    assert not sheet.sells
    assert sheet.held[0].verdict == "HOLD (compliance)"
    assert "mutual_funds" not in {c.bucket for c in sheet.compliance}


def test_momentum_lost_exit(synth_market):
    feats = synth_market["features"]
    stocks = {i.symbol: i for i in synth_market["universe"].by_asset_class(AssetClass.STOCK)}
    sleeve = {s: ff for s, ff in feats.items() if s in stocks}
    ranked, _ = rank_features(sleeve, stocks, AS_OF)
    from investlab import calendar as cal

    for cand in (c for c in ranked if c.score < 40 and not c.above_ema50):
        for back in range(6, 12):
            opened = cal.add_business_days(AS_OF, -back)
            trail, _ = trailing_stop(
                feats[cand.symbol], opened, AS_OF, CFG.strategy.trail_atr_multiple
            )
            if trail is not None and cand.close > trail:
                close = close_of(synth_market, cand.symbol)
                acct = account(
                    positions=[position(cand.symbol, 50, close, opened)], marks={cand.symbol: close}
                )
                sheet = sheet_for(synth_market, acct, entry_stops={cand.symbol: close / 2})
                assert [s.reason for s in sheet.sells] == ["momentum_lost"]
                return
    pytest.skip("no weak name without a trailing-stop breach in this fixture")


def test_bitcoin_etfs_are_one_exposure(synth_market_btc):
    allowed = DecaProfile(bitcoin_etfs_allowed=True, bitcoin_etf_decided_by="team")
    sheet = sheet_for(synth_market_btc, account(), profile=allowed)
    bought = {b.symbol for b in sheet.buys} & {"BTC1", "BTC2"}
    assert len(bought) == 1
    other = ({"BTC1", "BTC2"} - bought).pop()
    assert any(b.symbol == other and "same exposure" in b.reason for b in sheet.blocked)
    btc = next(b for b in sheet.buys if b.symbol in bought)
    assert any("team bitcoin-ETF ruling" in f for f in btc.flags)


def test_bitcoin_etfs_blocked_without_the_ruling(synth_market_btc):
    sheet = sheet_for(synth_market_btc, account())
    assert not {b.symbol for b in sheet.buys} & {"BTC1", "BTC2"}
    reasons = [b.reason for b in sheet.blocked if b.symbol in ("BTC1", "BTC2")]
    assert reasons and all("prohibited" in r for r in reasons)


def test_recent_sale_starts_a_cooldown(synth_market, fresh):
    top = fresh.buys[0].symbol
    trades = [
        {
            "session": "2026-09-08",
            "symbol": top,
            "action": "sell",
            "quantity": "10",
            "price": "50",
            "commission": "5",
            "fees": "0",
            "asset_class": "stock",
        }
    ]
    sheet = sheet_for(synth_market, account(), trades=trades)
    assert top not in {b.symbol for b in sheet.buys}
    assert any(b.symbol == top and "cooldown" in b.reason for b in sheet.blocked)


def test_earnings_inside_the_window_blocks_a_buy(synth_market, fresh, tmp_path):
    top = fresh.buys[0].symbol
    cal = EarningsCalendar(tmp_path / "earnings.csv")
    cal.set(EarningsEvent(top, AS_OF, Timing.AMC, "finviz"), today=AS_OF)
    sheet = sheet_for(synth_market, account(), earnings=cal)
    assert top not in {b.symbol for b in sheet.buys}
    assert any(b.symbol == top and "earnings" in b.reason for b in sheet.blocked)


def test_earnings_outside_the_window_is_fine_and_missing_dates_are_flagged(
    synth_market, fresh, tmp_path
):
    top = fresh.buys[0].symbol
    cal = EarningsCalendar(tmp_path / "earnings.csv")
    cal.set(EarningsEvent(top, date(2026, 10, 30), Timing.BMO, "finviz"), today=AS_OF)
    sheet = sheet_for(synth_market, account(), earnings=cal)
    buy = next(b for b in sheet.buys if b.symbol == top)
    assert not any("earnings" in f for f in buy.flags)
    others = [b for b in sheet.buys if b.symbol != top]
    assert all(any("no upcoming earnings date" in f for f in b.flags) for b in others)


def test_position_cap_stops_new_buys(synth_market):
    names = [f"S{i:02d}" for i in range(16, 24)]
    positions, marks, stops = [], {}, {}
    for sym in names:
        close = close_of(synth_market, sym)
        positions.append(position(sym, 10, close, AS_OF))
        marks[sym] = close
        stops[sym] = close / 2
    sheet = sheet_for(synth_market, account("20000", positions, marks), entry_stops=stops)
    assert not sheet.sells
    assert not sheet.buys
    assert any("maximum" in n for n in sheet.notes)


def test_drawdown_halt_blocks_buys_until_a_review(synth_market):
    curve = [
        EquityPoint(date(2026, 9, 1), Decimal("100000")),
        EquityPoint(date(2026, 9, 2), Decimal("85000")),
    ]
    halted = sheet_for(synth_market, account("85000"), equity_curve=curve)
    assert any(a.startswith("DRAWDOWN HALT") for a in halted.alerts)
    assert not halted.buys
    resumed = sheet_for(
        synth_market,
        account("85000"),
        equity_curve=curve,
        risk_reviews=[(date(2026, 9, 3), "reviewed in my own words")],
    )
    assert resumed.buys
    assert not any(a.startswith("DRAWDOWN HALT") for a in resumed.alerts)


def test_reduced_stage_halves_risk(synth_market):
    curve = [EquityPoint(date(2026, 9, 1), Decimal("100000"))]
    sheet = sheet_for(synth_market, account("93000"), equity_curve=curve)
    assert sheet.drawdown_stage == "reduced"
    for b in sheet.buys:
        assert b.planned_risk <= Decimal("0.01") * sheet.equity


def test_minimum_order_size_blocks_small_orders(synth_market):
    cfg = replace(CFG, strategy=replace(CFG.strategy, min_order_notional=Decimal("60000")))
    sheet = sheet_for(synth_market, account(), cfg=cfg)
    assert not sheet.buys
    assert any("minimum" in b.reason for b in sheet.blocked)


def test_tight_sector_cap_shrinks_or_blocks(synth_market):
    cfg = replace(CFG, strategy=replace(CFG.strategy, max_sector_weight=Decimal("0.05")))
    sheet = sheet_for(synth_market, account(), cfg=cfg)
    by_sector: dict[str, Decimal] = {}
    for b in sheet.buys:
        by_sector[b.sector] = by_sector.get(b.sector, Decimal(0)) + b.reference_close * b.quantity
    assert all(v <= Decimal("0.05") * sheet.equity for v in by_sector.values())


def test_stale_data_is_loud(synth_market):
    sheet = sheet_for(synth_market, account(), as_of=date(2026, 9, 25))
    assert sheet.data_is_stale
    assert sheet.alerts[0].startswith("STALE DATA")


def test_selling_below_the_stock_minimum_warns(synth_market):
    close = close_of(synth_market, "S00")
    qty = int(Decimal("12000") / close) + 1
    acct = account(positions=[position("S00", qty, close, date(2026, 8, 3))], marks={"S00": close})
    sheet = sheet_for(synth_market, acct, entry_stops={"S00": close + 1})
    assert any("under the $10,000 minimum" in a for a in sheet.alerts)


def test_no_buy_exceeds_the_strategy_position_cap(fresh):
    cap = CFG.strategy.max_position_weight * fresh.equity
    assert CFG.strategy.max_position_weight <= CFG.position_ceiling_fraction
    for b in fresh.buys:
        assert b.reference_close * b.quantity <= cap


def test_sheet_serializes(fresh):
    data = json.loads(json.dumps(fresh.to_dict()))
    assert data["fill_session"] == "2026-09-14"
    assert data["buys"][0]["stop"]
