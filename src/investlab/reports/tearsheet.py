"""Tear sheet for a backtest: HTML plus CSV and JSON, with a provenance footer.

Deliberately reported:

- Gross and net return over the window. Never annualised: a twelve-week game
  does not have an annual return.
- Return against the S&P 500 buy-and-hold, which is what DECA ranks on.
- Maximum drawdown depth and its duration in sessions.
- Average exposure, turnover, and what commissions and sell fees cost.
- Every round trip with its maximum adverse and favorable excursion (MAE/MFE)
  measured on closes, and how far below the stop each stop exit filled.

No Sharpe ratio: a few dozen daily observations cannot estimate one.
"""

from __future__ import annotations

import base64
import csv
import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from investlab.backtest.engine import BacktestSettings, CloseBook, RunResult
from investlab.performance import round_trips_from_trades

TWO = Decimal("0.01")


def _pct(part: Decimal, whole: Decimal) -> float:
    return float((part / whole * Decimal(100)).quantize(TWO)) if whole else 0.0


def max_drawdown(equity: list[Decimal]) -> tuple[float, int]:
    """(deepest peak-to-trough fall in percent, longest sessions spent below a
    prior peak)."""
    peak = None
    worst = Decimal(0)
    longest = current = 0
    for value in equity:
        if peak is None or value >= peak:
            peak = value
            current = 0
        else:
            current += 1
            longest = max(longest, current)
            worst = max(worst, (peak - value) / peak * Decimal(100))
    return float(worst.quantize(TWO)), longest


def round_trip_rows(result: RunResult, closes: CloseBook) -> list[dict[str, Any]]:
    trips, _ = round_trips_from_trades([t.as_row() for t in result.trades])
    rows = []
    for trip in trips:
        path = closes.between(trip.symbol, trip.bought_on, trip.sold_on) or [trip.buy_price]
        mae = (min(path) / trip.buy_price - 1) * 100
        mfe = (max(path) / trip.buy_price - 1) * 100
        rows.append(
            {
                "symbol": trip.symbol,
                "quantity": trip.quantity,
                "bought_on": trip.bought_on.isoformat(),
                "buy_price": str(trip.buy_price),
                "sold_on": trip.sold_on.isoformat(),
                "sell_price": str(trip.sell_price),
                "realized": str(trip.realized),
                "return_pct": float(trip.return_pct),
                "mae_pct": float(Decimal(mae).quantize(TWO)),
                "mfe_pct": float(Decimal(mfe).quantize(TWO)),
                "days_held": trip.days_held,
            }
        )
    return rows


def summarize(result: RunResult, benchmark: RunResult | None, closes: CloseBook) -> dict[str, Any]:
    start = result.starting_cash
    final = result.equity[-1] if result.equity else start
    total = _pct(final - start, start)
    gross = _pct(final + result.costs - start, start)
    bench = None
    if benchmark is not None and benchmark.equity:
        bench = _pct(benchmark.equity[-1] - benchmark.starting_cash, benchmark.starting_cash)
    dd, dd_len = max_drawdown([start, *result.equity])
    exposure = (
        float(
            (
                sum(
                    (i / e for i, e in zip(result.invested, result.equity, strict=True) if e),
                    Decimal(0),
                )
                / Decimal(len(result.equity))
                * 100
            ).quantize(TWO)
        )
        if result.equity
        else 0.0
    )
    notional = sum((t.price * Decimal(t.quantity) for t in result.trades), Decimal(0))
    avg_equity = (
        sum(result.equity, Decimal(0)) / Decimal(len(result.equity)) if result.equity else start
    )
    trips = round_trip_rows(result, closes)
    wins = [r for r in trips if Decimal(r["realized"]) > 0]
    stop_sells = [t for t in result.trades if t.action == "sell" and "stop" in t.reason]
    return {
        "name": result.name,
        "sessions": len(result.sessions),
        "final_equity": str(final),
        "total_return_pct": total,
        "gross_return_pct": gross,
        "benchmark_return_pct": bench,
        "excess_return_pct": round(total - bench, 2) if bench is not None else None,
        "max_drawdown_pct": dd,
        "max_drawdown_sessions": dd_len,
        "avg_exposure_pct": exposure,
        "turnover_pct": _pct(notional, avg_equity),
        "trades": len(result.trades),
        "costs": str(result.costs),
        "cost_drag_pct": _pct(result.costs, start),
        "round_trips": len(trips),
        "win_rate_pct": round(100 * len(wins) / len(trips), 1) if trips else None,
        "avg_mae_pct": round(sum(r["mae_pct"] for r in trips) / len(trips), 2) if trips else None,
        "avg_mfe_pct": round(sum(r["mfe_pct"] for r in trips) / len(trips), 2) if trips else None,
        "stop_exits": len(stop_sells),
        "notes": result.notes,
    }


def _chart_png(runs: list[RunResult]) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5), dpi=120)
    for run in runs:
        if run.sessions:
            ax.plot(
                run.sessions,
                [float(v) for v in run.equity],
                label=run.name,
                linewidth=2.2 if run.name == "strategy" else 1.2,
            )
    ax.set_ylabel("Equity ($)")
    ax.set_title("DECA order sheet replay vs baselines (not annualised)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def write_tearsheet(
    out_dir: Path,
    strategy: RunResult,
    baselines: list[RunResult],
    *,
    closes: CloseBook,
    settings: BacktestSettings,
    provenance: dict[str, str],
) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = [strategy, *baselines]
    bench = next((b for b in baselines if b.name == f"buy_and_hold_{settings.benchmark}"), None)
    summaries = [summarize(r, bench, closes) for r in runs]

    summary = {
        "window": {"start": settings.start.isoformat(), "end": settings.end.isoformat()},
        "benchmark": settings.benchmark,
        "runs": summaries,
        "provenance": provenance,
        "limitations": strategy.notes,
    }
    paths = {
        "summary": out_dir / "summary.json",
        "equity": out_dir / "equity.csv",
        "trades": out_dir / "trades.csv",
        "round_trips": out_dir / "round_trips.csv",
        "chart": out_dir / "equity.png",
        "html": out_dir / "report.html",
    }
    paths["summary"].write_text(json.dumps(summary, indent=2) + "\n")

    with paths["equity"].open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session", *[r.name for r in runs]])
        for i, d in enumerate(strategy.sessions):
            w.writerow(
                [d.isoformat(), *[str(r.equity[i]) if i < len(r.equity) else "" for r in runs]]
            )

    with paths["trades"].open("w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "session",
                "symbol",
                "action",
                "quantity",
                "price",
                "commission",
                "fees",
                "asset_class",
                "reason",
            ],
        )
        w.writeheader()
        for t in strategy.trades:
            w.writerow(t.as_row())

    trips = round_trip_rows(strategy, closes)
    with paths["round_trips"].open("w", newline="") as fh:
        fields = [
            "symbol",
            "quantity",
            "bought_on",
            "buy_price",
            "sold_on",
            "sell_price",
            "realized",
            "return_pct",
            "mae_pct",
            "mfe_pct",
            "days_held",
        ]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in trips:
            w.writerow(row)

    png = _chart_png(runs)
    paths["chart"].write_bytes(png)

    def cell(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    cols = [
        "name",
        "final_equity",
        "total_return_pct",
        "gross_return_pct",
        "excess_return_pct",
        "max_drawdown_pct",
        "max_drawdown_sessions",
        "avg_exposure_pct",
        "turnover_pct",
        "trades",
        "costs",
        "win_rate_pct",
        "avg_mae_pct",
        "avg_mfe_pct",
        "stop_exits",
    ]
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{cell(s[c])}</td>" for c in cols) + "</tr>" for s in summaries
    )
    notes_html = "".join(f"<li>{n}</li>" for n in strategy.notes)
    prov_html = "".join(f"<li><b>{k}</b>: {v}</li>" for k, v in provenance.items())
    css = (
        "body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;"
        "padding:0 1rem;color:#1d1d1f}"
        "table{border-collapse:collapse;font-size:13px;width:100%}"
        "td,th{border:1px solid #ddd;padding:4px 6px;text-align:right}"
        "td:first-child,th:first-child{text-align:left}th{background:#f4f4f5}"
        "img{max-width:100%}footer{font-size:12px;color:#555;margin-top:2rem}"
    )
    header_html = "".join(f"<th>{c}</th>" for c in cols)
    chart_src = "data:image/png;base64," + base64.b64encode(png).decode()
    html = "\n".join(
        [
            '<!doctype html><html><head><meta charset="utf-8">',
            f"<title>investlab backtest</title><style>{css}</style></head><body>",
            f"<h1>DECA order sheet replay, {settings.start} to {settings.end}</h1>",
            "<p>Returns are over the window and are <b>not annualised</b>. The benchmark is "
            f"buy-and-hold {settings.benchmark}.</p>",
            f'<img alt="Equity curves" src="{chart_src}">',
            '<div style="overflow-x:auto"><table>',
            f"<tr>{header_html}</tr>{rows_html}</table></div>",
            f"<h2>What this replay cannot see</h2><ul>{notes_html}</ul>",
            f"<footer><h3>Provenance</h3><ul>{prov_html}</ul>",
            "<p>Generated by investlab. Figures are computed from the cached price data named "
            "above; any analysis built on them is the student's own. The fixed universe is "
            "survivorship-biased and must be disclosed.</p></footer>",
            "</body></html>",
        ]
    )
    paths["html"].write_text(html)
    return paths
