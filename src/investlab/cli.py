"""Command line interface.

The tool's whole job is to print a sheet of orders you type into a competition
platform by hand, to tell you which rules currently bind, and to keep a ledger
that mirrors the platform. Neither DECA nor Wharton exposes an API, so nothing
here places a trade.

Every command reads from the local Parquet cache, never the network. `data
pull`, `data metadata` and `earnings pull` are the only commands that touch the
internet, which is what makes a run reproducible and what keeps the tool
working on a day Yahoo is broken.

The ledger (`ledger/deca/`) changes only through `fill`, `stop set`,
`snapshot`, `reconcile --save` and `risk-review`, and only from numbers Armaan
supplies off the platform. The order sheet never writes to it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from investlab import calendar as cal
from investlab import config as cfg_mod
from investlab.contracts import AccountState, AssetClass, Lot, Position
from investlab.data.cache import ParquetCache, SymbolNotCachedError
from investlab.data.universe import BIAS_WARNING, default_universe
from investlab.store import LedgerStore

app = typer.Typer(
    add_completion=False,
    help="Decision support for the DECA Stock Market Game and Wharton WInS.",
)
data_app = typer.Typer(help="Fetch and inspect the local price cache.")
app.add_typer(data_app, name="data")
journal_app = typer.Typer(help="Your own decision log. Required by both competitions.")
app.add_typer(journal_app, name="journal")
earnings_app = typer.Typer(help="Earnings dates that gate new buys and flag held positions.")
app.add_typer(earnings_app, name="earnings")
stop_app = typer.Typer(help="Entry stops recorded on held positions.")
app.add_typer(stop_app, name="stop")
from investlab.cli_wharton import wharton_app  # noqa: E402

app.add_typer(wharton_app, name="wharton")

console = Console()
err = Console(stderr=True)

# Exit codes. Never return 0 after swallowing a real failure.
EXIT_OK = 0
EXIT_BAD_CONFIG = 2
EXIT_NO_DATA = 3
EXIT_RULE_UNRESOLVED = 4
EXIT_VERIFICATION_FAILED = 5


EASTERN = ZoneInfo("America/New_York")
# S&P 500 trackers in preference order. Not a single ticker: yfinance returned
# zero price rows for SPY on 2026-09-08 while resolving its metadata fine, a
# Yahoo-side quirk that would otherwise have silently left the report with no
# benchmark. VOO and IVV track the same index.
BENCHMARK_CANDIDATES = ("SPY", "VOO", "IVV")


def today_et() -> date:
    """Today's date in US/Eastern, which is the only timezone the rules use.

    Both competitions state their deadlines in ET: DECA's diversification cut
    is 2026-10-23 at 4:00 p.m. ET and its orders price at the 4:00 p.m. ET
    close. Using the machine's local date instead would be wrong for anyone
    west of Eastern: at 10 p.m. Pacific on October 22 it is already October 23
    in New York, so a local-date tool would report a day of slack on a
    deadline that has already passed.
    """
    return datetime.now(EASTERN).date()


def now_et() -> datetime:
    return datetime.now(EASTERN)


def _store(profile: str) -> LedgerStore:
    """The per-competition ledger, tracked in git under `ledger/<profile>/`.

    DECA and Wharton are kept strictly apart: different capital, different
    rules, different asset-class definitions. A combined book would let a
    Wharton holding appear to satisfy a DECA requirement.
    """
    return LedgerStore(profile)


def _portfolio_path(profile: str) -> Path:
    return _store(profile).portfolio_path


def _money(text: str, what: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        console.print(f"[red]{what} must be a number,[/red] got {text!r}")
        raise typer.Exit(EXIT_BAD_CONFIG) from None


def _date(text: str, what: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        console.print(f"[red]{what} must be YYYY-MM-DD,[/red] got {text!r}")
        raise typer.Exit(EXIT_BAD_CONFIG) from None


def _build_provider_chain(cfg: cfg_mod.AppConfig) -> Any:
    """Build the provider chain named in config, in order.

    `chain()` with no arguments is an empty chain that silently returns no
    bars, so the providers are always constructed explicitly. An unavailable
    provider (Alpaca or Tiingo with no key) is skipped by the chain rather than
    failing the pull.
    """
    from investlab.data.providers import AlpacaProvider, TiingoProvider, YFinanceProvider, chain

    built = []
    for name in cfg.data.providers:
        if name == "yfinance":
            built.append(YFinanceProvider())
        elif name == "alpaca":
            built.append(AlpacaProvider())
        elif name == "tiingo":
            built.append(TiingoProvider())
        else:
            console.print(f"[yellow]Unknown provider in config, skipping:[/yellow] {name}")
    if not built:
        console.print("[red]No providers configured.[/red] Check config.data.providers.")
        raise typer.Exit(EXIT_BAD_CONFIG)
    return chain(*built)


def _load_profile(name: str, cfg: cfg_mod.AppConfig) -> Any:
    """Import a competition profile lazily so a half-built module cannot stop
    an unrelated command from running. DECA picks up the team's recorded
    rulings from `configs/deca_rulings.json`."""
    if name == "deca":
        from investlab.competitions.deca import DecaProfile

        try:
            rulings = cfg_mod.load_deca_rulings()
        except ValueError as exc:
            console.print(f"[red]configs/deca_rulings.json is invalid:[/red] {exc}")
            raise typer.Exit(EXIT_BAD_CONFIG) from exc
        return DecaProfile.from_rulings(rulings)
    if name == "wharton":
        from investlab.competitions.wharton import TradeBudget, WhartonProfile

        store = _store("wharton")
        used = len(store.trades()) if store.exists() else 0
        return WhartonProfile().with_trade_budget(TradeBudget(trades_used=used))
    console.print(f"[red]Unknown profile[/red] {name!r}. Use 'deca' or 'wharton'.")
    raise typer.Exit(EXIT_BAD_CONFIG)


def _load_account(profile: str, cache: ParquetCache, cfg: cfg_mod.AppConfig) -> AccountState:
    """Read the portfolio file and mark it to the most recent cached close.

    The competition platform is the authoritative record. This file is our
    mirror of it, and `reconcile` is what proves the two agree.
    """
    path = _portfolio_path(profile)
    if not path.exists():
        starting = cfg.deca.starting_cash if profile == "deca" else cfg.wharton.starting_cash
        err.print(
            Panel(
                f"No portfolio file at [cyan]{path}[/cyan].\n\n"
                f"Starting a fresh book with [bold]${starting:,}[/bold] cash.\n"
                "Run [cyan]investlab portfolio init[/cyan] to create it.",
                title="No portfolio yet",
                border_style="yellow",
            )
        )
        return AccountState(as_of=today_et(), cash=starting, positions=())

    raw = json.loads(path.read_text())
    positions: list[Position] = []
    marks: dict[str, Decimal] = {}
    missing: list[str] = []

    for p in raw.get("positions", []):
        lots = tuple(
            Lot(
                symbol=p["symbol"],
                quantity=int(lot["quantity"]),
                price=Decimal(str(lot["price"])),
                commission=Decimal(str(lot.get("commission", 0))),
                opened=date.fromisoformat(lot["opened"]),
            )
            for lot in p["lots"]
        )
        positions.append(
            Position(symbol=p["symbol"], asset_class=AssetClass(p["asset_class"]), lots=lots)
        )
        try:
            bars = cache.read(p["symbol"], today_et() - timedelta(days=10), today_et())
            marks[p["symbol"]] = bars[-1].close if bars else Decimal(str(p["lots"][0]["price"]))
        except SymbolNotCachedError:
            missing.append(p["symbol"])
            marks[p["symbol"]] = Decimal(str(p["lots"][0]["price"]))

    if missing:
        err.print(
            f"[yellow]Marked at cost (not cached):[/yellow] {', '.join(missing)}. "
            "Run 'investlab data pull' for current marks."
        )

    return AccountState(
        as_of=date.fromisoformat(raw["as_of"]) if "as_of" in raw else today_et(),
        cash=Decimal(str(raw.get("cash", 0))),
        positions=tuple(positions),
        marks=marks,
    )


def _sell_history(profile: str) -> tuple:
    from investlab.plan import sell_history_from_trades

    store = _store(profile)
    return sell_history_from_trades(store.trades()) if store.exists() else ()


def _check_rules(profile: str, prof: Any, account: AccountState, today: date) -> list:
    if profile == "deca":
        return prof.check_rules(account, today, _sell_history(profile))
    return prof.check_rules(account, today)


def _rule_lines(profile: str, prof: Any, account: AccountState) -> list[str]:
    return [
        f"{c.name}: {'ok' if c.satisfied else 'NOT MET'} ({c.status.value}) — {c.detail}"
        for c in _check_rules(profile, prof, account, today_et())
    ]


# ---------------------------------------------------------------------------
# environment and data
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Check the environment, the cache, and how fresh the data is."""
    import os

    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)

    t = Table(title="investlab doctor", show_header=True, header_style="bold")
    t.add_column("Check")
    t.add_column("Status")
    t.add_column("Detail")

    symbols = list(cache.symbols()) if cfg.data.cache_dir.exists() else []
    t.add_row(
        "Cache", "ok" if symbols else "empty", f"{len(symbols)} symbols in {cfg.data.cache_dir}"
    )

    stale_note = "no data"
    status = "n/a"
    if symbols:
        try:
            newest = max((cache.coverage(s)[1] for s in symbols if cache.coverage(s)), default=None)
            if newest:
                age = (today_et() - newest).days
                stale_note = f"newest bar {newest} ({age}d old)"
                status = "ok" if age <= cfg.data.staleness_days else "stale"
        except Exception as exc:  # noqa: BLE001 - doctor must never crash
            stale_note = f"unreadable: {exc}"
    t.add_row("Freshness", status, stale_note)

    alpaca = bool(os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"))
    t.add_row(
        "Providers",
        "ok",
        "yfinance; alpaca "
        + ("ready" if alpaca else "not configured (no APCA_API_KEY_ID)")
        + "; tiingo "
        + ("ready" if os.environ.get("TIINGO_API_KEY") else "not configured"),
    )

    t.add_row(
        "DECA",
        "ready",
        f"${cfg.deca.starting_cash:,} start, ${cfg.deca.commission_per_trade}/trade",
    )
    try:
        rulings = cfg_mod.load_deca_rulings()
        ruling = (
            "bitcoin ETFs allowed by team ruling"
            + (" (confirmed in writing)" if rulings.bitcoin_etf_written_source else " (UNVERIFIED)")
            if rulings.bitcoin_etfs_allowed
            else "bitcoin ETFs blocked (published ban)"
        )
    except ValueError as exc:
        ruling = f"invalid rulings file: {exc}"
    t.add_row("DECA rulings", "info", ruling)

    from investlab.earnings import EarningsCalendar

    upcoming = [e for e in EarningsCalendar().events() if e.date >= today_et()]
    t.add_row("Earnings calendar", "ok" if upcoming else "empty", f"{len(upcoming)} upcoming dates")

    w = cfg.wharton
    upcoming_w = [
        (d, label)
        for d, label in (
            (w.trading_start, "trading opens"),
            (w.roster_deadline, "team roster due"),
            (w.notes_analysis_deadline, "Trading Notes Analysis due"),
            (w.trading_end, "IPS due, trading ends"),
            (w.final_report_deadline, "Final Report due"),
        )
        if d >= today_et()
    ]
    t.add_row(
        "Wharton",
        "ready",
        f"${w.starting_cash:,} start"
        + (f"; next: {upcoming_w[0][1]} {upcoming_w[0][0]}" if upcoming_w else "; season over"),
    )

    days = (cfg.deca.diversification_deadline - today_et()).days
    t.add_row(
        "DECA diversification",
        "urgent" if days <= 14 else "pending",
        f"{days} days to {cfg.deca.diversification_deadline}",
    )
    names_days = (cfg.deca.student_names_deadline - today_et()).days
    if 0 <= names_days <= 7:
        t.add_row(
            "DECA student names", "urgent", f"due {cfg.deca.student_names_deadline} 4:00 p.m. ET"
        )
    console.print(t)
    console.print(f"[dim]{BIAS_WARNING}[/dim]")


@data_app.command("pull")
def data_pull(
    days: int = typer.Option(400, help="Calendar days of history to fetch."),
    symbols: str = typer.Option("", help="Comma-separated symbols. Default: the whole universe."),
) -> None:
    """Refresh the local price cache. Uses the network."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    universe = default_universe()

    wanted = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else list(universe.symbols())
    )
    # The benchmark is pulled but deliberately kept OUT of the universe, so the
    # screen can never propose buying it. DECA ranks on return against S&P 500
    # growth; holding the index is how you guarantee you match the thing you
    # need to beat.
    wanted += [b for b in BENCHMARK_CANDIDATES if b not in wanted]
    if not symbols:
        from investlab.cli_wharton import wharton_symbols

        wanted += [s for s in wharton_symbols() if s not in wanted]
    end = today_et()
    start = end - timedelta(days=days)

    console.print(f"Pulling [bold]{len(wanted)}[/bold] symbols, {start} to {end}...")
    provider = _build_provider_chain(cfg)
    bars = provider.fetch(wanted, start, end)
    if not bars:
        console.print("[red]No bars returned.[/red] Check your network and try again.")
        raise typer.Exit(EXIT_NO_DATA)

    cache.write(bars)
    got = {b.symbol for b in bars}
    sources = sorted({b.source for b in bars})
    console.print(
        f"[green]Cached {len(bars):,} bars across {len(got)} symbols[/green] "
        f"from {', '.join(sources)}."
    )
    if missed := sorted(set(wanted) - got):
        console.print(f"[yellow]No data for:[/yellow] {', '.join(missed)}")


@data_app.command("status")
def data_status() -> None:
    """Show what the cache holds."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    symbols = sorted(cache.symbols())
    if not symbols:
        console.print("Cache is empty. Run [cyan]investlab data pull[/cyan].")
        raise typer.Exit(EXIT_NO_DATA)

    t = Table(show_header=True, header_style="bold")
    t.add_column("Symbol")
    t.add_column("From")
    t.add_column("To")
    for s in symbols[:80]:
        cov = cache.coverage(s)
        t.add_row(s, str(cov[0]) if cov else "-", str(cov[1]) if cov else "-")
    console.print(t)
    if len(symbols) > 80:
        console.print(f"[dim]...and {len(symbols) - 80} more[/dim]")


_YF_EXCHANGES = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "PCX": "NYSE Arca",
    "BTS": "Cboe BZX",
    "ASE": "NYSE American",
}


@data_app.command("metadata")
def data_metadata(
    symbols: str = typer.Option("", help="Comma-separated symbols. Default: every stock and ETF."),
) -> None:
    """Check the universe's baked-in exchange and size against vendor metadata.

    DECA's eligibility gate trusts `data/universe.py`. A listing that moved to
    an ineligible venue, or a market cap under $25M, would be missed without
    this. Report only: the universe file is edited by hand, with a commit.
    """
    import yfinance as yf

    from investlab.competitions.deca import MIN_MARKET_CAP, _normalize_exchange

    universe = default_universe()
    wanted = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else [i.symbol for i in universe if i.asset_class in (AssetClass.STOCK, AssetClass.ETF)]
    )
    t = Table(title="Universe metadata check", header_style="bold")
    for col in ("Symbol", "Universe exchange", "Vendor exchange", "Size", "Status"):
        t.add_column(col)
    problems = 0
    for sym in wanted:
        inst = universe.get(sym)
        try:
            info = yf.Ticker(sym.replace(".", "-")).info
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            t.add_row(sym, inst.exchange, "?", "?", f"[yellow]lookup failed: {exc}[/yellow]")
            continue
        code = info.get("exchange") or ""
        vendor = _YF_EXCHANGES.get(code, code)
        size = info.get("marketCap") or info.get("totalAssets")
        status = "ok"
        if _normalize_exchange(vendor) != _normalize_exchange(inst.exchange):
            status = "[red]exchange differs[/red]"
            problems += 1
        if size is not None and Decimal(str(size)) < MIN_MARKET_CAP:
            status = "[red]below $25M[/red]"
            problems += 1
        t.add_row(
            sym, inst.exchange, vendor, f"${Decimal(str(size)):,.0f}" if size else "?", status
        )
    console.print(t)
    if problems:
        console.print(f"[red]{problems} metadata problem(s).[/red] Update data/universe.py.")
        raise typer.Exit(EXIT_RULE_UNRESOLVED)


# ---------------------------------------------------------------------------
# rules and the order sheet
# ---------------------------------------------------------------------------


@app.command()
def rules(profile: str = typer.Option("deca", help="deca or wharton")) -> None:
    """Show every competition rule and whether you currently satisfy it."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    prof = _load_profile(profile, cfg)
    account = _load_account(profile, cache, cfg)

    console.print(
        Panel(
            prof.execution_note(today_et()), title="How your orders will fill", border_style="cyan"
        )
    )

    checks = _check_rules(profile, prof, account, today_et())
    t = Table(show_header=True, header_style="bold")
    t.add_column("Rule")
    t.add_column("OK")
    t.add_column("Status")
    t.add_column("Detail", overflow="fold")
    t.add_column("Due")

    unresolved = 0
    for c in checks:
        ok = "[green]yes[/green]" if c.satisfied else "[red]NO[/red]"
        if not c.satisfied:
            unresolved += 1
        t.add_row(c.name, ok, c.status.value, c.detail, str(c.deadline) if c.deadline else "-")
    console.print(t)

    if unresolved:
        console.print(f"\n[red]{unresolved} rule(s) not satisfied.[/red]")
        raise typer.Exit(EXIT_RULE_UNRESOLVED)


def build_deca_sheet(limit: int = 0, as_of: date | None = None):
    """Assemble live inputs and build today's DECA order sheet."""
    from investlab.earnings import EarningsCalendar
    from investlab.plan import SheetInputs, build_sheet
    from investlab.screen import compute_features, load_bars

    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    prof = _load_profile("deca", cfg)
    today = as_of or today_et()
    account = _load_account("deca", cache, cfg)
    universe = default_universe()
    bars, _missing = load_bars(cache, list(universe.symbols()), today)
    if not bars:
        err.print("[red]No cached bars.[/red] Run 'investlab data pull' first.")
        raise typer.Exit(EXIT_NO_DATA)
    deca_cfg = cfg.deca
    if limit:
        deca_cfg = replace(deca_cfg, strategy=replace(deca_cfg.strategy, max_candidates=limit))
    store = _store("deca")
    starting = (
        Decimal(str(store.load_book().get("starting_cash", deca_cfg.starting_cash)))
        if store.exists()
        else deca_cfg.starting_cash
    )
    return build_sheet(
        SheetInputs(
            as_of=today,
            account=account,
            features=compute_features(bars),
            universe=universe,
            profile=prof,
            cfg=deca_cfg,
            starting_capital=starting,
            entry_stops=store.entry_stops() if store.exists() else {},
            earnings=EarningsCalendar(),
            trades=store.trades() if store.exists() else [],
            equity_curve=store.equity_curve() if store.exists() else [],
            risk_reviews=store.risk_reviews() if store.exists() else [],
            staleness_days=cfg.data.staleness_days,
        )
    )


@app.command()
def daily(
    profile: str = typer.Option("deca", help="deca (the order sheet is DECA-only)"),
    limit: int = typer.Option(0, help="Maximum new buys. Default: the strategy config."),
    save: bool = typer.Option(False, "--save", help="Write the sheet to research/deca/sheets/."),
    as_json: bool = typer.Option(False, "--json", help="Print the sheet as JSON."),
) -> None:
    """Print today's DECA order sheet: what to sell, the compliance buys, and
    what to buy. Signals come from the previous completed close, so run it in
    the morning; every order fills at the session's close."""
    if profile != "deca":
        console.print(
            "[yellow]The order sheet is built for DECA only.[/yellow] For Wharton, "
            "run `investlab wharton plan` (allocation orders) and "
            "`investlab wharton project` (Laura's projections)."
        )
        raise typer.Exit(EXIT_RULE_UNRESOLVED)

    from investlab.sheets import save_sheet, sheet_markdown

    sheet = build_deca_sheet(limit)
    if as_json:
        typer.echo(json.dumps(sheet.to_dict(), indent=2))
    else:
        console.print(Markdown(sheet_markdown(sheet)))
    if save:
        json_path, md_path = save_sheet(sheet)
        err.print(f"[green]Saved[/green] {md_path} and {json_path.name}")


@app.command("portfolio")
def portfolio_init(
    profile: str = typer.Option("deca", help="deca or wharton"),
    action: str = typer.Argument("init", help="init"),
) -> None:
    """Create the portfolio file you keep in step with your real account."""
    cfg = cfg_mod.load()
    path = _portfolio_path(profile)
    if path.exists():
        console.print(f"[yellow]Already exists:[/yellow] {path}")
        raise typer.Exit(EXIT_OK)

    starting = cfg.deca.starting_cash if profile == "deca" else cfg.wharton.starting_cash
    store = _store(profile)
    store.init(starting, today_et())
    store.render_summary(store.account(), generated=now_et().isoformat(timespec="seconds"))
    console.print(f"[green]Created[/green] {store.dir} with ${starting:,} cash.")


# ---------------------------------------------------------------------------
# recording what actually happened
# ---------------------------------------------------------------------------


@app.command()
def fill(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Ticker that filled."),
    action: str = typer.Option("buy", "--action", "-a", help="buy or sell"),
    quantity: int = typer.Option(..., "--quantity", "-q", help="Shares actually filled."),
    price: str = typer.Option(..., "--price", "-p", help="Fill price from the platform."),
    commission: str = typer.Option("", help="Defaults to the profile's commission."),
    when: str = typer.Option(
        "", help="Session the order filled, YYYY-MM-DD. Defaults to today ET."
    ),
    stop: str = typer.Option(
        "",
        "--stop",
        help="Entry stop for a buy. Default: the saved sheet's stop, else an ATR stop.",
    ),
    profile: str = typer.Option("deca", help="deca or wharton"),
    asset_class: str = typer.Option(
        "", help="stock, etf, mutual_fund or bond. Inferred from the universe when known."
    ),
) -> None:
    """Record a trade that actually executed, so the tool's book matches reality.

    Use the numbers from the platform, not the numbers the order sheet
    predicted. DECA prices at the session close, so what you were quoted when
    you clicked is not what you paid. On DECA's Equity Positions page the cost
    basis includes the $5 commission: fill price = (cost basis - 5) / shares.
    """
    from investlab.contracts import Action as ActionEnum

    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    account = _load_account(profile, cache, cfg)
    path = _portfolio_path(profile)
    if not path.exists():
        console.print(f"[red]No portfolio at {path}.[/red] Run 'investlab portfolio init' first.")
        raise typer.Exit(EXIT_BAD_CONFIG)

    sym = symbol.upper()
    try:
        act = ActionEnum(action.lower())
    except ValueError as exc:
        console.print(f"[red]Action must be buy or sell,[/red] got {action!r}")
        raise typer.Exit(EXIT_BAD_CONFIG) from exc
    if act not in (ActionEnum.BUY, ActionEnum.SELL):
        console.print(f"[red]Action must be buy or sell,[/red] got {action!r}")
        raise typer.Exit(EXIT_BAD_CONFIG)

    if asset_class:
        klass = AssetClass(asset_class.lower())
    else:
        try:
            klass = default_universe().get(sym).asset_class
        except Exception:  # noqa: BLE001 - unknown symbol is a user-fixable case
            console.print(
                f"[red]{sym} is not in the universe,[/red] so its asset class is unknown. "
                "Pass --asset-class (stock, etf, mutual_fund or bond). This matters: "
                "DECA counts ETFs as stocks and bond mutual funds as mutual funds, and "
                "guessing would corrupt the diversification check."
            )
            raise typer.Exit(EXIT_BAD_CONFIG) from None

    default_comm = (
        cfg.deca.commission_per_trade if profile == "deca" else cfg.wharton.commission_per_trade
    )
    comm = _money(commission, "--commission") if commission else default_comm
    session = _date(when, "--when") if when else today_et()

    if quantity <= 0:
        console.print("[red]Quantity must be positive.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)

    px = _money(price, "--price")
    before_cash = account.cash
    held = next((p for p in account.positions if p.symbol == sym), None)

    if act is ActionEnum.BUY:
        cost = px * Decimal(quantity) + comm
        if cost > before_cash:
            console.print(
                f"[red]Rejected:[/red] {sym} costs ${cost:,.2f} including commission "
                f"but only ${before_cash:,.2f} cash is recorded. If the platform "
                "really filled this, your book is out of step — reconcile it first."
            )
            raise typer.Exit(EXIT_BAD_CONFIG)
        after_cash = before_cash - cost
    else:
        owned = held.quantity if held else 0
        if quantity > owned:
            console.print(
                f"[red]Rejected:[/red] selling {quantity:,} {sym} but the book shows "
                f"{owned:,}. Shorting is disabled, so this is a bookkeeping "
                "mismatch rather than a short sale."
            )
            raise typer.Exit(EXIT_BAD_CONFIG)
        after_cash = before_cash + px * Decimal(quantity) - comm

    stop_value: Decimal | None = None
    stop_source = ""
    if act is ActionEnum.BUY and klass in (AssetClass.STOCK, AssetClass.ETF):
        if stop:
            stop_value, stop_source = _money(stop, "--stop"), "entered"
        else:
            from investlab.sheets import find_stop

            found = find_stop(sym, session)
            if found is not None:
                stop_value, stop_source = found[0], f"the {found[1]} sheet"
            else:
                from investlab.portfolio.exits import entry_stop_at
                from investlab.screen import compute_feature_frame

                try:
                    bars = cache.read(sym, session - timedelta(days=500), session)
                    if bars:
                        stop_value = entry_stop_at(
                            compute_feature_frame(sym, bars),
                            session,
                            px,
                            cfg.deca.strategy.entry_atr_multiple,
                        )
                        multiple = cfg.deca.strategy.entry_atr_multiple
                        stop_source = f"fill price minus {multiple}xATR(14)"
                except SymbolNotCachedError:
                    pass

    raw = json.loads(path.read_text())
    raw["cash"] = str(after_cash)
    raw["as_of"] = session.isoformat()
    by_symbol = {p["symbol"]: p for p in raw.get("positions", [])}
    if act is ActionEnum.BUY:
        entry = by_symbol.setdefault(sym, {"symbol": sym, "asset_class": klass.value, "lots": []})
        lot: dict[str, Any] = {
            "quantity": quantity,
            "price": str(px),
            "commission": str(comm),
            "opened": session.isoformat(),
        }
        if stop_value is not None:
            lot["stop"] = str(stop_value)
        entry["lots"].append(lot)
    else:
        remaining = quantity
        lots = by_symbol.get(sym, {}).get("lots", [])
        for existing in list(lots):  # FIFO, matching the ledger
            if remaining <= 0:
                break
            take = min(remaining, existing["quantity"])
            existing["quantity"] -= take
            remaining -= take
        by_symbol.get(sym, {})["lots"] = [x for x in lots if x["quantity"] > 0]
        if sym in by_symbol and not by_symbol[sym]["lots"]:
            del by_symbol[sym]
    raw["positions"] = list(by_symbol.values())
    store = _store(profile)
    store.save_book(raw)

    from investlab.store import TradeRecord

    fees = Decimal("0")
    store.append_trade(
        TradeRecord(
            session=session,
            recorded_at=now_et().isoformat(timespec="seconds"),
            symbol=sym,
            action=act.value,
            quantity=quantity,
            price=px,
            commission=comm,
            fees=fees,
            asset_class=klass.value,
            cash_after=after_cash,
        )
    )
    refreshed = _load_account(profile, cache, cfg)
    store.render_summary(
        refreshed,
        rule_lines=_rule_lines(profile, _load_profile(profile, cfg), refreshed),
        generated=now_et().isoformat(timespec="seconds"),
    )

    console.print(
        f"[green]Recorded[/green] {act.value} {quantity:,} {sym} @ ${px:,.2f} "
        f"(+${comm} commission) on {session}."
    )
    if stop_value is not None:
        console.print(f"Entry stop ${stop_value:,.2f} recorded (from {stop_source}).")
    elif act is ActionEnum.BUY and klass in (AssetClass.STOCK, AssetClass.ETF):
        console.print(
            "[yellow]No entry stop recorded.[/yellow] Set one with "
            f"[cyan]investlab stop set -s {sym} -p PRICE[/cyan]."
        )

    # The bond-ETF trap. DECA's guidelines say "all ETFs (including bond ETFs)
    # are classified as stocks", so buying AGG or BND to cover the bond leg
    # covers the STOCK leg instead and leaves the bond requirement untouched.
    if profile == "deca" and act is ActionEnum.BUY and klass is AssetClass.ETF:
        bonds_held = sum(
            (p.net_cost for p in account.positions if p.asset_class is AssetClass.BOND),
            Decimal("0"),
        )
        if bonds_held < cfg.deca.diversification_minimum:
            console.print(
                Panel(
                    f"[bold]{sym} counted toward STOCKS, not bonds.[/bold]\n\n"
                    "DECA classifies every ETF as a stock, bond ETFs included, so this "
                    "purchase did nothing for your bond requirement. Your bond leg is "
                    f"still ${bonds_held:,.2f} against a ${cfg.deca.diversification_minimum:,.0f} "
                    f"minimum due {cfg.deca.diversification_deadline}.\n\n"
                    "Only bonds SMG itself provides count, investment grade at BBB or "
                    "better. Find them in the platform's bond list, not by buying a "
                    "bond fund ticker.",
                    title="This did not satisfy the bond leg",
                    border_style="yellow",
                )
            )
    console.print(f"Cash ${before_cash:,.2f} -> [bold]${after_cash:,.2f}[/bold]")
    console.print(
        "\nNext: [cyan]investlab reconcile[/cyan] against the platform's account statistics, "
        f"then record why: [cyan]investlab journal add -s {sym} -a {act.value} -q {quantity} "
        f"-p {px}[/cyan]"
    )


def _parse_position(text: str) -> tuple[str, int, Decimal | None]:
    parts = text.split(":")
    if len(parts) not in (2, 3):
        console.print(f"[red]--position must be SYMBOL:SHARES[:COST_BASIS],[/red] got {text!r}")
        raise typer.Exit(EXIT_BAD_CONFIG)
    try:
        shares = int(parts[1].replace(",", ""))
    except ValueError:
        console.print(f"[red]Shares must be a whole number in[/red] {text!r}")
        raise typer.Exit(EXIT_BAD_CONFIG) from None
    basis = _money(parts[2], "cost basis") if len(parts) == 3 and parts[2] else None
    return parts[0].upper(), shares, basis


@app.command()
def reconcile(
    statement: Path = typer.Option(
        None,
        "--statement",
        help="JSON: as_of, cash, total_equity, positions[symbol, quantity, cost_basis].",
    ),
    cash: str = typer.Option("", "--cash", help="Cash Balance from the platform."),
    equity: str = typer.Option("", "--equity", help="Total Equity from the platform."),
    position: list[str] = typer.Option(
        [], "--position", help="SYMBOL:SHARES:COST_BASIS as the platform shows it. Repeatable."
    ),
    as_of: str = typer.Option("", "--as-of", help="Date the statement is as of."),
    profile: str = typer.Option("deca", help="deca or wharton"),
    save: bool = typer.Option(
        False, "--save", help="Keep the statement in ledger/<profile>/statements/ as evidence."
    ),
) -> None:
    """Prove the ledger matches the platform's account statistics.

    Cash, share counts and cost basis must agree to the cent. DECA's cost basis
    includes the $5 commission, so the ledger side adds lot commissions before
    comparing. Total equity is shown for information: it depends on which close
    each side marked to. Exits 5 on any mismatch.
    """
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    store = _store(profile)
    if not store.exists():
        console.print(f"[red]No ledger for {profile}.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)

    if statement is not None:
        data = json.loads(statement.read_text())
    else:
        data = {
            "as_of": as_of or today_et().isoformat(),
            "cash": cash,
            "total_equity": equity,
            "positions": [
                {"symbol": s, "quantity": q, "cost_basis": str(b) if b is not None else None}
                for s, q, b in (_parse_position(p) for p in position)
            ],
        }
    if not data.get("cash"):
        console.print("[red]A statement needs at least the cash balance.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)

    account = _load_account(profile, cache, cfg)
    t = Table(title=f"Ledger vs platform, as of {data.get('as_of')}", header_style="bold")
    for col in ("Item", "Ledger", "Platform", "Result"):
        t.add_column(col)
    failures = 0

    theirs_cash = _money(str(data["cash"]), "cash")
    ok = account.cash.quantize(Decimal("0.01")) == theirs_cash.quantize(Decimal("0.01"))
    failures += not ok
    t.add_row(
        "Cash",
        f"${account.cash:,.2f}",
        f"${theirs_cash:,.2f}",
        "[green]match[/green]" if ok else "[red]MISMATCH[/red]",
    )

    ours = {p.symbol: p for p in account.positions}
    theirs = {str(p["symbol"]).upper(): p for p in data.get("positions", [])}
    for sym in sorted(set(ours) | set(theirs)):
        mine, other = ours.get(sym), theirs.get(sym)
        my_qty = mine.quantity if mine else 0
        their_qty = int(other["quantity"]) if other else 0
        ok = my_qty == their_qty
        failures += not ok
        t.add_row(
            f"{sym} shares",
            f"{my_qty:,}",
            f"{their_qty:,}",
            "[green]match[/green]" if ok else "[red]MISMATCH[/red]",
        )
        if mine and other and other.get("cost_basis"):
            my_basis = mine.net_cost + sum((lot.commission for lot in mine.lots), Decimal("0"))
            their_basis = _money(str(other["cost_basis"]), "cost basis")
            ok = abs(my_basis - their_basis) <= Decimal("0.01")
            failures += not ok
            t.add_row(
                f"{sym} cost basis (incl. commission)",
                f"${my_basis:,.2f}",
                f"${their_basis:,.2f}",
                "[green]match[/green]" if ok else "[red]MISMATCH[/red]",
            )

    if data.get("total_equity"):
        their_eq = _money(str(data["total_equity"]), "total equity")
        diff = account.equity - their_eq
        t.add_row(
            "Total equity (info)",
            f"${account.equity:,.2f}",
            f"${their_eq:,.2f}",
            f"differs by ${diff:,.2f}" if diff else "[green]match[/green]",
        )
    console.print(t)

    if save:
        folder = store.dir / "statements"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{data.get('as_of') or today_et().isoformat()}.json"
        out.write_text(json.dumps(data, indent=2) + "\n")
        console.print(f"[dim]Saved statement to {out}[/dim]")

    if failures:
        console.print(
            f"[red]{failures} mismatch(es).[/red] The platform is authoritative: find the trade "
            "that was missed or mistyped and record or correct it before tomorrow's sheet."
        )
        raise typer.Exit(EXIT_VERIFICATION_FAILED)
    console.print("[green]The ledger agrees with the platform.[/green]")


@stop_app.command("set")
def stop_set(
    symbol: str = typer.Option(..., "--symbol", "-s"),
    price: str = typer.Option(..., "--price", "-p", help="The entry stop."),
    profile: str = typer.Option("deca"),
) -> None:
    """Record or correct the entry stop on a held position."""
    cfg = cfg_mod.load()
    store = _store(profile)
    if not store.exists():
        console.print(f"[red]No ledger for {profile}.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    value = _money(price, "--price")
    updated = store.set_stop(symbol.upper(), value)
    if not updated:
        console.print(f"[red]{symbol.upper()} is not held.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    cache = ParquetCache(cfg.data.cache_dir)
    account = _load_account(profile, cache, cfg)
    store.render_summary(
        account,
        rule_lines=_rule_lines(profile, _load_profile(profile, cfg), account),
        generated=now_et().isoformat(timespec="seconds"),
    )
    console.print(
        f"[green]Entry stop for {symbol.upper()} set to ${value:,.2f}[/green] on {updated} lot(s)."
    )


@stop_app.command("show")
def stop_show(profile: str = typer.Option("deca")) -> None:
    """Entry, trailing and active stop for every held stock and ETF."""
    sheet = build_deca_sheet()
    t = Table(title=f"Stops as of the {sheet.signal_session} close", header_style="bold")
    for col in (
        "Symbol",
        "Close",
        "Entry stop",
        "Trailing stop",
        "Active stop",
        "To stop",
        "Verdict",
    ):
        t.add_column(col)
    for h in sheet.held:
        if h.active_stop is None and h.verdict.startswith("HOLD (compliance)"):
            continue
        t.add_row(
            h.symbol,
            f"${h.last_close:,.2f}" if h.last_close is not None else "—",
            f"${h.entry_stop:,.2f}" if h.entry_stop is not None else "—",
            f"${h.trailing_stop:,.2f}" if h.trailing_stop is not None else "—",
            f"${h.active_stop:,.2f}" if h.active_stop is not None else "—",
            f"{h.distance_to_stop_pct:.2f}%" if h.distance_to_stop_pct is not None else "—",
            h.verdict,
        )
    console.print(t)


@app.command("risk-review")
def risk_review(
    note: str = typer.Option("", "--note", help="Your own words. Prompted if omitted."),
    profile: str = typer.Option("deca"),
) -> None:
    """Resume new buys after a drawdown halt, with your own review note.

    The note is yours and is stored verbatim. The high-water mark re-bases to
    equity on the review date, so a further 5% or 10% fall triggers again.
    """
    text = note.strip() or typer.prompt("Your review: what happened and what changes").strip()
    if not text:
        console.print("[red]An empty review is not recorded.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    store = _store(profile)
    store.add_risk_review(today_et(), text, now_et().isoformat(timespec="seconds"))
    console.print(f"[green]Recorded[/green] review for {today_et()} in {store.risk_reviews_path}")


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


@journal_app.command("add")
def journal_add(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Ticker you traded."),
    action: str = typer.Option(..., "--action", "-a", help="buy, sell, hold, exit, review"),
    quantity: int = typer.Option(0, "--quantity", "-q"),
    price: str = typer.Option("0", "--price", "-p"),
    profile: str = typer.Option("deca", help="deca or wharton"),
) -> None:
    """Record why you made a trade, in your own words.

    The tool supplies the surrounding facts. You supply the reasoning, and it
    is stored verbatim. This is deliberate: Wharton requires a Trading Note per
    trade and audits them, and both competitions require the analysis to be the
    team's own. Nothing here drafts, suggests, or autocompletes your reasoning.
    """
    from investlab.contracts import Action as ActionEnum
    from investlab.journal import DecisionFacts, Journal

    cfg = cfg_mod.load()
    try:
        act = ActionEnum(action.lower())
    except ValueError as exc:
        valid = ", ".join(a.value for a in ActionEnum)
        console.print(f"[red]Unknown action[/red] {action!r}. Use one of: {valid}")
        raise typer.Exit(EXIT_BAD_CONFIG) from exc

    console.print(
        Panel(
            "Write your own reasoning. Why this security, why this size, why now,\n"
            "and what would make you change your mind.\n\n"
            "[dim]Stored word for word. Wharton audits trading notes and states\n"
            '"You must use actual trading notes from trades you made on WInS."[/dim]',
            title=f"{act.value.upper()} {quantity} {symbol.upper()}",
            border_style="cyan",
        )
    )
    reasoning = typer.prompt("Your reasoning").strip()
    if not reasoning:
        console.print("[red]Empty reasoning is not recorded.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)

    facts = DecisionFacts(
        session=today_et(),
        symbol=symbol.upper(),
        action=act,
        quantity=quantity,
        price=Decimal(price),
        indicators={},
        binding_constraints=(),
        data_source=f"investlab cache ({', '.join(cfg.data.providers)})",
        note_fields={"profile": profile},
    )
    journal = Journal(_store(profile).journal_path)
    entry = journal.record(facts, reasoning)
    console.print(f"[green]Recorded[/green] {entry.entry_id} to {_store(profile).journal_path}")


@journal_app.command("list")
def journal_list(
    limit: int = typer.Option(15),
    profile: str = typer.Option("deca", help="deca or wharton"),
) -> None:
    """Show recent entries and verify the log has not been tampered with."""
    from investlab.journal import Journal

    journal = Journal(_store(profile).journal_path)
    entries = journal.entries()
    if not entries:
        console.print(
            "Journal is empty. Record your first entry with [cyan]investlab journal add[/cyan]."
        )
        return

    intact = journal.verify_chain()
    t = Table(show_header=True, header_style="bold")
    t.add_column("When")
    t.add_column("What")
    t.add_column("Your reasoning", overflow="fold")
    for e in entries[-limit:]:
        t.add_row(
            e.recorded_at.strftime("%Y-%m-%d %H:%M"),
            f"{e.facts.action.value} {e.facts.quantity} {e.facts.symbol}",
            e.reasoning,
        )
    console.print(t)
    console.print(
        f"[green]Hash chain intact[/green] across {len(entries)} entries."
        if intact
        else "[red]Hash chain BROKEN. The log has been edited outside the tool.[/red]"
    )


@journal_app.command("export")
def journal_export(
    out: Path = typer.Option(Path(""), help="Where to write. Defaults inside the ledger."),
    profile: str = typer.Option("deca", help="deca or wharton"),
) -> None:
    """Export an evidence packet: your entries plus a citable provenance footer."""
    from investlab.journal import Journal, export_evidence_packet

    cfg = cfg_mod.load()
    store = _store(profile)
    out = out if str(out) else store.dir / "evidence_packet.md"
    journal = Journal(store.journal_path)
    entries = journal.entries()
    if not entries:
        console.print("Nothing to export yet.")
        raise typer.Exit(EXIT_OK)

    text = export_evidence_packet(
        entries, data_sources=list(cfg.data.providers), generated_at=now_et()
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    console.print(f"[green]Wrote[/green] {len(entries)} entries to {out}")
    console.print(
        "[dim]The reasoning in this packet is yours. Cite the tool in APA only for "
        "the figures it computed, never for the analysis.[/dim]"
    )


# ---------------------------------------------------------------------------
# earnings
# ---------------------------------------------------------------------------


@earnings_app.command("set")
def earnings_set(
    symbol: str = typer.Option(..., "--symbol", "-s"),
    on: str = typer.Option(..., "--date", "-d", help="Report date, YYYY-MM-DD."),
    timing: str = typer.Option("unknown", "--timing", "-t", help="bmo, amc or unknown"),
    source: str = typer.Option("finviz", help="finviz, company or manual"),
) -> None:
    """Record a symbol's next earnings date, e.g. from its Finviz quote page."""
    from investlab.earnings import EarningsCalendar, EarningsEvent, Timing

    try:
        t = Timing(timing.lower())
    except ValueError:
        console.print("[red]--timing must be bmo, amc or unknown.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG) from None
    event = EarningsEvent(
        symbol=symbol.upper(),
        date=_date(on, "--date"),
        timing=t,
        source=source.lower(),
        recorded_at=now_et().isoformat(timespec="seconds"),
    )
    EarningsCalendar().set(event, today=today_et())
    console.print(
        f"[green]Recorded[/green] {event.symbol} earnings {event.label()}; price impact lands "
        f"on {', '.join(str(d) for d in event.gap_sessions())}."
    )


@earnings_app.command("list")
def earnings_list(
    all_dates: bool = typer.Option(False, "--all", help="Include past reports."),
) -> None:
    """Show recorded earnings dates."""
    from investlab.earnings import EarningsCalendar

    events = EarningsCalendar().events()
    if not all_dates:
        events = [e for e in events if e.date >= today_et()]
    if not events:
        console.print(
            "No earnings dates recorded. Use [cyan]investlab earnings set[/cyan] "
            "or [cyan]pull[/cyan]."
        )
        return
    t = Table(header_style="bold")
    for col in ("Symbol", "Date", "Timing", "Gap session", "Source"):
        t.add_column(col)
    for e in sorted(events, key=lambda e: (e.date, e.symbol)):
        t.add_row(
            e.symbol, str(e.date), e.timing.value, ", ".join(map(str, e.gap_sessions())), e.source
        )
    console.print(t)


@earnings_app.command("pull")
def earnings_pull(
    symbols: str = typer.Option("", help="Comma-separated. Default: every stock in the universe."),
) -> None:
    """Fill in upcoming earnings dates from yfinance. Never overwrites a date
    recorded by hand. Uses the network."""
    from investlab.earnings import EarningsCalendar, fetch_yfinance_events

    universe = default_universe()
    wanted = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else [i.symbol for i in universe.by_asset_class(AssetClass.STOCK)]
    )
    console.print(f"Looking up earnings dates for {len(wanted)} symbols...")
    events, missing = fetch_yfinance_events(
        wanted, today=today_et(), recorded_at=now_et().isoformat(timespec="seconds")
    )
    written = EarningsCalendar().merge_pulled(events, today=today_et())
    kept = len(events) - len(written)
    console.print(
        f"[green]Recorded {len(written)} date(s)[/green] ({kept} kept from manual entries)."
    )
    if missing:
        console.print(f"[yellow]No upcoming date found for:[/yellow] {', '.join(missing)}")


# ---------------------------------------------------------------------------
# performance
# ---------------------------------------------------------------------------


def _close_on(cache: ParquetCache, symbol: str, session: date) -> Decimal | None:
    try:
        bars = cache.read(symbol, session, session)
    except SymbolNotCachedError:
        return None
    return bars[-1].close if bars else None


def _benchmark(cache: ParquetCache, as_of: date) -> tuple[str, Decimal] | None:
    """The first cached S&P tracker with a usable close, and its price."""
    for symbol in BENCHMARK_CANDIDATES:
        try:
            bars = cache.read(symbol, as_of - timedelta(days=14), as_of)
        except SymbolNotCachedError:
            continue
        if bars:
            return symbol, bars[-1].close
    return None


@app.command()
def snapshot(
    profile: str = typer.Option("deca", help="deca, wharton, or 'both'."),
    session: str = typer.Option(
        "", "--session", help="Trading session to record, YYYY-MM-DD. Default: today ET."
    ),
) -> None:
    """Record one session's closing equity so the report has a curve.

    Refuses weekends and holidays, and refuses a session whose close is not in
    the cache yet, so the curve never carries a mislabeled row. Holdings are
    replayed from the trade log, which lets a missed session be backfilled.
    """
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    target = _date(session, "--session") if session else today_et()
    if target > today_et():
        console.print(f"[red]{target} is in the future.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    if not cal.is_session(target):
        console.print(f"[red]{target} is not a trading session.[/red] Nothing recorded.")
        raise typer.Exit(EXIT_BAD_CONFIG)

    bench = None
    bench_symbol = None
    for symbol in BENCHMARK_CANDIDATES:
        value = _close_on(cache, symbol, target)
        if value is not None:
            bench_symbol, bench = symbol, value
            break

    profiles = ["deca", "wharton"] if profile == "both" else [profile]
    for name in profiles:
        store = _store(name)
        if not store.exists():
            console.print(f"[dim]{name}: no ledger yet, skipping.[/dim]")
            continue
        cash, shares, classes = store.holdings_at(target)
        last_trade_price = {r["symbol"]: Decimal(r["price"]) for r in store.trades()}
        equity = cash
        missing = []
        for sym, qty in shares.items():
            mark = _close_on(cache, sym, target)
            if mark is None and classes.get(sym) is AssetClass.BOND:
                mark = last_trade_price.get(sym)
            if mark is None:
                missing.append(sym)
                continue
            equity += mark * Decimal(qty)
        if missing:
            console.print(
                f"[red]No {target} close cached for {', '.join(missing)}.[/red] Run "
                "'investlab data pull' after the close, then snapshot again."
            )
            raise typer.Exit(EXIT_NO_DATA)
        store.record_equity(target, equity, cash, bench)
        account = _load_account(name, cache, cfg)
        store.render_summary(
            account,
            rule_lines=_rule_lines(name, _load_profile(name, cfg), account),
            generated=now_et().isoformat(timespec="seconds"),
        )
        console.print(f"[green]{name}[/green] {target}  equity ${equity:,.2f}  cash ${cash:,.2f}")

    if bench is None:
        console.print(
            f"[yellow]No S&P benchmark close cached for {target}.[/yellow] The row was recorded "
            "without it; re-run after 'investlab data pull' to add it."
        )
    else:
        console.print(f"[dim]Benchmark: {bench_symbol} at ${bench:,.2f}[/dim]")


@app.command()
def ledger(
    profile: str = typer.Option("deca", help="deca or wharton"),
    refresh: bool = typer.Option(
        False, "--refresh", help="Regenerate LEDGER.md from current marks."
    ),
) -> None:
    """Show one competition's ledger, or regenerate its written summary."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    store = _store(profile)
    if not store.exists():
        console.print(
            f"No ledger for {profile}. Run [cyan]investlab portfolio init "
            f"--profile {profile}[/cyan]."
        )
        raise typer.Exit(EXIT_BAD_CONFIG)

    account = _load_account(profile, cache, cfg)
    if refresh:
        store.render_summary(
            account,
            rule_lines=_rule_lines(profile, _load_profile(profile, cfg), account),
            generated=now_et().isoformat(timespec="seconds"),
        )
        console.print(f"[green]Regenerated[/green] {store.summary_path}")

    book = store.load_book()
    starting = Decimal(str(book.get("starting_cash", account.cash)))
    pnl = account.equity - starting
    pct = (pnl / starting * Decimal(100)) if starting else Decimal(0)
    console.print(
        Panel(
            f"[bold]{profile.upper()}[/bold]  ·  as of {account.as_of}\n"
            f"Cash ${account.cash:,.2f}  ·  Equity ${account.equity:,.2f}  ·  "
            f"Started ${starting:,.2f}\n"
            f"P&L ${pnl:,.2f} ({pct:+.2f}%)  ·  {len(store.trades())} trades recorded",
            border_style="cyan",
        )
    )

    stops = store.entry_stops()
    if account.positions:
        t = Table(header_style="bold")
        for col in ("Symbol", "Class", "Shares", "Net cost", "Value", "Entry stop"):
            t.add_column(col, justify="right" if col not in ("Symbol", "Class") else "left")
        for pos in sorted(account.positions, key=lambda p: p.symbol):
            mark = account.marks.get(pos.symbol, Decimal("0"))
            stop = stops.get(pos.symbol)
            t.add_row(
                pos.symbol,
                pos.asset_class.value,
                f"{pos.quantity:,}",
                f"${pos.net_cost:,.2f}",
                f"${mark * Decimal(pos.quantity):,.2f}",
                f"${stop:,.2f}" if stop is not None else "—",
            )
        console.print(t)
    else:
        console.print("No open positions.")
    console.print(f"[dim]{store.dir}[/dim]")


@app.command()
def backtest(
    start: str = typer.Option(..., help="First session to trade, YYYY-MM-DD."),
    end: str = typer.Option("", help="Last session. Default: the newest cached close."),
    out: Path = typer.Option(Path("runs/backtest"), help="Folder for the tear sheet."),
    resume_after_halt: int = typer.Option(
        0, help="Sessions after a drawdown halt before resuming (0: never, the live policy)."
    ),
) -> None:
    """Replay the DECA order sheet over past sessions against the baselines.

    Needs about a year of history before --start: pull it with
    `investlab data pull --days 1100`. Bonds, earnings dates and delisted
    companies are outside what the replay can see; the report says so.
    """
    from investlab.backtest.baselines import run_baselines
    from investlab.backtest.engine import BacktestSettings, CloseBook, run_strategy
    from investlab.reports.tearsheet import write_tearsheet
    from investlab.screen import compute_features, load_bars

    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    universe = default_universe()
    start_d = _date(start, "--start")
    end_d = _date(end, "--end") if end else today_et()
    lookback = (end_d - start_d).days + 480
    symbols = list(universe.symbols()) + list(BENCHMARK_CANDIDATES)
    bars, _missing = load_bars(cache, symbols, end_d, lookback_days=lookback)
    benchmark = next((b for b in BENCHMARK_CANDIDATES if b in bars), None)
    if benchmark is None:
        console.print("[red]No benchmark bars cached.[/red] Run 'investlab data pull --days 1100'.")
        raise typer.Exit(EXIT_NO_DATA)
    first = min(b.session for b in bars[benchmark])
    if len(cal.sessions_between(first, start_d)) < 260:
        console.print(
            f"[yellow]Only {len(cal.sessions_between(first, start_d))} sessions of history before "
            f"{start_d}.[/yellow] The screen needs 252; early sessions will propose nothing. "
            "Run 'investlab data pull --days 1100' for a full warm-up."
        )

    settings = BacktestSettings(
        start=start_d,
        end=end_d,
        starting_cash=cfg.deca.starting_cash,
        resume_after_halt_sessions=resume_after_halt,
        benchmark=benchmark,
    )
    profile = _load_profile("deca", cfg)
    console.print("Computing features...")
    features = compute_features(bars)
    console.print(f"Replaying {start_d} to {end_d}...")
    strategy = run_strategy(bars, universe, profile, cfg.deca, settings, features=features)
    baselines = run_baselines(bars, universe, profile, cfg.deca, settings, features=features)
    paths = write_tearsheet(
        out,
        strategy,
        baselines,
        closes=CloseBook(bars),
        settings=settings,
        provenance={
            "generated": now_et().isoformat(timespec="seconds"),
            "cache_content_hash": cache.manifest()["content_hash"],
            "universe": universe.name,
            "bias_warning": BIAS_WARNING,
        },
    )
    summary = json.loads(paths["summary"].read_text())
    t = Table(title=f"Backtest {start_d} to {end_d}", header_style="bold")
    for col in ("Run", "Final equity", "Return", "vs benchmark", "Max drawdown", "Trades", "Costs"):
        t.add_column(col, justify="right" if col != "Run" else "left")
    for row in summary["runs"]:
        excess = row["excess_return_pct"]
        t.add_row(
            row["name"],
            f"${Decimal(row['final_equity']):,.2f}",
            f"{row['total_return_pct']:+.2f}%",
            f"{excess:+.2f}%" if excess is not None else "—",
            f"{row['max_drawdown_pct']:.2f}%",
            str(row["trades"]),
            f"${Decimal(row['costs']):,.2f}",
        )
    console.print(t)
    console.print(f"[green]Tear sheet:[/green] {paths['html']}")
    console.print(
        "[dim]Returns are over the window, not annualised. Bonds are held as cash, earnings "
        "dates are not applied, and the universe is survivorship-biased.[/dim]"
    )


@app.command("backtest-rolling")
def backtest_rolling(
    start: str = typer.Option(..., help="Earliest window start, YYYY-MM-DD."),
    end: str = typer.Option("", help="Latest window end. Default: the newest cached close."),
    length: int = typer.Option(62, help="Sessions per window. A DECA game is 62."),
    step: int = typer.Option(15, help="Sessions between window starts."),
    out: Path = typer.Option(Path("runs/rolling"), help="Folder for windows.csv, summary.json."),
) -> None:
    """Replay the sheet over many game-length windows against SPY, plain
    momentum and the compliance-aware baseline.

    The evidence standard for changing a strategy default: one long backtest
    swings with its start date. Needs `data pull --days 1100`.
    """
    import csv as csv_mod
    from dataclasses import asdict

    from investlab.backtest.rolling import run_rolling
    from investlab.screen import compute_features, load_bars

    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    universe = default_universe()
    start_d = _date(start, "--start")
    end_d = _date(end, "--end") if end else today_et()
    symbols = list(universe.symbols()) + list(BENCHMARK_CANDIDATES)
    bars, _missing = load_bars(cache, symbols, end_d, lookback_days=(end_d - start_d).days + 480)
    benchmark = next((b for b in BENCHMARK_CANDIDATES if b in bars), None)
    if benchmark is None:
        console.print("[red]No benchmark bars cached.[/red] Run 'investlab data pull --days 1100'.")
        raise typer.Exit(EXIT_NO_DATA)
    profile = _load_profile("deca", cfg)
    console.print("Replaying windows (a few minutes for two years of starts)...")
    windows, summaries = run_rolling(
        bars,
        universe,
        profile,
        cfg.deca,
        first_start=start_d,
        last_end=end_d,
        length=length,
        step=step,
        benchmark=benchmark,
        features=compute_features(bars),
    )
    if not windows:
        console.print(
            f"[red]Fewer than {length} cached sessions between {start_d} and {end_d}.[/red]"
        )
        raise typer.Exit(EXIT_NO_DATA)

    out.mkdir(parents=True, exist_ok=True)
    names = [s.name for s in summaries]
    with (out / "windows.csv").open("w", newline="") as fh:
        writer = csv_mod.writer(fh)
        writer.writerow(
            ["start", "end", f"{benchmark}_pct"]
            + [f"{n}_pct" for n in names]
            + [f"{n}_max_dd_pct" for n in names]
            + [f"{n}_trades" for n in names]
        )
        for w in windows:
            writer.writerow(
                [w.start.isoformat(), w.end.isoformat(), w.benchmark_pct]
                + [w.returns_pct[n] for n in names]
                + [w.max_drawdown_pct[n] for n in names]
                + [w.trades[n] for n in names]
            )
    assumptions = [
        "A drawdown halt resumes after 10 sessions; live, it needs Armaan's review.",
        "Bonds are held as cash; earnings dates are not applied.",
        BIAS_WARNING,
    ]
    (out / "summary.json").write_text(
        json.dumps(
            {
                "benchmark": benchmark,
                "length": length,
                "step": step,
                "first_start": windows[0].start.isoformat(),
                "last_start": windows[-1].start.isoformat(),
                "strategy_parameters": {k: str(v) for k, v in asdict(cfg.deca.strategy).items()},
                "summaries": [asdict(s) for s in summaries],
                "assumptions": assumptions,
            },
            indent=2,
        )
        + "\n"
    )

    span = f"{windows[0].start} to {windows[-1].end}"
    t = Table(title=f"{len(windows)} windows of {length} sessions, {span}", header_style="bold")
    for col in (
        "Run",
        "Mean ret",
        "Mean vs SPY",
        "Median",
        "Worst",
        "Best",
        "Beat",
        "Beat +5",
        "Beat comp.",
        "Avg DD",
        "Trades",
    ):
        t.add_column(col, justify="left" if col == "Run" else "right")
    for s in summaries:
        t.add_row(
            s.name,
            f"{s.mean_return_pct:+.2f}%",
            f"{s.mean_excess_pct:+.2f}",
            f"{s.median_excess_pct:+.2f}",
            f"{s.worst_excess_pct:+.2f}",
            f"{s.best_excess_pct:+.2f}",
            f"{s.beat_benchmark_pct:.0f}%",
            f"{s.beat_benchmark_by_5_pct:.0f}%",
            f"{s.beat_compliance_pct:.0f}%",
            f"{s.avg_max_drawdown_pct:.2f}%",
            f"{s.avg_trades:.1f}",
        )
    console.print(t)
    console.print(f"[green]Wrote[/green] {out / 'windows.csv'} and {out / 'summary.json'}")
    console.print(
        "[dim]" + " ".join(assumptions[:2]) + " The universe is survivorship-biased.[/dim]"
    )


@app.command("cash-adjust")
def cash_adjust(
    amount: str = typer.Option(..., "--amount", help="Positive credits cash; negative debits it."),
    kind: str = typer.Option(..., "--kind", help="interest, dividend, fee or other"),
    when: str = typer.Option("", "--when", help="Session it posted, YYYY-MM-DD. Default: today."),
    symbol: str = typer.Option("", "--symbol", "-s", help="For a dividend, the paying ticker."),
    note: str = typer.Option("", "--note"),
    profile: str = typer.Option("deca"),
) -> None:
    """Record cash the platform moved without a trade: DECA's weekly interest
    credit, a dividend, or a fee. Use the platform's number."""
    from investlab.store import CASH_EVENT_KINDS

    cfg = cfg_mod.load()
    store = _store(profile)
    if not store.exists():
        console.print(f"[red]No ledger for {profile}.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    if kind.lower() not in CASH_EVENT_KINDS:
        console.print(f"[red]--kind must be one of {', '.join(CASH_EVENT_KINDS)}.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    value = _money(amount, "--amount")
    session = _date(when, "--when") if when else today_et()
    before = Decimal(str(store.load_book()["cash"]))
    try:
        after = store.add_cash_event(
            session,
            kind.lower(),
            value,
            symbol=symbol.upper(),
            note=note,
            recorded_at=now_et().isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG) from exc
    account = _load_account(profile, ParquetCache(cfg.data.cache_dir), cfg)
    store.render_summary(
        account,
        rule_lines=_rule_lines(profile, _load_profile(profile, cfg), account),
        generated=now_et().isoformat(timespec="seconds"),
    )
    console.print(
        f"[green]Recorded[/green] {kind.lower()} {value:+,.2f} on {session}. "
        f"Cash ${before:,.2f} -> [bold]${after:,.2f}[/bold]"
    )


@app.command()
def split(
    symbol: str = typer.Option(..., "--symbol", "-s"),
    ratio: str = typer.Option(
        ..., "--ratio", help="New shares per old share: 2 for a 2-for-1, 0.1 for a 1-for-10."
    ),
    when: str = typer.Option(..., "--when", help="Effective session, YYYY-MM-DD."),
    profile: str = typer.Option("deca"),
) -> None:
    """Restate a held position after a stock split: shares, cost per share and
    the entry stop. Re-run `data pull` afterwards so cached closes match."""
    cfg = cfg_mod.load()
    store = _store(profile)
    if not store.exists():
        console.print(f"[red]No ledger for {profile}.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    value = _money(ratio, "--ratio")
    try:
        updated = store.apply_split(
            symbol.upper(),
            value,
            _date(when, "--when"),
            recorded_at=now_et().isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG) from exc
    if not updated:
        console.print(f"[red]{symbol.upper()} is not held.[/red]")
        raise typer.Exit(EXIT_BAD_CONFIG)
    account = _load_account(profile, ParquetCache(cfg.data.cache_dir), cfg)
    store.render_summary(
        account,
        rule_lines=_rule_lines(profile, _load_profile(profile, cfg), account),
        generated=now_et().isoformat(timespec="seconds"),
    )
    console.print(
        f"[green]Applied a {value}-for-1 split[/green] to {updated} {symbol.upper()} lot(s). "
        "Run `investlab data pull`, then `investlab reconcile` against the platform."
    )


@app.command()
def version() -> None:
    """Show version and provenance."""
    console.print("investlab 0.2.0")
    console.print(f"[dim]run at {now_et().isoformat(timespec='seconds')}[/dim]")


if __name__ == "__main__":
    app()
