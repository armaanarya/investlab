"""Cross-sectional percentile ranks.

`percentile_ranks` never invents its own eligible set — the caller passes
one in explicitly, typically the fixed `data.universe.default_universe()`
filtered to whatever asset class or liquidity screen applies that day. A
symbol present in `values` but absent from `eligible` is ignored silently;
a symbol in `eligible` but missing from `values` (absent entirely, or NaN)
is handled by the declared `MissingPolicy`.

Ranking is ordinal, ties broken by ascending symbol so the result is
deterministic and stable run to run: the sort key is `(-value, symbol)`
when `ascending=False` (higher is better) and `(value, symbol)` when
`ascending=True` (lower is better). Percentile position `i` in a set of
size `N` maps to `(N - 1 - i) / (N - 1)`, so the best name scores `1.0` and
the worst scores `0.0`.

Below `min_eligible` names, `percentile_ranks` returns `InsufficientCoverage`
— an explicit marker object, never an exception and never a partial
ranking on too few names to mean anything. `min_eligible` is checked
against the actual size of the ranked set (`n_eligible`): under
`MissingPolicy.DROP` that is `len(eligible) - len(dropped)`; under
`MissingPolicy.RANK_WORST` every eligible symbol is ranked (present or
appended as worst), so it is `len(eligible)`. That is also why the
percentile formula's `N - 1` denominator is never zero — the coverage gate
guarantees `N >= min_eligible >= 1` before it is ever evaluated with the
default `MIN_ELIGIBLE = 20`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd

MIN_ELIGIBLE = 20


class MissingPolicy(str, Enum):
    DROP = "drop"
    RANK_WORST = "rank_worst"


@dataclass(frozen=True)
class RankResult:
    as_of: date
    ranks: dict[str, float]
    dropped: tuple[str, ...]
    n_eligible: int
    policy: MissingPolicy
    ascending: bool


@dataclass(frozen=True)
class InsufficientCoverage:
    as_of: date
    n_eligible: int
    required: int
    detail: str


def _is_missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def percentile_ranks(
    values: Mapping[str, float],
    as_of: date,
    *,
    eligible: Iterable[str],
    missing: MissingPolicy = MissingPolicy.DROP,
    ascending: bool = False,
    min_eligible: int = MIN_ELIGIBLE,
) -> RankResult | InsufficientCoverage:
    eligible_sorted = sorted(set(eligible))

    present: dict[str, float] = {}
    missing_symbols: list[str] = []
    for symbol in eligible_sorted:
        v = values.get(symbol)
        if _is_missing(v):
            missing_symbols.append(symbol)
        else:
            present[symbol] = float(v)

    if missing is MissingPolicy.DROP:
        dropped = tuple(missing_symbols)
        n_eligible = len(present)
    else:
        dropped = ()
        n_eligible = len(present) + len(missing_symbols)

    if n_eligible < min_eligible:
        return InsufficientCoverage(
            as_of=as_of,
            n_eligible=n_eligible,
            required=min_eligible,
            detail=(
                f"only {n_eligible} eligible name(s) at {as_of}, "
                f"need at least {min_eligible}"
            ),
        )

    if ascending:
        present_order = sorted(present.keys(), key=lambda s: (present[s], s))
    else:
        present_order = sorted(present.keys(), key=lambda s: (-present[s], s))

    if missing is MissingPolicy.RANK_WORST:
        order = present_order + sorted(missing_symbols)
    else:
        order = present_order

    n = len(order)
    ranks = {symbol: (n - 1 - i) / (n - 1) for i, symbol in enumerate(order)}

    return RankResult(
        as_of=as_of,
        ranks=ranks,
        dropped=dropped,
        n_eligible=n_eligible,
        policy=missing,
        ascending=ascending,
    )


def cross_section(series_by_symbol: Mapping[str, pd.Series], as_of: date) -> dict[str, float]:
    """Pull each symbol's feature value at `as_of` out of its own
    date-indexed series, producing the flat `{symbol: value}` mapping
    `percentile_ranks` expects as `values`.

    A symbol whose series has no entry for `as_of`, or whose value there is
    NaN, is simply omitted — `percentile_ranks`'s `eligible` set and
    `missing` policy decide how that omission is handled, not this
    function.
    """
    result: dict[str, float] = {}
    for symbol, series in series_by_symbol.items():
        if as_of not in series.index:
            continue
        value = series.loc[as_of]
        if isinstance(value, pd.Series):
            # A duplicate index entry makes the lookup ambiguous; skip
            # rather than silently pick one.
            continue
        if _is_missing(value):
            continue
        result[symbol] = float(value)
    return result
