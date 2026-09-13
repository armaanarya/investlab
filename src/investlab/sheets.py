"""Saved order sheets.

Every morning's sheet is written to `research/deca/sheets/<fill session>.md`
and `.json`. Two reasons. The markdown is what gets read on a phone before
school. The JSON is what `investlab fill` reads to recover the stop a buy was
sized against, so recording a fill never depends on someone remembering to
copy the stop across.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from investlab.earnings import research_root
from investlab.plan import OrderSheet


def sheets_dir() -> Path:
    return research_root() / "deca" / "sheets"


def save_sheet(sheet: OrderSheet) -> tuple[Path, Path]:
    out = sheets_dir()
    out.mkdir(parents=True, exist_ok=True)
    stem = sheet.fill_session.isoformat()
    json_path = out / f"{stem}.json"
    md_path = out / f"{stem}.md"
    json_path.write_text(json.dumps(sheet.to_dict(), indent=2) + "\n")
    md_path.write_text(sheet_markdown(sheet))
    return json_path, md_path


def load_sheet(session: date) -> dict | None:
    path = sheets_dir() / f"{session.isoformat()}.json"
    return json.loads(path.read_text()) if path.exists() else None


def find_stop(symbol: str, session: date, lookback_days: int = 7) -> tuple[Decimal, date] | None:
    """The stop from the most recent saved sheet, on or before `session`, that
    proposed buying `symbol`."""
    for back in range(lookback_days + 1):
        day = session - timedelta(days=back)
        sheet = load_sheet(day)
        if not sheet:
            continue
        for buy in sheet.get("buys", []):
            if buy["symbol"] == symbol:
                return Decimal(buy["stop"]), day
    return None


def _m(value: Decimal | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def sheet_markdown(sheet: OrderSheet) -> str:
    L: list[str] = []
    freshness = "STALE DATA" if sheet.data_is_stale else "data fresh"
    L += [
        f"# DECA order sheet: fills at the {sheet.fill_session} close",
        "",
        f"Signal close {sheet.signal_session} · equity {_m(sheet.equity)} · cash "
        f"{_m(sheet.cash)} · {freshness} · drawdown stage {sheet.drawdown_stage}",
        "",
        f"Enter orders on {sheet.fill_session} before 4:00 p.m. ET (1:00 p.m. Pacific). "
        "Every order fills at that session's close. Limit orders do not rest.",
        "",
    ]

    if sheet.alerts:
        L += ["## Alerts", ""] + [f"- {a}" for a in sheet.alerts] + [""]

    L += ["## Sell", ""]
    if sheet.sells:
        L += [
            "| Ticker | Shares | Why | Close | Active stop | Est. proceeds | Est. realized |",
            "|---|---:|---|---:|---:|---:|---:|",
        ]
        for s in sheet.sells:
            L.append(
                f"| {s.symbol} | {s.quantity:,} | {s.reason.replace('_', ' ')} | "
                f"{_m(s.reference_close)} | {_m(s.active_stop)} | {_m(s.estimated_proceeds)} | "
                f"{_m(s.estimated_realized)} |"
            )
        L.append("")
        L += [f"- {s.symbol}: {s.detail}" for s in sheet.sells] + [""]
    else:
        L += ["Nothing to sell.", ""]

    L += ["## Compliance buys (DECA diversification)", ""]
    if sheet.compliance:
        L += ["| Leg | Order | Est. cost | Target net cost |", "|---|---|---:|---:|"]
        for c in sheet.compliance:
            order = (
                f"buy {c.quantity:,} {c.symbol} @ ~{_m(c.reference_price)}"
                if c.symbol
                else "buy an SMG-listed bond, BBB or better"
            )
            L.append(
                f"| {c.bucket.replace('_', ' ')} | {order} | {_m(c.estimated_cost)} | "
                f"{_m(c.target_net_cost)} |"
            )
        L.append("")
        for c in sheet.compliance:
            L.append(f"- **{c.bucket.replace('_', ' ')}**: {c.instructions}")
            L += [f"  - alternative: {a}" for a in c.alternatives]
        L.append("")
    else:
        L += ["All three DECA classes are at or above the $10,000 minimum.", ""]

    L += ["## Buy", ""]
    if sheet.buys:
        L += [
            "| Ticker | Shares | Est. cost | Stop | Planned risk | If it gaps 20% "
            "| Bound by | Score |",
            "|---|---:|---:|---:|---:|---:|---|---:|",
        ]
        for b in sheet.buys:
            L.append(
                f"| {b.symbol} | {b.quantity:,} | {_m(b.estimated_cost)} | {_m(b.stop)} | "
                f"{_m(b.planned_risk)} | {_m(b.gap_stress_loss)} | {b.binding_constraint} | "
                f"{b.score:.0f} |"
            )
        L.append("")
        for b in sheet.buys:
            L += [f"- {b.symbol}: {flag}" for flag in b.flags]
        L += [
            "",
            "Score is a 0-100 cross-sectional rank, not a probability. Planned risk is the "
            "loss to the stop; the gap column is the loss on a 20% overnight move.",
            "",
        ]
    else:
        L += ["No new buys today.", ""]

    L += ["## Held positions", ""]
    if sheet.held:
        L += [
            "| Ticker | Shares | Close | Return | Active stop | To stop | Score "
            "| Next earnings | Verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
        for h in sheet.held:
            ret = f"{h.return_pct:+.2f}%" if h.return_pct is not None else "—"
            dist = f"{h.distance_to_stop_pct:.2f}%" if h.distance_to_stop_pct is not None else "—"
            score = f"{h.score:.0f}" if h.score is not None else "—"
            L.append(
                f"| {h.symbol} | {h.quantity:,} | {_m(h.last_close)} | {ret} | "
                f"{_m(h.active_stop)} | {dist} | {score} | {h.next_earnings or '—'} | "
                f"**{h.verdict}** |"
            )
        L.append("")
        for h in sheet.held:
            L += [f"- {h.symbol}: {flag}" for flag in h.flags]
        L.append("")
    else:
        L += ["No open positions.", ""]

    open_rules = [c for c in sheet.rule_checks if not c.satisfied or c.status.value != "verified"]
    if open_rules:
        L += ["## Rules to know", ""]
        L += [f"- **{c.name}** ({c.status.value}): {c.detail}" for c in open_rules]
        L.append("")

    if sheet.blocked:
        L += ["## Screened but blocked", ""]
        L += [f"- {b.symbol}: {b.reason}" for b in sheet.blocked[:12]]
        L.append("")

    if sheet.notes:
        L += ["## Notes", ""] + [f"- {n}" for n in sheet.notes] + [""]

    L += [
        "---",
        "",
        "After trading, send the account statistics and the trades you actually made. The "
        "ledger is updated only from those, never from this sheet. Record your own reasoning "
        "with `investlab journal add`.",
        "",
    ]
    return "\n".join(L)
