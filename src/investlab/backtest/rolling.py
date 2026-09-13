"""Many game-length replays instead of one long one.

A DECA game is 62 sessions, and what decides qualification is how the book does
against the S&P 500 over that stretch. One long backtest answers a different
question, and a single replay swings with its start date: moving the start by
one week moved the old defaults' result against SPY by as much as 40 points.

This module replays the order sheet over many 62-session windows and reports
how often, and by how much, it beat SPY and the compliance-aware baseline. It
is the evidence standard for changing a strategy default.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from investlab.competitions.deca import DecaProfile
from investlab.config import DecaConfig
from investlab.contracts import AssetClass, Bar
from investlab.data.universe import Universe
from investlab.money import usd
from investlab.reports.tearsheet import max_drawdown
from investlab.screen import FeatureFrame, compute_features

from .baselines import run_compliance_aware, run_momentum
from .engine import BacktestSettings, CloseBook, RunResult, run_strategy

GAME_SESSIONS = 62


@dataclass(frozen=True, slots=True)
class WindowResult:
    start: date
    end: date
    benchmark_pct: float
    returns_pct: dict[str, float]
    max_drawdown_pct: dict[str, float]
    trades: dict[str, int]


@dataclass(frozen=True, slots=True)
class RollingSummary:
    name: str
    windows: int
    mean_return_pct: float
    mean_excess_pct: float
    median_excess_pct: float
    worst_excess_pct: float
    best_excess_pct: float
    beat_benchmark_pct: float
    beat_benchmark_by_5_pct: float
    beat_compliance_pct: float
    avg_max_drawdown_pct: float
    avg_trades: float


def _return_pct(run: RunResult) -> float:
    return float((run.equity[-1] / run.starting_cash - 1) * 100) if run.equity else 0.0


def run_rolling(
    bars: dict[str, list[Bar]],
    universe: Universe,
    profile: DecaProfile,
    cfg: DecaConfig,
    *,
    first_start: date,
    last_end: date,
    length: int = GAME_SESSIONS,
    step: int = 15,
    benchmark: str = "SPY",
    resume_after_halt_sessions: int = 10,
    features: dict[str, FeatureFrame] | None = None,
) -> tuple[list[WindowResult], list[RollingSummary]]:
    """Replay the strategy, plain momentum and the compliance-aware baseline
    over every `length`-session window starting every `step` sessions.

    Halts resume after `resume_after_halt_sessions` because the replay has no
    one to write the review a live halt needs; that assumption is reported with
    the results.
    """
    features = features if features is not None else compute_features(bars)
    closes = CloseBook(bars)
    sessions = [d for d in closes.sessions(benchmark) if first_start <= d <= last_end]
    if len(sessions) < length:
        return [], []
    fund = next((f for f in cfg.strategy.compliance_mutual_funds if f in bars), None)
    stocks = [i.symbol for i in universe.by_asset_class(AssetClass.STOCK) if i.symbol in bars]
    target = usd(
        cfg.diversification_minimum * (Decimal(1) + cfg.strategy.compliance_buffer_fraction)
    )

    windows: list[WindowResult] = []
    for i in range(0, len(sessions) - length + 1, step):
        window = sessions[i : i + length]
        start, end = window[0], window[-1]
        settings = BacktestSettings(
            start=start,
            end=end,
            starting_cash=cfg.starting_cash,
            resume_after_halt_sessions=resume_after_halt_sessions,
            benchmark=benchmark,
        )
        runs: dict[str, RunResult] = {
            "strategy": run_strategy(bars, universe, profile, cfg, settings, features=features),
            "momentum_top8": run_momentum(
                features,
                stocks,
                closes,
                window,
                settings,
                profile.commission_per_trade,
                profile.sec_fee_rate,
                cfg.min_shares_per_buy,
            ),
        }
        if fund is not None:
            runs["compliance_aware"] = run_compliance_aware(
                fund, benchmark, closes, window, settings, profile.commission_per_trade, 1, target
            )
        bench = float((closes.on(benchmark, end) / closes.on(benchmark, start) - 1) * 100)
        windows.append(
            WindowResult(
                start=start,
                end=end,
                benchmark_pct=round(bench, 2),
                returns_pct={k: round(_return_pct(v), 2) for k, v in runs.items()},
                max_drawdown_pct={
                    k: max_drawdown([v.starting_cash, *v.equity])[0] for k, v in runs.items()
                },
                trades={k: len(v.trades) for k, v in runs.items()},
            )
        )

    summaries: list[RollingSummary] = []
    names = list(windows[0].returns_pct)
    n = len(windows)
    for name in names:
        excess = [w.returns_pct[name] - w.benchmark_pct for w in windows]
        vs_comp = (
            [w.returns_pct[name] - w.returns_pct["compliance_aware"] for w in windows]
            if "compliance_aware" in names
            else []
        )
        summaries.append(
            RollingSummary(
                name=name,
                windows=n,
                mean_return_pct=round(statistics.mean(w.returns_pct[name] for w in windows), 2),
                mean_excess_pct=round(statistics.mean(excess), 2),
                median_excess_pct=round(statistics.median(excess), 2),
                worst_excess_pct=round(min(excess), 2),
                best_excess_pct=round(max(excess), 2),
                beat_benchmark_pct=round(100 * sum(x > 0 for x in excess) / n, 1),
                beat_benchmark_by_5_pct=round(100 * sum(x > 5 for x in excess) / n, 1),
                beat_compliance_pct=(
                    round(100 * sum(x > 0 for x in vs_comp) / n, 1) if vs_comp else 0.0
                ),
                avg_max_drawdown_pct=round(
                    statistics.mean(w.max_drawdown_pct[name] for w in windows), 2
                ),
                avg_trades=round(statistics.mean(w.trades[name] for w in windows), 1),
            )
        )
    return windows, summaries
