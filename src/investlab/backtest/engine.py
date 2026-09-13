"""A purpose-built daily event loop that replays the DECA order sheet.

For each session t the sheet is built exactly as the morning run builds it,
from bars through the previous close, and every order fills at t's close,
which is how DECA prices an order entered during the session. Signals lag
fills by one session here as they do live.

What the replay cannot see, stated in every report it feeds:

- **Bonds.** Individual bond price history is not obtainable at zero budget, so
  the DECA bond set-aside sits in cash and earns nothing.
- **Earnings dates.** There is no point-in-time earnings calendar, so the
  earnings filter is off and gap losses around reports are understated.
- **Removed companies.** The universe is fixed as of 2026-09-06 and is
  survivorship-biased.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from investlab import calendar as cal
from investlab.competitions.deca import DecaProfile
from investlab.config import DecaConfig
from investlab.contracts import Action, AssetClass, Bar, Fill
from investlab.data.universe import BIAS_WARNING, Universe
from investlab.money import round_shares, usd, usd_ceil
from investlab.performance import EquityPoint
from investlab.plan import SheetInputs, build_sheet
from investlab.portfolio.ledger import Ledger
from investlab.screen import FeatureFrame, compute_features

BOND_NOTE = (
    "Bond leg held as cash: individual bond prices are not obtainable at zero budget, "
    "so the DECA bond set-aside earns nothing in the replay."
)
EARNINGS_NOTE = (
    "Earnings filter not applied: there is no point-in-time earnings calendar, so gap "
    "losses around reports are understated."
)


@dataclass(frozen=True, slots=True)
class BacktestSettings:
    start: date
    end: date
    starting_cash: Decimal = Decimal("100000")
    # 0 keeps the live policy: a halt lifts only on a recorded review, and the
    # replay has no one to write one.
    resume_after_halt_sessions: int = 0
    benchmark: str = "SPY"


@dataclass(frozen=True, slots=True)
class SimTrade:
    session: date
    symbol: str
    action: str
    quantity: int
    price: Decimal
    commission: Decimal
    fees: Decimal
    asset_class: str
    reason: str

    def as_row(self) -> dict[str, str]:
        return {
            "session": self.session.isoformat(),
            "symbol": self.symbol,
            "action": self.action,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "commission": str(self.commission),
            "fees": str(self.fees),
            "asset_class": self.asset_class,
            "reason": self.reason,
        }


@dataclass(slots=True)
class RunResult:
    name: str
    starting_cash: Decimal
    sessions: list[date] = field(default_factory=list)
    equity: list[Decimal] = field(default_factory=list)
    cash: list[Decimal] = field(default_factory=list)
    invested: list[Decimal] = field(default_factory=list)
    trades: list[SimTrade] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def record(self, session: date, cash: Decimal, invested: Decimal) -> None:
        self.sessions.append(session)
        self.cash.append(usd(cash))
        self.invested.append(usd(invested))
        self.equity.append(usd(cash + invested))

    @property
    def costs(self) -> Decimal:
        return sum((t.commission + t.fees for t in self.trades), Decimal("0"))


class CloseBook:
    """Closes by symbol and session, with carry-forward lookup."""

    def __init__(self, bars_by_symbol: dict[str, list[Bar]]) -> None:
        self._sessions: dict[str, list[date]] = {}
        self._closes: dict[str, list[Decimal]] = {}
        for symbol, bars in bars_by_symbol.items():
            ordered = sorted(bars, key=lambda b: b.session)
            self._sessions[symbol] = [b.session for b in ordered]
            self._closes[symbol] = [b.close for b in ordered]

    def sessions(self, symbol: str) -> list[date]:
        return self._sessions.get(symbol, [])

    def on(self, symbol: str, session: date) -> Decimal | None:
        ss = self._sessions.get(symbol)
        if not ss:
            return None
        i = bisect.bisect_left(ss, session)
        return self._closes[symbol][i] if i < len(ss) and ss[i] == session else None

    def last(self, symbol: str, session: date) -> Decimal | None:
        ss = self._sessions.get(symbol)
        if not ss:
            return None
        i = bisect.bisect_right(ss, session)
        return self._closes[symbol][i - 1] if i else None

    def between(self, symbol: str, first: date, last: date) -> list[Decimal]:
        ss = self._sessions.get(symbol, [])
        lo, hi = bisect.bisect_left(ss, first), bisect.bisect_right(ss, last)
        return self._closes.get(symbol, [])[lo:hi]


def trading_sessions(closes: CloseBook, settings: BacktestSettings) -> list[date]:
    return [d for d in closes.sessions(settings.benchmark) if settings.start <= d <= settings.end]


def marked(ledger: Ledger, closes: CloseBook, session: date) -> tuple[dict[str, Decimal], Decimal]:
    marks: dict[str, Decimal] = {}
    invested = Decimal("0")
    for p in ledger.positions:
        px = closes.last(p.symbol, session)
        marks[p.symbol] = px if px is not None else p.lots[0].price
        invested += marks[p.symbol] * Decimal(p.quantity)
    return marks, invested


def sim_buy(
    ledger: Ledger,
    result: RunResult,
    session: date,
    symbol: str,
    quantity: int,
    price: Decimal,
    commission: Decimal,
    asset_class: AssetClass,
    reason: str,
    min_shares: int,
) -> int:
    """Buy at `price`, shrinking to what cash affords. Returns shares bought."""
    if price <= 0:
        return 0
    if usd(price * Decimal(quantity)) + commission > ledger.cash:
        quantity = round_shares((ledger.cash - commission) / price)
        while quantity > 0 and usd(price * Decimal(quantity)) + commission > ledger.cash:
            quantity -= 1
    if quantity < min_shares:
        return 0
    ledger.apply_fill(
        Fill(symbol, Action.BUY, quantity, price, commission, Decimal("0"), session), asset_class
    )
    result.trades.append(
        SimTrade(
            session,
            symbol,
            "buy",
            quantity,
            price,
            commission,
            Decimal("0"),
            asset_class.value,
            reason,
        )
    )
    return quantity


def sim_sell(
    ledger: Ledger,
    result: RunResult,
    session: date,
    symbol: str,
    quantity: int,
    price: Decimal,
    commission: Decimal,
    fee_rate: Decimal,
    asset_class: str,
    reason: str,
) -> None:
    fee = usd_ceil(fee_rate * Decimal(quantity) * price)
    ledger.apply_fill(Fill(symbol, Action.SELL, quantity, price, commission, fee, session))
    result.trades.append(
        SimTrade(session, symbol, "sell", quantity, price, commission, fee, asset_class, reason)
    )


def run_strategy(
    bars: dict[str, list[Bar]],
    universe: Universe,
    profile: DecaProfile,
    cfg: DecaConfig,
    settings: BacktestSettings,
    *,
    features: dict[str, FeatureFrame] | None = None,
) -> RunResult:
    features = features if features is not None else compute_features(bars)
    closes = CloseBook(bars)
    sessions = trading_sessions(closes, settings)
    ledger = Ledger(settings.starting_cash)
    stops: dict[str, Decimal] = {}
    curve: list[EquityPoint] = []
    reviews: list[tuple[date, str]] = []
    halted_since: date | None = None
    result = RunResult(
        name="strategy",
        starting_cash=settings.starting_cash,
        notes=[BOND_NOTE, EARNINGS_NOTE, BIAS_WARNING],
    )
    halts = 0

    for t in sessions:
        prev = cal.prev_session(t)
        marks, _ = marked(ledger, closes, prev)
        account = ledger.snapshot(marks, prev)
        sheet = build_sheet(
            SheetInputs(
                as_of=prev,
                account=account,
                features=features,
                universe=universe,
                profile=profile,
                cfg=cfg,
                starting_capital=settings.starting_cash,
                entry_stops=dict(stops),
                earnings=None,
                trades=[tr.as_row() for tr in result.trades],
                equity_curve=list(curve),
                risk_reviews=list(reviews),
                staleness_days=7,
                evaluate_rules=False,
                check_earnings=False,
            )
        )

        if sheet.fill_session == t:
            for s in sheet.sells:
                px = closes.on(s.symbol, t)
                if px is None:
                    continue
                sim_sell(
                    ledger,
                    result,
                    t,
                    s.symbol,
                    s.quantity,
                    px,
                    profile.commission_per_trade,
                    profile.sec_fee_rate,
                    s.asset_class,
                    s.reason,
                )
                stops.pop(s.symbol, None)

            for c in sheet.compliance:
                if c.bucket != "mutual_funds" or not c.symbol:
                    continue
                px = closes.on(c.symbol, t)
                if px is None or px <= 0:
                    continue
                need = math.ceil(cfg.diversification_minimum / px)
                qty = max(c.quantity or 0, need)
                sim_buy(
                    ledger,
                    result,
                    t,
                    c.symbol,
                    qty,
                    px,
                    profile.commission_per_trade,
                    AssetClass.MUTUAL_FUND,
                    "compliance",
                    need,
                )

            for b in sheet.buys:
                px = closes.on(b.symbol, t)
                if px is None:
                    continue
                bought = sim_buy(
                    ledger,
                    result,
                    t,
                    b.symbol,
                    b.quantity,
                    px,
                    profile.commission_per_trade,
                    AssetClass(b.asset_class),
                    "entry",
                    cfg.min_shares_per_buy,
                )
                if bought:
                    stops[b.symbol] = b.stop

        _, invested = marked(ledger, closes, t)
        result.record(t, ledger.cash, invested)
        curve.append(EquityPoint(t, result.equity[-1], None))

        if sheet.drawdown_stage == "halted":
            if halted_since is None:
                halted_since = t
                halts += 1
            elif settings.resume_after_halt_sessions and (
                len(cal.sessions_between(halted_since, t)) - 1
                >= settings.resume_after_halt_sessions
            ):
                wait = settings.resume_after_halt_sessions
                reviews.append((t, f"backtest assumption: resume {wait} sessions after a halt"))
                halted_since = None
        else:
            halted_since = None

    if halts:
        result.notes.append(f"Drawdown halt triggered {halts} time(s).")
    return result
