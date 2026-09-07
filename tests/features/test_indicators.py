"""Indicator golden-value tests.

Every value here is either hand-computed (comment shows the arithmetic) or
derived from an exact structural property (RSI of a monotonic series, a
breakout level that must exclude the current bar, ATR's linear response to
a uniform price scale). Warm-up positions must be NaN, never a plausible-
looking wrong number — a NaN that silently became 0 would be worse than a
crash, because a downstream rank or sizing decision would treat it as data.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from investlab.contracts import Bar
from investlab.features.indicators import (
    atr,
    breakout_level,
    ema,
    frame_from_bars,
    macd,
    realized_volatility,
    relative_volume,
    relative_volume_latest,
    rsi,
    sma,
)


def make_bar(symbol: str, session: date, o, h, l, c, ac=None, volume: int = 1000) -> Bar:
    ac = c if ac is None else ac
    return Bar(
        symbol=symbol,
        session=session,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        adj_close=Decimal(str(ac)),
        volume=volume,
        source="test",
    )


# ---------------------------------------------------------------------------
# frame_from_bars: the Decimal -> float boundary
# ---------------------------------------------------------------------------


def test_frame_from_bars_converts_decimal_to_float64():
    bars = [
        make_bar("AAPL", date(2026, 1, 2), "100.10", "101.50", "99.90", "100.75", "100.75", 1000),
        make_bar("AAPL", date(2026, 1, 3), "100.75", "102.00", "100.50", "101.90", "101.90", 1100),
    ]
    df = frame_from_bars(bars)
    assert list(df.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    for col in df.columns:
        assert df[col].dtype == np.float64
    assert df.loc[date(2026, 1, 2), "close"] == pytest.approx(100.75)
    assert list(df.index) == [date(2026, 1, 2), date(2026, 1, 3)]


def test_frame_from_bars_sorts_by_session_regardless_of_input_order():
    bars = [
        make_bar("AAPL", date(2026, 1, 3), 101, 102, 100, 101.5),
        make_bar("AAPL", date(2026, 1, 2), 100, 101, 99, 100.5),
    ]
    df = frame_from_bars(bars)
    assert list(df.index) == [date(2026, 1, 2), date(2026, 1, 3)]


# ---------------------------------------------------------------------------
# sma
# ---------------------------------------------------------------------------


def test_sma_first_valid_at_n_minus_one():
    v = pd.Series([1.0, 2, 3, 4, 5])
    got = sma(v, 3)
    assert np.isnan(got.iloc[0]) and np.isnan(got.iloc[1])
    assert got.iloc[2] == 2.0
    assert got.iloc[3] == 3.0
    assert got.iloc[4] == 4.0


# ---------------------------------------------------------------------------
# ema
# ---------------------------------------------------------------------------


def test_ema_seeds_with_sma():
    v = pd.Series([1.0, 2, 3, 4, 5])
    got = ema(v, 3)
    assert np.isnan(got.iloc[0]) and np.isnan(got.iloc[1])
    assert got.iloc[2] == 2.0  # SMA(1, 2, 3)
    assert got.iloc[3] == 0.5 * 4 + 0.5 * 2.0  # a = 2/4
    assert got.iloc[4] == 0.5 * 5 + 0.5 * got.iloc[3]


def test_ema_skips_leading_nan_rather_than_treating_as_zero():
    # Mirrors MACD's signal line: an EMA computed over a series that starts
    # with leading NaN (here, 3 of them) must seed from the first n *valid*
    # observations, not from index 0.
    v = pd.Series([np.nan, np.nan, np.nan, 1.0, 2.0, 3.0, 4.0])
    got = ema(v, 3)
    assert got.iloc[:5].isna().all()  # first 3 valid obs land the seed at index 5
    assert got.iloc[5] == 2.0  # SMA(1, 2, 3)
    assert got.iloc[6] == 0.5 * 4 + 0.5 * 2.0


# ---------------------------------------------------------------------------
# macd
# ---------------------------------------------------------------------------


def test_macd_first_valid_indices_match_sma_seeded_ema():
    v = pd.Series(np.arange(1.0, 61.0))  # linear ramp, 60 points
    result = macd(v, fast=12, slow=26, signal=9)

    assert result.macd.iloc[:25].isna().all()
    assert not np.isnan(result.macd.iloc[25])
    assert result.signal.iloc[:33].isna().all()
    assert not np.isnan(result.signal.iloc[33])

    hist = result.macd - result.signal
    pd.testing.assert_series_equal(result.histogram, hist, check_names=False)


def test_macd_equals_ema_difference():
    v = pd.Series(np.sin(np.linspace(0, 10, 80)) * 5 + 100)
    result = macd(v)
    expected = ema(v, 12) - ema(v, 26)
    pd.testing.assert_series_equal(result.macd, expected, check_names=False)


# ---------------------------------------------------------------------------
# rsi
# ---------------------------------------------------------------------------


def test_rsi_of_monotonic_rise_is_100():
    v = pd.Series(np.arange(1.0, 31.0))  # strictly increasing
    got = rsi(v, 14)
    assert got.iloc[:14].isna().all()
    assert (got.iloc[14:] == 100.0).all()


def test_rsi_of_monotonic_fall_is_0():
    v = pd.Series(np.arange(30.0, 0.0, -1.0))  # strictly decreasing
    got = rsi(v, 14)
    assert got.iloc[:14].isna().all()
    assert (got.iloc[14:] == 0.0).all()


def test_rsi_of_flat_series_is_50():
    v = pd.Series([100.0] * 30)
    got = rsi(v, 14)
    assert got.iloc[:14].isna().all()
    assert (got.iloc[14:] == 50.0).all()


def test_rsi_first_valid_at_index_n():
    v = pd.Series(np.arange(1.0, 20.0))
    got = rsi(v, 14)
    assert np.isnan(got.iloc[13])
    assert not np.isnan(got.iloc[14])


# ---------------------------------------------------------------------------
# atr
# ---------------------------------------------------------------------------


def test_atr_golden_hand_computation():
    # TR1 = max(12-9, |12-9|, |9-9|)     = max(3, 3, 0)   = 3
    # TR2 = max(12.5-11.5, |12.5-11|, |11.5-11|) = max(1, 1.5, 0.5) = 1.5
    # TR3 = max(14-12, |14-12|, |12-12|) = max(2, 2, 0)   = 2
    # seed (n=2) = mean(TR1, TR2) = mean(3, 1.5) = 2.25, valid at index 2
    # step at index 3: avg = (2.25*1 + 2) / 2 = 2.125
    high = pd.Series([10.0, 12.0, 12.5, 14.0])
    low = pd.Series([8.0, 9.0, 11.5, 12.0])
    close = pd.Series([9.0, 11.0, 12.0, 13.5])

    got = atr(high, low, close, n=2)

    assert np.isnan(got.iloc[0])
    assert np.isnan(got.iloc[1])
    assert got.iloc[2] == pytest.approx(2.25)
    assert got.iloc[3] == pytest.approx(2.125)


def test_atr_first_bar_has_no_prior_close_and_is_nan():
    high = pd.Series([10.0, 12.0, 12.5, 14.0, 15.0])
    low = pd.Series([8.0, 9.0, 11.5, 12.0, 13.0])
    close = pd.Series([9.0, 11.0, 12.0, 13.5, 14.5])
    got = atr(high, low, close, n=2)
    assert np.isnan(got.iloc[0])


def test_atr_units_are_consistent_on_a_split_adjusted_series():
    rng = np.random.default_rng(2026)
    n_bars = 60
    close = 100 + np.cumsum(rng.normal(0, 1, n_bars))
    high = close + rng.uniform(0.5, 2.0, n_bars)
    low = close - rng.uniform(0.5, 2.0, n_bars)

    got = atr(pd.Series(high), pd.Series(low), pd.Series(close), n=14)
    got_halved = atr(pd.Series(high) / 2, pd.Series(low) / 2, pd.Series(close) / 2, n=14)

    ratio = (got_halved / got).dropna()
    assert np.allclose(ratio, 0.5)


# ---------------------------------------------------------------------------
# realized_volatility
# ---------------------------------------------------------------------------


def test_realized_volatility_first_valid_at_index_n():
    v = pd.Series(100 + np.arange(30.0))
    got = realized_volatility(v, n=20, periods_per_year=252)
    assert got.iloc[:20].isna().all()
    assert not np.isnan(got.iloc[20])


def test_realized_volatility_golden_two_point_window():
    # Two known returns over a 3-point series so std is hand-computable.
    # prices: 100, 110, 99 -> returns: 0.10, -0.10 (10% up, 10% down)
    close = pd.Series([100.0, 110.0, 99.0])
    got = realized_volatility(close, n=2, periods_per_year=1)
    # sample std (ddof=1) of [0.10, -0.10]: mean=0, deviations +-0.10,
    # variance = (0.10^2 + 0.10^2) / (2-1) = 0.02, std = sqrt(0.02)
    expected = np.sqrt(0.02)
    assert got.iloc[2] == pytest.approx(expected)
    assert np.isnan(got.iloc[0])
    assert np.isnan(got.iloc[1])


# ---------------------------------------------------------------------------
# breakout_level
# ---------------------------------------------------------------------------


def test_breakout_level_excludes_current_bar():
    h = pd.Series([1.0, 2, 3, 10, 4])
    got = breakout_level(h, 3)
    assert got.iloc[3] == 3.0  # NOT 10.0 - excludes h[3] itself
    assert got.iloc[4] == 10.0


def test_breakout_level_is_nan_before_n_preceding_bars_exist():
    h = pd.Series([1.0, 2, 3, 4, 5])
    got = breakout_level(h, 3)
    assert got.iloc[:3].isna().all()


# ---------------------------------------------------------------------------
# relative_volume
# ---------------------------------------------------------------------------


def test_relative_volume_returns_none_not_inf_on_zero_denominator():
    volume = pd.Series([0.0] * 20 + [100.0])
    series = relative_volume(volume, n=20)
    assert np.isnan(series.iloc[20])  # never inf
    assert relative_volume_latest(volume, n=20) is None


def test_relative_volume_golden_value():
    volume = pd.Series([10.0] * 20 + [30.0])
    series = relative_volume(volume, n=20)
    assert series.iloc[20] == pytest.approx(3.0)
    assert relative_volume_latest(volume, n=20) == pytest.approx(3.0)


def test_relative_volume_none_when_window_incomplete():
    volume = pd.Series([10.0] * 5)
    assert relative_volume_latest(volume, n=20) is None


# ---------------------------------------------------------------------------
# Warm-up NaN sanity sweep
# ---------------------------------------------------------------------------


def test_all_warmups_are_nan_not_wrong_numbers():
    rng = np.random.default_rng(7)
    n_bars = 80
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n_bars)))
    high = close + rng.uniform(0.1, 1.0, n_bars)
    low = close - rng.uniform(0.1, 1.0, n_bars)
    volume = pd.Series(rng.integers(1_000, 10_000, n_bars).astype(float))

    checks = [
        (ema(close, 20), 19),
        (rsi(close, 14), 14),
        (atr(high, low, close, 14), 14),
        (realized_volatility(close, 20), 20),
        (breakout_level(high, 20), 20),
        (relative_volume(volume, 20), 20),
    ]
    for series, first_valid in checks:
        assert series.iloc[:first_valid].isna().all()
        assert not series.iloc[first_valid:].isna().any()
        assert series.dtype == np.float64
