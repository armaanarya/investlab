"""Candidate screening.

Turns cached bars into a ranked shortlist. Nothing here decides to trade: it
produces evidence, the profile's rules decide eligibility, and the sizing
solver decides quantity. The student decides everything else.

Every value is computed from bars strictly at or before the decision date, so
the screen can be replayed on a past date and give the same answer it gave
then. That property is tested by the prefix-invariance test in the features
suite, and it is the whole reason to compute from a local cache rather than a
live quote.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from investlab.contracts import Bar, Instrument
from investlab.features.indicators import (
    atr,
    breakout_level,
    ema,
    frame_from_bars,
    macd,
    relative_volume,
    rsi,
)
from investlab.features.ranking import InsufficientCoverage, percentile_ranks

# A stock needs enough history for its slowest indicator to be meaningful.
# EMA200 is mathematically defined at 200 bars but numerically unsettled;
# 252 sessions is one trading year and is the research default.
MIN_HISTORY = 252


@dataclass(frozen=True, slots=True)
class Screened:
    """One candidate with the numbers behind it.

    `score` is a 0-100 cross-sectional rank. It is explicitly NOT a
    probability, and nothing downstream may present it as one.
    """

    symbol: str
    instrument: Instrument
    close: Decimal
    score: float
    momentum_21d: float
    rsi14: float
    atr14: Decimal
    breakout_20d: Decimal
    rel_volume: float
    above_ema50: bool
    above_ema200: bool

    @property
    def protective_reference(self) -> Decimal:
        """A 2x ATR stop distance below the close.

        This is a reference for sizing, not a guaranteed exit. DECA prices
        every fill at the session close and does not execute intraday stops,
        so on that profile this is a review threshold: you see it breached on
        a completed close and place an order for the next close.
        """
        return self.close - (Decimal(2) * self.atr14)


def screen_universe(
    bars_by_symbol: dict[str, list[Bar]],
    instruments: dict[str, Instrument],
    as_of: date,
    *,
    min_history: int = MIN_HISTORY,
) -> tuple[list[Screened], dict[str, str]]:
    """Rank every symbol with enough history. Returns (ranked, skipped).

    `skipped` maps a symbol to why it was dropped, so a name never disappears
    silently. A candidate the student expected to see and cannot is a question
    the tool should be able to answer.
    """
    metrics: dict[str, dict] = {}
    skipped: dict[str, str] = {}

    for symbol, bars in bars_by_symbol.items():
        usable = [b for b in bars if b.session <= as_of]
        if len(usable) < min_history:
            skipped[symbol] = f"only {len(usable)} sessions, needs {min_history}"
            continue

        frame = frame_from_bars(usable)
        close, high, low, vol = frame["close"], frame["high"], frame["low"], frame["volume"]

        rsi14 = rsi(close).iloc[-1]
        atr14 = atr(high, low, close).iloc[-1]
        rv = relative_volume(vol).iloc[-1]
        brk = breakout_level(high).iloc[-1]
        e50, e200 = ema(close, 50).iloc[-1], ema(close, 200).iloc[-1]
        macd_res = macd(close)
        hist = macd_res.histogram.iloc[-1]

        last, prior = close.iloc[-1], close.iloc[-22]
        if prior <= 0 or any(v != v for v in (rsi14, atr14, e50, e200, hist)):
            skipped[symbol] = "indicator warm-up incomplete"
            continue

        momentum = (last / prior) - 1.0
        metrics[symbol] = {
            "close": Decimal(str(round(last, 4))),
            "momentum": float(momentum),
            "rsi": float(rsi14),
            "atr": Decimal(str(round(atr14, 4))),
            "breakout": Decimal(str(round(brk, 4))) if brk == brk else Decimal("0"),
            "relvol": float(rv) if rv == rv else 0.0,
            # Normalising the MACD histogram by ATR is what makes it
            # comparable across stocks at different price levels.
            "hist_atr": float(hist) / float(atr14) if atr14 else 0.0,
            "above50": bool(last > e50),
            "above200": bool(last > e200),
        }

    if not metrics:
        return [], skipped

    eligible = list(metrics)
    components = []
    for key in ("momentum", "relvol", "hist_atr"):
        result = percentile_ranks({s: metrics[s][key] for s in eligible}, as_of, eligible=eligible)
        if isinstance(result, InsufficientCoverage):
            for s in eligible:
                skipped.setdefault(s, f"insufficient cross-section for {key}")
            return [], skipped
        components.append(result.ranks)

    ranked: list[Screened] = []
    for symbol in eligible:
        m = metrics[symbol]
        # percentile_ranks returns 0-1; present as 0-100 so a reader is never
        # tempted to read 0.87 as "87% confident". It is a rank, not a
        # probability, and the two must not be confusable.
        score = 100.0 * sum(c[symbol] for c in components) / len(components)
        ranked.append(
            Screened(
                symbol=symbol,
                instrument=instruments[symbol],
                close=m["close"],
                score=round(score, 1),
                momentum_21d=m["momentum"],
                rsi14=m["rsi"],
                atr14=m["atr"],
                breakout_20d=m["breakout"],
                rel_volume=m["relvol"],
                above_ema50=m["above50"],
                above_ema200=m["above200"],
            )
        )

    # Ties break on symbol so the same inputs always give the same order.
    ranked.sort(key=lambda s: (-s.score, s.symbol))
    return ranked, skipped


def load_bars(cache, symbols: list[str], as_of: date, lookback_days: int = 500):
    """Read what the cache holds, reporting rather than raising on gaps."""
    from investlab.data.cache import SymbolNotCachedError

    out: dict[str, list[Bar]] = {}
    missing: list[str] = []
    start = as_of - timedelta(days=lookback_days)
    for s in symbols:
        try:
            bars = cache.read(s, start, as_of)
            if bars:
                out[s] = bars
            else:
                missing.append(s)
        except SymbolNotCachedError:
            missing.append(s)
    return out, missing
