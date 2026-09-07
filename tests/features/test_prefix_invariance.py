"""Prefix invariance: the non-negotiable leakage test.

Compute every feature on a price series, then append future bars and
recompute. Every feature value at every earlier date must be byte-identical
to what it was before the future bars existed. A failure here means the
system is producing lookahead fiction — some window or rolling calculation
is reaching forward in time — and every downstream rank, order, and
backtest result built on that feature is worthless. This is written now,
before `features/ranking.py`, not last: if this test cannot pass, nothing
built on top of `indicators.py` is trustworthy regardless of how many of
its own unit tests are green.

If any check below fails, the bug is in the indicator (a rolling window
reaching past `i`, a `shift` in the wrong direction, an EMA seed computed
over the whole series instead of a warm-up prefix) — never in this test.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
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
    rsi,
)

SEED = 20260906
TOTAL_BARS = 300


def _build_bars(n_bars: int, seed: int = SEED) -> list[Bar]:
    """A deterministic geometric-walk OHLCV series, high/low guaranteed to
    bracket open/close.

    Draws exactly five scalars per bar, one bar at a time, from a single
    seeded generator — never a single vectorised call sized to `n_bars`.
    That is what makes the *data* itself prefix invariant: the random
    draws consumed to build bar `i` do not depend on how many bars are
    requested in total, so `_build_bars(300)[:50] == _build_bars(50)`
    below is not a coincidence of the RNG, it is a property this generator
    is written to guarantee.
    """
    rng = np.random.default_rng(seed)
    price = 100.0
    session = date(2025, 1, 2)
    bars = []
    for _ in range(n_bars):
        ret = rng.normal(0.0005, 0.02)
        price = max(price * (1 + ret), 1.0)
        open_jitter = rng.normal(0.0, 0.002)
        high_jitter = abs(rng.normal(0.0, 0.006))
        low_jitter = abs(rng.normal(0.0, 0.006))
        vol_draw = rng.normal(1_000_000, 200_000)

        close_ = price
        open_ = price * (1 + open_jitter)
        high_ = max(open_, close_) * (1 + high_jitter)
        low_ = min(open_, close_) * (1 - low_jitter)
        volume = max(int(abs(vol_draw)), 1)

        bars.append(
            Bar(
                symbol="TEST",
                session=session,
                open=Decimal(str(round(open_, 6))),
                high=Decimal(str(round(high_, 6))),
                low=Decimal(str(round(low_, 6))),
                close=Decimal(str(round(close_, 6))),
                adj_close=Decimal(str(round(close_, 6))),
                volume=volume,
                source="test",
            )
        )
        session = session + timedelta(days=1)
    return bars


def assert_bitwise_identical(a, b, label: str) -> None:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    assert a.shape == b.shape, label
    na, nb = np.isnan(a), np.isnan(b)
    assert np.array_equal(na, nb), f"{label}: NaN positions differ"
    assert a[~na].tobytes() == b[~nb].tobytes(), f"{label}: values differ"


def test_bar_generator_itself_is_prefix_invariant():
    # If this fails, the test data is the problem, not the indicators —
    # fix _build_bars before trusting anything else in this file.
    full = _build_bars(TOTAL_BARS)
    short = _build_bars(120)
    assert full[:120] == short


@pytest.mark.parametrize("cut", [50, 120, 200, 299])
def test_prefix_invariance_across_all_indicators(cut):
    full_bars = _build_bars(TOTAL_BARS)
    prefix_bars = full_bars[:cut]  # "yesterday's" cache, before today's pull

    full_frame = frame_from_bars(full_bars)
    prefix_frame = frame_from_bars(prefix_bars)
    assert list(prefix_frame.index) == list(full_frame.index[:cut])

    def check(label: str, full_series, prefix_series) -> None:
        assert_bitwise_identical(full_series.to_numpy()[:cut], prefix_series.to_numpy(), label)

    check("ema(20)", ema(full_frame["close"], 20), ema(prefix_frame["close"], 20))

    full_macd = macd(full_frame["close"])
    prefix_macd = macd(prefix_frame["close"])
    check("macd.macd", full_macd.macd, prefix_macd.macd)
    check("macd.signal", full_macd.signal, prefix_macd.signal)
    check("macd.histogram", full_macd.histogram, prefix_macd.histogram)

    check("rsi(14)", rsi(full_frame["close"], 14), rsi(prefix_frame["close"], 14))

    check(
        "atr(14)",
        atr(full_frame["high"], full_frame["low"], full_frame["close"], 14),
        atr(prefix_frame["high"], prefix_frame["low"], prefix_frame["close"], 14),
    )

    check(
        "realized_volatility(20)",
        realized_volatility(full_frame["adj_close"], 20),
        realized_volatility(prefix_frame["adj_close"], 20),
    )

    check(
        "breakout_level(20)",
        breakout_level(full_frame["high"], 20),
        breakout_level(prefix_frame["high"], 20),
    )

    check(
        "relative_volume(20)",
        relative_volume(full_frame["volume"], 20),
        relative_volume(prefix_frame["volume"], 20),
    )
