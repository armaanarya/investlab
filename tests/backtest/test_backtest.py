from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from investlab.backtest.baselines import run_baselines
from investlab.backtest.engine import BacktestSettings, CloseBook, run_strategy
from investlab.competitions.deca import DecaProfile
from investlab.config import DecaConfig
from investlab.reports.tearsheet import max_drawdown, write_tearsheet
from investlab.screen import compute_features

SETTINGS = BacktestSettings(start=date(2026, 6, 1), end=date(2026, 9, 11))


@pytest.fixture(scope="module")
def replay(synth_market):
    return run_strategy(
        synth_market["bars"],
        synth_market["universe"],
        DecaProfile(),
        DecaConfig(),
        SETTINGS,
        features=synth_market["features"],
    )


def test_sessions_follow_the_benchmark(synth_market, replay):
    expected = [
        d
        for d in CloseBook(synth_market["bars"]).sessions("SPY")
        if SETTINGS.start <= d <= SETTINGS.end
    ]
    assert replay.sessions == expected


def test_cash_never_goes_negative_and_equity_adds_up(replay):
    assert min(replay.cash) >= 0
    assert all(
        e == c + i for e, c, i in zip(replay.equity, replay.cash, replay.invested, strict=True)
    )


def test_trades_are_whole_shares_and_the_compliance_leg_goes_first(replay):
    assert replay.trades
    assert all(isinstance(t.quantity, int) and t.quantity > 0 for t in replay.trades)
    first = [t for t in replay.trades if t.session == replay.sessions[0]]
    fund = [t for t in first if t.reason == "compliance"]
    assert fund and fund[0].price * fund[0].quantity >= Decimal("10000")
    assert all(t.quantity >= 10 for t in replay.trades if t.reason == "entry")


def test_replay_never_sees_the_future(synth_market, replay):
    cut = date(2026, 8, 3)
    bars = {s: [b for b in bs if b.session <= cut] for s, bs in synth_market["bars"].items()}
    short = run_strategy(
        bars,
        synth_market["universe"],
        DecaProfile(),
        DecaConfig(),
        BacktestSettings(start=SETTINGS.start, end=cut),
        features=compute_features(bars),
    )
    k = len(short.sessions)
    assert replay.equity[:k] == short.equity
    assert [t for t in replay.trades if t.session <= cut] == short.trades


def test_baselines_cover_the_same_sessions(synth_market, replay):
    runs = run_baselines(
        synth_market["bars"],
        synth_market["universe"],
        DecaProfile(),
        DecaConfig(),
        SETTINGS,
        features=synth_market["features"],
    )
    names = {r.name for r in runs}
    assert {
        "cash",
        "buy_and_hold_SPY",
        "equal_weight",
        "momentum_top8",
        "compliance_aware",
    } <= names
    assert all(r.sessions == replay.sessions for r in runs)
    cash = next(r for r in runs if r.name == "cash")
    assert cash.equity == sorted(cash.equity)


def test_tearsheet_writes_every_artifact(synth_market, replay, tmp_path):
    runs = run_baselines(
        synth_market["bars"],
        synth_market["universe"],
        DecaProfile(),
        DecaConfig(),
        SETTINGS,
        features=synth_market["features"],
    )
    paths = write_tearsheet(
        tmp_path,
        replay,
        runs,
        closes=CloseBook(synth_market["bars"]),
        settings=SETTINGS,
        provenance={"generated": "test"},
    )
    for p in paths.values():
        assert p.exists()
    summary = json.loads(paths["summary"].read_text())
    assert len(summary["runs"]) == 1 + len(runs)
    html = paths["html"].read_text()
    assert "not annualised" in html and "survivorship" in html.lower()


def test_max_drawdown_depth_and_duration():
    depth, sessions = max_drawdown([Decimal(v) for v in (100, 110, 99, 121)])
    assert depth == 10.0 and sessions == 1
