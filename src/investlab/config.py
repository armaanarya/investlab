"""Typed configuration.

Two competitions run concurrently with different rules, different capital, and
deliberately different risk postures. Every number that differs between them
lives here, in one file, so a value is never hard-coded in a strategy and never
silently shared between profiles that should not share it.

Wharton's 2026-27 starting capital is unknown until 2026-09-15. It is therefore
a config value with a labeled provisional default, never a constant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

ProfileName = Literal["deca", "wharton"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "data_cache"
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
DEFAULT_JOURNAL_PATH = REPO_ROOT / "runs" / "journal.jsonl"


@dataclass(frozen=True, slots=True)
class DataConfig:
    cache_dir: Path = DEFAULT_CACHE_DIR
    # Bars older than this many calendar days mark a run as stale rather than
    # failing it. A stale run still prints; it prints labeled.
    staleness_days: int = 4
    providers: tuple[str, ...] = ("yfinance", "tiingo")
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


@dataclass(frozen=True, slots=True)
class WhartonConfig:
    """Wharton WInS.

    `season_verified` is False until the 2026-27 materials land on 2026-09-15.
    While False, every value below is last season's and the tool refuses to
    emit an actionable order sheet.
    """

    season_verified: bool = False
    # Provisional. 2025-26 was $500,000. The $100,000 figure that appears in
    # StockTrak's own boilerplate FAQ and across secondary sites is wrong for
    # this competition.
    starting_cash: Decimal = Decimal(500000)
    commission_per_trade: Decimal = Decimal(25)
    commission_per_bond: Decimal = Decimal(10)
    min_price: Decimal = Decimal(5)

    materials_release: date = date(2026, 9, 15)
    trading_start: date = date(2026, 9, 28)
    roster_deadline: date = date(2026, 10, 9)
    ips_deadline: date = date(2026, 11, 6)
    final_report_deadline: date = date(2026, 12, 4)

    hard_trade_cap: int = 200
    # Self-imposed, well under the cap. At $25 a trade this holds commission
    # drag near 0.2% of a $500k book, and Wharton tells teams outright that
    # this is not a trading game.
    trade_budget: int = 40

    approved_etfs: tuple[str, ...] = ()
    allow_margin: bool = False
    allow_shorting: bool = False

    risk: RiskConfig = field(
        default_factory=lambda: RiskConfig(
            risk_fraction_per_trade=Decimal("0.005"),
            max_positions=15,
            position_ceiling_fraction=Decimal("0.15"),
            cash_floor_fraction=Decimal("0.05"),
            aggregate_open_risk_fraction=Decimal("0.04"),
        )
    )


@dataclass(frozen=True, slots=True)
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    deca: DecaConfig = field(default_factory=DecaConfig)
    wharton: WhartonConfig = field(default_factory=WhartonConfig)
    journal_path: Path = DEFAULT_JOURNAL_PATH
    runs_dir: Path = DEFAULT_RUNS_DIR


def _decimalize(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Money arrives from JSON as str or float; every one becomes Decimal.

    Constructing Decimal from a float would carry binary rounding error into
    the ledger, so values are stringified first.
    """
    out = dict(raw)
    for key in keys:
        if key in out and out[key] is not None:
            out[key] = Decimal(str(out[key]))
    return out


def load_wharton_season(path: Path) -> WhartonConfig:
    """Load the 2026-27 Wharton values once they are published on Sept 15.

    Expects JSON with at least `starting_cash` and `approved_etfs`. Supplying
    those is what flips the profile out of its unverified state, so the loader
    validates rather than trusting.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"No Wharton season config at {path}. "
            "The 2026-27 materials release 2026-09-15 via SurveyMonkey Apply; "
            "until then the Wharton profile stays unverified and will not "
            "emit an order sheet."
        )

    raw = json.loads(path.read_text())
    missing = [k for k in ("starting_cash", "approved_etfs") if k not in raw]
    if missing:
        raise ValueError(
            f"Wharton season config at {path} is missing {', '.join(missing)}. "
            "Both are required to mark the season verified."
        )
    if not raw["approved_etfs"]:
        raise ValueError(
            "approved_etfs is empty. Wharton requires holding at least one ETF "
            "from its Approved List, so an empty list cannot be correct. Copy "
            "the list from the 2026-27 materials."
        )

    money_keys = ("starting_cash", "commission_per_trade", "commission_per_bond", "min_price")
    raw = _decimalize(raw, money_keys)
    raw["approved_etfs"] = tuple(str(t).upper() for t in raw["approved_etfs"])
    raw["season_verified"] = True

    known = {f for f in WhartonConfig.__dataclass_fields__}
    return WhartonConfig(**{k: v for k, v in raw.items() if k in known})


def load(wharton_season_path: Path | None = None) -> AppConfig:
    """Build the application config, upgrading Wharton if its season file exists."""
    cfg = AppConfig()
    if wharton_season_path is not None and wharton_season_path.exists():
        return AppConfig(
            data=cfg.data,
            deca=cfg.deca,
            wharton=load_wharton_season(wharton_season_path),
            journal_path=cfg.journal_path,
            runs_dir=cfg.runs_dir,
        )
    return cfg
