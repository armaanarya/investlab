"""Per-competition ledger store, committed to the repository.

Each competition gets its own directory under `ledger/`. They are separate on
purpose: DECA and Wharton have different capital, different rules, different
asset-class definitions, and deliberately different strategies. A single
combined book would let a Wharton position quietly satisfy a DECA requirement,
which is the exact mistake that gets a team disqualified.

Each directory holds four files:

    portfolio.json   machine state: cash, positions, lots. The source of truth.
    trades.csv       append-only log of every fill, oldest first.
    journal.jsonl    the student's own reasoning, hash-chained, append-only.
    LEDGER.md        generated summary any human or agent can read at a glance.

These are tracked in git, so the history of the competition is the history of
the repository. `LEDGER.md` is regenerated on every write and is never edited
by hand: edit the JSON or append a trade, and the summary follows.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from investlab.contracts import AccountState, AssetClass, Lot, Position
from investlab.performance import (
    EquityPoint,
    Performance,
    open_positions_from_account,
    round_trips_from_trades,
)

DEFAULT_LEDGER_ROOT = Path(__file__).resolve().parents[2] / "ledger"

# Overridable so a test run can never write into the real, git-tracked book.
# A suite that appended live trades to the competition ledger is not a
# hypothetical: it happened once, and this is the fix.
LEDGER_ROOT_ENV = "INVESTLAB_LEDGER_ROOT"


def ledger_root() -> Path:
    import os

    override = os.environ.get(LEDGER_ROOT_ENV)
    return Path(override) if override else DEFAULT_LEDGER_ROOT


TRADE_COLUMNS = [
    "session",
    "recorded_at",
    "symbol",
    "action",
    "quantity",
    "price",
    "commission",
    "fees",
    "asset_class",
    "cash_after",
    "note",
]


CASH_EVENT_COLUMNS = ["session", "kind", "symbol", "amount", "cash_after", "recorded_at", "note"]
# DECA credits 0.75%/yr interest on positive cash, posted Saturdays, and pays
# dividends to cash. Neither is a trade, and both break reconciliation unless
# recorded.
CASH_EVENT_KINDS = ("interest", "dividend", "fee", "other")
SPLIT_COLUMNS = ["effective", "symbol", "ratio", "recorded_at"]


@dataclass(frozen=True, slots=True)
class TradeRecord:
    session: date
    recorded_at: str
    symbol: str
    action: str
    quantity: int
    price: Decimal
    commission: Decimal
    fees: Decimal
    asset_class: str
    cash_after: Decimal
    note: str = ""

    def as_row(self) -> dict[str, str]:
        return {
            "session": self.session.isoformat(),
            "recorded_at": self.recorded_at,
            "symbol": self.symbol,
            "action": self.action,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "commission": str(self.commission),
            "fees": str(self.fees),
            "asset_class": self.asset_class,
            "cash_after": str(self.cash_after),
            "note": self.note,
        }


class LedgerStore:
    """Everything persisted for one competition."""

    def __init__(self, profile: str, root: Path | None = None) -> None:
        if profile not in {"deca", "wharton"}:
            raise ValueError(f"unknown profile {profile!r}; expected 'deca' or 'wharton'")
        self.profile = profile
        self.dir = (root or ledger_root()) / profile

    # -- paths -------------------------------------------------------------

    @property
    def portfolio_path(self) -> Path:
        return self.dir / "portfolio.json"

    @property
    def trades_path(self) -> Path:
        return self.dir / "trades.csv"

    @property
    def journal_path(self) -> Path:
        return self.dir / "journal.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.dir / "LEDGER.md"

    @property
    def equity_path(self) -> Path:
        return self.dir / "equity.csv"

    @property
    def risk_reviews_path(self) -> Path:
        return self.dir / "risk_reviews.jsonl"

    # -- stops -------------------------------------------------------------

    def entry_stops(self) -> dict[str, Decimal | None]:
        """The entry stop recorded on each position's lots. A position with
        several lots uses the highest recorded stop, the most protective one."""
        out: dict[str, Decimal | None] = {}
        if not self.exists():
            return out
        for entry in self.load_book().get("positions", []):
            stops = [Decimal(str(lot["stop"])) for lot in entry["lots"] if lot.get("stop")]
            out[entry["symbol"]] = max(stops) if stops else None
        return out

    def set_stop(self, symbol: str, stop: Decimal) -> int:
        """Record `stop` on every open lot of `symbol`. Returns lots updated."""
        book = self.load_book()
        updated = 0
        for entry in book.get("positions", []):
            if entry["symbol"] != symbol:
                continue
            for lot in entry["lots"]:
                lot["stop"] = str(stop)
                updated += 1
        if updated:
            self.save_book(book)
        return updated

    # -- drawdown reviews --------------------------------------------------

    def risk_reviews(self) -> list[tuple[date, str]]:
        if not self.risk_reviews_path.exists():
            return []
        out = []
        for line in self.risk_reviews_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                out.append((date.fromisoformat(row["session"]), row["note"]))
        return out

    def add_risk_review(self, session: date, note: str, recorded_at: str) -> None:
        if not note.strip():
            raise ValueError("a drawdown review needs the student's own note")
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.risk_reviews_path.open("a") as fh:
            fh.write(
                json.dumps(
                    {"session": session.isoformat(), "note": note, "recorded_at": recorded_at}
                )
                + "\n"
            )

    # -- cash events and splits --------------------------------------------

    @property
    def cash_events_path(self) -> Path:
        return self.dir / "cash_events.csv"

    @property
    def splits_path(self) -> Path:
        return self.dir / "splits.csv"

    @staticmethod
    def _rows(path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        with path.open(newline="") as fh:
            return list(csv.DictReader(fh))

    def _append(self, path: Path, columns: list[str], row: dict[str, str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    def cash_events(self) -> list[dict[str, str]]:
        return self._rows(self.cash_events_path)

    def add_cash_event(
        self,
        session: date,
        kind: str,
        amount: Decimal,
        *,
        symbol: str = "",
        note: str = "",
        recorded_at: str = "",
    ) -> Decimal:
        """Move cash without a trade. Returns the new cash balance."""
        if kind not in CASH_EVENT_KINDS:
            raise ValueError(f"kind must be one of {', '.join(CASH_EVENT_KINDS)}, got {kind!r}")
        if amount == 0:
            raise ValueError("a cash event of zero records nothing")
        book = self.load_book()
        cash = Decimal(str(book["cash"])) + amount
        book["cash"] = str(cash)
        self.save_book(book)
        self._append(
            self.cash_events_path,
            CASH_EVENT_COLUMNS,
            {
                "session": session.isoformat(),
                "kind": kind,
                "symbol": symbol,
                "amount": str(amount),
                "cash_after": str(cash),
                "recorded_at": recorded_at,
                "note": note,
            },
        )
        return cash

    def splits(self) -> list[dict[str, str]]:
        return self._rows(self.splits_path)

    def apply_split(
        self, symbol: str, ratio: Decimal, effective: date, *, recorded_at: str = ""
    ) -> int:
        """Restate every lot of `symbol` in post-split shares: quantity times
        `ratio` (rounded down), cost per share and entry stop divided by it.
        Returns lots updated; 0 when the symbol is not held."""
        if ratio <= 0:
            raise ValueError(f"split ratio must be positive, got {ratio}")
        book = self.load_book()
        updated = 0
        for entry in book.get("positions", []):
            if entry["symbol"] != symbol:
                continue
            for lot in entry["lots"]:
                scaled = Decimal(lot["quantity"]) * ratio
                lot["quantity"] = int(scaled.to_integral_value(rounding=ROUND_FLOOR))
                lot["price"] = str(
                    (Decimal(str(lot["price"])) / ratio).quantize(Decimal("0.000001"))
                )
                if lot.get("stop"):
                    lot["stop"] = str((Decimal(str(lot["stop"])) / ratio).quantize(Decimal("0.01")))
                updated += 1
            entry["lots"] = [lot for lot in entry["lots"] if lot["quantity"] > 0]
        if not updated:
            return 0
        book["positions"] = [e for e in book["positions"] if e["lots"]]
        self.save_book(book)
        self._append(
            self.splits_path,
            SPLIT_COLUMNS,
            {
                "effective": effective.isoformat(),
                "symbol": symbol,
                "ratio": str(ratio),
                "recorded_at": recorded_at,
            },
        )
        return updated

    def adjusted_trades(self) -> list[dict[str, str]]:
        """The trade log restated in post-split shares, the units the price
        cache and the platform use once a split has happened. `trades.csv`
        itself is never rewritten."""
        rows = [dict(r) for r in self.trades()]
        for split in self.splits():
            effective = date.fromisoformat(split["effective"])
            ratio = Decimal(split["ratio"])
            for r in rows:
                if r["symbol"] == split["symbol"] and date.fromisoformat(r["session"]) < effective:
                    scaled = Decimal(r["quantity"]) * ratio
                    r["quantity"] = str(int(scaled.to_integral_value(rounding=ROUND_FLOOR)))
                    r["price"] = str(Decimal(r["price"]) / ratio)
        return rows

    # -- replay ------------------------------------------------------------

    def holdings_at(self, session: date) -> tuple[Decimal, dict[str, int], dict[str, AssetClass]]:
        """Cash and share counts after every trade and cash event recorded on
        or before `session`, replayed from the starting cash. Used to backfill
        an equity snapshot for a past session."""
        book = self.load_book()
        cash = Decimal(str(book["starting_cash"]))
        for event in self.cash_events():
            if date.fromisoformat(event["session"]) <= session:
                cash += Decimal(event["amount"])
        shares: dict[str, int] = {}
        classes: dict[str, AssetClass] = {}
        for row in self.adjusted_trades():
            if date.fromisoformat(row["session"]) > session:
                continue
            qty = int(row["quantity"])
            px = Decimal(row["price"])
            comm = Decimal(row["commission"]) + Decimal(row.get("fees") or "0")
            sym = row["symbol"]
            classes[sym] = AssetClass(row["asset_class"])
            if row["action"] == "buy":
                cash -= px * qty + comm
                shares[sym] = shares.get(sym, 0) + qty
            else:
                cash += px * qty - comm
                shares[sym] = shares.get(sym, 0) - qty
        return cash, {s: q for s, q in shares.items() if q}, classes

    # -- equity curve ------------------------------------------------------

    def record_equity(
        self, session: date, equity: Decimal, cash: Decimal, benchmark: Decimal | None
    ) -> None:
        """Append or replace today's equity snapshot.

        One row per session. Re-running on the same day overwrites rather than
        appends, so a curve is never distorted by how many times the tool was
        run that day.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        rows = {r["session"]: r for r in self.equity_curve_rows()}
        rows[session.isoformat()] = {
            "session": session.isoformat(),
            "equity": str(equity.quantize(Decimal("0.01"))),
            "cash": str(cash.quantize(Decimal("0.01"))),
            "benchmark": str(benchmark) if benchmark is not None else "",
        }
        with self.equity_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["session", "equity", "cash", "benchmark"])
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])

    def equity_curve_rows(self) -> list[dict[str, str]]:
        if not self.equity_path.exists():
            return []
        with self.equity_path.open(newline="") as fh:
            return list(csv.DictReader(fh))

    def equity_curve(self) -> list[EquityPoint]:
        points = []
        for row in self.equity_curve_rows():
            bench = row.get("benchmark") or ""
            points.append(
                EquityPoint(
                    session=date.fromisoformat(row["session"]),
                    equity=Decimal(row["equity"]),
                    benchmark=Decimal(bench) if bench else None,
                )
            )
        return points

    def performance(self, account: AccountState) -> Performance:
        """Assemble the full performance picture from what is on disk."""
        book = self.load_book()
        trips, commissions = round_trips_from_trades(self.adjusted_trades())
        return Performance(
            starting_capital=Decimal(str(book.get("starting_cash", account.cash))),
            account=account,
            open_positions=open_positions_from_account(account),
            round_trips=trips,
            equity_curve=self.equity_curve(),
            commissions_paid=commissions,
        )

    # -- portfolio ---------------------------------------------------------

    def exists(self) -> bool:
        return self.portfolio_path.exists()

    def init(self, starting_cash: Decimal, as_of: date) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.save_book(
            {
                "profile": self.profile,
                "as_of": as_of.isoformat(),
                "starting_cash": str(starting_cash),
                "cash": str(starting_cash),
                "positions": [],
            }
        )
        if not self.trades_path.exists():
            with self.trades_path.open("w", newline="") as fh:
                csv.DictWriter(fh, fieldnames=TRADE_COLUMNS).writeheader()

    def load_book(self) -> dict[str, Any]:
        return json.loads(self.portfolio_path.read_text())

    def save_book(self, book: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.portfolio_path.write_text(json.dumps(book, indent=2) + "\n")

    def account(self, marks: dict[str, Decimal] | None = None) -> AccountState:
        """Build an AccountState from the stored book.

        Positions are marked at cost when no mark is supplied, which keeps this
        usable offline. Callers that have prices should always pass them.
        """
        book = self.load_book()
        positions: list[Position] = []
        resolved: dict[str, Decimal] = {}
        for entry in book.get("positions", []):
            lots = tuple(
                Lot(
                    symbol=entry["symbol"],
                    quantity=int(lot["quantity"]),
                    price=Decimal(str(lot["price"])),
                    commission=Decimal(str(lot.get("commission", 0))),
                    opened=date.fromisoformat(lot["opened"]),
                )
                for lot in entry["lots"]
            )
            positions.append(
                Position(
                    symbol=entry["symbol"],
                    asset_class=AssetClass(entry["asset_class"]),
                    lots=lots,
                )
            )
            supplied = (marks or {}).get(entry["symbol"])
            resolved[entry["symbol"]] = (
                supplied if supplied is not None else Decimal(str(entry["lots"][0]["price"]))
            )
        return AccountState(
            as_of=date.fromisoformat(book["as_of"]),
            cash=Decimal(str(book["cash"])),
            positions=tuple(positions),
            marks=resolved,
        )

    # -- trades ------------------------------------------------------------

    def append_trade(self, record: TradeRecord) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        new_file = not self.trades_path.exists()
        with self.trades_path.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=TRADE_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(record.as_row())

    def trades(self) -> list[dict[str, str]]:
        if not self.trades_path.exists():
            return []
        with self.trades_path.open(newline="") as fh:
            return list(csv.DictReader(fh))

    # -- summary -----------------------------------------------------------

    def render_summary(
        self,
        account: AccountState,
        *,
        rule_lines: list[str] | None = None,
        generated: str = "",
    ) -> str:
        """Write LEDGER.md: the full performance report for this competition.

        One file, regenerated on every write, written for someone arriving with
        no context. Deliberately not annualised: a twelve-week game does not
        have an annual return, and presenting one is how a student report ends
        up overstating itself.
        """
        perf = self.performance(account)
        name = "DECA Stock Market Game" if self.profile == "deca" else "Wharton WInS"
        bench_label = "S&P 500 (SPY)"
        L: list[str] = []

        L += [
            f"# {name} — performance report",
            "",
            "Generated by `investlab`, rewritten on every recorded trade. Do not edit",
            "by hand. The competition platform is the authoritative record; this",
            "mirrors it. Returns are period returns over the competition window and",
            "are **not annualised**.",
            "",
            f"**As of {account.as_of}**",
            "",
            "## Headline",
            "",
            "| | |",
            "|---|---:|",
            f"| Equity | **${perf.equity:,.2f}** |",
            f"| Started with | ${perf.starting_capital:,.2f} |",
            f"| Total P&L | **${perf.total_pnl:,.2f} ({perf.total_return_pct:+.2f}%)** |",
            f"| Realised (closed trades) | ${perf.realized_pnl:,.2f} |",
            f"| Unrealised (open positions) | ${perf.unrealized_pnl:,.2f} |",
            f"| Cash | ${account.cash:,.2f} ({perf.cash_pct:.1f}% of equity) |",
            f"| Invested at cost | ${perf.invested:,.2f} |",
            f"| Commissions paid | ${perf.commissions_paid:,.2f} |",
            f"| Trades recorded | {len(self.trades())} |",
        ]
        if perf.equity_curve:
            L.append(f"| Max drawdown (recorded) | {perf.max_drawdown_pct:.2f}% |")
        L.append("")

        bench = perf.benchmark_return_pct
        if bench is not None:
            excess = perf.excess_return_pct
            L += [
                f"### Versus {bench_label}",
                "",
                "DECA ranks on percent return against S&P 500 growth, so this line is"
                if self.profile == "deca"
                else "For context only: Wharton judges strategy and articulation, not returns.",
                "the one that decides whether you qualify." if self.profile == "deca" else "",
                "",
                f"- Benchmark over the same window: **{bench:+.2f}%**",
                f"- Excess return: **{excess:+.2f}%**" if excess is not None else "",
                "",
            ]
        elif len(perf.equity_curve) < 2:
            L += [
                f"### Versus {bench_label}",
                "",
                "Not enough history yet. The comparison needs at least two recorded",
                "sessions; run `investlab snapshot` on each trading day to build it.",
                "",
            ]

        L += ["## Open positions", ""]
        if perf.open_positions:
            stops = self.entry_stops()
            L += [
                "| Symbol | Class | Shares | Avg cost | Mark | Cost basis | Value | "
                "Unrealised | Return | Days | Entry stop |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
            for p in perf.open_positions:
                stop = stops.get(p.symbol)
                L.append(
                    f"| {p.symbol} | {p.asset_class} | {p.quantity:,} | ${p.avg_cost:,.2f} | "
                    f"${p.mark:,.2f} | ${p.cost_basis:,.2f} | ${p.market_value:,.2f} | "
                    f"${p.unrealized:,.2f} | {p.return_pct:+.2f}% | {p.days_held} | "
                    f"{f'${stop:,.2f}' if stop is not None else '—'} |"
                )
        else:
            L.append("None.")
        L.append("")

        L += ["## Closed trades", ""]
        if perf.round_trips:
            L += [
                "| Symbol | Shares | Bought | At | Sold | At | Fees | Realised | Return | Days |",
                "|---|---:|---|---:|---|---:|---:|---:|---:|---:|",
            ]
            for t in perf.round_trips:
                L.append(
                    f"| {t.symbol} | {t.quantity:,} | {t.bought_on} | ${t.buy_price:,.2f} | "
                    f"{t.sold_on} | ${t.sell_price:,.2f} | ${t.commissions:,.2f} | "
                    f"${t.realized:,.2f} | {t.return_pct:+.2f}% | {t.days_held} |"
                )
            L.append("")
            rate = perf.win_rate_pct
            L += [
                f"- Closed: {len(perf.round_trips)} · winners {len(perf.wins)} · "
                f"losers {len(perf.losses)}"
                + (f" · win rate {rate:.0f}%" if rate is not None else ""),
            ]
            if perf.best:
                b = perf.best
                L.append(f"- Best: **{b.symbol}** ${b.realized:,.2f} ({b.return_pct:+.2f}%)")
            if perf.worst and perf.worst is not perf.best:
                w = perf.worst
                L.append(f"- Worst: **{w.symbol}** ${w.realized:,.2f} ({w.return_pct:+.2f}%)")
        else:
            L.append("None yet. Nothing has been sold.")
        L.append("")

        if self.profile == "deca":
            L += [
                "## DECA diversification",
                "",
                "Measured on **net cost at purchase**, not market value. Every ETF counts",
                "as a stock, bond ETFs included; bond mutual funds count as mutual funds.",
                "A decline below the minimum needs no action; a **sale** starts a",
                "one-business-day clock.",
                "",
                "| Class | Net cost | $10,000 minimum by 2026-10-23 |",
                "|---|---:|---|",
            ]
            for klass, label in (
                (AssetClass.STOCK, "stocks"),
                (AssetClass.ETF, "ETFs (count as stocks)"),
                (AssetClass.MUTUAL_FUND, "mutual funds"),
                (AssetClass.BOND, "bonds"),
            ):
                total = sum(
                    (p.net_cost for p in account.positions if p.asset_class is klass),
                    Decimal("0"),
                )
                status = (
                    "counts toward stocks"
                    if klass is AssetClass.ETF
                    else ("met" if total >= Decimal("10000") else "**NOT MET**")
                )
                L.append(f"| {label} | ${total:,.2f} | {status} |")
            L.append("")

        if rule_lines:
            L += ["## Rule status", ""] + [f"- {line}" for line in rule_lines] + [""]

        curve = perf.equity_curve
        L += ["## Equity curve", ""]
        if curve:
            L += ["| Session | Equity | Benchmark |", "|---|---:|---:|"]
            for point in curve[-30:]:
                b = f"${point.benchmark:,.2f}" if point.benchmark is not None else "—"
                L.append(f"| {point.session} | ${point.equity:,.2f} | {b} |")
            if len(curve) > 30:
                L.append(
                    f"\n<sub>Showing the last 30 of {len(curve)} sessions. "
                    "Full history in `equity.csv`.</sub>"
                )
        else:
            L.append("No snapshots yet. Run `investlab snapshot` on each trading day.")
        L.append("")

        events = self.cash_events()
        if events:
            L += [
                "## Cash events (interest, dividends, fees)",
                "",
                "| Session | Kind | Symbol | Amount | Cash after |",
                "|---|---|---|---:|---:|",
            ]
            for e in events:
                L.append(
                    f"| {e['session']} | {e['kind']} | {e['symbol'] or '—'} | "
                    f"${Decimal(e['amount']):,.2f} | ${Decimal(e['cash_after']):,.2f} |"
                )
            L.append("")

        L += ["## All recorded trades", ""]
        rows = self.trades()
        if rows:
            L += [
                "| Session | Symbol | Action | Shares | Price | Commission | Cash after |",
                "|---|---|---|---:|---:|---:|---:|",
            ]
            for row in rows:
                L.append(
                    f"| {row['session']} | {row['symbol']} | {row['action']} | "
                    f"{int(row['quantity']):,} | ${Decimal(row['price']):,.2f} | "
                    f"${Decimal(row['commission']):,.2f} | "
                    f"${Decimal(row['cash_after']):,.2f} |"
                )
        else:
            L.append("None yet.")

        L += [
            "",
            "---",
            "",
            "Reasoning for each trade lives in `journal.jsonl` and is the student's own",
            "work, stored verbatim and hash-chained. It is never generated.",
        ]
        if generated:
            L.append(f"\n<sub>Generated {generated}</sub>")

        text = "\n".join(line for line in L if line is not None) + "\n"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(text)
        return text
