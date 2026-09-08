"""Performance measurement for one competition.

Answers the questions a report has to answer: what did we buy and at what
price, what did we sell and at what price, what did each of those earn, how is
the book doing overall, and how does that compare to the benchmark we are
actually ranked against.

Two things this deliberately does not do. It does not annualise returns from a
twelve-week game, because a 10% gain over twelve weeks is not a 47% annual
return and presenting it that way is the single most common way a student
report overstates itself. And it does not compute a Sharpe ratio from a few
dozen daily observations, where the standard error swamps the estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from investlab.contracts import AccountState

TWO = Decimal("0.01")


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    """Percent, or zero when the denominator is zero.

    A zero denominator means "no basis to measure against", not "no change",
    so callers should check `whole` before presenting the result as a return.
    """
    if whole == 0:
        return Decimal("0")
    return (part / whole * Decimal(100)).quantize(TWO)


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """A closed position: shares bought, later sold, matched FIFO."""

    symbol: str
    quantity: int
    bought_on: date
    buy_price: Decimal
    sold_on: date
    sell_price: Decimal
    commissions: Decimal

    @property
    def proceeds(self) -> Decimal:
        return self.sell_price * Decimal(self.quantity)

    @property
    def cost(self) -> Decimal:
        return self.buy_price * Decimal(self.quantity)

    @property
    def realized(self) -> Decimal:
        """Net of both commissions. Gross P&L flatters a $5-a-trade game."""
        return (self.proceeds - self.cost - self.commissions).quantize(TWO)

    @property
    def return_pct(self) -> Decimal:
        return _pct(self.realized, self.cost)

    @property
    def days_held(self) -> int:
        return (self.sold_on - self.bought_on).days


@dataclass(frozen=True, slots=True)
class OpenPosition:
    symbol: str
    asset_class: str
    quantity: int
    avg_cost: Decimal
    mark: Decimal
    opened: date
    as_of: date

    @property
    def cost_basis(self) -> Decimal:
        return (self.avg_cost * Decimal(self.quantity)).quantize(TWO)

    @property
    def market_value(self) -> Decimal:
        return (self.mark * Decimal(self.quantity)).quantize(TWO)

    @property
    def unrealized(self) -> Decimal:
        return (self.market_value - self.cost_basis).quantize(TWO)

    @property
    def return_pct(self) -> Decimal:
        return _pct(self.unrealized, self.cost_basis)

    @property
    def days_held(self) -> int:
        return (self.as_of - self.opened).days


@dataclass(frozen=True, slots=True)
class EquityPoint:
    session: date
    equity: Decimal
    benchmark: Decimal | None = None


@dataclass(slots=True)
class Performance:
    starting_capital: Decimal
    account: AccountState
    open_positions: list[OpenPosition] = field(default_factory=list)
    round_trips: list[RoundTrip] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    commissions_paid: Decimal = Decimal("0")

    # -- headline ----------------------------------------------------------

    @property
    def equity(self) -> Decimal:
        return self.account.equity.quantize(TWO)

    @property
    def total_pnl(self) -> Decimal:
        return (self.equity - self.starting_capital).quantize(TWO)

    @property
    def total_return_pct(self) -> Decimal:
        return _pct(self.total_pnl, self.starting_capital)

    @property
    def realized_pnl(self) -> Decimal:
        return sum((t.realized for t in self.round_trips), Decimal("0")).quantize(TWO)

    @property
    def unrealized_pnl(self) -> Decimal:
        return sum((p.unrealized for p in self.open_positions), Decimal("0")).quantize(TWO)

    @property
    def invested(self) -> Decimal:
        return sum((p.cost_basis for p in self.open_positions), Decimal("0")).quantize(TWO)

    @property
    def cash_pct(self) -> Decimal:
        return _pct(self.account.cash, self.equity)

    # -- trade quality -----------------------------------------------------

    @property
    def wins(self) -> list[RoundTrip]:
        return [t for t in self.round_trips if t.realized > 0]

    @property
    def losses(self) -> list[RoundTrip]:
        return [t for t in self.round_trips if t.realized < 0]

    @property
    def win_rate_pct(self) -> Decimal | None:
        """None, not zero, when nothing has closed. An empty win rate is
        undefined, and reporting 0% would read as 'everything lost'."""
        if not self.round_trips:
            return None
        return _pct(Decimal(len(self.wins)), Decimal(len(self.round_trips)))

    @property
    def best(self) -> RoundTrip | None:
        return max(self.round_trips, key=lambda t: t.realized, default=None)

    @property
    def worst(self) -> RoundTrip | None:
        return min(self.round_trips, key=lambda t: t.realized, default=None)

    # -- curve -------------------------------------------------------------

    @property
    def peak_equity(self) -> Decimal:
        values = [p.equity for p in self.equity_curve] or [self.equity]
        return max(max(values), self.starting_capital)

    @property
    def max_drawdown_pct(self) -> Decimal:
        """Largest peak-to-trough fall in the recorded curve.

        Only as good as the snapshots taken. A gap in the curve hides whatever
        happened inside it, so this understates rather than overstates.
        """
        peak = self.starting_capital
        worst = Decimal("0")
        for point in self.equity_curve:
            peak = max(peak, point.equity)
            if peak > 0:
                fall = (peak - point.equity) / peak * Decimal(100)
                worst = max(worst, fall)
        return worst.quantize(TWO)

    @property
    def benchmark_return_pct(self) -> Decimal | None:
        """Benchmark return over the same window as the recorded curve."""
        marked = [p for p in self.equity_curve if p.benchmark is not None]
        if len(marked) < 2:
            return None
        first, last = marked[0].benchmark, marked[-1].benchmark
        if not first:
            return None
        return ((last - first) / first * Decimal(100)).quantize(TWO)

    @property
    def excess_return_pct(self) -> Decimal | None:
        """Return above the benchmark. This is what DECA actually ranks."""
        bench = self.benchmark_return_pct
        if bench is None:
            return None
        # Measured over the curve's window, so the portfolio side uses the same
        # window rather than inception-to-date.
        if len(self.equity_curve) < 2 or not self.equity_curve[0].equity:
            return None
        first, last = self.equity_curve[0].equity, self.equity_curve[-1].equity
        port = (last - first) / first * Decimal(100)
        return (port - bench).quantize(TWO)


def round_trips_from_trades(rows: list[dict[str, str]]) -> tuple[list[RoundTrip], Decimal]:
    """Match sells against earlier buys FIFO, the same order the ledger uses.

    Returns the closed round trips and the total commission paid across every
    recorded trade, open or closed.
    """
    lots: dict[str, list[dict]] = {}
    closed: list[RoundTrip] = []
    commissions = Decimal("0")

    for row in rows:
        symbol = row["symbol"]
        qty = int(row["quantity"])
        price = Decimal(row["price"])
        comm = Decimal(row["commission"])
        session = date.fromisoformat(row["session"])
        commissions += comm

        if row["action"] == "buy":
            lots.setdefault(symbol, []).append(
                {"qty": qty, "price": price, "opened": session, "comm": comm}
            )
            continue

        remaining = qty
        # A sell's commission is spread across the lots it closes, so a single
        # sale that closes two lots does not charge the fee twice.
        while remaining > 0 and lots.get(symbol):
            lot = lots[symbol][0]
            take = min(remaining, lot["qty"])
            share = comm * Decimal(take) / Decimal(qty) if qty else Decimal("0")
            buy_share = lot["comm"] * Decimal(take) / Decimal(lot["qty"]) if lot["qty"] else 0
            closed.append(
                RoundTrip(
                    symbol=symbol,
                    quantity=take,
                    bought_on=lot["opened"],
                    buy_price=lot["price"],
                    sold_on=session,
                    sell_price=price,
                    commissions=(share + Decimal(buy_share)).quantize(TWO),
                )
            )
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] == 0:
                lots[symbol].pop(0)

    return closed, commissions.quantize(TWO)


def open_positions_from_account(account: AccountState) -> list[OpenPosition]:
    out: list[OpenPosition] = []
    for pos in account.positions:
        if pos.quantity == 0:
            continue
        avg = pos.net_cost / Decimal(pos.quantity)
        out.append(
            OpenPosition(
                symbol=pos.symbol,
                asset_class=pos.asset_class.value,
                quantity=pos.quantity,
                avg_cost=avg.quantize(Decimal("0.0001")),
                mark=account.marks.get(pos.symbol, avg),
                opened=min(lot.opened for lot in pos.lots),
                as_of=account.as_of,
            )
        )
    return sorted(out, key=lambda p: p.unrealized, reverse=True)
