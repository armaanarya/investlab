"""The authoritative position record mirroring the official platform statement.

The platform is the real record; this module is our reconciled copy. It is
the only mutable thing in the portfolio layer: cash, lots, receivables,
liabilities, and the full fill history all live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence

from investlab.contracts import Action, AssetClass, AccountState, Fill, Lot, Position
from investlab.money import ZERO, usd

_SELL_ACTIONS = frozenset({Action.SELL, Action.REDUCE, Action.EXIT})
_BUY_ACTIONS = frozenset({Action.BUY})


class LedgerError(Exception):
    """Base class for ledger errors."""


class InsufficientCashError(LedgerError):
    """A buy would drive cash below zero and margin is disabled."""


class InsufficientSharesError(LedgerError):
    """A sell would consume more shares than are held."""


class DuplicateDividendError(LedgerError):
    """The same (symbol, ex_date, pay_date) dividend was recognized twice."""


class UnknownSymbolError(LedgerError):
    """A mark, split, or dividend named a symbol the ledger doesn't hold."""


@dataclass(frozen=True, slots=True)
class RealizedTrade:
    """One lot-slice closed out by a sell."""

    symbol: str
    quantity: int
    opened: date
    closed: date
    proceeds: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal


class Ledger:
    """Cash, lots, receivables, liabilities, and fill history for one account."""

    def __init__(self, cash: Decimal, *, allow_margin: bool = False) -> None:
        self._cash: Decimal = usd(cash)
        self._allow_margin = allow_margin
        self._lots: dict[str, list[Lot]] = {}
        self._classes: dict[str, AssetClass] = {}
        self._fills: list[Fill] = []
        self._realized: list[RealizedTrade] = []
        self._receivables: Decimal = ZERO
        self._liabilities: Decimal = ZERO

    # -- read-only views ---------------------------------------------------

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(
            Position(symbol, self._classes[symbol], tuple(lots))
            for symbol, lots in self._lots.items()
            if lots
        )

    @property
    def receivables(self) -> Decimal:
        return self._receivables

    @property
    def liabilities(self) -> Decimal:
        return self._liabilities

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    @property
    def realized(self) -> tuple[RealizedTrade, ...]:
        return tuple(self._realized)

    @property
    def realized_pnl(self) -> Decimal:
        return sum((t.realized_pnl for t in self._realized), ZERO)

    # -- fills ---------------------------------------------------------------

    def apply_fill(self, fill: Fill, asset_class: AssetClass = AssetClass.STOCK) -> None:
        if fill.action in _BUY_ACTIONS:
            self._apply_buy(fill, asset_class)
        elif fill.action in _SELL_ACTIONS:
            self._apply_sell(fill)
        else:
            raise ValueError(
                f"{fill.action!r} is a plan verb, not a transaction; "
                "only BUY/SELL/REDUCE/EXIT fills may be applied"
            )

    def _validate_fill_shape(self, fill: Fill) -> None:
        if fill.quantity <= 0:
            raise ValueError(f"fill quantity must be > 0, got {fill.quantity}")
        if fill.price < 0:
            raise ValueError(f"fill price must be >= 0, got {fill.price}")
        if fill.commission < 0:
            raise ValueError(f"fill commission must be >= 0, got {fill.commission}")
        if fill.fees < 0:
            raise ValueError(f"fill fees must be >= 0, got {fill.fees}")

    def _apply_buy(self, fill: Fill, asset_class: AssetClass) -> None:
        self._validate_fill_shape(fill)
        notional = usd(fill.price * Decimal(fill.quantity))
        total_cost = notional + fill.commission + fill.fees
        new_cash = self._cash - total_cost
        if not self._allow_margin and new_cash < ZERO:
            raise InsufficientCashError(
                f"buying {fill.quantity} {fill.symbol} @ {fill.price} costs {total_cost}, "
                f"only {self._cash} cash available"
            )
        lot = Lot(fill.symbol, fill.quantity, fill.price, fill.commission, fill.session)
        self._cash = new_cash
        self._lots.setdefault(fill.symbol, []).append(lot)
        self._classes.setdefault(fill.symbol, asset_class)
        self._fills.append(fill)

    def _apply_sell(self, fill: Fill) -> None:
        self._validate_fill_shape(fill)
        lots = self._lots.get(fill.symbol, [])
        held = sum(lot.quantity for lot in lots)
        if fill.quantity > held:
            raise InsufficientSharesError(
                f"selling {fill.quantity} {fill.symbol} but only {held} held"
            )

        # FIFO by (opened, insertion order). Python sort is stable, so
        # sorting by `opened` alone preserves insertion order among ties.
        ordered = sorted(range(len(lots)), key=lambda i: lots[i].opened)

        remaining_to_sell = fill.quantity
        slices: list[tuple[int, int]] = []  # (lot_index, consumed_qty)
        for idx in ordered:
            if remaining_to_sell <= 0:
                break
            available = lots[idx].quantity
            consumed = min(available, remaining_to_sell)
            slices.append((idx, consumed))
            remaining_to_sell -= consumed

        total_qty = Decimal(fill.quantity)
        commission_allocated = ZERO
        fees_allocated = ZERO
        new_realized: list[RealizedTrade] = []
        new_lots = list(lots)
        remove_indices: set[int] = set()

        for slice_num, (idx, consumed) in enumerate(slices):
            lot = lots[idx]
            is_last = slice_num == len(slices) - 1

            if is_last:
                exit_commission = fill.commission - commission_allocated
                exit_fees = fill.fees - fees_allocated
            else:
                exit_commission = usd(fill.commission * Decimal(consumed) / total_qty)
                exit_fees = usd(fill.fees * Decimal(consumed) / total_qty)
                commission_allocated += exit_commission
                fees_allocated += exit_fees

            basis = lot.price * Decimal(consumed)
            if consumed == lot.quantity:
                entry_commission_consumed = lot.commission
                remaining_commission = ZERO
            else:
                entry_commission_consumed = usd(
                    lot.commission * Decimal(consumed) / Decimal(lot.quantity)
                )
                remaining_commission = lot.commission - entry_commission_consumed

            proceeds_net = usd(fill.price * Decimal(consumed)) - exit_commission - exit_fees
            cost_basis = basis + entry_commission_consumed
            realized_pnl = proceeds_net - cost_basis

            new_realized.append(
                RealizedTrade(
                    symbol=fill.symbol,
                    quantity=consumed,
                    opened=lot.opened,
                    closed=fill.session,
                    proceeds=proceeds_net,
                    cost_basis=cost_basis,
                    realized_pnl=realized_pnl,
                )
            )

            remaining_qty = lot.quantity - consumed
            if remaining_qty == 0:
                remove_indices.add(idx)
            else:
                new_lots[idx] = Lot(
                    lot.symbol, remaining_qty, lot.price, remaining_commission, lot.opened
                )

        rebuilt = [lot for i, lot in enumerate(new_lots) if i not in remove_indices]

        total_proceeds = usd(fill.price * total_qty) - fill.commission - fill.fees
        self._cash = self._cash + total_proceeds
        self._lots[fill.symbol] = rebuilt
        self._realized.extend(new_realized)
        self._fills.append(fill)

    # -- snapshot -------------------------------------------------------------

    def snapshot(self, marks: Mapping[str, Decimal], as_of: date) -> AccountState:
        positions = self.positions
        for pos in positions:
            if pos.symbol not in marks:
                raise UnknownSymbolError(f"no mark supplied for held position {pos.symbol}")
        marks_copy = {symbol: usd(value) for symbol, value in marks.items()}
        return AccountState(
            as_of=as_of,
            cash=self._cash,
            positions=positions,
            marks=marks_copy,
            receivables=self._receivables,
            liabilities=self._liabilities,
        )
