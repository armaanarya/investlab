"""NYSE trading calendar, 2020-01-01 through 2027-12-31, in US/Eastern terms.

The session set is loaded once at import time from `pandas-market-calendars`
into a module-level `tuple[date, ...]`, a `frozenset[date]` for O(1)
membership and a `dict[date, int]` for O(1) index lookup. Neighbour queries
for non-session dates use `bisect` against the sorted tuple.

Design decisions:

- `add_business_days(d, n)`: `n > 0` applies `next_session` n times; `n < 0`
  applies `prev_session` `|n|` times; `n == 0` returns `d` unchanged when `d`
  is itself a session, and raises `CalendarRangeError` when it is not,
  because "zero business days from a Saturday" has no defensible answer —
  the caller should snap with `next_session`/`prev_session` first.
- Any date outside [`CALENDAR_START`, `CALENDAR_END`], or arithmetic that
  would need a session before the start or after the end, raises
  `CalendarRangeError` naming the bound that was violated.
- `sessions_between(a, b)` is inclusive of both ends when they are sessions,
  and silently clips non-session endpoints to the sessions that fall inside
  the window (it does not raise for a non-session endpoint). A reversed
  range (`a > b`) returns an empty list rather than raising.
"""

from __future__ import annotations

import bisect
from datetime import date

import pandas_market_calendars as mcal

CALENDAR_START: date = date(2020, 1, 1)
CALENDAR_END: date = date(2027, 12, 31)


class CalendarRangeError(ValueError):
    """Raised when a date or a computed result falls outside the loaded
    calendar range, or when zero-day arithmetic is asked of a non-session."""


def _load_sessions() -> tuple[date, ...]:
    cal = mcal.get_calendar("NYSE")
    valid_days = cal.valid_days(
        start_date=CALENDAR_START.isoformat(), end_date=CALENDAR_END.isoformat()
    )
    return tuple(ts.date() for ts in valid_days)


_SESSIONS: tuple[date, ...] = _load_sessions()
_SESSION_SET: frozenset[date] = frozenset(_SESSIONS)
_SESSION_INDEX: dict[date, int] = {d: i for i, d in enumerate(_SESSIONS)}


def sessions() -> tuple[date, ...]:
    """All NYSE sessions in range, ascending, inclusive of both ends."""
    return _SESSIONS


def _check_in_range(d: date) -> None:
    if d < CALENDAR_START:
        raise CalendarRangeError(f"{d} is before the calendar start {CALENDAR_START}")
    if d > CALENDAR_END:
        raise CalendarRangeError(f"{d} is after the calendar end {CALENDAR_END}")


def is_session(d: date) -> bool:
    """True when `d` is an NYSE trading session. Raises for `d` outside the
    loaded calendar range."""
    _check_in_range(d)
    return d in _SESSION_SET


def next_session(d: date) -> date:
    """The first session strictly after `d`. Raises `CalendarRangeError` if
    there is no such session within the loaded range."""
    _check_in_range(d)
    idx = _SESSION_INDEX.get(d)
    if idx is not None:
        next_idx = idx + 1
    else:
        # d is not a session; find the first session strictly after it.
        next_idx = bisect.bisect_right(_SESSIONS, d)
    if next_idx >= len(_SESSIONS):
        raise CalendarRangeError(
            f"no session strictly after {d} within the calendar end {CALENDAR_END}"
        )
    return _SESSIONS[next_idx]


def prev_session(d: date) -> date:
    """The last session strictly before `d`. Raises `CalendarRangeError` if
    there is no such session within the loaded range."""
    _check_in_range(d)
    idx = _SESSION_INDEX.get(d)
    if idx is not None:
        prev_idx = idx - 1
    else:
        # d is not a session; find the last session strictly before it.
        prev_idx = bisect.bisect_left(_SESSIONS, d) - 1
    if prev_idx < 0:
        raise CalendarRangeError(
            f"no session strictly before {d} within the calendar start {CALENDAR_START}"
        )
    return _SESSIONS[prev_idx]


def sessions_between(a: date, b: date) -> list[date]:
    """Sessions in `[a, b]`, inclusive of both ends. Non-session endpoints
    are clipped to the sessions that fall inside the window. A reversed
    range (`a > b`) returns an empty list. Raises `CalendarRangeError` if
    either endpoint is outside the loaded calendar range."""
    _check_in_range(a)
    _check_in_range(b)
    if a > b:
        return []
    lo = bisect.bisect_left(_SESSIONS, a)
    hi = bisect.bisect_right(_SESSIONS, b)
    return list(_SESSIONS[lo:hi])


def add_business_days(d: date, n: int) -> date:
    """Walk `n` NYSE sessions from `d`.

    `n > 0` applies `next_session` n times; `n < 0` applies `prev_session`
    `|n|` times. `n == 0` returns `d` when `d` is a session and raises
    `CalendarRangeError` otherwise — snap with `next_session`/`prev_session`
    first if you need zero-day arithmetic from a non-session date.
    """
    _check_in_range(d)
    if n == 0:
        if d not in _SESSION_SET:
            raise CalendarRangeError(
                f"{d} is not a session; zero business days from a non-session date "
                "is undefined, snap with next_session/prev_session first"
            )
        return d
    result = d
    if n > 0:
        for _ in range(n):
            result = next_session(result)
    else:
        for _ in range(-n):
            result = prev_session(result)
    return result
