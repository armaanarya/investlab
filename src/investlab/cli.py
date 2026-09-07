"""Command line interface.

The tool's whole job is to print a sheet of orders you type into a competition
platform by hand, and to tell you which rules currently bind. Neither DECA nor
Wharton exposes an API, so nothing here places a trade.

Every command reads from the local Parquet cache, never the network. `data
pull` is the only command that touches the internet, which is what makes a run
reproducible and what keeps the tool working on a day Yahoo is broken.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from investlab import config as cfg_mod
from investlab.contracts import AccountState, AssetClass, Lot, Position
from investlab.data.cache import ParquetCache, SymbolNotCachedError
from investlab.data.universe import BIAS_WARNING, default_universe

app = typer.Typer(
    add_completion=False,
    help="Decision support for the DECA Stock Market Game and Wharton WInS.",
)
data_app = typer.Typer(help="Fetch and inspect the local price cache.")
app.add_typer(data_app, name="data")

console = Console()

# Exit codes. Never return 0 after swallowing a real failure.
EXIT_OK = 0
EXIT_BAD_CONFIG = 2
EXIT_NO_DATA = 3
EXIT_RULE_UNRESOLVED = 4


EASTERN = ZoneInfo("America/New_York")


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


def _portfolio_path(profile: str) -> Path:
    return cfg_mod.DEFAULT_RUNS_DIR / f"portfolio_{profile}.json"


def _build_provider_chain(cfg: cfg_mod.AppConfig) -> Any:
    """Build the provider chain named in config, in order.

    `chain()` with no arguments is an empty chain that silently returns no
    bars, so the providers are always constructed explicitly. An unavailable
    provider (Tiingo with no API key) is skipped by the chain rather than
    failing the pull.
    """
    from investlab.data.providers import TiingoProvider, YFinanceProvider, chain

    built = []
    for name in cfg.data.providers:
        if name == "yfinance":
            built.append(YFinanceProvider())
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
    an unrelated command from running."""
    if name == "deca":
        from investlab.competitions.deca import DecaProfile

        return DecaProfile()
    if name == "wharton":
        try:
            from investlab.competitions.wharton import WhartonProfile
        except ImportError as exc:
            console.print(f"[red]Wharton profile unavailable:[/red] {exc}")
            raise typer.Exit(EXIT_BAD_CONFIG) from exc
        return WhartonProfile()
    console.print(f"[red]Unknown profile[/red] {name!r}. Use 'deca' or 'wharton'.")
    raise typer.Exit(EXIT_BAD_CONFIG)


def _load_account(profile: str, cache: ParquetCache, cfg: cfg_mod.AppConfig) -> AccountState:
    """Read the student-maintained portfolio file and mark it to the cache.

    The competition platform is the authoritative record. This file is our
    mirror of it, and `reconcile` is what proves the two agree.
    """
    path = _portfolio_path(profile)
    if not path.exists():
        starting = (
            cfg.deca.starting_cash if profile == "deca" else cfg.wharton.starting_cash
        )
        console.print(
            Panel(
                f"No portfolio file at [cyan]{path}[/cyan].\n\n"
                f"Starting a fresh book with [bold]${starting:,}[/bold] cash.\n"
                "Run [cyan]investlab portfolio init[/cyan] to create it, then keep it in "
                "step with your real account after every trade.",
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
        console.print(
            f"[yellow]Marked at cost (not cached):[/yellow] {', '.join(missing)}. "
            "Run 'investlab data pull' for current marks."
        )

    return AccountState(
        as_of=date.fromisoformat(raw["as_of"]) if "as_of" in raw else today_et(),
        cash=Decimal(str(raw.get("cash", 0))),
        positions=tuple(positions),
        marks=marks,
    )


@app.command()
def doctor() -> None:
    """Check the environment, the cache, and how fresh the data is."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)

    t = Table(title="investlab doctor", show_header=True, header_style="bold")
    t.add_column("Check")
    t.add_column("Status")
    t.add_column("Detail")

    symbols = list(cache.symbols()) if cfg.data.cache_dir.exists() else []
    t.add_row("Cache", "ok" if symbols else "empty", f"{len(symbols)} symbols in {cfg.data.cache_dir}")

    stale_note = "no data"
    if symbols:
        try:
            newest = max(
                (cache.coverage(s)[1] for s in symbols if cache.coverage(s)), default=None
            )
            if newest:
                age = (today_et() - newest).days
                stale_note = f"newest bar {newest} ({age}d old)"
        except Exception as exc:  # noqa: BLE001 - doctor must never crash
            stale_note = f"unreadable: {exc}"
    t.add_row("Freshness", "ok" if symbols else "n/a", stale_note)

    t.add_row("DECA", "ready", f"${cfg.deca.starting_cash:,} start, ${cfg.deca.commission_per_trade}/trade")
    t.add_row(
        "Wharton",
        "unverified" if not cfg.wharton.season_verified else "ready",
        f"materials release {cfg.wharton.materials_release}"
        if not cfg.wharton.season_verified
        else f"${cfg.wharton.starting_cash:,} start",
    )

    days = (cfg.deca.diversification_deadline - today_et()).days
    t.add_row(
        "DECA diversification",
        "urgent" if days <= 14 else "pending",
        f"{days} days to {cfg.deca.diversification_deadline}",
    )
    console.print(t)
    console.print(f"[dim]{BIAS_WARNING}[/dim]")


@data_app.command("pull")
def data_pull(
    days: int = typer.Option(400, help="Calendar days of history to fetch."),
    symbols: str = typer.Option("", help="Comma-separated symbols. Default: the whole universe."),
) -> None:
    """Refresh the local price cache. The only command that uses the network."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    universe = default_universe()

    wanted = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else list(universe.symbols())
    )
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
    console.print(f"[green]Cached {len(bars):,} bars across {len(got)} symbols.[/green]")
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
    for s in symbols[:60]:
        cov = cache.coverage(s)
        t.add_row(s, str(cov[0]) if cov else "-", str(cov[1]) if cov else "-")
    console.print(t)
    if len(symbols) > 60:
        console.print(f"[dim]...and {len(symbols) - 60} more[/dim]")


@app.command()
def rules(profile: str = typer.Option("deca", help="deca or wharton")) -> None:
    """Show every competition rule and whether you currently satisfy it."""
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    prof = _load_profile(profile, cfg)
    account = _load_account(profile, cache, cfg)

    console.print(Panel(prof.execution_note(today_et()), title="How your orders will fill", border_style="cyan"))

    checks = prof.check_rules(account, today_et())
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


@app.command()
def daily(
    profile: str = typer.Option("deca", help="deca or wharton"),
    limit: int = typer.Option(8, help="Maximum candidate orders to show."),
) -> None:
    """Print today's order sheet: what to type into the platform, and why.

    Signals come from the previous completed close, so run this in the morning.
    """
    cfg = cfg_mod.load()
    cache = ParquetCache(cfg.data.cache_dir)
    prof = _load_profile(profile, cfg)
    account = _load_account(profile, cache, cfg)

    console.print(
        Panel(
            f"[bold]{prof.name}[/bold]  ·  {today_et()}  ·  "
            f"equity ${account.equity:,.2f}  ·  cash ${account.cash:,.2f}",
            border_style="cyan",
        )
    )
    console.print(Panel(prof.execution_note(today_et()), title="How these fill", border_style="cyan"))

    checks = prof.check_rules(account, today_et())
    blocking = [c for c in checks if not c.satisfied]
    for c in blocking:
        due = f" (due {c.deadline})" if c.deadline else ""
        console.print(f"[yellow]RULE:[/yellow] {c.name}{due} — {c.detail}")

    console.print(
        "\n[dim]Order sheet generation needs the candidate screen, which is "
        "still being wired. Rule status and portfolio marks above are live.[/dim]"
    )
    console.print(
        "\n[bold]After you enter any trade, record why:[/bold] "
        "[cyan]investlab journal add[/cyan]\n"
        "[dim]Wharton audits trading notes and both competitions require the "
        "reasoning to be yours. This tool will not write it for you.[/dim]"
    )


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "profile": profile,
                "as_of": today_et().isoformat(),
                "cash": str(starting),
                "positions": [],
                "_note": (
                    "Mirror of your real competition account. After every trade, add "
                    "or amend a lot here so the tool's numbers match the platform's."
                ),
            },
            indent=2,
        )
    )
    console.print(f"[green]Created[/green] {path} with ${starting:,} cash.")


@app.command()
def version() -> None:
    """Show version and provenance."""
    console.print("investlab 0.1.0")
    console.print(f"[dim]run at {now_et().isoformat(timespec='seconds')}[/dim]")


if __name__ == "__main__":
    app()
