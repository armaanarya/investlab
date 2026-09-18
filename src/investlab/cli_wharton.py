"""`investlab wharton ...`: the WInS allocation plan and Laura Gao's projections.

Every number printed here is evidence for the team to evaluate. Nothing here
writes a trading note, the IPS, or any part of the Final Report.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from investlab import config as cfg_mod
from investlab import wharton_client as wc
from investlab.competitions import wharton as w
from investlab.contracts import AssetClass, Bar, Instrument
from investlab.data.cache import ParquetCache, SymbolNotCachedError

wharton_app = typer.Typer(help="Wharton WInS: allocation plan, client cash flows, projections.")
console = Console()

PROJECTIONS_DIR = cfg_mod.REPO_ROOT / "research" / "wharton" / "projections"


def _book(path: Path | None = None) -> wc.StrategyBook:
    try:
        return wc.load_strategy_book(path or wc.DEFAULT_STRATEGY_PATH)
    except wc.StrategyConfigError as exc:
        console.print(f"[red]configs/wharton_strategy.json:[/red] {exc}")
        raise typer.Exit(2) from exc


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _placeholder_banner(book: wc.StrategyBook) -> None:
    if book.placeholder_sleeves():
        console.print(
            Panel(
                "Return and volatility assumptions for "
                f"{', '.join(book.placeholder_sleeves())} are placeholders. Replace them "
                "with figures from a source you cite before any number here goes in a "
                "deliverable.",
                title="Placeholder assumptions",
                border_style="yellow",
            )
        )


# ---------------------------------------------------------------------------
# cashflows
# ---------------------------------------------------------------------------


@wharton_app.command()
def cashflows() -> None:
    """Laura's cash flows from the case study, and what the reserve costs."""
    t = Table(title="Laura Gao: case-study cash flows (start of each year)")
    t.add_column("Year")
    t.add_column("Case year #", justify="right")
    t.add_column("Flow", justify="right")
    t.add_column("What")
    for y, amt in wc.CONTRIBUTIONS:
        t.add_row(str(y), str(wc.year_number(y)), f"+{_money(amt)}", "contribution")
    t.add_row(
        str(wc.COSPONSOR_YEAR),
        str(wc.year_number(wc.COSPONSOR_YEAR)),
        "-",
        "quote co-sponsors a range",
    )
    t.add_row(
        str(wc.RESERVE_YEAR),
        str(wc.year_number(wc.RESERVE_YEAR)),
        "-",
        "set aside operating reserve",
    )
    for y in range(wc.FIRST_PAYMENT_YEAR, wc.LAST_PAYMENT_YEAR + 1):
        t.add_row(str(y), str(wc.year_number(y)), f"-{_money(wc.PAYMENT)}", "operating payment")
    console.print(t)

    r = Table(title="Reserve needed at start of 2033 if it earns a fixed rate")
    r.add_column("Rate", justify="right")
    r.add_column("Reserve", justify="right")
    r.add_column("Same, in 2027 dollars at that rate", justify="right")
    for rate in (0.0, 0.02, 0.03, 0.035, 0.04, 0.045, 0.05):
        pv = wc.reserve_pv(rate)
        r.add_row(f"{rate:.1%}", _money(pv), _money(pv / (1 + rate) ** 6))
    console.print(r)
    console.print(
        "[dim]$450,000 goes in. At 4% the reserve alone is worth about $334,000 in "
        "2027 dollars, so the facility contribution comes from what the rest earns.[/dim]"
    )


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


def _projection_table(book: wc.StrategyBook, p: wc.Projection) -> None:
    strat = book.strategies[p.strategy]
    t = Table(title=f"{p.strategy}  ({p.paths:,} paths, seed {p.seed})", show_header=True)
    t.add_column("Measure")
    t.add_column("P10", justify="right")
    t.add_column("P25", justify="right")
    t.add_column("P50", justify="right")
    t.add_column("P75", justify="right")
    t.add_column("P90", justify="right")
    qs = (0.10, 0.25, 0.50, 0.75, 0.90)
    t.add_row("Portfolio, start 2031", *[_money(p.pct(p.value_2031, q)) for q in qs])
    t.add_row("Portfolio, start 2033", *[_money(p.pct(p.value_2033, q)) for q in qs])
    t.add_row("Facility contribution", *[_money(p.pct(p.contribution, q)) for q in qs])
    t.add_row(
        "  in 2027 dollars",
        *[_money(p.real(p.pct(p.contribution, q))) for q in qs],
    )
    console.print(t)

    for first, last, mu, vol in p.phase_moments:
        console.print(f"  {first}-{last}: expected {mu:.2%} a year, volatility {vol:.2%}")
    console.print(f"  At expected returns, no volatility: {_money(p.deterministic_2033)} in 2033")
    pol = strat.reserve
    how = (
        f"present value at {pol.discount_rate:.2%}"
        if pol.method == "pv"
        else f"smallest reserve funding all payments in {pol.confidence:.0%} of reserve paths"
    )
    console.print(f"  Operating reserve: {_money(p.reserve)} ({how})")
    console.print(
        f"  All ten payments funded in [bold]{p.funding_probability:.1%}[/bold] of paths "
        "(portfolio reaches the reserve, and the reserve lasts)"
    )
    console.print(
        f"  Flexibility kept: {strat.buffer_fraction:.0%} of what is left after the reserve"
        + (f" plus {_money(strat.buffer_dollars)}" if strat.buffer_dollars else "")
    )

    r = Table(title="Candidate ranges to quote co-sponsors (from today's view)")
    r.add_column("Range")
    r.add_column("Low", justify="right")
    r.add_column("High", justify="right")
    r.add_column("Share of paths inside", justify="right")
    r.add_column("Share at or above low", justify="right")
    for lo, hi in ((0.10, 0.50), (0.20, 0.60), (0.25, 0.75), (0.10, 0.90)):
        low = p.pct(p.contribution, lo)
        r.add_row(
            f"P{lo * 100:.0f}-P{hi * 100:.0f}",
            _money(low),
            _money(p.pct(p.contribution, hi)),
            f"{p.range_mass(lo, hi):.0%}",
            f"{float((p.contribution >= low).mean()):.0%}",
        )
    console.print(r)

    c = Table(title="The 2031 view: if the portfolio is worth X at the start of 2031")
    c.add_column("Value, start 2031")
    c.add_column("Contribution if 2031-32 are poor (P10)", justify="right")
    c.add_column("median (P50)", justify="right")
    c.add_column("strong (P90)", justify="right")
    for q31 in (0.25, 0.50, 0.75):
        v31 = p.pct(p.value_2031, q31)
        c.add_row(
            f"{_money(v31)} (P{q31 * 100:.0f})",
            *[
                _money(
                    p.contribution_from_2031(v31, strat.buffer_fraction, strat.buffer_dollars, q)
                )
                for q in (0.10, 0.50, 0.90)
            ],
        )
    console.print(c)


@wharton_app.command()
def project(
    strategy: str = typer.Option("all", help="A strategy name from the config, or 'all'."),
    paths: int = typer.Option(20_000, help="Simulated paths."),
    seed: int = typer.Option(7, help="Random seed. Quote it with any number you cite."),
    save: bool = typer.Option(
        False, "--save", help="Write results to research/wharton/projections/."
    ),
) -> None:
    """Simulate each strategy to 2033: the reserve, the chance all ten payments
    are funded, and the range of facility contributions."""
    book = _book()
    _placeholder_banner(book)
    names = list(book.strategies) if strategy == "all" else [strategy]
    for n in names:
        if n not in book.strategies:
            console.print(
                f"[red]Unknown strategy[/red] {n!r}. Defined: {', '.join(book.strategies)}"
            )
            raise typer.Exit(2)

    results = []
    for n in names:
        p = wc.project(book, n, paths=paths, seed=seed)
        _projection_table(book, p)
        console.print()
        results.append(p)

    if len(results) > 1:
        s = Table(title="Side by side")
        s.add_column("Strategy")
        s.add_column("Reserve", justify="right")
        s.add_column("Payments funded", justify="right")
        s.add_column("Contribution P10", justify="right")
        s.add_column("P50", justify="right")
        s.add_column("P90", justify="right")
        for p in results:
            s.add_row(
                p.strategy + (" (active)" if p.strategy == book.active else ""),
                _money(p.reserve),
                f"{p.funding_probability:.1%}",
                *[_money(p.pct(p.contribution, q)) for q in (0.10, 0.50, 0.90)],
            )
        console.print(s)

    if save:
        from investlab.cli import today_et

        raw = wc.DEFAULT_STRATEGY_PATH.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()[:12]
        PROJECTIONS_DIR.mkdir(parents=True, exist_ok=True)
        out = PROJECTIONS_DIR / f"{today_et().isoformat()}-{strategy}.json"
        payload = {
            "generated": today_et().isoformat(),
            "config_sha256_12": digest,
            "paths": paths,
            "seed": seed,
            "placeholder_sleeves": book.placeholder_sleeves(),
            "results": [
                {
                    "strategy": p.strategy,
                    "reserve": round(p.reserve, 2),
                    "payments_funded_probability": round(p.funding_probability, 4),
                    "deterministic_2033": round(p.deterministic_2033, 2),
                    "percentiles": {
                        name: {
                            f"p{int(q * 100)}": round(p.pct(arr, q), 2)
                            for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
                        }
                        for name, arr in (
                            ("value_2031", p.value_2031),
                            ("value_2033", p.value_2033),
                            ("facility_contribution", p.contribution),
                        )
                    },
                }
                for p in results
            ],
        }
        out.write_text(json.dumps(payload, indent=2) + "\n")
        console.print(f"[green]Saved[/green] {out} (config {digest})")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def _last_bar(cache: ParquetCache, symbol: str, today) -> Bar | None:
    try:
        bars = cache.read(symbol, today - timedelta(days=10), today)
    except SymbolNotCachedError:
        return None
    return bars[-1] if bars else None


def wharton_symbols(book: wc.StrategyBook | None = None) -> list[str]:
    """Every ticker the WInS plan can hold, for `data pull`."""
    try:
        book = book or wc.load_strategy_book()
    except wc.StrategyConfigError:
        return []
    return sorted({t for sleeve in book.wins.get("holdings", {}).values() for t in sleeve})


@wharton_app.command()
def plan() -> None:
    """The WInS order plan: target sleeve weights from the active strategy,
    what the book holds, and whole-share orders for any sleeve outside its
    band. Reference prices are the last close; WInS fills at live prices."""
    from investlab.cli import _load_account, _store, today_et

    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    book = _book()
    today = today_et()
    strat = book.strategies[book.active]
    first_year = min(p.first_year for p in strat.phases)
    targets = strat.weights_for(first_year)
    wins = book.wins
    band = float(wins.get("rebalance_band", 0.05))
    holdings_map: dict[str, dict[str, float]] = wins.get("holdings", {})
    meta: dict[str, dict] = wins.get("instruments", {})

    store = _store("wharton")
    trades_used = len(store.trades()) if store.exists() else 0
    profile = w.WhartonProfile().with_trade_budget(w.TradeBudget(trades_used=trades_used))
    account = _load_account("wharton", cache, cfg)
    equity = account.equity
    window = w.trading_window(today)

    _placeholder_banner(book)
    status = {
        w.TradingWindow.NOT_OPEN: (
            f"PREVIEW: trading opens {w.TRADING_BEGINS}. Nothing below can be placed yet."
        ),
        w.TradingWindow.OPEN: f"Trading open until {w.TRADING_ENDS}, when the portfolio freezes.",
        w.TradingWindow.FROZEN: "Trading has ended. The portfolio is frozen.",
    }[window]
    console.print(
        Panel(
            f"Active strategy: [bold]{book.active}[/bold] (targets = its {first_year} phase)\n"
            f"Equity ${equity:,.2f}, cash ${account.cash:,.2f}\n"
            f"Trades used {trades_used} of {w.SELF_IMPOSED_TRADE_BUDGET} budget "
            f"({w.HARD_TRADE_CAP} cap)\n{status}",
            title="Wharton WInS plan",
            border_style="cyan",
        )
    )

    held_value: dict[str, Decimal] = {}
    for pos in account.positions:
        held_value[pos.symbol] = Decimal(pos.quantity) * account.marks.get(pos.symbol, Decimal(0))
    mapped = {t for s in holdings_map.values() for t in s}
    unmapped = sorted(set(held_value) - mapped)

    sleeves = Table(title="Sleeves: target vs held")
    sleeves.add_column("Sleeve")
    sleeves.add_column("Target", justify="right")
    sleeves.add_column("Held", justify="right")
    sleeves.add_column("Drift", justify="right")
    sleeves.add_column("Action")

    orders: list[tuple[str, str, int, Decimal, str]] = []  # action, symbol, qty, price, note
    missing_prices: list[str] = []
    eq = float(equity) if equity > 0 else 0.0
    for sleeve in sorted(set(targets) | set(holdings_map)):
        tgt = targets.get(sleeve, 0.0)
        tickers = holdings_map.get(sleeve, {})
        held = sum(float(held_value.get(t, 0)) for t in tickers)
        held_w = held / eq if eq else 0.0
        drift = held_w - tgt
        act = abs(drift) > band or (tgt > 0 and held == 0)
        sleeves.add_row(
            sleeve, f"{tgt:.1%}", f"{held_w:.1%}", f"{drift:+.1%}", "rebalance" if act else "hold"
        )
        if not act or not tickers:
            continue
        for sym, frac in tickers.items():
            b = _last_bar(cache, sym, today)
            if b is None:
                missing_prices.append(sym)
                continue
            want = Decimal(str(eq * tgt * frac))
            have = held_value.get(sym, Decimal(0))
            delta = want - have
            qty = int((abs(delta) / b.close).to_integral_value(rounding=ROUND_DOWN))
            if qty == 0:
                continue
            orders.append(("BUY" if delta > 0 else "SELL", sym, qty, b.close, ""))
    console.print(sleeves)
    if unmapped:
        console.print(
            f"[yellow]Held but not in any sleeve:[/yellow] {', '.join(unmapped)}. "
            "Add them to wins.holdings or they count as drift nowhere."
        )

    # Sells first, then buys within the cash they leave.
    orders.sort(key=lambda o: (o[0] != "SELL", -(o[2] * o[3])))
    cash = account.cash
    out = Table(title="Orders (reference = last close; WInS fills live)")
    out.add_column("Action")
    out.add_column("Symbol")
    out.add_column("Shares", justify="right")
    out.add_column("Ref price", justify="right")
    out.add_column("Est. value", justify="right")
    out.add_column("Check")
    blocked_any = False
    placed = 0
    for action, sym, qty, price, _ in orders:
        info = meta.get(sym, {})
        inst = Instrument(sym, info.get("name", sym), AssetClass.ETF, info.get("exchange", "WInS"))
        b = _last_bar(cache, sym, today)
        assert b is not None
        check = "ok"
        blocked = profile.evaluate(inst, b)
        if blocked is None and action == "BUY":
            afford = int(
                ((cash - w.STOCK_COMMISSION) / price).to_integral_value(rounding=ROUND_DOWN)
            )
            if afford < qty:
                qty = max(0, afford)
                check = "cut to cash"
        if blocked is None:
            blocked = profile.volume_block(sym, qty, b.volume)
        if blocked is not None:
            check = blocked.detail
            blocked_any = True
        elif qty <= 0:
            continue
        else:
            value = price * qty
            cash += (
                value - w.STOCK_COMMISSION if action == "SELL" else -(value + w.STOCK_COMMISSION)
            )
            placed += 1
        out.add_row(action, sym, f"{qty:,}", f"${price:,.2f}", f"${price * qty:,.2f}", check)
    if orders:
        console.print(out)
        console.print(
            f"{placed} trades, ${w.STOCK_COMMISSION * placed:,} commission; cash after about "
            f"${cash:,.2f}. Volume check uses the last session's volume "
            f"(WInS limit: 2x the current day's)."
        )
    else:
        console.print("[green]Every sleeve is inside its band. No orders.[/green]")
    if missing_prices:
        console.print(
            f"[yellow]No cached price for[/yellow] {', '.join(sorted(missing_prices))}. "
            "Run `investlab data pull`."
        )
    if blocked_any:
        console.print("[red]Some orders are blocked; see the Check column.[/red]")
    if wins.get("note"):
        console.print(f"[dim]{wins['note']}[/dim]")
    console.print(
        "[dim]Each trade needs a Trading Note in WInS, written by the team at the time. "
        f"The Trading Notes Analysis ({w.NOTES_ANALYSIS_DUE}) uses three of them.[/dim]"
    )
