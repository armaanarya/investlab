"""Aggregate planned open risk, concentration, cash floor, and the two-stage
drawdown policy.

A position with no valid protective reference must never contribute zero
risk — silence is the one unacceptable answer, so it is either stressed at a
documented conservative rate or blocks new risk outright, depending on
policy. Drawdown resumption is always an explicit, logged transition; it is
never an invisible auto-reset, and it always carries the student's own note.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Mapping

from investlab.contracts import AccountState, BlockedOrder, BlockReason, SizingConstraints
from investlab.money import ZERO, usd

# --------------------------------------------------------------------------
# Open risk and concentration
# --------------------------------------------------------------------------


class MissingReferencePolicy(str, Enum):
    STRESS = "stress"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    reduce_at: Decimal = Decimal("0.05")
    halt_at: Decimal = Decimal("0.10")
    reduced_multiplier: Decimal = Decimal("0.5")
    cash_floor_fraction: Decimal = Decimal("0")
    max_position_weight: Decimal = Decimal("0.30")
    max_sector_weight: Decimal = Decimal("0.50")
    max_aggregate_risk_fraction: Decimal = Decimal("0.06")
    missing_reference_stress: Decimal = Decimal("0.20")


@dataclass(frozen=True, slots=True)
class PositionRisk:
    symbol: str
    quantity: int
    mark: Decimal
    protective_reference: Decimal | None
    planned_risk: Decimal
    is_stressed: bool
    basis: str


@dataclass(frozen=True, slots=True)
class OpenRiskReport:
    positions: tuple[PositionRisk, ...]
    total_planned_risk: Decimal
    risk_fraction_of_equity: Decimal
    unreferenced: tuple[str, ...]
    breached_references: tuple[str, ...]
    blocks_new_risk: bool


def aggregate_open_risk(
    state: AccountState,
    references: Mapping[str, Decimal],
    *,
    limits: RiskLimits = RiskLimits(),
    policy: MissingReferencePolicy = MissingReferencePolicy.STRESS,
) -> OpenRiskReport:
    positions: list[PositionRisk] = []
    unreferenced: list[str] = []
    breached: list[str] = []
    total = ZERO
    blocks_new_risk = False

    for pos in state.positions:
        mark = state.marks[pos.symbol]
        qty = pos.quantity
        reference = references.get(pos.symbol)

        if reference is None:
            is_stressed = True
            planned_risk = usd(Decimal(qty) * mark * limits.missing_reference_stress)
            basis = (
                f"{limits.missing_reference_stress * 100}% stress: no protective reference"
            )
            unreferenced.append(pos.symbol)
            if policy is MissingReferencePolicy.BLOCK:
                blocks_new_risk = True
        elif reference >= mark:
            is_stressed = False
            planned_risk = ZERO
            basis = f"protective reference {reference} at or above mark {mark}: already breached"
            breached.append(pos.symbol)
        else:
            is_stressed = False
            planned_risk = usd(Decimal(qty) * (mark - reference))
            basis = f"planned risk to reference {reference}"

        positions.append(
            PositionRisk(pos.symbol, qty, mark, reference, planned_risk, is_stressed, basis)
        )
        total += planned_risk

    risk_fraction = total / state.equity if state.equity != 0 else ZERO

    return OpenRiskReport(
        positions=tuple(positions),
        total_planned_risk=total,
        risk_fraction_of_equity=risk_fraction,
        unreferenced=tuple(unreferenced),
        breached_references=tuple(breached),
        blocks_new_risk=blocks_new_risk,
    )


@dataclass(frozen=True, slots=True)
class ConcentrationBreach:
    scope: str
    key: str
    weight: Decimal
    limit: Decimal


@dataclass(frozen=True, slots=True)
class ConcentrationReport:
    position_weights: dict[str, Decimal]
    sector_weights: dict[str, Decimal]
    breaches: tuple[ConcentrationBreach, ...]

    @property
    def satisfied(self) -> bool:
        return not self.breaches


_UNCLASSIFIED = "UNCLASSIFIED"


def concentration(
    state: AccountState,
    sectors: Mapping[str, str],
    *,
    limits: RiskLimits = RiskLimits(),
) -> ConcentrationReport:
    equity = state.equity
    position_weights: dict[str, Decimal] = {}
    sector_totals: dict[str, Decimal] = {}

    for pos in state.positions:
        mark = state.marks[pos.symbol]
        value = Decimal(pos.quantity) * mark
        position_weights[pos.symbol] = value / equity if equity != 0 else ZERO
        sector = sectors.get(pos.symbol, _UNCLASSIFIED)
        sector_totals[sector] = sector_totals.get(sector, ZERO) + value

    sector_weights = {
        sector: (value / equity if equity != 0 else ZERO)
        for sector, value in sector_totals.items()
    }

    breaches: list[ConcentrationBreach] = []
    for symbol, weight in position_weights.items():
        if weight > limits.max_position_weight:
            breaches.append(
                ConcentrationBreach("position", symbol, weight, limits.max_position_weight)
            )
    for sector, weight in sector_weights.items():
        if weight > limits.max_sector_weight:
            breaches.append(
                ConcentrationBreach("sector", sector, weight, limits.max_sector_weight)
            )

    return ConcentrationReport(
        position_weights=position_weights,
        sector_weights=sector_weights,
        breaches=tuple(breaches),
    )


@dataclass(frozen=True, slots=True)
class RiskCheck:
    name: str
    satisfied: bool
    detail: str


def cash_floor_check(state: AccountState, *, limits: RiskLimits = RiskLimits()) -> RiskCheck:
    equity = state.equity
    floor = limits.cash_floor_fraction * equity
    satisfied = state.cash >= floor
    detail = (
        f"cash {state.cash} vs floor {floor} "
        f"({limits.cash_floor_fraction} of equity {equity})"
    )
    return RiskCheck(name="cash_floor", satisfied=satisfied, detail=detail)


# --------------------------------------------------------------------------
# Drawdown policy
# --------------------------------------------------------------------------


class DrawdownStage(str, Enum):
    NORMAL = "normal"
    REDUCED = "reduced"
    HALTED = "halted"


@dataclass(frozen=True, slots=True)
class RiskEvent:
    at: date
    from_stage: DrawdownStage
    to_stage: DrawdownStage
    equity: Decimal
    high_water_mark: Decimal
    drawdown_fraction: Decimal
    note: str


@dataclass(frozen=True, slots=True)
class DrawdownState:
    high_water_mark: Decimal
    equity: Decimal
    drawdown_fraction: Decimal
    stage: DrawdownStage
    risk_multiplier: Decimal
    entries_allowed: bool
    detail: str


_RISK_MULTIPLIER_BY_STAGE = {
    DrawdownStage.NORMAL: Decimal("1"),
    DrawdownStage.HALTED: ZERO,
}


class DrawdownMonitor:
    """Tracks equity against its high-water mark and enforces the two-stage
    drawdown policy: halve new-trade risk at 5% below the mark, halt new
    entries at 10%. The high-water mark only ever ratchets up. A halt never
    lifts itself — only `record_review`, given the student's own note, does."""

    def __init__(self, high_water_mark: Decimal, *, limits: RiskLimits = RiskLimits()) -> None:
        self._limits = limits
        self._hwm = high_water_mark
        self._stage = DrawdownStage.NORMAL
        self._events: list[RiskEvent] = []
        self._state = DrawdownState(
            high_water_mark=high_water_mark,
            equity=high_water_mark,
            drawdown_fraction=ZERO,
            stage=DrawdownStage.NORMAL,
            risk_multiplier=Decimal("1"),
            entries_allowed=True,
            detail="initialized at the high-water mark",
        )

    @property
    def state(self) -> DrawdownState:
        return self._state

    @property
    def events(self) -> tuple[RiskEvent, ...]:
        return tuple(self._events)

    def _risk_multiplier(self, stage: DrawdownStage) -> Decimal:
        if stage is DrawdownStage.REDUCED:
            return self._limits.reduced_multiplier
        return _RISK_MULTIPLIER_BY_STAGE[stage]

    def observe(self, equity: Decimal, as_of: date) -> DrawdownState:
        if equity > self._hwm:
            self._hwm = equity

        drawdown = (self._hwm - equity) / self._hwm if self._hwm != 0 else ZERO

        prior_stage = self._stage
        if prior_stage is DrawdownStage.HALTED:
            # A halt never lifts itself, no matter how far equity recovers.
            new_stage = DrawdownStage.HALTED
        elif drawdown >= self._limits.halt_at:
            new_stage = DrawdownStage.HALTED
        elif drawdown >= self._limits.reduce_at:
            new_stage = DrawdownStage.REDUCED
        else:
            new_stage = DrawdownStage.NORMAL

        if new_stage != prior_stage:
            self._events.append(
                RiskEvent(
                    at=as_of,
                    from_stage=prior_stage,
                    to_stage=new_stage,
                    equity=equity,
                    high_water_mark=self._hwm,
                    drawdown_fraction=drawdown,
                    note="",
                )
            )
        self._stage = new_stage

        self._state = DrawdownState(
            high_water_mark=self._hwm,
            equity=equity,
            drawdown_fraction=drawdown,
            stage=new_stage,
            risk_multiplier=self._risk_multiplier(new_stage),
            entries_allowed=new_stage is not DrawdownStage.HALTED,
            detail=f"drawdown {drawdown} vs reduce {self._limits.reduce_at} / halt {self._limits.halt_at}",
        )
        return self._state

    def record_review(self, as_of: date, note: str) -> DrawdownState:
        """Explicitly resume after a halt. Requires the student's own
        non-empty note — this module never writes one — and always appends
        a logged transition, so resumption is never invisible."""
        if not note.strip():
            raise ValueError(
                "resuming after a halt requires the student's own review note; "
                "none is generated or defaulted here"
            )

        prior_stage = self._stage
        equity = self._state.equity
        drawdown = (self._hwm - equity) / self._hwm if self._hwm != 0 else ZERO
        new_stage = DrawdownStage.NORMAL

        self._events.append(
            RiskEvent(
                at=as_of,
                from_stage=prior_stage,
                to_stage=new_stage,
                equity=equity,
                high_water_mark=self._hwm,
                drawdown_fraction=drawdown,
                note=note,
            )
        )
        self._stage = new_stage
        self._state = DrawdownState(
            high_water_mark=self._hwm,
            equity=equity,
            drawdown_fraction=drawdown,
            stage=new_stage,
            risk_multiplier=self._risk_multiplier(new_stage),
            entries_allowed=True,
            detail=f"resumed by recorded review: {note}",
        )
        return self._state

    def throttle(self, constraints: SizingConstraints) -> SizingConstraints:
        """A new `SizingConstraints` with `risk_fraction` scaled by the
        current stage multiplier (0 when halted, which makes the solver
        block on MIN_LOT_EXCEEDS_RISK)."""
        return replace(
            constraints, risk_fraction=constraints.risk_fraction * self._state.risk_multiplier
        )

    def gate_new_entry(self, symbol: str) -> BlockedOrder | None:
        """A BlockedOrder while halted, else None. `BlockReason` has no
        dedicated risk-halt member; RULE_CONFLICT is used and is a contract
        gap to report, not fix here."""
        if self._stage is DrawdownStage.HALTED:
            return BlockedOrder(
                symbol,
                BlockReason.RULE_CONFLICT,
                "new entries halted by the drawdown policy pending a recorded review",
            )
        return None
