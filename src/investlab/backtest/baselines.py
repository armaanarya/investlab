"""Baselines the strategy has to beat before any result means anything.

Each runs over the same sessions, fills at the same closes, and pays the same
$5 commission and sell fee as the strategy. A strategy that cannot beat these
has produced a valid negative result, not a bug.

- **cash**: the DECA credit of 0.75%/yr on a positive balance.
- **buy_and_hold_<benchmark>**: everything in the S&P 500 tracker on day one.
  DECA ranks against S&P 500 growth, so this is the line that decides
  qualification.
- **equal_weight**: every eligible stock in the universe, equal dollars, held.
- **momentum_top8**: the eight strongest 21-session returns, rebalanced every
  21 sessions, no filters, no stops.
- **compliance_aware**: $10,200 in the S&P 500 index fund, $10,205 in cash
  standing in for the bond leg, the rest in the benchmark. The honest
  comparison, because the real book carries the same forced $20,000.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

from investlab.competitions.deca import DecaProfile
from investlab.config import DecaConfig
from investlab.contracts import AssetClass, Bar
from investlab.data.universe import FIXED_INCOME, Universe
from investlab.money import round_shares, usd
from investlab.portfolio.ledger import Ledger
from investlab.screen import FeatureFrame, compute_features

from .engine import (
    BacktestSettings,
    CloseBook,
    RunResult,
    marked,
    sim_buy,
    sim_sell,
    trading_sessions,
)

CASH_CREDIT_RATE = Decimal("0.0075")


def run_cash(sessions: list[date], settings: BacktestSettings) -> RunResult:
    result = RunResult(
        "cash", settings.starting_cash, notes=["DECA credits 0.75%/yr on positive cash."]
    )
    if not sessions:
        return result
    first = sessions[0]
    for d in sessions:
        accrued = (
            settings.starting_cash * CASH_CREDIT_RATE * Decimal((d - first).days) / Decimal(365)
        )
        result.record(d, settings.starting_cash + accrued, Decimal("0"))
    return result


def run_buy_and_hold(
    symbol: str,
    closes: CloseBook,
    sessions: list[date],
    settings: BacktestSettings,
    commission: Decimal,
    min_shares: int,
) -> RunResult:
    result = RunResult(f"buy_and_hold_{symbol}", settings.starting_cash)
    ledger = Ledger(settings.starting_cash)
    for i, d in enumerate(sessions):
        if i == 0:
            px = closes.on(symbol, d)
            if px:
                qty = round_shares((ledger.cash - commission) / px)
                sim_buy(
                    ledger,
                    result,
                    d,
                    symbol,
                    qty,
                    px,
                    commission,
                    AssetClass.ETF,
                    "baseline",
                    min_shares,
                )
        _, invested = marked(ledger, closes, d)
        result.record(d, ledger.cash, invested)
    return result


def run_equal_weight(
    symbols: list[str],
    closes: CloseBook,
    sessions: list[date],
    settings: BacktestSettings,
    commission: Decimal,
    min_shares: int,
) -> RunResult:
    result = RunResult("equal_weight", settings.starting_cash)
    ledger = Ledger(settings.starting_cash)
    for i, d in enumerate(sessions):
        if i == 0:
            priced = [s for s in symbols if closes.on(s, d)]
            if priced:
                per = settings.starting_cash / Decimal(len(priced))
                for s in priced:
                    px = closes.on(s, d)
                    qty = round_shares((per - commission) / px)
                    sim_buy(
                        ledger,
                        result,
                        d,
                        s,
                        qty,
                        px,
                        commission,
                        AssetClass.STOCK,
                        "baseline",
                        min_shares,
                    )
        _, invested = marked(ledger, closes, d)
        result.record(d, ledger.cash, invested)
    return result


def _momentum_at(ff: FeatureFrame, as_of: date) -> float | None:
    n = ff.count_upto(as_of)
    if not n:
        return None
    row = ff.frame.iloc[n - 1]
    prior = row["prior21"]
    if prior != prior or prior <= 0:
        return None
    return float(row["close"] / prior - 1.0)


def run_momentum(
    features: dict[str, FeatureFrame],
    symbols: list[str],
    closes: CloseBook,
    sessions: list[date],
    settings: BacktestSettings,
    commission: Decimal,
    fee_rate: Decimal,
    min_shares: int,
    top_n: int = 8,
    every: int = 21,
) -> RunResult:
    from investlab import calendar as cal

    result = RunResult(f"momentum_top{top_n}", settings.starting_cash)
    ledger = Ledger(settings.starting_cash)
    for i, d in enumerate(sessions):
        if i % every == 0:
            prev = cal.prev_session(d)
            scores = {
                s: m
                for s in symbols
                if s in features and (m := _momentum_at(features[s], prev)) is not None
            }
            top = sorted(scores, key=lambda s: (-scores[s], s))[:top_n]
            for p in ledger.positions:
                if p.symbol not in top and (px := closes.on(p.symbol, d)):
                    sim_sell(
                        ledger,
                        result,
                        d,
                        p.symbol,
                        p.quantity,
                        px,
                        commission,
                        fee_rate,
                        "stock",
                        "rebalance",
                    )
            held = {p.symbol for p in ledger.positions}
            new = [s for s in top if s not in held and closes.on(s, d)]
            if new:
                per = ledger.cash / Decimal(len(new))
                for s in new:
                    px = closes.on(s, d)
                    qty = round_shares((per - commission) / px)
                    sim_buy(
                        ledger,
                        result,
                        d,
                        s,
                        qty,
                        px,
                        commission,
                        AssetClass.STOCK,
                        "rebalance",
                        min_shares,
                    )
        _, invested = marked(ledger, closes, d)
        result.record(d, ledger.cash, invested)
    return result


def run_compliance_aware(
    fund: str,
    benchmark: str,
    closes: CloseBook,
    sessions: list[date],
    settings: BacktestSettings,
    commission: Decimal,
    min_shares: int,
    target: Decimal,
) -> RunResult:
    result = RunResult(
        "compliance_aware",
        settings.starting_cash,
        notes=[f"{fund} holds the mutual fund leg; the bond leg is cash, as in the strategy."],
    )
    ledger = Ledger(settings.starting_cash)
    for i, d in enumerate(sessions):
        if i == 0:
            fpx = closes.on(fund, d)
            if fpx:
                q = math.ceil(target / fpx)
                sim_buy(
                    ledger,
                    result,
                    d,
                    fund,
                    q,
                    fpx,
                    commission,
                    AssetClass.MUTUAL_FUND,
                    "compliance",
                    1,
                )
            bpx = closes.on(benchmark, d)
            if bpx:
                spend = ledger.cash - (target + commission)
                q = round_shares((spend - commission) / bpx)
                sim_buy(
                    ledger,
                    result,
                    d,
                    benchmark,
                    q,
                    bpx,
                    commission,
                    AssetClass.ETF,
                    "baseline",
                    min_shares,
                )
        _, invested = marked(ledger, closes, d)
        result.record(d, ledger.cash, invested)
    return result


def run_baselines(
    bars: dict[str, list[Bar]],
    universe: Universe,
    profile: DecaProfile,
    cfg: DecaConfig,
    settings: BacktestSettings,
    *,
    features: dict[str, FeatureFrame] | None = None,
) -> list[RunResult]:
    features = features if features is not None else compute_features(bars)
    closes = CloseBook(bars)
    sessions = trading_sessions(closes, settings)
    commission = profile.commission_per_trade
    stocks = [
        i.symbol
        for i in universe
        if i.asset_class is AssetClass.STOCK and i.sector != FIXED_INCOME and i.symbol in bars
    ]
    target = usd(
        cfg.diversification_minimum * (Decimal(1) + cfg.strategy.compliance_buffer_fraction)
    )
    fund = next((f for f in cfg.strategy.compliance_mutual_funds if f in bars), None)
    runs = [
        run_cash(sessions, settings),
        run_buy_and_hold(settings.benchmark, closes, sessions, settings, commission, 1),
        run_equal_weight(stocks, closes, sessions, settings, commission, cfg.min_shares_per_buy),
        run_momentum(
            features,
            stocks,
            closes,
            sessions,
            settings,
            commission,
            profile.sec_fee_rate,
            cfg.min_shares_per_buy,
        ),
    ]
    if fund is not None:
        runs.append(
            run_compliance_aware(
                fund, settings.benchmark, closes, sessions, settings, commission, 1, target
            )
        )
    return runs
