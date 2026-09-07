"""Technical indicators, computed in float64 on the pandas/numpy side.

Money is `Decimal` and lives in `contracts.py` types. **Indicators are not
money.** `frame_from_bars` is the single Decimal -> float boundary: every
`Bar.open/high/low/close/adj_close` (Decimal) is converted to a float64
column exactly once, here, and nothing downstream ever converts back. This
module never imports `money.py`.

`Bar.high`/`Bar.low`/`Bar.close` are raw, unadjusted prices; `Bar.adj_close`
is the provider's split/dividend-adjusted close. ATR and `breakout_level`
must be computed on raw OHLC; `realized_volatility` must be computed on
`adj_close`. Mixing the two in one calculation is a documented bug class
(see `contracts.Bar`'s docstring) and this module does not guard against a
caller passing the wrong column — the caller decides which column of the
`frame_from_bars` output to pass to which function.

Every function below returns a `pd.Series` on the input's index, dtype
float64, with NaN during warm-up rather than a plausible-looking wrong
number. The exact warm-up length and seeding rule for each function is
documented on the function itself; see also
`docs/superpowers/plans/2026-09-06-core-data.md` Task 5 for the full
derivation and `tests/features/test_prefix_invariance.py` for the
non-negotiable leakage test every one of these must pass: appending future
bars and recomputing must not change a single earlier value, byte for byte.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from investlab.contracts import Bar

__all__ = [
    "frame_from_bars",
    "sma",
    "ema",
    "MacdResult",
    "macd",
    "rsi",
    "atr",
    "realized_volatility",
    "breakout_level",
    "relative_volume",
    "relative_volume_latest",
]


def frame_from_bars(bars: Sequence[Bar]) -> pd.DataFrame:
    """The Decimal -> float boundary.

    Returns a `pd.DataFrame` indexed by `session` (ascending, deduplicated
    by the caller — this function does not merge, `data/cache.py` does),
    with float64 columns `open`, `high`, `low`, `close`, `adj_close`, and
    `volume`. Bars are sorted by session regardless of input order.
    """
    ordered = sorted(bars, key=lambda b: b.session)
    index = pd.Index([b.session for b in ordered], name="session")
    data = {
        "open": [float(b.open) for b in ordered],
        "high": [float(b.high) for b in ordered],
        "low": [float(b.low) for b in ordered],
        "close": [float(b.close) for b in ordered],
        "adj_close": [float(b.adj_close) for b in ordered],
        "volume": [float(b.volume) for b in ordered],
    }
    return pd.DataFrame(data, index=index, dtype=np.float64)


def sma(values: pd.Series, n: int) -> pd.Series:
    """Simple moving average over a trailing window of `n` observations,
    inclusive of the current bar. First valid at position `n - 1`."""
    v = values.astype(np.float64)
    return v.rolling(window=n, min_periods=n).mean()


def ema(values: pd.Series, n: int) -> pd.Series:
    """Exponential moving average, seeded with the SMA of the first `n`
    *valid* (non-NaN) observations.

    Leading NaNs are skipped when finding the seed, not treated as zero -
    this matters for `macd`'s signal line, which is an EMA of a series with
    warm-up-length leading NaNs of its own. The seed lands at the position
    of the n-th valid observation; every valid observation after that
    updates the recurrence `e[i] = a*v[i] + (1-a)*e[i-1]` with
    `a = 2 / (n + 1)`, skipping over any interior NaN gap without treating
    it as zero (the previous *valid* ema carries forward unchanged, and the
    output at the gap position stays NaN).
    """
    v = values.astype(np.float64)
    result = pd.Series(np.nan, index=v.index, dtype=np.float64)

    valid_mask = v.notna().to_numpy()
    positions = np.flatnonzero(valid_mask)
    if len(positions) < n:
        return result

    seed_positions = positions[:n]
    seed_value = float(v.iloc[seed_positions].mean())
    seed_pos = positions[n - 1]
    result.iloc[seed_pos] = seed_value

    alpha = 2.0 / (n + 1)
    prev = seed_value
    for pos in positions[n:]:
        cur = float(v.iloc[pos])
        e = alpha * cur + (1 - alpha) * prev
        result.iloc[pos] = e
        prev = e

    return result


@dataclass(frozen=True)
class MacdResult:
    macd: pd.Series
    signal: pd.Series
    histogram: pd.Series


def macd(values: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> MacdResult:
    """`macd = ema(v, fast) - ema(v, slow)`; `signal = ema(macd, signal)`;
    `histogram = macd - signal`.

    With SMA seeding and the default 12/26/9, `macd` is first valid at
    index `slow - 1` (25) and `signal` at index `slow - 1 + signal` (33).
    """
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return MacdResult(macd=macd_line, signal=signal_line, histogram=histogram)


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    # Declared edge cases: a flat series carries no direction (50.0); an
    # all-gain run has no losses to divide by (100.0, not +inf); an
    # all-loss run has no gains (0.0).
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi(values: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI. Deltas are taken from position 1 (`delta[0]` is NaN -
    there is no prior observation). The seed `avg_gain`/`avg_loss` is the
    SMA of the first `n` deltas (positions `1..n`), first valid at position
    `n`. After the seed, Wilder smoothing applies:
    `avg[i] = (avg[i-1] * (n-1) + cur[i]) / n`.
    """
    v = values.astype(np.float64)
    result = pd.Series(np.nan, index=v.index, dtype=np.float64)
    if len(v) <= n:
        return result

    delta = v.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = float(gain.iloc[1 : n + 1].mean())
    avg_loss = float(loss.iloc[1 : n + 1].mean())
    result.iloc[n] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(n + 1, len(v)):
        avg_gain = (avg_gain * (n - 1) + float(gain.iloc[i])) / n
        avg_loss = (avg_loss * (n - 1) + float(loss.iloc[i])) / n
        result.iloc[i] = _rsi_from_averages(avg_gain, avg_loss)

    return result


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder ATR on raw OHLC. `TR[0]` is NaN - there is no prior close to
    compare against, and fabricating `H - L` for the first bar would
    pollute the seed. `TR[i] = max(H[i]-L[i], |H[i]-C[i-1]|, |L[i]-C[i-1]|)`
    for `i >= 1`. Seed = mean of `TR[1..n]`, first valid at position `n`.
    Then Wilder smoothing: `avg[i] = (avg[i-1]*(n-1) + TR[i]) / n`.
    """
    h = high.astype(np.float64)
    lo = low.astype(np.float64)
    c = close.astype(np.float64)
    result = pd.Series(np.nan, index=h.index, dtype=np.float64)
    if len(h) <= n:
        return result

    prev_close = c.shift(1)
    tr = pd.concat(
        [h - lo, (h - prev_close).abs(), (lo - prev_close).abs()], axis=1
    ).max(axis=1)
    # No prior close for the first bar - do not fabricate a TR from H - L.
    tr.iloc[0] = np.nan

    seed = float(tr.iloc[1 : n + 1].mean())
    result.iloc[n] = seed
    avg = seed
    for i in range(n + 1, len(h)):
        avg = (avg * (n - 1) + float(tr.iloc[i])) / n
        result.iloc[i] = avg

    return result


def realized_volatility(close: pd.Series, n: int = 20, periods_per_year: int = 252) -> pd.Series:
    """Rolling annualized volatility of simple daily returns.

    `r[i] = close[i] / close[i-1] - 1`; rolling sample std (`ddof=1`) over
    `n` returns, scaled by `sqrt(periods_per_year)`. First valid at
    position `n`. Callers pass `adj_close` - raw `close` carries split and
    dividend jumps that are not real return.
    """
    c = close.astype(np.float64)
    returns = c.pct_change()
    vol = returns.rolling(window=n, min_periods=n).std(ddof=1)
    return vol * math.sqrt(periods_per_year)


def breakout_level(high: pd.Series, n: int = 20) -> pd.Series:
    """`out[i] = max(high[i-n : i])` - the max high over the preceding `n`
    sessions, excluding the current bar. NaN until `n` preceding bars
    exist (position `< n`).
    """
    h = high.astype(np.float64)
    return h.shift(1).rolling(window=n, min_periods=n).max()


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """`out[i] = volume[i] / mean(volume[i-n : i])` - current volume over
    the mean of the preceding `n` sessions (current bar excluded from the
    denominator). NaN, never `inf`, when the denominator is zero or the
    preceding window is incomplete.
    """
    v = volume.astype(np.float64)
    denom = v.shift(1).rolling(window=n, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        result = v / denom
    return result.where(denom != 0, np.nan)


def relative_volume_latest(volume: pd.Series, n: int = 20) -> float | None:
    """The most recent `relative_volume` value, or `None` (never `inf`
    and never NaN-as-a-float) when the window is incomplete or the
    denominator is zero.
    """
    series = relative_volume(volume, n)
    if series.empty:
        return None
    last = series.iloc[-1]
    if pd.isna(last):
        return None
    return float(last)
