from __future__ import annotations

from datetime import date

from investlab.backtest.engine import CloseBook
from investlab.backtest.rolling import run_rolling
from investlab.competitions.deca import DecaProfile
from investlab.config import DecaConfig


def test_windows_are_game_length_and_scored_against_spy(synth_market):
    bars, universe, features = (
        synth_market["bars"],
        synth_market["universe"],
        synth_market["features"],
    )
    windows, summaries = run_rolling(
        bars,
        universe,
        DecaProfile(),
        DecaConfig(),
        first_start=date(2026, 6, 1),
        last_end=date(2026, 9, 11),
        length=20,
        step=20,
        features=features,
    )
    closes = CloseBook(bars)
    sessions = [d for d in closes.sessions("SPY") if date(2026, 6, 1) <= d <= date(2026, 9, 11)]
    assert len(windows) == (len(sessions) - 20) // 20 + 1
    for w in windows:
        span = [d for d in sessions if w.start <= d <= w.end]
        assert len(span) == 20
        expected = round(float((closes.on("SPY", w.end) / closes.on("SPY", w.start) - 1) * 100), 2)
        assert w.benchmark_pct == expected
    assert {s.name for s in summaries} == {"strategy", "momentum_top8", "compliance_aware"}
    for s in summaries:
        assert s.windows == len(windows)
        assert 0 <= s.beat_benchmark_pct <= 100
        assert s.worst_excess_pct <= s.median_excess_pct <= s.best_excess_pct


def test_a_range_shorter_than_one_game_returns_nothing(synth_market):
    windows, summaries = run_rolling(
        synth_market["bars"],
        synth_market["universe"],
        DecaProfile(),
        DecaConfig(),
        first_start=date(2026, 9, 1),
        last_end=date(2026, 9, 11),
        features=synth_market["features"],
    )
    assert windows == [] and summaries == []
