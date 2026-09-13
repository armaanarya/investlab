"""Earnings calendar for the DECA order sheet.

An earnings report is the single largest source of the overnight gaps no stop
can prevent. The first sheet of the season proposed ORCL from the Sept 10
close; ORCL reported after that close and filled about 7% below the estimate.

Dates live in a tracked CSV, `research/deca/earnings.csv`, so a lookup made one
morning is still there the next. Two ways in:

- `investlab earnings set`: a date read off the ticker's Finviz quote page (or
  the company's investor relations page). Preferred, and never overwritten by
  an automatic pull.
- `investlab earnings pull`: best-effort from yfinance's earnings calendar,
  which carries a timestamp that tells before-open from after-close reports.

A report's risk lands on a specific session: the first session whose price
reflects it. Before-open (BMO) on day D gaps into D; after-close (AMC) on D gaps
into the next session. An unknown time is treated as both.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from investlab import calendar as cal

RESEARCH_ROOT_ENV = "INVESTLAB_RESEARCH_ROOT"
DEFAULT_RESEARCH_ROOT = Path(__file__).resolve().parents[2] / "research"
EASTERN = ZoneInfo("America/New_York")

COLUMNS = ["symbol", "date", "timing", "source", "recorded_at"]
MANUAL_SOURCES = frozenset({"finviz", "manual", "company"})


def research_root() -> Path:
    override = os.environ.get(RESEARCH_ROOT_ENV)
    return Path(override) if override else DEFAULT_RESEARCH_ROOT


class Timing(str, Enum):
    BMO = "bmo"
    AMC = "amc"
    UNKNOWN = "unknown"


def timing_from_timestamp(ts: datetime) -> Timing:
    """Before 9:30 a.m. ET is before the open; 4:00 p.m. or later is after the
    close. Anything in between (vendors often store placeholders like 3:00 p.m.)
    is unknown rather than guessed."""
    local = ts.astimezone(EASTERN) if ts.tzinfo else ts.replace(tzinfo=EASTERN)
    t = local.timetz().replace(tzinfo=None)
    if t < time(9, 30):
        return Timing.BMO
    if t >= time(16, 0):
        return Timing.AMC
    return Timing.UNKNOWN


@dataclass(frozen=True, slots=True)
class EarningsEvent:
    symbol: str
    date: date
    timing: Timing
    source: str
    recorded_at: str = ""

    @property
    def is_manual(self) -> bool:
        return self.source in MANUAL_SOURCES

    def gap_sessions(self) -> tuple[date, ...]:
        """The session(s) whose close first reflects the report."""
        same = self.date if cal.is_session(self.date) else cal.next_session(self.date)
        after = cal.next_session(self.date)
        if self.timing is Timing.BMO:
            return (same,)
        if self.timing is Timing.AMC:
            return (after,)
        return tuple(sorted({same, after}))

    def label(self) -> str:
        return f"{self.date.isoformat()} {self.timing.value.upper()} ({self.source})"


class EarningsCalendar:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (research_root() / "deca" / "earnings.csv")

    def events(self) -> list[EarningsEvent]:
        if not self.path.exists():
            return []
        with self.path.open(newline="") as fh:
            return [
                EarningsEvent(
                    symbol=r["symbol"],
                    date=date.fromisoformat(r["date"]),
                    timing=Timing(r["timing"]),
                    source=r["source"],
                    recorded_at=r.get("recorded_at", ""),
                )
                for r in csv.DictReader(fh)
            ]

    def _write(self, events: Iterable[EarningsEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(events, key=lambda e: (e.symbol, e.date))
        with self.path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for e in ordered:
                w.writerow(
                    {
                        "symbol": e.symbol,
                        "date": e.date.isoformat(),
                        "timing": e.timing.value,
                        "source": e.source,
                        "recorded_at": e.recorded_at,
                    }
                )

    def set(self, event: EarningsEvent, *, today: date) -> None:
        """Record the next report for a symbol, replacing any other upcoming
        date for it. Past reports stay as history."""
        kept = [
            e
            for e in self.events()
            if not (e.symbol == event.symbol and (e.date >= today or e.date == event.date))
        ]
        self._write([*kept, event])

    def merge_pulled(self, pulled: Iterable[EarningsEvent], *, today: date) -> list[EarningsEvent]:
        """Add automatically pulled dates without overwriting an upcoming date
        someone recorded by hand. Returns the events actually written."""
        current = self.events()
        manual_upcoming = {e.symbol for e in current if e.is_manual and e.date >= today}
        written: list[EarningsEvent] = []
        for event in pulled:
            if event.symbol in manual_upcoming:
                continue
            current = [
                e
                for e in current
                if not (e.symbol == event.symbol and (e.date >= today or e.date == event.date))
            ]
            current.append(event)
            written.append(event)
        self._write(current)
        return written

    def for_symbol(self, symbol: str) -> list[EarningsEvent]:
        return sorted((e for e in self.events() if e.symbol == symbol), key=lambda e: e.date)

    def next_event(self, symbol: str, after: date) -> EarningsEvent | None:
        """The earliest report whose price impact lands after `after`."""
        for e in self.for_symbol(symbol):
            if max(e.gap_sessions()) > after:
                return e
        return None

    def event_in_window(
        self, symbol: str, after_session: date, through_session: date
    ) -> EarningsEvent | None:
        """A report whose gap session falls in (after_session, through_session]."""
        for e in self.for_symbol(symbol):
            if any(after_session < g <= through_session for g in e.gap_sessions()):
                return e
        return None


def fetch_yfinance_events(
    symbols: Iterable[str],
    *,
    today: date,
    ticker_factory: Callable[[str], Any] | None = None,
    recorded_at: str = "",
) -> tuple[list[EarningsEvent], list[str]]:
    """Next report per symbol from yfinance. Returns (events, symbols with none).

    Never raises for one bad symbol: a vendor failure is a missing date, and a
    missing date is flagged on the sheet rather than assumed safe.
    """
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker

    events: list[EarningsEvent] = []
    missing: list[str] = []
    for symbol in symbols:
        try:
            frame = ticker_factory(symbol.replace(".", "-")).get_earnings_dates(limit=8)
        except Exception:  # noqa: BLE001 - vendor failure is a missing date
            frame = None
        upcoming: list[datetime] = []
        if frame is not None and len(frame.index):
            for ts in frame.index:
                py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                local = py.astimezone(EASTERN) if py.tzinfo else py
                if local.date() >= today:
                    upcoming.append(py)
        if not upcoming:
            missing.append(symbol)
            continue
        nxt = min(upcoming)
        local_date = (nxt.astimezone(EASTERN) if nxt.tzinfo else nxt).date()
        events.append(
            EarningsEvent(
                symbol=symbol,
                date=local_date,
                timing=timing_from_timestamp(nxt),
                source="yfinance",
                recorded_at=recorded_at,
            )
        )
    return events, missing
