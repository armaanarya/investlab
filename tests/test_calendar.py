"""NYSE trading calendar tests.

Holiday dates below are derived independently of the calendar library, from
the NYSE rule (e.g. MLK Day is the third Monday in January, Independence Day
observes to Friday when July 4 lands on a Saturday). If a test here fails,
suspect the implementation before suspecting the arithmetic.
"""

from datetime import date

import pytest

from investlab.calendar import (
    CALENDAR_END,
    CALENDAR_START,
    CalendarRangeError,
    add_business_days,
    is_session,
    next_session,
    prev_session,
    sessions,
    sessions_between,
)

# NYSE holidays observed in 2026, all of which fall on a weekday.
HOLIDAYS_2026 = [
    (date(2026, 1, 1), "New Year's Day"),
    (date(2026, 1, 19), "MLK Day"),
    (date(2026, 2, 16), "Washington's Birthday"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 5, 25), "Memorial Day"),
    (date(2026, 6, 19), "Juneteenth"),
    (date(2026, 7, 3), "Independence Day (observed)"),
    (date(2026, 9, 7), "Labor Day"),
    (date(2026, 11, 26), "Thanksgiving"),
    (date(2026, 12, 25), "Christmas"),
]


class TestIsSession:
    def test_thanksgiving_2026_is_not_a_session(self):
        assert is_session(date(2026, 11, 26)) is False

    def test_day_before_thanksgiving_2026_is_a_session(self):
        assert is_session(date(2026, 11, 25)) is True

    def test_black_friday_2026_is_a_session(self):
        # Early close at 1pm ET, but a session all the same.
        assert is_session(date(2026, 11, 27)) is True

    @pytest.mark.parametrize("day,name", HOLIDAYS_2026, ids=[n for _, n in HOLIDAYS_2026])
    def test_2026_holidays_are_not_sessions(self, day, name):
        assert is_session(day) is False, f"{name} on {day} should not be a session"

    def test_weekends_are_not_sessions(self):
        assert is_session(date(2026, 9, 5)) is False  # Saturday
        assert is_session(date(2026, 9, 6)) is False  # Sunday

    def test_ordinary_weekday_is_a_session(self):
        assert is_session(date(2026, 9, 4)) is True  # Friday

    def test_out_of_range_date_raises(self):
        with pytest.raises(CalendarRangeError):
            is_session(date(2019, 12, 31))
        with pytest.raises(CalendarRangeError):
            is_session(date(2028, 1, 1))


class TestSessionCount:
    def test_2026_has_251_sessions(self):
        # 2026 starts and ends on a Thursday: 52*5 + 1 = 261 weekdays, minus
        # the 10 weekday holidays above.
        year = [d for d in sessions() if d.year == 2026]
        assert len(year) == 251

    def test_calendar_spans_2020_through_2027(self):
        all_sessions = sessions()
        assert CALENDAR_START == date(2020, 1, 1)
        assert CALENDAR_END == date(2027, 12, 31)
        assert all_sessions[0] == date(2020, 1, 2)  # Jan 1 is a holiday
        assert all_sessions[-1] == date(2027, 12, 31)

    def test_sessions_are_sorted_and_unique(self):
        all_sessions = sessions()
        assert list(all_sessions) == sorted(set(all_sessions))


class TestNeighbours:
    def test_next_session_is_strictly_after(self):
        assert next_session(date(2026, 11, 25)) == date(2026, 11, 27)

    def test_next_session_from_a_holiday(self):
        assert next_session(date(2026, 11, 26)) == date(2026, 11, 27)

    def test_next_session_from_a_saturday(self):
        assert next_session(date(2026, 9, 5)) == date(2026, 9, 8)  # Labor Day Mon 7th

    def test_prev_session_is_strictly_before(self):
        assert prev_session(date(2026, 11, 27)) == date(2026, 11, 25)

    def test_prev_session_from_a_holiday(self):
        assert prev_session(date(2026, 11, 26)) == date(2026, 11, 25)

    def test_next_session_past_the_end_raises(self):
        with pytest.raises(CalendarRangeError):
            next_session(date(2027, 12, 31))

    def test_prev_session_before_the_start_raises(self):
        with pytest.raises(CalendarRangeError):
            prev_session(date(2020, 1, 2))


class TestSessionsBetween:
    def test_is_inclusive_of_both_ends(self):
        got = sessions_between(date(2026, 11, 23), date(2026, 11, 27))
        assert got == [
            date(2026, 11, 23),
            date(2026, 11, 24),
            date(2026, 11, 25),
            date(2026, 11, 27),
        ]

    def test_non_session_endpoints_are_clipped_not_rejected(self):
        got = sessions_between(date(2026, 11, 26), date(2026, 11, 29))
        assert got == [date(2026, 11, 27)]

    def test_reversed_range_is_empty(self):
        assert sessions_between(date(2026, 11, 27), date(2026, 11, 23)) == []

    def test_single_session_range(self):
        assert sessions_between(date(2026, 11, 27), date(2026, 11, 27)) == [date(2026, 11, 27)]


class TestAddBusinessDays:
    def test_one_business_day_skips_thanksgiving(self):
        # The DECA one-business-day replacement clock depends on exactly this.
        assert add_business_days(date(2026, 11, 25), 1) == date(2026, 11, 27)

    def test_one_business_day_skips_weekend_and_labor_day(self):
        assert add_business_days(date(2026, 9, 4), 1) == date(2026, 9, 8)

    def test_from_a_non_session_moves_to_the_next_session(self):
        assert add_business_days(date(2026, 11, 26), 1) == date(2026, 11, 27)

    def test_negative_walks_backwards(self):
        assert add_business_days(date(2026, 11, 27), -1) == date(2026, 11, 25)

    def test_multiple_days_across_a_holiday_week(self):
        assert add_business_days(date(2026, 11, 23), 4) == date(2026, 11, 30)

    def test_zero_on_a_session_is_identity(self):
        assert add_business_days(date(2026, 11, 25), 0) == date(2026, 11, 25)

    def test_zero_on_a_non_session_raises(self):
        with pytest.raises(CalendarRangeError):
            add_business_days(date(2026, 11, 26), 0)

    def test_running_off_the_end_raises(self):
        with pytest.raises(CalendarRangeError):
            add_business_days(date(2027, 12, 30), 10)
