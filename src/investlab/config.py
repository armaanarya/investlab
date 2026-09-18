"""Typed configuration.

Two competitions run concurrently with different rules, different capital, and
deliberately different risk postures. Every number that differs between them
lives here, in one file, so a value is never hard-coded in a strategy and never
silently shared between profiles that should not share it.

Wharton's 2026-27 values come from the materials published 2026-09-15.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

ProfileName = Literal["deca", "wharton"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "data_cache"
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_JOURNAL_PATH = REPO_ROOT / "runs" / "journal.jsonl"
DEFAULT_DECA_RULINGS = REPO_ROOT / "configs" / "deca_rulings.json"


@dataclass(frozen=True, slots=True)
class DataConfig:
    cache_dir: Path = DEFAULT_CACHE_DIR
    # Bars older than this many calendar days mark a run as stale rather than
    # failing it. A stale run still prints; it prints labeled.
    staleness_days: int = 4
    # Tried in order; each later provider is asked only for what the earlier
    # ones missed. Alpaca and Tiingo are skipped when their keys are unset.
    providers: tuple[str, ...] = ("yfinance", "alpaca", "tiingo")
    lookback_days: int = 400


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Risk posture. The two profiles differ here on purpose.

    DECA is a step-function tournament: only the top 25 per region advance, so
    the objective is P(qualify), not expected return, and concentration is a
    deliberate choice rather than an oversight. Wharton is judged on strategy
    and articulation with a 200-trade cap and $25 commissions, so turnover is
    the thing to minimise.
    """

    risk_fraction_per_trade: Decimal
    max_positions: int
    position_ceiling_fraction: Decimal
    cash_floor_fraction: Decimal
    aggregate_open_risk_fraction: Decimal
    # Drawdown from the equity high-water mark at which new-trade risk halves.
    drawdown_derisk: Decimal = Decimal("0.05")
    # Drawdown at which new entries halt pending a recorded review.
    drawdown_halt: Decimal = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class DecaStrategyConfig:
    """The DECA order-sheet algorithm's parameters. Strategy choices, not rules.

    Rationale for each lives in `docs/STRATEGY.md`. Change them there and here
    together, and re-run `investlab backtest` before trusting a new value.
    """

    # Entry stop sits this many ATR(14) below the signal close. Chosen over 2x
    # on 30 rolling 12-week windows (docs/STRATEGY.md): 2x stopped most trades
    # out on ordinary noise.
    entry_atr_multiple: Decimal = Decimal("3")
    # Trailing stop sits this many ATR(14) below the highest close since entry.
    # Wider than the entry stop so a new position is not trailed out on noise.
    trail_atr_multiple: Decimal = Decimal("5")
    # The strategy's own cap on one position, under DECA's 30% rule. At 30% the
    # risk budget put the book into three or four names and results swung with
    # the start date; 15% spreads it across more.
    max_position_weight: Decimal = Decimal("0.15")

    # New buys need a cross-sectional score at or above this and a close above
    # EMA50. Score is a 0-100 rank, not a probability.
    entry_min_score: float = 60.0
    entry_requires_above_ema50: bool = True

    # A held position whose score falls below this while closing under EMA50
    # has lost the signal it was bought on.
    exit_score_below: float = 40.0
    exit_decay_min_hold_sessions: int = 5

    # Sessions after a sale before the same name (or exposure group) can be
    # bought again. Stops churn at $5 a side.
    reentry_cooldown_sessions: int = 10

    # Orders smaller than this cost more than 0.25% in commission alone.
    min_order_notional: Decimal = Decimal("2000")

    # Block a new buy when earnings land between the signal close and this
    # many sessions after the fill; warn on held names inside the shorter window.
    earnings_block_sessions: int = 5
    earnings_warn_sessions: int = 2

    max_sector_weight: Decimal = Decimal("0.50")
    max_candidates: int = 8

    # The compliance legs buy this much above the $10,000 net-cost minimum, so
    # a fund's NAV slipping between the signal close and the fill close cannot
    # leave the class short.
    compliance_buffer_fraction: Decimal = Decimal("0.02")
    # Mutual fund for the compliance leg, in preference order. S&P 500 index
    # funds first: DECA ranks against S&P 500 growth, so the forced $10,000
    # tracks the benchmark rather than adding a bet.
    compliance_mutual_funds: tuple[str, ...] = ("FXAIX", "VFIAX", "VTSAX")


@dataclass(frozen=True, slots=True)
class DecaRulings:
    """Team rulings on questions the published DECA rules leave open or that
    the team reads differently. Each carries who decided and on what basis,
    and stays CONFLICTING until a written source is recorded."""

    bitcoin_etfs_allowed: bool = False
    bitcoin_etf_decided_by: str = ""
    bitcoin_etf_decided_on: date | None = None
    bitcoin_etf_basis: str = ""
    bitcoin_etf_written_source: str | None = None


def load_deca_rulings(path: Path = DEFAULT_DECA_RULINGS) -> DecaRulings:
    """Read `configs/deca_rulings.json`. A missing file means no rulings: the
    published text applies as written."""
    if not path.exists():
        return DecaRulings()
    raw = json.loads(path.read_text())
    btc = raw.get("bitcoin_etfs_allowed")
    if btc is None:
        return DecaRulings()
    if not isinstance(btc.get("value"), bool):
        raise ValueError(f"{path}: bitcoin_etfs_allowed.value must be true or false")
    if btc["value"]:
        missing = [k for k in ("decided_by", "decided_on", "basis") if not btc.get(k)]
        if missing:
            raise ValueError(
                f"{path}: a ruling that lifts a published ban must record {', '.join(missing)}"
            )
    decided_on = btc.get("decided_on")
    return DecaRulings(
        bitcoin_etfs_allowed=btc["value"],
        bitcoin_etf_decided_by=btc.get("decided_by", ""),
        bitcoin_etf_decided_on=date.fromisoformat(decided_on) if decided_on else None,
        bitcoin_etf_basis=btc.get("basis", ""),
        bitcoin_etf_written_source=btc.get("written_source") or None,
    )


@dataclass(frozen=True, slots=True)
class DecaConfig:
    """DECA SMG. Every value below is verified; see docs/rules/deca-verified.md."""

    starting_cash: Decimal = Decimal(100000)
    commission_per_trade: Decimal = Decimal(5)
    # UNVERIFIED. The SEC fee is charged on sells but its rate appears in no
    # DECA or SIFMA document we could reach. Treated as an assumption and
    # labeled as such wherever it affects a number the student sees.
    sec_fee_rate: Decimal = Decimal("0.0000278")
    sec_fee_rate_is_assumed: bool = True

    game_start: date = date(2026, 9, 8)
    game_end: date = date(2026, 12, 4)
    diversification_deadline: date = date(2026, 10, 23)
    student_names_deadline: date = date(2026, 10, 16)

    diversification_minimum: Decimal = Decimal(10000)
    # Budget above the minimum because DECA measures net cost MINUS the $5 fee,
    # so a purchase of exactly $10,000 lands $5 short of the requirement.
    diversification_target: Decimal = Decimal(10100)

    min_price: Decimal = Decimal(3)
    min_market_cap: Decimal = Decimal(25000000)
    min_shares_per_buy: int = 10
    position_ceiling_fraction: Decimal = Decimal("0.30")  # 20% x 1.5

    # Permitted by the rules, disabled by us. Margin is a late-season variance
    # lever, not a default: 7%/yr interest and a maintenance breach triggers
    # automatic liquidation after seven days.
    allow_margin: bool = False
    allow_shorting: bool = False
    margin_enable_after: date = date(2026, 11, 1)

    risk: RiskConfig = field(
        default_factory=lambda: RiskConfig(
            risk_fraction_per_trade=Decimal("0.02"),
            max_positions=8,
            position_ceiling_fraction=Decimal("0.30"),
            cash_floor_fraction=Decimal("0.02"),
            aggregate_open_risk_fraction=Decimal("0.12"),
        )
    )
    strategy: DecaStrategyConfig = field(default_factory=DecaStrategyConfig)


@dataclass(frozen=True, slots=True)
class WhartonConfig:
    """Wharton WInS, 2026-27. Every value is from the materials published
    2026-09-15; see docs/rules/wharton-verified.md. The rules engine itself is
    `competitions/wharton.py`; this mirrors the numbers the CLI needs."""

    season_verified: bool = True
    # $300,000. Last season was $500,000; StockTrak's boilerplate says $100,000.
    starting_cash: Decimal = Decimal(300000)
    commission_per_trade: Decimal = Decimal(25)
    commission_per_bond: Decimal = Decimal(10)
    min_price: Decimal = Decimal(5)

    materials_release: date = date(2026, 9, 15)
    trading_start: date = date(2026, 9, 28)
    roster_deadline: date = date(2026, 10, 9)
    notes_analysis_deadline: date = date(2026, 10, 23)
    ips_deadline: date = date(2026, 11, 6)
    # Same day as the IPS: the portfolio freezes.
    trading_end: date = date(2026, 11, 6)
    final_report_instructions: date = date(2026, 11, 9)
    final_report_deadline: date = date(2026, 12, 4)

    hard_trade_cap: int = 200
    # Self-imposed, well under the cap. Wharton says it "does not require
    # frequent or same-day trading" and judges the strategy, not activity.
    trade_budget: int = 40

    allow_margin: bool = False
    allow_shorting: bool = False


@dataclass(frozen=True, slots=True)
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    deca: DecaConfig = field(default_factory=DecaConfig)
    wharton: WhartonConfig = field(default_factory=WhartonConfig)
    journal_path: Path = DEFAULT_JOURNAL_PATH
    runs_dir: Path = DEFAULT_RUNS_DIR


CACHE_DIR_ENV = "INVESTLAB_CACHE_DIR"


def load() -> AppConfig:
    """Build the application config.

    `INVESTLAB_CACHE_DIR` points the price cache somewhere else, which is how
    the test suite keeps its synthetic bars out of the real cache.
    """
    import os

    cache_override = os.environ.get(CACHE_DIR_ENV)
    if cache_override:
        cfg = AppConfig(data=DataConfig(cache_dir=Path(cache_override)))
    else:
        cfg = AppConfig()
    return cfg
