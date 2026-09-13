"""Exit rules, on hand-computable bars.

The fixture is flat at 100 for 20 sessions, rallies a dollar a day to 110,
then drops to 103. Every bar spans its close by a dollar either side, so the
true range is exactly 2 through the rally and ATR(14) stays at 2.00 until the
drop, whose true range is 8.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np

from investlab import calendar as cal
from investlab.contracts import Bar
from investlab.portfolio.exits import ExitReason, entry_stop_at, evaluate_exit, trailing_stop
from investlab.screen import compute_feature_frame

SESSIONS = cal.sessions_between(date(2026, 3, 2), date(2026, 8, 31))
OPENED = SESSIONS[19]
THREE = Decimal("3")


def _bar(i: int, close: float, spread: float = 1.0) -> Bar:
    c = Decimal(str(close))
    s = Decimal(str(spread))
    return Bar("X", SESSIONS[i], c, c + s, c - s, c, c, 1000, "test")


def _frame():
    closes = [100.0] * 20 + [101.0 + k for k in range(10)] + [103.0]
    return compute_feature_frame("X", [_bar(i, c) for i, c in enumerate(closes)])


def _evaluate(ff, as_of, entry_stop, score=80.0, above=True, min_hold=5):
    return evaluate_exit(
        "X",
        ff,
        opened=OPENED,
        as_of=as_of,
        entry_stop=entry_stop,
        score=score,
        above_ema50=above,
        trail_atr_multiple=THREE,
        exit_score_below=40.0,
        min_hold_sessions=min_hold,
    )


def test_trailing_stop_is_highest_close_minus_three_atr():
    trail, high = trailing_stop(_frame(), OPENED, SESSIONS[29], THREE)
    assert high == Decimal("110.00")
    assert trail == Decimal("104.00")


def test_trailing_stop_does_not_fall_when_atr_widens():
    trail, _ = trailing_stop(_frame(), OPENED, SESSIONS[30], THREE)
    assert trail == Decimal("104.00")


def test_hold_while_above_every_stop():
    ev = _evaluate(_frame(), SESSIONS[29], Decimal("95"))
    assert ev.reason is None
    assert ev.active_stop == Decimal("104.00")
    assert ev.distance_to_stop_pct == Decimal("5.45")


def test_close_through_trailing_stop_is_a_trailing_stop_exit():
    ev = _evaluate(_frame(), SESSIONS[30], Decimal("95"))
    assert ev.reason is ExitReason.TRAILING_STOP
    assert ev.last_close == Decimal("103.00")
    assert ev.sessions_held == 11


def test_entry_stop_fires_when_it_is_the_higher_stop():
    ev = _evaluate(_frame(), OPENED, Decimal("100"))
    assert ev.reason is ExitReason.STOP
    assert ev.active_stop == Decimal("100")


def test_momentum_lost_waits_for_the_minimum_hold():
    ff = _frame()
    early = _evaluate(ff, SESSIONS[22], Decimal("50"), score=30.0, above=False, min_hold=5)
    assert early.reason is None
    ready = _evaluate(ff, SESSIONS[22], Decimal("50"), score=30.0, above=False, min_hold=3)
    assert ready.reason is ExitReason.MOMENTUM_LOST


def test_momentum_lost_needs_the_close_under_ema50():
    ev = _evaluate(_frame(), SESSIONS[22], Decimal("50"), score=30.0, above=True, min_hold=3)
    assert ev.reason is None


def test_entry_stop_at_uses_the_atr_on_the_fill_session():
    ff = _frame()
    assert entry_stop_at(ff, SESSIONS[19], Decimal("100"), Decimal("2")) == Decimal("96.00")
    assert entry_stop_at(ff, SESSIONS[5], Decimal("100"), Decimal("2")) is None


def test_no_bars_never_sells():
    ev = evaluate_exit(
        "X",
        None,
        opened=OPENED,
        as_of=SESSIONS[30],
        entry_stop=Decimal("95"),
        score=None,
        above_ema50=None,
        trail_atr_multiple=THREE,
        exit_score_below=40.0,
        min_hold_sessions=5,
    )
    assert ev.reason is None
    assert "no cached bars" in ev.detail


def test_trailing_stop_only_ever_rises_on_a_random_walk():
    rng = np.random.default_rng(3)
    price, bars = 100.0, []
    for i in range(120):
        price = max(1.0, price * (1 + rng.normal(0, 0.02)))
        bars.append(_bar(i, round(price, 2), spread=round(abs(rng.normal(0, 1.5)) + 0.1, 2)))
    ff = compute_feature_frame("X", bars)
    previous = None
    for i in range(19, 120):
        trail, _ = trailing_stop(ff, OPENED, SESSIONS[i], THREE)
        if previous is not None and trail is not None:
            assert trail >= previous
        previous = trail if trail is not None else previous
