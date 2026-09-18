"""Laura Gao's cash flows and the projection engine."""

from __future__ import annotations

import json

import pytest

from investlab import wharton_client as wc


def test_case_numbering_and_flows():
    assert wc.year_number(2026) == 0 and wc.year_number(2033) == 7
    assert sum(a for _, a in wc.CONTRIBUTIONS) == 450_000
    assert wc.N_PAYMENTS == 10


def test_reserve_pv_is_an_annuity_due():
    assert wc.reserve_pv(0.0) == 500_000
    # First payment undiscounted: at 4% the reserve is about $421,767.
    assert wc.reserve_pv(0.04) == pytest.approx(421_766.9, abs=1)


def test_the_shipped_config_loads_and_every_strategy_projects():
    book = wc.load_strategy_book()
    assert book.active in book.strategies
    for name in book.strategies:
        p = wc.project(book, name, paths=2_000, seed=1)
        assert 0.0 <= p.funding_probability <= 1.0
        assert (p.contribution >= 0).all()


def test_projection_is_reproducible_with_a_seed():
    book = wc.load_strategy_book()
    a = wc.project(book, book.active, paths=1_000, seed=3)
    b = wc.project(book, book.active, paths=1_000, seed=3)
    assert (a.value_2033 == b.value_2033).all()


def test_riskless_world_matches_the_deterministic_path(tmp_path):
    cfg = {
        "sleeves": {"bills": {"expected_return": 0.03, "volatility": 0.0, "source": "test"}},
        "strategies": {
            "flat": {
                "phases": [{"from": 2027, "to": 2032, "weights": {"bills": 1.0}}],
                "reserve": {
                    "method": "pv",
                    "discount_rate": 0.03,
                    "expected_return": 0.03,
                    "volatility": 0.0,
                },
            }
        },
        "active": "flat",
    }
    path = tmp_path / "s.json"
    path.write_text(json.dumps(cfg))
    p = wc.project(wc.load_strategy_book(path), "flat", paths=100)
    expected = 300_000 * 1.03**6 + 150_000 * 1.03**5
    assert p.deterministic_2033 == pytest.approx(expected)
    assert p.pct(p.value_2033, 0.5) == pytest.approx(expected)
    assert p.funding_probability == 1.0
    assert p.pct(p.contribution, 0.5) == pytest.approx(expected - wc.reserve_pv(0.03))


def test_confidence_reserve_grows_with_the_confidence_asked():
    pol = wc.ReservePolicy("confidence", expected_return=0.04, volatility=0.06)
    low = wc.size_reserve_for_confidence(pol, 0.80, paths=4_000)
    high = wc.size_reserve_for_confidence(pol, 0.99, paths=4_000)
    assert high > low > 0


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda c: c["strategies"]["x"]["phases"][0]["weights"].update(a=0.5), "sum to"),
        (lambda c: c["strategies"]["x"]["phases"][0].update({"from": 2029}), "missing"),
        (lambda c: c.update(active="nope"), "active"),
        (lambda c: c["strategies"]["x"]["reserve"].update(method="guess"), "method"),
        (lambda c: c.update(correlations={"a|zzz": 0.5}), "unknown sleeve"),
    ],
)
def test_bad_configs_are_rejected(tmp_path, mutate, fragment):
    cfg = {
        "sleeves": {"a": {"expected_return": 0.05, "volatility": 0.1}},
        "strategies": {
            "x": {
                "phases": [{"from": 2027, "to": 2032, "weights": {"a": 1.0}}],
                "reserve": {"method": "pv", "expected_return": 0.04, "volatility": 0.0},
            }
        },
        "active": "x",
    }
    mutate(cfg)
    path = tmp_path / "s.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(wc.StrategyConfigError, match=fragment):
        wc.load_strategy_book(path)
