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
from dataclasses import replace
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
from investlab.contracts import AccountState, AssetClass, Lot, Position, RuleStatus
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
        starting = cfg.deca.starting_cash if profile == "deca" else cfg.wharton.starting_cash
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
    t.add_row(
        "Cache", "ok" if symbols else "empty", f"{len(symbols)} symbols in {cfg.data.cache_dir}"
    )

    stale_note = "no data"
    if symbols:
        try:
            newest = max((cache.coverage(s)[1] for s in symbols if cache.coverage(s)), default=None)
            if newest:
                age = (today_et() - newest).days
                stale_note = f"newest bar {newest} ({age}d old)"
        except Exception as exc:  # noqa: BLE001 - doctor must never crash
            stale_note = f"unreadable: {exc}"
    t.add_row("Freshness", "ok" if symbols else "n/a", stale_note)

    t.add_row(
        "DECA",
        "ready",
        f"${cfg.deca.starting_cash:,} start, ${cfg.deca.commission_per_trade}/trade",
    )
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

    console.print(
        Panel(
            prof.execution_note(today_et()), title="How your orders will fill", border_style="cyan"
        )
    )

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
    console.print(
        Panel(prof.execution_note(today_et()), title="How these fill", border_style="cyan")
    )

    checks = prof.check_rules(account, today_et())
    blocking = [c for c in checks if not c.satisfied]
    for c in blocking:
        due = f" (due {c.deadline})" if c.deadline else ""
        console.print(f"[yellow]RULE:[/yellow] {c.name}{due} — {c.detail}")

    # --- screen, gate, size -------------------------------------------------
    from investlab.portfolio.sizing import Candidate, size_batch
    from investlab.screen import load_bars, screen_universe

    universe = default_universe()
    held = {p.symbol for p in account.positions}
    bars, missing = load_bars(cache, list(universe.symbols()), today_et())
    if not bars:
        console.print("[red]No cached bars.[/red] Run 'investlab data pull' first.")
        raise typer.Exit(EXIT_NO_DATA)

    instruments = {s: universe.get(s) for s in bars}
    ranked, skipped = screen_universe(bars, instruments, today_et())

    newest = max((b[-1].session for b in bars.values()), default=None)
    stale = newest is not None and (today_et() - newest).days > cfg.data.staleness_days
    if stale:
        console.print(
            f"[yellow]STALE DATA:[/yellow] newest bar is {newest}. "
            "Treat everything below as indicative and re-pull before acting."
        )

    constraints = prof.sizing_constraints(account)

    # Reserve the compliance requirement before sizing any equity.
    #
    # DECA needs $10,000 of net cost in EACH of stocks, mutual funds and bonds
    # by 2026-10-23, and the tool's own screen only ever proposes equities. Left
    # alone, the sizer happily commits the entire $100,000 to stocks and leaves
    # nothing to buy the fund and bond legs with, which is a disqualification
    # rather than a bad trade. The equity sleeve gets what is left after the
    # outstanding legs are set aside.
    reserved = Decimal("0")
    if profile == "deca":
        target = cfg.deca.diversification_target
        for bucket in (AssetClass.MUTUAL_FUND, AssetClass.BOND):
            have = sum(
                (p.net_cost for p in account.positions if p.asset_class is bucket),
                Decimal("0"),
            )
            reserved += max(Decimal("0"), target - have)
        if reserved:
            console.print(
                f"[cyan]Reserved ${reserved:,.2f}[/cyan] of cash for the mutual fund and "
                f"bond legs due {cfg.deca.diversification_deadline}. "
                f"Equity sleeve is sized against the remaining "
                f"${max(Decimal('0'), constraints.spendable_cash - reserved):,.2f}."
            )
            constraints = replace(
                constraints,
                spendable_cash=max(Decimal("0"), constraints.spendable_cash - reserved),
            )

    blocked = []
    eligible: list = []
    for cand in ranked:
        if len(eligible) >= limit:
            break
        if cand.symbol in held:
            continue
        ok, reason = prof.is_eligible(cand.instrument, bars[cand.symbol][-1])
        if ok:
            eligible.append(cand)
        else:
            blocked.append((cand.symbol, reason))

    # Size the whole shortlist in one batch, not one call per candidate.
    # size_batch reserves cash sequentially in rank order, so a later
    # candidate cannot spend a dollar an earlier one already committed.
    # Sizing each independently produced eight orders totalling $216,000
    # against a $100,000 account.
    by_rank = tuple(
        Candidate(
            symbol=c.symbol,
            price_bound=c.close,
            protective_reference=c.protective_reference,
            rank=i,
            rationale=f"rank {c.score:.0f}/100, 21d momentum {c.momentum_21d * 100:+.1f}%",
        )
        for i, c in enumerate(eligible)
    )
    results = size_batch(by_rank, constraints)

    orders = []
    for cand, result in zip(eligible, results, strict=True):
        if hasattr(result, "quantity"):
            orders.append((cand, result))
        else:
            blocked.append((cand.symbol, f"{result.reason.value}: {result.detail}"))

    # A profile with an unresolved rule does not get to hand out an order
    # sheet. Wharton's 2026-27 capital, approved ETF list and client mandate
    # are unknown until Sept 15, so sizes computed against last season's
    # numbers would look authoritative while being guesses. The screen still
    # runs; it is labeled research rather than instructions.
    unresolved_rules = [c for c in checks if not c.satisfied and c.status is RuleStatus.INCOMPLETE]
    if unresolved_rules and orders:
        console.print(
            Panel(
                "[bold]No order sheet.[/bold] "
                + "; ".join(c.detail for c in unresolved_rules)
                + "\n\nThe ranking below is research only. Sizes are withheld because "
                "they would be computed against unconfirmed capital and an "
                "unconfirmed eligible-security list.",
                title="Rules unresolved",
                border_style="yellow",
            )
        )
        rt = Table(title="Candidate ranking (research only, not an order sheet)")
        rt.add_column("Rank", justify="right")
        rt.add_column("Ticker")
        rt.add_column("Score", justify="right")
        rt.add_column("21d momentum", justify="right")
        rt.add_column("RSI14", justify="right")
        rt.add_column("Trend")
        for i, (cand, _) in enumerate(orders, 1):
            rt.add_row(
                str(i),
                cand.symbol,
                f"{cand.score:.0f}",
                f"{cand.momentum_21d * 100:+.1f}%",
                f"{cand.rsi14:.0f}",
                ("above" if cand.above_ema50 else "below") + " EMA50",
            )
        console.print(rt)
        console.print(
            "[dim]Score is a cross-sectional rank from 0 to 100. It is not a "
            "probability and must never be reported as one.[/dim]"
        )
        orders = []

    if orders:
        t = Table(title="Type these into the platform", header_style="bold")
        t.add_column("Ticker")
        t.add_column("Action")
        t.add_column("Shares", justify="right")
        t.add_column("Est. cost", justify="right")
        t.add_column("Stop ref", justify="right")
        t.add_column("Planned risk", justify="right")
        t.add_column("If it gaps 20%", justify="right")
        t.add_column("Bound by")
        for cand, o in orders:
            t.add_row(
                cand.symbol,
                o.action.value,
                f"{o.quantity:,}",
                f"${o.estimated_notional + o.estimated_commission:,.2f}",
                f"${o.protective_reference:,.2f}" if o.protective_reference else "-",
                f"${o.planned_risk:,.2f}",
                f"[red]${o.gap_stress_loss:,.2f}[/red]",
                o.binding_constraint,
            )
        console.print(t)
        console.print(
            "[dim]Planned risk is not maximum loss. The gap column is what the same "
            "position costs on a 20% overnight move, which no stop prevents.[/dim]"
        )
    else:
        console.print("\n[bold]No orders today.[/bold] Nothing cleared every constraint.")

    if blocked:
        bt = Table(title="Screened but blocked", header_style="bold")
        bt.add_column("Ticker")
        bt.add_column("Why", overflow="fold")
        for sym, why in blocked[:12]:
            bt.add_row(sym, why)
        console.print(bt)

    if skipped:
        console.print(f"[dim]{len(skipped)} symbols skipped for short history.[/dim]")

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


journal_app = typer.Typer(help="Your own decision log. Required by both competitions.")
app.add_typer(journal_app, name="journal")


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
    journal = Journal(cfg.journal_path)
    entry = journal.record(facts, reasoning)
    console.print(f"[green]Recorded[/green] {entry.entry_id} to {cfg.journal_path}")


@journal_app.command("list")
def journal_list(limit: int = typer.Option(15)) -> None:
    """Show recent entries and verify the log has not been tampered with."""
    from investlab.journal import Journal

    cfg = cfg_mod.load()
    journal = Journal(cfg.journal_path)
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
    out: Path = typer.Option(Path("runs/evidence_packet.md"), help="Where to write."),
) -> None:
    """Export an evidence packet: your entries plus a citable provenance footer."""
    from investlab.journal import Journal, export_evidence_packet

    cfg = cfg_mod.load()
    journal = Journal(cfg.journal_path)
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


@app.command()
def version() -> None:
    """Show version and provenance."""
    console.print("investlab 0.1.0")
    console.print(f"[dim]run at {now_et().isoformat(timespec='seconds')}[/dim]")


if __name__ == "__main__":
    app()
