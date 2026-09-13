"""The DECA order sheet for one session: what to sell, what the compliance
legs need, and what to buy.

`build_sheet` is a pure function of its inputs. The `daily` command feeds it
the live ledger and cache; the backtest feeds it a simulated book and bars cut
off at each past session. The same code decides both, which is the only way a
backtest says anything about the sheet Armaan actually receives.

Order of work, and why:

1. **Exits first.** A held position that has hit its stop or lost its signal is
   sold before anything new is considered. Sale proceeds are NOT counted as
   spendable for same-day buys: DECA prices both at the same close and the
   order it processes them in is unpublished, so a buy funded by a same-day
   sale could land on margin.
2. **Compliance legs next.** The $10,000 mutual fund and bond minimums are a
   disqualification if missed, so their cash is set aside before any
   discretionary buy.
3. **New buys last**, in score order, each passing eligibility, the entry
   filters, earnings, cooldown, exposure group, sector cap, aggregate risk,
   position count and minimum order size before it is sized.

Output is numbers, dates, and the names of the constraints that bound them.
Never a view on a company.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from investlab import calendar as cal
from investlab.competitions.deca import ClassifiedFill, DecaProfile
from investlab.config import DecaConfig
from investlab.contracts import (
    AccountState,
    Action,
    AssetClass,
    Bar,
    Fill,
    Instrument,
    RuleCheck,
    RuleStatus,
    SizedOrder,
)
from investlab.data.universe import FIXED_INCOME, Universe
from investlab.earnings import EarningsCalendar, EarningsEvent
from investlab.money import usd, usd_ceil
from investlab.performance import EquityPoint
from investlab.portfolio.exits import ExitEvaluation, entry_stop_at, evaluate_exit
from investlab.portfolio.risk import DrawdownMonitor, DrawdownStage, RiskLimits
from investlab.portfolio.sizing import size_order
from investlab.screen import FeatureFrame, Screened, rank_features

SLEEVE_CLASSES = (AssetClass.STOCK, AssetClass.ETF)
COMPLIANCE_CLASSES = (AssetClass.MUTUAL_FUND, AssetClass.BOND)
MISSING_STOP_STRESS = Decimal("0.20")

# Sessions that close early. DECA prices at the close, so the order cutoff moves.
EARLY_CLOSES = {date(2026, 11, 27): "1:00 p.m. ET (10:00 a.m. Pacific)"}


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SellOrder:
    symbol: str
    asset_class: str
    quantity: int
    reason: str
    reference_close: Decimal
    estimated_proceeds: Decimal
    estimated_commission: Decimal
    estimated_sec_fee: Decimal
    estimated_realized: Decimal
    active_stop: Decimal | None
    detail: str


@dataclass(frozen=True, slots=True)
class BuyOrder:
    symbol: str
    asset_class: str
    sector: str
    quantity: int
    reference_close: Decimal
    estimated_cost: Decimal
    stop: Decimal
    planned_risk: Decimal
    gap_stress_loss: Decimal
    binding_constraint: str
    score: float
    momentum_21d_pct: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComplianceOrder:
    bucket: str
    symbol: str | None
    quantity: int | None
    reference_price: Decimal | None
    estimated_cost: Decimal
    target_net_cost: Decimal
    instructions: str
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HeldReview:
    symbol: str
    asset_class: str
    sector: str
    quantity: int
    avg_cost: Decimal
    last_close: Decimal | None
    close_session: date | None
    market_value: Decimal | None
    unrealized: Decimal | None
    return_pct: Decimal | None
    entry_stop: Decimal | None
    trailing_stop: Decimal | None
    active_stop: Decimal | None
    distance_to_stop_pct: Decimal | None
    sessions_held: int
    score: float | None
    next_earnings: str | None
    verdict: str
    detail: str
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Blocked:
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class OrderSheet:
    profile: str
    as_of: date
    fill_session: date
    signal_session: date | None
    data_is_stale: bool
    equity: Decimal
    cash: Decimal
    drawdown_stage: str
    rule_checks: tuple[RuleCheck, ...]
    alerts: tuple[str, ...]
    sells: tuple[SellOrder, ...]
    compliance: tuple[ComplianceOrder, ...]
    buys: tuple[BuyOrder, ...]
    held: tuple[HeldReview, ...]
    blocked: tuple[Blocked, ...]
    notes: tuple[str, ...]

    @property
    def has_actions(self) -> bool:
        return bool(self.sells or self.compliance or self.buys)

    def stop_for(self, symbol: str) -> Decimal | None:
        return next((b.stop for b in self.buys if b.symbol == symbol), None)

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(v) for v in value]
            if hasattr(value, "value") and not isinstance(value, (str, int, float)):
                return value.value
            return value

        return convert(asdict(self))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SheetInputs:
    as_of: date
    account: AccountState
    features: dict[str, FeatureFrame]
    universe: Universe
    profile: DecaProfile
    cfg: DecaConfig
    starting_capital: Decimal
    entry_stops: dict[str, Decimal | None] = field(default_factory=dict)
    earnings: EarningsCalendar | None = None
    trades: list[dict[str, str]] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    risk_reviews: list[tuple[date, str]] = field(default_factory=list)
    staleness_days: int = 4
    evaluate_rules: bool = True
    check_earnings: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar_from_features(ff: FeatureFrame, n: int) -> Bar | None:
    if n <= 0:
        return None
    row = ff.frame.iloc[n - 1]
    close = Decimal(str(round(float(row["close"]), 6)))
    high = max(Decimal(str(round(float(row["high"]), 6))), close)
    low = min(Decimal(str(round(float(row["low"]), 6))), close)
    return Bar(ff.symbol, ff.sessions[n - 1], close, high, low, close, close, 0, "features")


def sell_history_from_trades(
    trades: list[dict[str, str]], universe: Universe | None = None
) -> tuple[ClassifiedFill, ...]:
    out: list[ClassifiedFill] = []
    for row in trades:
        if row.get("action") != "sell":
            continue
        klass = AssetClass(row.get("asset_class") or "stock")
        fill = Fill(
            symbol=row["symbol"],
            action=Action.SELL,
            quantity=int(row["quantity"]),
            price=Decimal(row["price"]),
            commission=Decimal(row.get("commission") or "0"),
            fees=Decimal(row.get("fees") or "0"),
            session=date.fromisoformat(row["session"]),
        )
        out.append(ClassifiedFill(fill=fill, asset_class=klass))
    return tuple(out)


def _group(inst: Instrument | None, symbol: str) -> str:
    return (inst.exposure_group if inst and inst.exposure_group else None) or symbol


def _instrument(universe: Universe, symbol: str) -> Instrument | None:
    try:
        return universe.get(symbol)
    except KeyError:
        return None


def _sessions_after(start: date, end: date) -> int:
    """Sessions strictly after `start` up to and including `end`."""
    if end <= start:
        return 0
    return len(cal.sessions_between(cal.next_session(start), end))


def drawdown_state(
    starting_capital: Decimal,
    curve: list[EquityPoint],
    equity_now: Decimal,
    as_of: date,
    reviews: list[tuple[date, str]],
    cfg: DecaConfig,
):
    """Replay the recorded equity curve through the two-stage drawdown policy.

    A halt lifts only on a review Armaan recorded himself on or after the halt
    session. Nothing here writes or defaults that note. A review re-bases the
    high-water mark to equity on that session: without it the same drawdown
    would re-trigger the halt on the very next observation, and a review could
    never lift anything. A further 5% or 10% fall from the new base triggers
    again.
    """
    limits = RiskLimits(reduce_at=cfg.risk.drawdown_derisk, halt_at=cfg.risk.drawdown_halt)
    monitor = DrawdownMonitor(starting_capital, limits=limits)
    points = [(p.session, p.equity) for p in curve if p.session < as_of]
    points.append((as_of, equity_now))
    ordered_reviews = sorted(reviews)
    halted_on: date | None = None
    for session, equity in points:
        state = monitor.observe(equity, session)
        if state.stage is DrawdownStage.HALTED:
            if halted_on is None:
                halted_on = session
            usable = [r for r in ordered_reviews if halted_on <= r[0] <= session]
            if usable:
                monitor = DrawdownMonitor(equity, limits=limits)
                monitor.observe(equity, session)
                halted_on = None
        else:
            halted_on = None
    return monitor.state


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


def build_sheet(inp: SheetInputs) -> OrderSheet:
    cfg, strat, profile, account, universe = (
        inp.cfg,
        inp.cfg.strategy,
        inp.profile,
        inp.account,
        inp.universe,
    )
    as_of = inp.as_of
    alerts: list[str] = []
    notes: list[str] = []
    blocked: list[Blocked] = []

    # -- data freshness and which close the orders fill at ------------------
    newest = max(
        (
            ff.sessions[ff.count_upto(as_of) - 1]
            for ff in inp.features.values()
            if ff.count_upto(as_of)
        ),
        default=None,
    )
    stale = newest is None or (as_of - newest).days > inp.staleness_days
    if cal.is_session(as_of) and (newest is None or newest < as_of):
        fill_session = as_of
    else:
        fill_session = cal.next_session(as_of)
    signal_session = newest
    if stale:
        alerts.append(
            f"STALE DATA: newest bar is {newest}. Re-run `investlab data pull`; treat every "
            "number below as indicative only."
        )
    if fill_session in EARLY_CLOSES:
        alerts.append(
            f"EARLY CLOSE on {fill_session}: the market closes at {EARLY_CLOSES[fill_session]}. "
            "Enter orders before then."
        )

    # -- rules ----------------------------------------------------------------
    sell_history = sell_history_from_trades(inp.trades)
    checks: tuple[RuleCheck, ...] = ()
    if inp.evaluate_rules:
        checks = tuple(profile.check_rules(account, as_of, sell_history))

    equity = account.equity
    minimum = cfg.diversification_minimum

    # -- screen the momentum sleeve -----------------------------------------
    sleeve = {
        s: ff
        for s, ff in inp.features.items()
        if (inst := _instrument(universe, s)) is not None
        and inst.asset_class in SLEEVE_CLASSES
        and inst.sector != FIXED_INCOME
    }
    instruments = {s: universe.get(s) for s in sleeve}
    ranked, skipped = rank_features(sleeve, instruments, as_of)
    by_symbol: dict[str, Screened] = {c.symbol: c for c in ranked}
    if skipped:
        notes.append(f"{len(skipped)} sleeve symbol(s) not ranked (short history or warm-up).")

    # -- held positions: exits and review -----------------------------------
    sells: list[SellOrder] = []
    held_reviews: list[HeldReview] = []
    open_risk = Decimal("0")
    sector_value: dict[str, Decimal] = {}
    remaining_equity_positions: list[str] = []
    sold_net_cost_by_bucket: dict[str, Decimal] = {}
    held_groups: set[str] = set()

    warn_through = cal.add_business_days(fill_session, strat.earnings_warn_sessions)
    for pos in sorted(account.positions, key=lambda p: p.symbol):
        sym = pos.symbol
        inst = _instrument(universe, sym)
        sector = inst.sector if inst else ""
        qty = pos.quantity
        avg_cost = (pos.net_cost / Decimal(qty)).quantize(Decimal("0.0001")) if qty else Decimal(0)
        mark = account.marks.get(sym)
        mv = usd(mark * Decimal(qty)) if mark is not None else None
        unreal = usd(mv - pos.net_cost) if mv is not None else None
        ret = (
            (unreal / pos.net_cost * Decimal(100)).quantize(Decimal("0.01"))
            if (unreal is not None and pos.net_cost)
            else None
        )
        ff = inp.features.get(sym)
        n = ff.count_upto(as_of) if ff else 0
        close_session = ff.sessions[n - 1] if n else None

        next_e: EarningsEvent | None = None
        flags: list[str] = []
        if inp.check_earnings and inp.earnings is not None and pos.asset_class is AssetClass.STOCK:
            after = signal_session or as_of
            next_e = inp.earnings.next_event(sym, after)
            soon = inp.earnings.event_in_window(sym, after, warn_through)
            if soon is not None:
                flags.append(
                    f"earnings {soon.label()} lands within {strat.earnings_warn_sessions} "
                    "sessions: overnight gap risk no stop prevents"
                )
            elif next_e is None:
                flags.append("no upcoming earnings date on file: look it up on Finviz")

        if pos.asset_class in COMPLIANCE_CLASSES:
            held_reviews.append(
                HeldReview(
                    sym,
                    pos.asset_class.value,
                    sector,
                    qty,
                    avg_cost,
                    mark,
                    close_session,
                    mv,
                    unreal,
                    ret,
                    None,
                    None,
                    None,
                    None,
                    0,
                    None,
                    next_e.label() if next_e else None,
                    "HOLD (compliance)",
                    "compliance sleeve: never sold by the exit rules",
                    tuple(flags),
                )
            )
            continue

        opened = min(lot.opened for lot in pos.lots)
        entry_stop = inp.entry_stops.get(sym)
        if entry_stop is None and ff is not None:
            first_lot = min(pos.lots, key=lambda lot: lot.opened)
            entry_stop = entry_stop_at(ff, opened, first_lot.price, strat.entry_atr_multiple)
            if entry_stop is not None:
                flags.append(
                    f"no entry stop recorded; using fill price minus "
                    f"{strat.entry_atr_multiple}xATR = ${entry_stop:,.2f}. Record it with "
                    "`investlab stop set`"
                )
        scr = by_symbol.get(sym)
        ev: ExitEvaluation = evaluate_exit(
            sym,
            ff,
            opened=opened,
            as_of=as_of,
            entry_stop=entry_stop,
            score=scr.score if scr else None,
            above_ema50=scr.above_ema50 if scr else None,
            trail_atr_multiple=strat.trail_atr_multiple,
            exit_score_below=strat.exit_score_below,
            min_hold_sessions=strat.exit_decay_min_hold_sessions,
        )
        if inst is not None and inst.is_spot_bitcoin_etf and not profile.bitcoin_etf_written_source:
            flags.append("held under the team bitcoin-ETF ruling; no written DECA confirmation")

        verdict = "SELL" if ev.should_sell else "HOLD"
        held_reviews.append(
            HeldReview(
                sym,
                pos.asset_class.value,
                sector,
                qty,
                avg_cost,
                ev.last_close or mark,
                ev.close_session,
                mv,
                unreal,
                ret,
                ev.entry_stop,
                ev.trailing_stop,
                ev.active_stop,
                ev.distance_to_stop_pct,
                ev.sessions_held,
                scr.score if scr else None,
                next_e.label() if next_e else None,
                verdict,
                ev.detail,
                tuple(flags),
            )
        )

        if ev.should_sell and ev.last_close is not None:
            fee = usd_ceil(profile.sec_fee_rate * Decimal(qty) * ev.last_close)
            commission = profile.commission_per_trade
            proceeds = usd(ev.last_close * Decimal(qty)) - commission - fee
            entry_commissions = sum((lot.commission for lot in pos.lots), Decimal("0"))
            sells.append(
                SellOrder(
                    symbol=sym,
                    asset_class=pos.asset_class.value,
                    quantity=qty,
                    reason=ev.reason.value if ev.reason else "",
                    reference_close=ev.last_close,
                    estimated_proceeds=proceeds,
                    estimated_commission=commission,
                    estimated_sec_fee=fee,
                    estimated_realized=usd(proceeds - pos.net_cost - entry_commissions),
                    active_stop=ev.active_stop,
                    detail=ev.detail,
                )
            )
            bucket = "stocks"
            sold_net_cost_by_bucket[bucket] = sold_net_cost_by_bucket.get(bucket, 0) + pos.net_cost
        else:
            remaining_equity_positions.append(sym)
            held_groups.add(_group(inst, sym))
            price = ev.last_close or mark or Decimal(0)
            if ev.active_stop is not None:
                open_risk += Decimal(qty) * max(Decimal(0), price - ev.active_stop)
            else:
                open_risk += Decimal(qty) * price * MISSING_STOP_STRESS
            if price:
                sector_value[sector] = sector_value.get(sector, Decimal(0)) + price * Decimal(qty)

    if sells:
        names = ", ".join(f"{s.symbol} ({s.reason.replace('_', ' ')})" for s in sells)
        alerts.insert(0, f"SELL: {names}. Sale proceeds are not counted toward today's buys.")

    # -- DECA diversification after proposed sales ---------------------------
    stock_net = sum(
        (p.net_cost for p in account.positions if p.asset_class in SLEEVE_CLASSES), Decimal("0")
    )
    sold_stock = sold_net_cost_by_bucket.get("stocks", Decimal("0"))
    if sells and stock_net >= minimum and stock_net - sold_stock < minimum:
        alerts.append(
            f"These sales take the stocks class to ${stock_net - sold_stock:,.2f} net cost, "
            f"under the ${minimum:,.0f} minimum. DECA requires restoring it within one "
            f"business day of the sale ({cal.next_session(fill_session)})."
        )

    # -- compliance legs ----------------------------------------------------
    cash = account.cash
    available = cash
    compliance: list[ComplianceOrder] = []
    target = usd(minimum * (Decimal(1) + strat.compliance_buffer_fraction))
    deadline = cfg.diversification_deadline
    days_left = (deadline - as_of).days

    mf_have = sum(
        (p.net_cost for p in account.positions if p.asset_class is AssetClass.MUTUAL_FUND),
        Decimal("0"),
    )
    if mf_have < minimum:
        need = max(Decimal(0), target - mf_have)
        options: list[tuple[str, int, Decimal, Decimal]] = []
        for fund in strat.compliance_mutual_funds:
            ff = inp.features.get(fund)
            n = ff.count_upto(as_of) if ff else 0
            if not n:
                continue
            price = Decimal(str(round(float(ff.frame["close"].iloc[n - 1]), 2)))
            if price <= 0:
                continue
            q = max(math.ceil(need / price), cfg.min_shares_per_buy)
            options.append((fund, q, price, usd(price * Decimal(q)) + profile.commission_per_trade))
        if options:
            fund, q, price, cost = options[0]
            alternatives = tuple(
                f"{f} {qq} shares @ ${pp:,.2f} = ${cc:,.2f}" for f, qq, pp, cc in options[1:]
            )
            compliance.append(
                ComplianceOrder(
                    bucket="mutual_funds",
                    symbol=fund,
                    quantity=q,
                    reference_price=price,
                    estimated_cost=cost,
                    target_net_cost=target,
                    instructions=(
                        f"Buy {q} shares of {fund} (S&P 500 index fund) to reach at least "
                        f"${target:,.2f} net cost, {strat.compliance_buffer_fraction * 100:.0f}% "
                        f"over the ${minimum:,.0f} minimum. If SMG does not list {fund}, use the "
                        "first alternative it does list."
                    ),
                    alternatives=alternatives,
                )
            )
            available -= cost
            if cost > cash:
                alerts.append(
                    f"Not enough cash for the mutual fund leg: it needs ${cost:,.2f}, cash is "
                    f"${cash:,.2f}. A sale has to fund it before {deadline}."
                )
        else:
            available -= need + profile.commission_per_trade
            notes.append("No cached mutual fund prices; mutual fund leg reserved but not sized.")

    bond_have = sum(
        (p.net_cost for p in account.positions if p.asset_class is AssetClass.BOND), Decimal("0")
    )
    if bond_have < minimum:
        need = max(Decimal(0), target - bond_have)
        # Corporate and municipal bonds trade in $1,000 face, so the set-aside is
        # the face needed at par, not the bare target: 10.2 bonds is 11 bonds.
        corp_face = int(math.ceil(float(need) / 1000.0)) * 1000
        face_at_98 = int(math.ceil(float(need) / 0.98 / 1000.0)) * 1000
        bond_cost = Decimal(corp_face) + profile.commission_per_trade
        spare = available - profile.commission_per_trade
        max_price = (spare / Decimal(corp_face) * 100).quantize(Decimal("0.01"))
        afford = (
            f"With the orders above, cash covers ${corp_face:,} face at a price up to "
            f"{max_price:.2f} per 100."
            if spare > 0
            else "Cash does not cover the bond leg after the orders above; a sale must fund it."
        )
        compliance.append(
            ComplianceOrder(
                bucket="bonds",
                symbol=None,
                quantity=None,
                reference_price=None,
                estimated_cost=bond_cost,
                target_net_cost=target,
                instructions=(
                    "Pick a bond from SMG's own bond list rated BBB or better (a bond ETF does "
                    "NOT count). Corporate and municipal bonds trade in $1,000 face, Treasuries "
                    f"in $100. Face needed = ${need:,.2f} / (price / 100), rounded UP to the "
                    f"increment: at 100.00 that is ${corp_face:,} face; at 98.00, "
                    f"${face_at_98:,}. {afford} Record it with "
                    "`investlab fill --asset-class bond` using the platform's net cost."
                ),
            )
        )
        available -= bond_cost

    for c in compliance:
        label = "mutual funds" if c.bucket == "mutual_funds" else "bonds"
        when = (
            f"{days_left} days left, due {deadline} 4:00 p.m. ET"
            if days_left >= 0
            else f"PAST the {deadline} deadline"
        )
        alerts.append(f"DECA {label} leg short of ${minimum:,.0f} net cost ({when}).")

    floor = usd(cfg.risk.cash_floor_fraction * equity)
    available = max(Decimal(0), available - floor)
    notes.append(
        f"Cash ${cash:,.2f}; after compliance set-asides and a ${floor:,.2f} cash floor, "
        f"${available:,.2f} is available for new buys."
    )

    # -- drawdown policy ----------------------------------------------------
    dd = drawdown_state(
        inp.starting_capital, inp.equity_curve, equity, as_of, inp.risk_reviews, cfg
    )
    if dd.stage is DrawdownStage.HALTED:
        alerts.append(
            f"DRAWDOWN HALT: equity is {dd.drawdown_fraction * 100:.1f}% below its high of "
            f"${dd.high_water_mark:,.2f}. No new buys until Armaan records a review with "
            "`investlab risk-review`. Exits still apply."
        )
    elif dd.stage is DrawdownStage.REDUCED:
        alerts.append(
            f"Drawdown {dd.drawdown_fraction * 100:.1f}% from the high: new-trade risk is halved."
        )

    # -- new buys -----------------------------------------------------------
    buys: list[BuyOrder] = []
    constraints = profile.sizing_constraints(account)
    constraints = replace(
        constraints,
        risk_fraction=cfg.risk.risk_fraction_per_trade * dd.risk_multiplier,
        position_ceiling_fraction=min(cfg.position_ceiling_fraction, strat.max_position_weight),
    )
    slots = cfg.risk.max_positions - len(remaining_equity_positions)
    risk_cap = cfg.risk.aggregate_open_risk_fraction * equity
    sold_symbols = {s.symbol for s in sells}
    held_symbols = {p.symbol for p in account.positions}

    recent_sales: dict[str, date] = {}
    for fill in sell_history:
        inst = _instrument(universe, fill.fill.symbol)
        key = _group(inst, fill.fill.symbol)
        recent_sales[key] = max(recent_sales.get(key, fill.fill.session), fill.fill.session)
    for s in sells:
        recent_sales[_group(_instrument(universe, s.symbol), s.symbol)] = fill_session

    block_through = cal.add_business_days(fill_session, strat.earnings_block_sessions)
    ruling_conflicting = any(
        c.name == "bitcoin_etf_ruling" and c.status is RuleStatus.CONFLICTING for c in checks
    ) or (profile.bitcoin_etfs_allowed and not profile.bitcoin_etf_written_source)

    candidates = ranked
    if available < strat.min_order_notional:
        candidates = []
        notes.append(
            f"Less than ${strat.min_order_notional:,.0f} is free after set-asides, so no new "
            "buys were sized."
        )

    for cand in candidates:
        if len(buys) >= min(slots, strat.max_candidates):
            break
        sym = cand.symbol
        if sym in held_symbols or sym in sold_symbols:
            continue
        inst = cand.instrument
        if cand.score < strat.entry_min_score:
            break
        if not dd.entries_allowed:
            blocked.append(Blocked(sym, "drawdown halt: no new entries until a recorded review"))
            break
        group = _group(inst, sym)
        if group in held_groups or group in {
            _group(_instrument(universe, b.symbol), b.symbol) for b in buys
        }:
            blocked.append(Blocked(sym, f"same exposure as a position already held ({group})"))
            continue
        last_sale = recent_sales.get(group)
        if (
            last_sale is not None
            and _sessions_after(last_sale, fill_session) <= strat.reentry_cooldown_sessions
        ):
            blocked.append(
                Blocked(
                    sym,
                    f"sold on {last_sale}; re-entry cooldown is "
                    f"{strat.reentry_cooldown_sessions} sessions",
                )
            )
            continue
        if strat.entry_requires_above_ema50 and not cand.above_ema50:
            blocked.append(Blocked(sym, "close is below EMA50; entries need an uptrend"))
            continue

        ff = inp.features[sym]
        n = ff.count_upto(as_of)
        ok, why = profile.is_eligible(
            inst, _bar_from_features(ff, n), _bar_from_features(ff, n - 1)
        )
        if not ok:
            blocked.append(Blocked(sym, why))
            continue

        flags: list[str] = []
        if inp.check_earnings and inst.asset_class is AssetClass.STOCK:
            if inp.earnings is None:
                flags.append("earnings calendar not loaded: check Finviz before entering")
            else:
                hit = inp.earnings.event_in_window(sym, signal_session or as_of, block_through)
                if hit is not None:
                    blocked.append(
                        Blocked(
                            sym,
                            f"earnings {hit.label()} falls between the {signal_session} signal "
                            f"close and {strat.earnings_block_sessions} sessions after the fill",
                        )
                    )
                    continue
                if inp.earnings.next_event(sym, signal_session or as_of) is None:
                    flags.append("no upcoming earnings date on file: check Finviz before entering")
        if inst.is_spot_bitcoin_etf and ruling_conflicting:
            flags.append(
                "eligible only by the team bitcoin-ETF ruling; the published DECA text bans "
                "bitcoin and no written confirmation is on file"
            )
        if stale:
            flags.append("stale data")

        stop = cand.protective_reference_at(strat.entry_atr_multiple).quantize(Decimal("0.01"))
        sized = size_order(sym, cand.close, stop, replace(constraints, spendable_cash=available))
        binding_override = None
        if isinstance(sized, SizedOrder):
            headroom = strat.max_sector_weight * equity - sector_value.get(inst.sector, Decimal(0))
            if sized.estimated_notional > headroom:
                sized = size_order(
                    sym,
                    cand.close,
                    stop,
                    replace(
                        constraints,
                        spendable_cash=min(
                            available, max(Decimal(0), headroom) + profile.commission_per_trade
                        ),
                    ),
                )
                binding_override = "sector_cap"
        if not isinstance(sized, SizedOrder):
            cap_pct = strat.max_sector_weight * 100
            why = (
                sized.detail
                if binding_override is None
                else f"{inst.sector} would exceed the {cap_pct:.0f}% sector cap"
            )
            blocked.append(Blocked(sym, why))
            continue
        if sized.estimated_notional < strat.min_order_notional:
            limited_by = binding_override or sized.binding_constraint
            blocked.append(
                Blocked(
                    sym,
                    f"order would be ${sized.estimated_notional:,.2f}, under the "
                    f"${strat.min_order_notional:,.0f} minimum (limited by {limited_by})",
                )
            )
            continue
        if open_risk + sized.planned_risk > risk_cap:
            blocked.append(
                Blocked(
                    sym,
                    f"open risk ${open_risk:,.2f} plus ${sized.planned_risk:,.2f} would exceed "
                    f"the {cfg.risk.aggregate_open_risk_fraction * 100:.0f}% aggregate cap "
                    f"(${risk_cap:,.2f})",
                )
            )
            continue

        cost = sized.estimated_notional + sized.estimated_commission
        buys.append(
            BuyOrder(
                symbol=sym,
                asset_class=inst.asset_class.value,
                sector=inst.sector,
                quantity=sized.quantity,
                reference_close=cand.close,
                estimated_cost=cost,
                stop=stop,
                planned_risk=sized.planned_risk,
                gap_stress_loss=sized.gap_stress_loss,
                binding_constraint=binding_override or sized.binding_constraint,
                score=cand.score,
                momentum_21d_pct=round(cand.momentum_21d * 100, 2),
                flags=tuple(flags),
            )
        )
        available -= cost
        open_risk += sized.planned_risk
        sector_value[inst.sector] = (
            sector_value.get(inst.sector, Decimal(0)) + sized.estimated_notional
        )

    if slots <= 0:
        notes.append(f"Position count is at the {cfg.risk.max_positions}-position maximum.")

    return OrderSheet(
        profile=profile.name,
        as_of=as_of,
        fill_session=fill_session,
        signal_session=signal_session,
        data_is_stale=stale,
        equity=usd(equity),
        cash=usd(cash),
        drawdown_stage=dd.stage.value,
        rule_checks=checks,
        alerts=tuple(alerts),
        sells=tuple(sells),
        compliance=tuple(compliance),
        buys=tuple(buys),
        held=tuple(held_reviews),
        blocked=tuple(blocked),
        notes=tuple(notes),
    )
