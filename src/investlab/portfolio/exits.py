"""Exit rules for held positions.

DECA prices every order at the session close and executes no intraday stops,
so every rule here is evaluated on a completed close and answered with an order
for the next close. Three rules, in priority order:

1. **Stop.** The close is at or below the active stop, which is the higher of
   the entry stop recorded with the fill and the trailing stop.
2. **Trailing stop.** The highest close since entry minus `trail_atr_multiple`
   x ATR(14), ratcheted so it only ever rises. Named separately from the entry
   stop so the sheet says whether a position was stopped out at a loss or gave
   back part of a gain.
3. **Momentum lost.** After a minimum hold, the cross-sectional score has
   fallen below the exit threshold while the close is under EMA50. The signal
   the position was bought on is gone.

Every value is read from `FeatureFrame` rows at or before the decision date, so
the rules replay identically in a backtest. Output is numbers and the name of
the rule that fired, never an opinion about the company.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from investlab.screen import FeatureFrame


class ExitReason(str, Enum):
    STOP = "stop"
    TRAILING_STOP = "trailing_stop"
    MOMENTUM_LOST = "momentum_lost"


@dataclass(frozen=True, slots=True)
class ExitEvaluation:
    symbol: str
    close_session: date | None
    last_close: Decimal | None
    entry_stop: Decimal | None
    trailing_stop: Decimal | None
    active_stop: Decimal | None
    highest_close: Decimal | None
    sessions_held: int
    score: float | None
    above_ema50: bool | None
    reason: ExitReason | None
    detail: str

    @property
    def should_sell(self) -> bool:
        return self.reason is not None

    @property
    def distance_to_stop_pct(self) -> Decimal | None:
        if self.active_stop is None or not self.last_close:
            return None
        return ((self.last_close - self.active_stop) / self.last_close * Decimal(100)).quantize(
            Decimal("0.01")
        )


def _dec(value: float) -> Decimal:
    return Decimal(str(round(float(value), 4)))


def entry_stop_at(
    ff: FeatureFrame, session: date, price: Decimal, atr_multiple: Decimal
) -> Decimal | None:
    """`price - atr_multiple x ATR(14)` using the ATR as of `session`'s close.

    The default entry stop for a fill recorded without one. None when the
    cache does not reach back to `session` or ATR is still warming up.
    """
    idx = ff.index_of(session)
    if idx is None:
        return None
    a = ff.frame["atr14"].iloc[idx]
    if a != a:
        return None
    return (price - atr_multiple * _dec(a)).quantize(Decimal("0.01"))


def trailing_stop(
    ff: FeatureFrame, opened: date, as_of: date, atr_multiple: Decimal
) -> tuple[Decimal | None, Decimal | None]:
    """(ratcheted trailing stop, highest close) from `opened` through `as_of`.

    The fill happens at `opened`'s close, so that close starts the high-water
    mark. At each later session the candidate stop is the running highest close
    minus `atr_multiple` x that session's ATR; the stop is the maximum candidate
    seen so far, so a widening ATR can never lower it.
    """
    start = ff.index_of(opened)
    if start is None:
        start = ff.count_upto(opened)
    end = ff.count_upto(as_of)
    if end <= start:
        return None, None
    closes = ff.frame["close"].iloc[start:end]
    atrs = ff.frame["atr14"].iloc[start:end]
    highest = float("-inf")
    best: float | None = None
    multiple = float(atr_multiple)
    for c, a in zip(closes, atrs, strict=True):
        highest = max(highest, float(c))
        if a != a:
            continue
        candidate = highest - multiple * float(a)
        best = candidate if best is None else max(best, candidate)
    trail = _dec(best).quantize(Decimal("0.01")) if best is not None else None
    return trail, _dec(highest).quantize(Decimal("0.01"))


def evaluate_exit(
    symbol: str,
    ff: FeatureFrame | None,
    *,
    opened: date,
    as_of: date,
    entry_stop: Decimal | None,
    score: float | None,
    above_ema50: bool | None,
    trail_atr_multiple: Decimal,
    exit_score_below: float,
    min_hold_sessions: int,
) -> ExitEvaluation:
    if ff is None or ff.count_upto(as_of) == 0:
        return ExitEvaluation(
            symbol,
            None,
            None,
            entry_stop,
            None,
            entry_stop,
            None,
            0,
            score,
            above_ema50,
            None,
            "no cached bars; exits cannot be evaluated, re-pull data",
        )

    n = ff.count_upto(as_of)
    close_session = ff.sessions[n - 1]
    last_close = _dec(ff.frame["close"].iloc[n - 1]).quantize(Decimal("0.01"))
    trail, highest = trailing_stop(ff, opened, as_of, trail_atr_multiple)
    candidates = [s for s in (entry_stop, trail) if s is not None]
    active = max(candidates) if candidates else None
    held = max(0, n - ff.count_upto(opened))

    reason: ExitReason | None = None
    detail = "hold"
    if active is not None and last_close <= active:
        fired_trail = trail is not None and (entry_stop is None or trail > entry_stop)
        reason = ExitReason.TRAILING_STOP if fired_trail else ExitReason.STOP
        label = "trailing stop" if fired_trail else "entry stop"
        detail = (
            f"{close_session} close ${last_close:,.2f} is at or below the {label} ${active:,.2f}"
        )
    elif (
        held >= min_hold_sessions
        and score is not None
        and score < exit_score_below
        and above_ema50 is False
    ):
        reason = ExitReason.MOMENTUM_LOST
        detail = (
            f"score {score:.0f} is below {exit_score_below:.0f} and the close is under "
            f"EMA50 after {held} sessions held"
        )
    elif active is None:
        detail = "hold; no stop could be computed (cache does not reach the entry session)"

    return ExitEvaluation(
        symbol=symbol,
        close_session=close_session,
        last_close=last_close,
        entry_stop=entry_stop,
        trailing_stop=trail,
        active_stop=active,
        highest_close=highest,
        sessions_held=held,
        score=score,
        above_ema50=above_ema50,
        reason=reason,
        detail=detail,
    )
