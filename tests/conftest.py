"""Global test guards."""

from datetime import date
from decimal import Decimal

import numpy as np
import pytest

from investlab import calendar as cal
from investlab.config import CACHE_DIR_ENV
from investlab.contracts import AssetClass, Bar, Instrument
from investlab.data.universe import Universe
from investlab.earnings import RESEARCH_ROOT_ENV
from investlab.store import LEDGER_ROOT_ENV


@pytest.fixture(autouse=True)
def isolate_ledger(tmp_path, monkeypatch):
    """Point every test at a throwaway ledger and research root.

    Autouse and unconditional: the real ledger under `ledger/` and the research
    files under `research/` are git-tracked competition state, and a test run
    once appended live trades to the ledger. No test may reach either, whether
    or not it remembers to ask. Provider credentials are cleared too, so no
    test can reach a live data API by accident.
    """
    monkeypatch.setenv(LEDGER_ROOT_ENV, str(tmp_path / "ledger"))
    monkeypatch.setenv(RESEARCH_ROOT_ENV, str(tmp_path / "research"))
    monkeypatch.setenv(CACHE_DIR_ENV, str(tmp_path / "cache"))
    for var in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "TIINGO_API_KEY"):
        monkeypatch.delenv(var, raising=False)


SECTORS = (
    "Information Technology",
    "Financials",
    "Energy",
    "Health Care",
    "Industrials",
    "Consumer Staples",
)


def build_synthetic_market(
    n_stocks: int = 24,
    start: date = date(2025, 1, 2),
    end: date = date(2026, 9, 11),
    seed: int = 11,
    with_bitcoin: bool = False,
):
    """A deterministic market: `n_stocks` stocks with drifts from strongly
    negative to strongly positive, SPY, the FXAIX fund, and optionally two
    spot bitcoin ETFs sharing one exposure group."""
    sessions = cal.sessions_between(start, end)
    rng = np.random.default_rng(seed)
    bars: dict[str, list[Bar]] = {}
    instruments: list[Instrument] = []

    def walk(symbol: str, p0: float, drift: float, vol: float) -> list[Bar]:
        price = p0
        out = []
        for d in sessions:
            ret = drift + rng.normal(0.0, vol)
            o = price
            c = max(o * (1 + ret), 1.0)
            spread = abs(rng.normal(0.0, vol / 2))
            h = max(o, c) * (1 + spread)
            lo = min(o, c) * (1 - spread)
            q = lambda x: Decimal(str(round(x, 4)))  # noqa: E731
            bo, bh, bl, bc = q(o), q(h), q(lo), q(c)
            bh, bl = max(bh, bo, bc), min(bl, bo, bc)
            volume = int(1_000_000 * (1 + rng.random()))
            out.append(Bar(symbol, d, bo, bh, bl, bc, bc, volume, "synthetic"))
            price = c
        return out

    half = n_stocks / 2
    for i in range(n_stocks):
        sym = f"S{i:02d}"
        bars[sym] = walk(sym, 50.0 + 5 * i, 0.002 * (i - half) / half, 0.012)
        instruments.append(
            Instrument(
                sym,
                sym,
                AssetClass.STOCK,
                "NYSE",
                market_cap=Decimal("100000000000"),
                sector=SECTORS[i % len(SECTORS)],
            )
        )
    bars["SPY"] = walk("SPY", 400.0, 0.0004, 0.008)
    bars["FXAIX"] = walk("FXAIX", 200.0, 0.0004, 0.008)
    instruments.append(
        Instrument(
            "FXAIX",
            "Fidelity 500 Index Fund",
            AssetClass.MUTUAL_FUND,
            "MUTUAL FUND",
            sector="US Equity Fund",
        )
    )
    if with_bitcoin:
        for sym in ("BTC1", "BTC2"):
            # A strong trend that accelerates over the last month on rising
            # volume, so both land at the top of every ranking component.
            series = walk(sym, 60.0, 0.003, 0.010)
            price = float(series[-22].close)
            for k, b in enumerate(series[-21:]):
                o, price = price, price * 1.012
                vol = 1_000_000 * (3 if k == 20 else 1.5)
                series[len(series) - 21 + k] = Bar(
                    sym,
                    b.session,
                    Decimal(str(round(o, 4))),
                    Decimal(str(round(price * 1.004, 4))),
                    Decimal(str(round(o * 0.996, 4))),
                    Decimal(str(round(price, 4))),
                    Decimal(str(round(price, 4))),
                    int(vol),
                    "synthetic",
                )
            bars[sym] = series
            instruments.append(
                Instrument(
                    sym,
                    sym,
                    AssetClass.ETF,
                    "NASDAQ",
                    market_cap=Decimal("50000000000"),
                    is_commodity_or_crypto_trust=True,
                    is_spot_bitcoin_etf=True,
                    sector="Digital Assets",
                    exposure_group="BTC",
                )
            )
    return Universe(name="synthetic", instruments=tuple(instruments)), bars


@pytest.fixture(scope="session")
def synth_market():
    from investlab.screen import compute_features

    universe, bars = build_synthetic_market()
    return {"universe": universe, "bars": bars, "features": compute_features(bars)}


@pytest.fixture(scope="session")
def synth_market_btc():
    from investlab.screen import compute_features

    universe, bars = build_synthetic_market(with_bitcoin=True)
    return {"universe": universe, "bars": bars, "features": compute_features(bars)}
