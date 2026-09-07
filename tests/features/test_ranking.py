"""Cross-sectional ranking tests.

The eligible set is always passed in explicitly by the caller — this module
never invents one. A symbol in `values` but not in `eligible` is ignored
silently; a symbol in `eligible` but missing (absent or NaN) from `values`
is handled by the declared `MissingPolicy`. Below `min_eligible` names, the
function returns an explicit `InsufficientCoverage` marker, never a
partial ranking and never an exception.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from investlab.features.ranking import (
    MIN_ELIGIBLE,
    InsufficientCoverage,
    MissingPolicy,
    RankResult,
    cross_section,
    percentile_ranks,
)

D = date(2026, 9, 4)


def _symbols(n: int) -> list[str]:
    return [f"S{i:02d}" for i in range(n)]


def test_below_twenty_names_returns_insufficient_coverage():
    symbols = _symbols(19)
    values = {s: float(i) for i, s in enumerate(symbols)}
    out = percentile_ranks(values, D, eligible=symbols)
    assert isinstance(out, InsufficientCoverage)
    assert out.n_eligible == 19
    assert out.required == MIN_ELIGIBLE
    assert out.as_of == D


def test_exactly_min_eligible_is_sufficient():
    symbols = _symbols(MIN_ELIGIBLE)
    values = {s: float(i) for i, s in enumerate(symbols)}
    out = percentile_ranks(values, D, eligible=symbols)
    assert isinstance(out, RankResult)


def test_best_is_one_and_worst_is_zero():
    symbols = _symbols(20)
    values = {s: float(i) for i, s in enumerate(symbols)}  # S19 highest
    out = percentile_ranks(values, D, eligible=symbols)
    assert isinstance(out, RankResult)
    assert out.ranks["S19"] == 1.0
    assert out.ranks["S00"] == 0.0
    assert out.as_of == D
    assert out.policy == MissingPolicy.DROP
    assert out.ascending is False


def test_ranks_are_monotonic_in_value():
    symbols = _symbols(20)
    values = {s: float(i) for i, s in enumerate(symbols)}
    out = percentile_ranks(values, D, eligible=symbols)
    ordered = sorted(symbols, key=lambda s: values[s])
    ranks_in_value_order = [out.ranks[s] for s in ordered]
    assert ranks_in_value_order == sorted(ranks_in_value_order)


def test_ties_break_by_symbol_and_are_stable():
    symbols = _symbols(20)
    values = {s: 1.0 for s in symbols}  # every value tied
    out = percentile_ranks(values, D, eligible=symbols)
    assert isinstance(out, RankResult)
    # Ties broken by ascending symbol: S00 sorts first (best), S19 last (worst).
    assert out.ranks["S00"] == 1.0
    assert out.ranks["S19"] == 0.0
    step = 1.0 / 19
    assert out.ranks["S01"] == pytest.approx(1.0 - step)
    assert out.ranks["S02"] == pytest.approx(1.0 - 2 * step)


def test_drop_policy_excludes_missing_and_reports_them():
    symbols = _symbols(22)
    values = {s: float(i) for i, s in enumerate(symbols)}
    del values["S05"]
    del values["S10"]
    out = percentile_ranks(values, D, eligible=symbols, missing=MissingPolicy.DROP)
    assert isinstance(out, RankResult)
    assert out.dropped == ("S05", "S10")
    assert "S05" not in out.ranks
    assert "S10" not in out.ranks
    assert out.n_eligible == 20  # 22 eligible minus 2 dropped


def test_rank_worst_policy_places_missing_last():
    symbols = _symbols(22)
    values = {s: float(i) for i, s in enumerate(symbols)}
    del values["S05"]
    del values["S21"]
    out = percentile_ranks(values, D, eligible=symbols, missing=MissingPolicy.RANK_WORST)
    assert isinstance(out, RankResult)
    assert out.dropped == ()
    assert out.n_eligible == 22  # missing symbols still count toward n_eligible
    present_ranks = [out.ranks[s] for s in symbols if s not in ("S05", "S21")]
    assert out.ranks["S05"] < min(present_ranks)
    assert out.ranks["S21"] < min(present_ranks)
    # Among the missing themselves, ascending symbol order: S05 before S21.
    assert out.ranks["S05"] > out.ranks["S21"]
    assert out.ranks["S21"] == 0.0


def test_nan_counts_as_missing():
    symbols = _symbols(21)
    values = {s: float(i) for i, s in enumerate(symbols)}
    values["S05"] = float("nan")
    out = percentile_ranks(values, D, eligible=symbols, missing=MissingPolicy.DROP)
    assert isinstance(out, RankResult)
    assert "S05" in out.dropped
    assert "S05" not in out.ranks
    assert out.n_eligible == 20


def test_ascending_flips_the_order():
    symbols = _symbols(20)
    values = {s: float(i) for i, s in enumerate(symbols)}  # S00 lowest, S19 highest
    out = percentile_ranks(values, D, eligible=symbols, ascending=True)
    assert isinstance(out, RankResult)
    assert out.ranks["S00"] == 1.0
    assert out.ranks["S19"] == 0.0
    assert out.ascending is True


def test_symbols_not_in_eligible_are_ignored_silently():
    symbols = _symbols(20)
    values = {s: float(i) for i, s in enumerate(symbols)}
    values["NOT_ELIGIBLE"] = 999.0
    out = percentile_ranks(values, D, eligible=symbols)
    assert isinstance(out, RankResult)
    assert "NOT_ELIGIBLE" not in out.ranks
    assert out.n_eligible == 20


def test_custom_min_eligible_threshold():
    symbols = _symbols(10)
    values = {s: float(i) for i, s in enumerate(symbols)}
    out = percentile_ranks(values, D, eligible=symbols, min_eligible=10)
    assert isinstance(out, RankResult)
    out2 = percentile_ranks(values, D, eligible=symbols, min_eligible=11)
    assert isinstance(out2, InsufficientCoverage)


# ---------------------------------------------------------------------------
# cross_section
# ---------------------------------------------------------------------------


def test_cross_section_extracts_value_at_date():
    d0, d1 = date(2026, 1, 2), date(2026, 1, 3)
    s_a = pd.Series([1.0, 2.0], index=pd.Index([d0, d1], name="session"))
    s_b = pd.Series([np.nan, 5.0], index=pd.Index([d0, d1], name="session"))
    s_c = pd.Series([9.0], index=pd.Index([d0], name="session"))  # no entry for d1

    at_d1 = cross_section({"A": s_a, "B": s_b, "C": s_c}, d1)
    assert at_d1 == {"A": 2.0, "B": 5.0}

    at_d0 = cross_section({"A": s_a, "B": s_b, "C": s_c}, d0)
    assert at_d0 == {"A": 1.0, "C": 9.0}  # B is NaN at d0, omitted


def test_cross_section_feeds_directly_into_percentile_ranks():
    symbols = _symbols(20)
    series_by_symbol = {
        s: pd.Series([float(i)], index=pd.Index([D], name="session")) for i, s in enumerate(symbols)
    }
    values = cross_section(series_by_symbol, D)
    out = percentile_ranks(values, D, eligible=symbols)
    assert isinstance(out, RankResult)
    assert out.ranks["S19"] == 1.0
