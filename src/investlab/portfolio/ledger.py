"""The authoritative position record mirroring the official platform statement.

The platform is the real record; this module is our reconciled copy. It is
the only mutable thing in the portfolio layer: cash, lots, receivables,
liabilities, and the full fill history all live here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from investlab.contracts import AccountState, Action, AssetClass, Fill, Lot, Position
from investlab.money import ZERO, round_shares, usd

_SELL_ACTIONS = frozenset({Action.SELL, Action.REDUCE, Action.EXIT})
_BUY_ACTIONS = frozenset({Action.BUY})

# Sub-cent precision for split-adjusted per-share basis: a cent would lose
# value on, say, a 3-for-1 split. Documented here rather than in money.py
# because it is specific to lot-basis rescaling, not general money math.
_EIGHT_DP = Decimal("0.00000001")


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


@dataclass(frozen=True, slots=True)
class SplitRecord:
    """One split event. `cash_in_lieu` is the value of fractional shares
    that could not be issued, moved to cash so no value is lost or created."""

    symbol: str
    ratio: Decimal
    effective: date
    cash_in_lieu: Decimal


@dataclass(frozen=True, slots=True)
class DividendAccrual:
    """A dividend recognized as a receivable on the ex-date and converted
    to cash on the pay date. `paid` is False until `settle_dividends` runs."""

    symbol: str
    per_share: Decimal
    ex_date: date
    pay_date: date
    shares: int
    amount: Decimal
    paid: bool = False


@dataclass(frozen=True, slots=True)
class FillDiscrepancy:
    """A fill the ledger and the platform statement both recorded, but with
    differing field values."""

    ours: Fill
    theirs: Fill
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """A read-only diff against an imported platform statement. Never
    rewrites our history to match — differences stay visible."""

    matched: tuple[Fill, ...]
    only_in_ledger: tuple[Fill, ...]
    only_in_statement: tuple[Fill, ...]
    discrepancies: tuple[FillDiscrepancy, ...]

    @property
    def in_agreement(self) -> bool:
        return not (self.only_in_ledger or self.only_in_statement or self.discrepancies)

    def summary(self) -> str:
        return (
            f"matched={len(self.matched)} "
            f"discrepancies={len(self.discrepancies)} "
            f"only_in_ledger={len(self.only_in_ledger)} "
            f"only_in_statement={len(self.only_in_statement)}"
        )


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
        self._dividends: list[DividendAccrual] = []
        self._dividend_keys: set[tuple[str, date, date]] = set()

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

    @property
    def dividends(self) -> tuple[DividendAccrual, ...]:
        return tuple(self._dividends)

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

    # -- splits ---------------------------------------------------------------

    def apply_split(self, symbol: str, ratio: Decimal, effective: date) -> SplitRecord:
        if ratio <= 0:
            raise ValueError(f"split ratio must be positive, got {ratio}")
        lots = self._lots.get(symbol)
        if not lots:
            raise UnknownSymbolError(f"cannot split {symbol}: no position held")

        new_lots: list[Lot] = []
        total_cash_in_lieu = ZERO
        for lot in lots:
            scaled_qty = Decimal(lot.quantity) * ratio
            new_qty = round_shares(scaled_qty)
            # Per-share basis scales inversely with the split ratio, so
            # quantity * price (economic value) is preserved before rounding.
            new_price = (lot.price / ratio).quantize(_EIGHT_DP, rounding=ROUND_HALF_UP)
            fractional_shares = scaled_qty - Decimal(new_qty)
            cash_in_lieu_lot = usd(fractional_shares * new_price)
            total_cash_in_lieu += cash_in_lieu_lot
            if new_qty > 0:
                new_lots.append(Lot(symbol, new_qty, new_price, lot.commission, lot.opened))

        self._lots[symbol] = new_lots
        self._cash += total_cash_in_lieu
        return SplitRecord(
            symbol=symbol, ratio=ratio, effective=effective, cash_in_lieu=total_cash_in_lieu
        )

    # -- dividends --------------------------------------------------------------

    def apply_dividend(
        self, symbol: str, per_share: Decimal, ex_date: date, pay_date: date
    ) -> DividendAccrual:
        key = (symbol, ex_date, pay_date)
        if key in self._dividend_keys:
            raise DuplicateDividendError(
                f"dividend for {symbol} ex {ex_date} pay {pay_date} already recognized"
            )
        lots = self._lots.get(symbol)
        if not lots:
            raise UnknownSymbolError(f"cannot accrue a dividend for {symbol}: no position held")

        shares_held = sum(lot.quantity for lot in lots)
        amount = usd(per_share * Decimal(shares_held))
        self._receivables += amount
        self._dividend_keys.add(key)
        accrual = DividendAccrual(
            symbol=symbol,
            per_share=per_share,
            ex_date=ex_date,
            pay_date=pay_date,
            shares=shares_held,
            amount=amount,
            paid=False,
        )
        self._dividends.append(accrual)
        return accrual

    def settle_dividends(self, as_of: date) -> tuple[DividendAccrual, ...]:
        settled: list[DividendAccrual] = []
        for i, accrual in enumerate(self._dividends):
            if accrual.paid or accrual.pay_date > as_of:
                continue
            paid_accrual = DividendAccrual(
                symbol=accrual.symbol,
                per_share=accrual.per_share,
                ex_date=accrual.ex_date,
                pay_date=accrual.pay_date,
                shares=accrual.shares,
                amount=accrual.amount,
                paid=True,
            )
            self._dividends[i] = paid_accrual
            self._receivables -= accrual.amount
            self._cash += accrual.amount
            settled.append(paid_accrual)
        return tuple(settled)

    # -- reconciliation ---------------------------------------------------------

    def reconcile(self, statement_fills: Sequence[Fill]) -> Reconciliation:
        """Diff our fill history against the platform's, read-only.

        The platform is authoritative; this never edits our fills to match.
        Matching, in order: (1) exact identity as a multiset, so equal fills
        pair off; (2) leftovers pair on (symbol, session, action) and report
        which fields differ; (3) whatever is still unpaired is ours alone or
        the platform's alone.
        """

        def identity_key(f: Fill) -> tuple:
            return (f.symbol, f.session, f.action, f.quantity, f.price, f.commission, f.fees)

        def pair_key(f: Fill) -> tuple:
            return (f.symbol, f.session, f.action)

        theirs_remaining = list(statement_fills)

        matched: list[Fill] = []
        unmatched_ours: list[Fill] = []
        for our_fill in self._fills:
            key = identity_key(our_fill)
            idx = next((i for i, t in enumerate(theirs_remaining) if identity_key(t) == key), None)
            if idx is None:
                unmatched_ours.append(our_fill)
            else:
                matched.append(our_fill)
                del theirs_remaining[idx]

        discrepancies: list[FillDiscrepancy] = []
        only_in_ledger: list[Fill] = []
        for our_fill in unmatched_ours:
            key = pair_key(our_fill)
            idx = next((i for i, t in enumerate(theirs_remaining) if pair_key(t) == key), None)
            if idx is None:
                only_in_ledger.append(our_fill)
            else:
                their_fill = theirs_remaining.pop(idx)
                fields = tuple(
                    name
                    for name in ("quantity", "price", "commission", "fees")
                    if getattr(our_fill, name) != getattr(their_fill, name)
                )
                discrepancies.append(FillDiscrepancy(our_fill, their_fill, fields))

        return Reconciliation(
            matched=tuple(matched),
            only_in_ledger=tuple(only_in_ledger),
            only_in_statement=tuple(theirs_remaining),
            discrepancies=tuple(discrepancies),
        )
