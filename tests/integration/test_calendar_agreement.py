"""The DECA module implements its own business-day arithmetic rather than
importing `calendar.py`, so that it could be built and tested while the data
layer was still in flight (see the ruling in the SDD ledger).

That duplication is only safe while the two agree. This test is the thing that
keeps them honest: if either implementation's holiday set drifts, the DECA
one-business-day replacement clock silently starts computing wrong deadlines,
and a missed replacement is a diversification violation.
"""

from datetime import date, timedelta

import pytest

import investlab.calendar as cal
import investlab.competitions.deca as deca

SEASON_START = date(2026, 9, 1)
SEASON_END = date(2026, 12, 31)


def _season_days():
    d = SEASON_START
    while d <= SEASON_END:
        yield d
        d += timedelta(days=1)


def test_session_definitions_agree_across_the_competition_window():
    disagreements = [
        (d, cal.is_session(d), deca.is_business_day(d))
        for d in _season_days()
        if cal.is_session(d) != deca.is_business_day(d)
    ]
    assert disagreements == [], (
        "calendar.py and competitions/deca.py disagree on which days are "
        f"sessions: {disagreements}"
    )


@pytest.mark.parametrize(
    "sale_date,expected",
    [
        (date(2026, 10, 23), date(2026, 10, 26)),  # diversification deadline, Fri -> Mon
        (date(2026, 11, 20), date(2026, 11, 23)),  # ordinary Friday
        (date(2026, 11, 25), date(2026, 11, 27)),  # Wed before Thanksgiving, skips Nov 26
    ],
)
def test_replacement_clock_agrees(sale_date, expected):
    """DECA gives one business day to restore a $10,000 holding after a sale."""
    assert deca.next_business_day(sale_date) == expected
    assert cal.add_business_days(sale_date, 1) == expected


def test_thanksgiving_2026_is_not_a_session_in_either_implementation():
    thanksgiving = date(2026, 11, 26)
    assert not cal.is_session(thanksgiving)
    assert not deca.is_business_day(thanksgiving)
