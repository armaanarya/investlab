from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from investlab.earnings import (
    EASTERN,
    EarningsCalendar,
    EarningsEvent,
    Timing,
    fetch_yfinance_events,
    timing_from_timestamp,
)


def test_timing_from_timestamp():
    assert timing_from_timestamp(datetime(2026, 9, 10, 16, 0, tzinfo=EASTERN)) is Timing.AMC
    assert timing_from_timestamp(datetime(2026, 10, 30, 6, 0, tzinfo=EASTERN)) is Timing.BMO
    assert timing_from_timestamp(datetime(2026, 12, 10, 15, 0, tzinfo=EASTERN)) is Timing.UNKNOWN
    assert timing_from_timestamp(datetime(2026, 9, 10, 20, 0, tzinfo=UTC)) is Timing.AMC


def test_gap_sessions():
    assert EarningsEvent("ORCL", date(2026, 9, 10), Timing.AMC, "finviz").gap_sessions() == (
        date(2026, 9, 11),
    )
    # A before-open report dated on a Saturday first shows up on Monday.
    assert EarningsEvent("X", date(2026, 9, 12), Timing.BMO, "finviz").gap_sessions() == (
        date(2026, 9, 14),
    )
    assert EarningsEvent("X", date(2026, 9, 10), Timing.UNKNOWN, "finviz").gap_sessions() == (
        date(2026, 9, 10),
        date(2026, 9, 11),
    )


def test_the_oracle_case_is_caught(tmp_path):
    """The Sept 11 sheet used the Sept 10 close; ORCL reported that evening."""
    cal = EarningsCalendar(tmp_path / "e.csv")
    cal.set(EarningsEvent("ORCL", date(2026, 9, 10), Timing.AMC, "finviz"), today=date(2026, 9, 10))
    assert cal.event_in_window("ORCL", date(2026, 9, 10), date(2026, 9, 18)) is not None
    assert cal.event_in_window("ORCL", date(2026, 9, 11), date(2026, 9, 18)) is None


def test_set_replaces_the_upcoming_date_and_keeps_history(tmp_path):
    cal = EarningsCalendar(tmp_path / "e.csv")
    today = date(2026, 9, 12)
    cal.set(EarningsEvent("ORCL", date(2026, 9, 10), Timing.AMC, "finviz"), today=date(2026, 9, 1))
    cal.set(EarningsEvent("ORCL", date(2026, 12, 10), Timing.UNKNOWN, "yfinance"), today=today)
    cal.set(EarningsEvent("ORCL", date(2026, 12, 9), Timing.AMC, "finviz"), today=today)
    dates = [e.date for e in cal.for_symbol("ORCL")]
    assert dates == [date(2026, 9, 10), date(2026, 12, 9)]
    assert cal.next_event("ORCL", today).date == date(2026, 12, 9)


def test_pull_never_overwrites_a_manual_date(tmp_path):
    cal = EarningsCalendar(tmp_path / "e.csv")
    today = date(2026, 9, 12)
    cal.set(EarningsEvent("ORCL", date(2026, 12, 9), Timing.AMC, "finviz"), today=today)
    written = cal.merge_pulled(
        [
            EarningsEvent("ORCL", date(2026, 12, 10), Timing.UNKNOWN, "yfinance"),
            EarningsEvent("CVX", date(2026, 10, 30), Timing.BMO, "yfinance"),
        ],
        today=today,
    )
    assert [e.symbol for e in written] == ["CVX"]
    assert cal.next_event("ORCL", today).date == date(2026, 12, 9)
    assert cal.next_event("CVX", today).timing is Timing.BMO


class _FakeTicker:
    def __init__(self, stamps):
        self._stamps = stamps

    def get_earnings_dates(self, limit=8):
        if self._stamps is None:
            raise RuntimeError("vendor down")
        index = pd.DatetimeIndex(self._stamps)
        return pd.DataFrame({"EPS Estimate": [1.0] * len(index)}, index=index)


def test_fetch_yfinance_events_picks_the_next_report():
    stamps = {
        "ORCL": [
            pd.Timestamp("2026-12-10 16:00", tz="America/New_York"),
            pd.Timestamp("2026-09-10 16:00", tz="America/New_York"),
        ],
        "CVX": [pd.Timestamp("2026-10-30 06:00", tz="America/New_York")],
        "BAD": None,
    }
    events, missing = fetch_yfinance_events(
        ["ORCL", "CVX", "BAD"],
        today=date(2026, 9, 12),
        ticker_factory=lambda s: _FakeTicker(stamps[s]),
    )
    by = {e.symbol: e for e in events}
    assert by["ORCL"].date == date(2026, 12, 10) and by["ORCL"].timing is Timing.AMC
    assert by["CVX"].timing is Timing.BMO
    assert missing == ["BAD"]
