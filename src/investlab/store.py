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
from decimal import Decimal
from pathlib import Path
from typing import Any

from investlab.contracts import AccountState, AssetClass, Lot, Position

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
        """Regenerate LEDGER.md. Written for an agent picking this up cold."""
        book = self.load_book()
        starting = Decimal(str(book.get("starting_cash", account.cash)))
        rows = self.trades()

        name = "DECA Stock Market Game" if self.profile == "deca" else "Wharton WInS"
        lines = [
            f"# {name} ledger",
            "",
            "Generated by `investlab`. Do not edit by hand: it is rewritten on every",
            "recorded fill. The authoritative record is the competition platform;",
            "this mirrors it.",
            "",
            f"- **As of:** {account.as_of}",
            f"- **Cash:** ${account.cash:,.2f}",
            f"- **Equity:** ${account.equity:,.2f}",
            f"- **Starting capital:** ${starting:,.2f}",
        ]

        if starting:
            pnl = account.equity - starting
            pct = (pnl / starting) * Decimal(100)
            lines.append(f"- **P&L:** ${pnl:,.2f} ({pct:+.2f}%)")
        lines += [f"- **Trades recorded:** {len(rows)}", ""]

        lines += ["## Positions", ""]
        if account.positions:
            lines += [
                "| Symbol | Class | Shares | Net cost | Mark | Value |",
                "|---|---|---:|---:|---:|---:|",
            ]
            for pos in sorted(account.positions, key=lambda p: p.symbol):
                mark = account.marks.get(pos.symbol, Decimal("0"))
                lines.append(
                    f"| {pos.symbol} | {pos.asset_class.value} | {pos.quantity:,} | "
                    f"${pos.net_cost:,.2f} | ${mark:,.2f} | "
                    f"${mark * Decimal(pos.quantity):,.2f} |"
                )
        else:
            lines.append("No open positions.")
        lines.append("")

        if self.profile == "deca":
            lines += ["## Asset class totals (DECA diversification)", ""]
            lines += [
                "DECA counts every ETF as a **stock**, bond ETFs included, and bond",
                "mutual funds as **mutual funds**. Measured on net cost at purchase,",
                "not market value.",
                "",
                "| Class | Net cost | $10,000 minimum |",
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
                if klass is AssetClass.ETF:
                    status = "counts toward stocks"
                else:
                    status = "met" if total >= Decimal("10000") else "**NOT MET**"
                lines.append(f"| {label} | ${total:,.2f} | {status} |")
            lines.append("")

        if rule_lines:
            lines += ["## Rule status", ""] + [f"- {line}" for line in rule_lines] + [""]

        lines += ["## Trade history", ""]
        if rows:
            lines += [
                "| Session | Symbol | Action | Shares | Price | Commission | Cash after |",
                "|---|---|---|---:|---:|---:|---:|",
            ]
            for row in rows:
                lines.append(
                    f"| {row['session']} | {row['symbol']} | {row['action']} | "
                    f"{int(row['quantity']):,} | ${Decimal(row['price']):,.2f} | "
                    f"${Decimal(row['commission']):,.2f} | "
                    f"${Decimal(row['cash_after']):,.2f} |"
                )
        else:
            lines.append("No trades recorded yet.")
        lines += ["", "---", ""]
        lines.append(
            "Reasoning for each trade lives in `journal.jsonl` and is the student's "
            "own work, stored verbatim. It is never generated."
        )
        if generated:
            lines.append(f"\n<sub>Generated {generated}</sub>")

        text = "\n".join(lines) + "\n"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(text)
        return text
