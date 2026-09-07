"""The integer share solver.

Given `SizingConstraints`, an entry price bound `P`, and a protective
reference `S`, choose the largest integer `q` that satisfies every cap at
once: the risk budget, spendable cash, and the position ceiling. The solver
never forces the configured minimum and never rounds a share count up — if
the minimum permitted quantity would violate a cap, the order is blocked
with the binding reason rather than shaved down to fit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from investlab.contracts import (
    Action,
    BlockedOrder,
    BlockReason,
    SizedOrder,
    SizingConstraints,
)
from investlab.money import round_shares, usd, usd_ceil

GAP_STRESS_FRACTION = Decimal("0.20")

_ONE = Decimal("1")
_MAX_CORRECTION_STEPS = 100_000


class BindingConstraint(str, Enum):
    RISK_BUDGET = "risk_budget"
    SPENDABLE_CASH = "spendable_cash"
    POSITION_CEILING = "position_ceiling"


def _exit_commission(quantity: int, exit_price: Decimal, constraints: SizingConstraints) -> Decimal:
    """Exit commission plus the proportional sell fee, estimated at
    `exit_price` and rounded up so a rounding error can never make an order
    look affordable when it is not."""
    fee = usd_ceil(constraints.sell_fee_rate * Decimal(quantity) * exit_price)
    return constraints.commission_per_trade + fee


def planned_loss(
    quantity: int,
    price_bound: Decimal,
    protective_reference: Decimal,
    constraints: SizingConstraints,
) -> Decimal:
    """L(q) = q*(P-S) + entry_commission + exit_commission."""
    entry_commission = constraints.commission_per_trade
    exit_commission = _exit_commission(quantity, protective_reference, constraints)
    per_share_risk = price_bound - protective_reference
    return usd(Decimal(quantity) * per_share_risk + entry_commission + exit_commission)


def _risk_cap(
    price_bound: Decimal, protective_reference: Decimal, constraints: SizingConstraints
) -> int:
    budget = constraints.risk_fraction * constraints.equity
    per_share_risk = price_bound - protective_reference
    commission_estimate = constraints.commission_per_trade
    seed = round_shares((budget - 2 * commission_estimate) / per_share_risk)
    seed = max(seed, 0)

    steps = 0
    while (
        planned_loss(seed + 1, price_bound, protective_reference, constraints) <= budget
        and steps < _MAX_CORRECTION_STEPS
    ):
        seed += 1
        steps += 1
    steps = 0
    while (
        seed > 0
        and planned_loss(seed, price_bound, protective_reference, constraints) > budget
        and steps < _MAX_CORRECTION_STEPS
    ):
        seed -= 1
        steps += 1
    return seed


def _cash_cap(price_bound: Decimal, constraints: SizingConstraints) -> int:
    entry_commission = constraints.commission_per_trade
    return max(0, round_shares((constraints.spendable_cash - entry_commission) / price_bound))


def _ceiling_cap(
    price_bound: Decimal, current_position_value: Decimal, constraints: SizingConstraints
) -> int:
    headroom = constraints.position_ceiling_fraction * constraints.equity - current_position_value
    return max(0, round_shares(headroom / price_bound))


def size_order(
    symbol: str,
    price_bound: Decimal,
    protective_reference: Decimal | None,
    constraints: SizingConstraints,
    *,
    current_position_value: Decimal = Decimal("0"),
    rationale: str = "",
) -> SizedOrder | BlockedOrder:
    if price_bound < constraints.min_price:
        return BlockedOrder(
            symbol,
            BlockReason.PRICE_BELOW_MINIMUM,
            f"price {price_bound} is below the minimum of {constraints.min_price}",
        )

    if protective_reference is None or protective_reference >= price_bound:
        detail = (
            "no protective reference supplied"
            if protective_reference is None
            else f"protective reference {protective_reference} is not below price {price_bound}"
        )
        return BlockedOrder(symbol, BlockReason.INSUFFICIENT_EVIDENCE, detail)

    caps: list[tuple[BindingConstraint, int]] = [
        (BindingConstraint.RISK_BUDGET, _risk_cap(price_bound, protective_reference, constraints)),
        (BindingConstraint.SPENDABLE_CASH, _cash_cap(price_bound, constraints)),
        (
            BindingConstraint.POSITION_CEILING,
            _ceiling_cap(price_bound, current_position_value, constraints),
        ),
    ]
    binding, quantity = min(caps, key=lambda item: item[1])

    floor_quantity = max(constraints.min_shares, 1)
    if quantity < floor_quantity:
        reason_by_binding = {
            BindingConstraint.RISK_BUDGET: BlockReason.MIN_LOT_EXCEEDS_RISK,
            BindingConstraint.SPENDABLE_CASH: BlockReason.MIN_LOT_EXCEEDS_CASH,
            BindingConstraint.POSITION_CEILING: BlockReason.POSITION_CEILING,
        }
        return BlockedOrder(
            symbol,
            reason_by_binding[binding],
            f"minimum lot of {floor_quantity} exceeds the {binding.value} cap of {quantity}",
        )

    entry_commission = constraints.commission_per_trade
    notional = usd(Decimal(quantity) * price_bound)
    risk = planned_loss(quantity, price_bound, protective_reference, constraints)

    gapped_price = price_bound * (_ONE - GAP_STRESS_FRACTION)
    gap_exit_commission = _exit_commission(quantity, gapped_price, constraints)
    gap_stress_loss = usd(
        Decimal(quantity) * price_bound * GAP_STRESS_FRACTION
        + entry_commission
        + gap_exit_commission
    )

    return SizedOrder(
        symbol=symbol,
        action=Action.BUY,
        quantity=quantity,
        price_bound=price_bound,
        estimated_notional=notional,
        estimated_commission=entry_commission,
        protective_reference=protective_reference,
        planned_risk=risk,
        gap_stress_loss=gap_stress_loss,
        binding_constraint=binding.value,
        rationale=rationale,
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate order fed to `size_batch`."""

    symbol: str
    price_bound: Decimal
    protective_reference: Decimal | None
    rank: int = 0
    current_position_value: Decimal = Decimal("0")
    rationale: str = ""


def size_batch(
    candidates: tuple[Candidate, ...] | list[Candidate],
    constraints: SizingConstraints,
) -> tuple[SizedOrder | BlockedOrder, ...]:
    """Size candidates in deterministic (rank, symbol) order, reserving cash
    sequentially so two candidates can never spend the same dollar. Equity
    and the ceiling denominator stay pinned to the original constraints —
    only `spendable_cash` shrinks as each order is reserved."""
    ordered = sorted(candidates, key=lambda c: (c.rank, c.symbol))
    results: list[SizedOrder | BlockedOrder] = []
    remaining_cash = constraints.spendable_cash

    for candidate in ordered:
        step_constraints = replace(constraints, spendable_cash=remaining_cash)
        result = size_order(
            candidate.symbol,
            candidate.price_bound,
            candidate.protective_reference,
            step_constraints,
            current_position_value=candidate.current_position_value,
            rationale=candidate.rationale,
        )
        results.append(result)
        if isinstance(result, SizedOrder):
            remaining_cash -= result.estimated_notional + result.estimated_commission

    return tuple(results)
