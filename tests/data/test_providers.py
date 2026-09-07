"""Price provider tests.

Everything here is offline: yfinance and Tiingo are exercised through an
injected `downloader`/`http_get` so the default suite never touches the
network. The one real call per provider is `@pytest.mark.live` and lives at
the bottom of this file.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from investlab.contracts import Bar
from investlab.data.providers import (
    ChainProvider,
    TiingoProvider,
    YFinanceProvider,
    chain,
    to_vendor_symbol,
)

FIXTURES = Path(__file__).parent / "fixtures"
START = date(2026, 8, 3)
END = date(2026, 8, 5)


def frame_for(tickers: list[str], rows: dict[str, list[dict]]) -> pd.DataFrame:
    """Build a yfinance-shaped MultiIndex frame: columns
    (Price, Ticker) in {Adj Close, Close, High, Low, Open, Volume} x tickers,
    one row per session date, matching what `yf.download(auto_adjust=False)`
    returns (verified 2026-09-06, see the core-data plan)."""
    fields = ["Adj Close", "Close", "High", "Low", "Open", "Volume"]
    all_dates = sorted({r["session"] for t in tickers for r in rows.get(t, [])})
    index = pd.DatetimeIndex(all_dates, name="Date")
    columns = pd.MultiIndex.from_product([fields, tickers], names=["Price", "Ticker"])
    df = pd.DataFrame(index=index, columns=columns, dtype="float64")
    for ticker in tickers:
        for row in rows.get(ticker, []):
            d = pd.Timestamp(row["session"])
            df.loc[d, ("Adj Close", ticker)] = row["adj_close"]
            df.loc[d, ("Close", ticker)] = row["close"]
            df.loc[d, ("High", ticker)] = row["high"]
            df.loc[d, ("Low", ticker)] = row["low"]
            df.loc[d, ("Open", ticker)] = row["open"]
            df.loc[d, ("Volume", ticker)] = row["volume"]
    return df


def one_row(session, o, h, l, c, ac, v):
    return {
        "session": session,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "adj_close": ac,
        "volume": v,
    }


# ---------------------------------------------------------------------------
# to_vendor_symbol
# ---------------------------------------------------------------------------


def test_to_vendor_symbol_replaces_dot_with_dash():
    assert to_vendor_symbol("BRK.B") == "BRK-B"


def test_to_vendor_symbol_leaves_plain_tickers_alone():
    assert to_vendor_symbol("AAPL") == "AAPL"


# ---------------------------------------------------------------------------
# YFinanceProvider
# ---------------------------------------------------------------------------


def test_brk_dot_b_is_sent_as_dash_and_returned_as_dot():
    seen = {}

    def fake_download(tickers, **kw):
        seen["tickers"] = list(tickers)
        return frame_for(
            ["BRK-B"],
            {"BRK-B": [one_row(date(2026, 8, 3), 500, 510, 495, 504, 503, 4249000)]},
        )

    provider = YFinanceProvider(downloader=fake_download)
    bars = provider.fetch(["BRK.B"], START, END)

    assert seen["tickers"] == ["BRK-B"]
    assert {b.symbol for b in bars} == {"BRK.B"}


def test_missing_symbol_returns_no_bars_and_does_not_raise():
    def fake_download(tickers, **kw):
        # yfinance's real behaviour for an unknown ticker: columns present,
        # zero rows (verified 2026-09-06).
        return frame_for(["NOPE"], {})

    provider = YFinanceProvider(downloader=fake_download)
    bars = provider.fetch(["NOPE"], START, END)
    assert bars == []


def test_transient_failure_is_retried_once_and_succeeds():
    calls = {"n": 0}

    def fake_download(tickers, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return frame_for(["AAPL"], {})  # empty on first try
        return frame_for(
            ["AAPL"],
            {"AAPL": [one_row(date(2026, 8, 3), 302, 305, 301, 303, 303.1, 75052000)]},
        )

    provider = YFinanceProvider(downloader=fake_download, retries=1)
    bars = provider.fetch(["AAPL"], START, END)

    assert calls["n"] == 2
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"


def test_retry_happens_at_most_once():
    calls = {"n": 0}

    def fake_download(tickers, **kw):
        calls["n"] += 1
        return frame_for(["AAPL"], {})  # always empty: a genuine delisting

    provider = YFinanceProvider(downloader=fake_download, retries=1)
    bars = provider.fetch(["AAPL"], START, END)

    assert calls["n"] == 2  # first try + exactly one retry, never more
    assert bars == []


def test_auto_adjust_false_is_passed():
    seen = {}

    def fake_download(tickers, **kw):
        seen["kw"] = kw
        return frame_for(["AAPL"], {})

    YFinanceProvider(downloader=fake_download).fetch(["AAPL"], START, END)
    assert seen["kw"].get("auto_adjust") is False


def test_close_and_adj_close_are_both_populated_and_differ():
    def fake_download(tickers, **kw):
        return frame_for(
            ["AAPL"],
            {"AAPL": [one_row(date(2026, 8, 3), 302, 305, 301, 303.42, 303.15, 75052000)]},
        )

    bars = YFinanceProvider(downloader=fake_download).fetch(["AAPL"], START, END)
    assert len(bars) == 1
    bar = bars[0]
    assert bar.close == Decimal("303.42")
    assert bar.adj_close == Decimal("303.15")
    assert bar.close != bar.adj_close


def test_rows_with_nan_ohlc_are_dropped():
    def fake_download(tickers, **kw):
        frame = frame_for(
            ["AAPL"],
            {
                "AAPL": [
                    one_row(date(2026, 8, 3), 302, 305, 301, 303.42, 303.15, 75052000),
                    one_row(date(2026, 8, 4), 303, 306, 302, 304.0, 303.9, 68001000),
                ]
            },
        )
        # Corrupt one row's High to NaN, simulating a partial vendor gap.
        frame.loc[pd.Timestamp(date(2026, 8, 4)), ("High", "AAPL")] = float("nan")
        return frame

    bars = YFinanceProvider(downloader=fake_download).fetch(["AAPL"], START, END)
    assert len(bars) == 1
    assert bars[0].session == date(2026, 8, 3)


def test_fetch_returns_bars_for_multiple_symbols_independently():
    def fake_download(tickers, **kw):
        return frame_for(
            ["AAPL", "MSFT"],
            {
                "AAPL": [one_row(date(2026, 8, 3), 302, 305, 301, 303.42, 303.15, 75052000)],
                "MSFT": [one_row(date(2026, 8, 3), 500, 510, 495, 505, 504, 20000000)],
            },
        )

    bars = YFinanceProvider(downloader=fake_download).fetch(["AAPL", "MSFT"], START, END)
    assert {b.symbol for b in bars} == {"AAPL", "MSFT"}


def test_yfinance_available_is_true_without_credentials():
    assert YFinanceProvider(downloader=lambda *a, **k: None).available() is True


# ---------------------------------------------------------------------------
# TiingoProvider
# ---------------------------------------------------------------------------


def test_tiingo_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    provider = TiingoProvider()
    assert provider.available() is False
    assert provider.fetch(["AAPL"], START, END) == []


def test_tiingo_available_with_explicit_key():
    provider = TiingoProvider(api_key="fake-key-123")
    assert provider.available() is True


def test_tiingo_available_with_env_key(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "fake-key-from-env")
    assert TiingoProvider().available() is True


def test_tiingo_parses_recorded_fixture():
    fixture_bytes = (FIXTURES / "tiingo_aapl.json").read_bytes()

    def fake_http_get(url: str) -> bytes:
        assert "AAPL" in url
        return fixture_bytes

    provider = TiingoProvider(api_key="fake-key", http_get=fake_http_get)
    bars = provider.fetch(["AAPL"], date(2026, 8, 3), date(2026, 8, 5))

    assert len(bars) == 3
    assert all(b.symbol == "AAPL" for b in bars)
    assert all(b.source == "tiingo" for b in bars)
    first = sorted(bars, key=lambda b: b.session)[0]
    assert first.session == date(2026, 8, 3)
    assert first.close == Decimal("303.420013")
    assert first.adj_close == Decimal("303.158569")


def test_tiingo_never_raises_for_a_bad_response():
    def fake_http_get(url: str) -> bytes:
        return b"not json"

    provider = TiingoProvider(api_key="fake-key", http_get=fake_http_get)
    assert provider.fetch(["AAPL"], START, END) == []


# ---------------------------------------------------------------------------
# ChainProvider
# ---------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, name: str, bars_by_symbol: dict[str, list[Bar]], available: bool = True):
        self.name = name
        self._bars_by_symbol = bars_by_symbol
        self._available = available
        self.fetch_calls: list[list[str]] = []

    def available(self) -> bool:
        return self._available

    def fetch(self, symbols: list[str], start: date, end: date) -> list[Bar]:
        self.fetch_calls.append(list(symbols))
        out: list[Bar] = []
        for s in symbols:
            out.extend(self._bars_by_symbol.get(s, []))
        return out


def _bar(symbol: str, source: str) -> Bar:
    return Bar(
        symbol=symbol,
        session=date(2026, 8, 3),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        adj_close=Decimal("100.5"),
        volume=1000,
        source=source,
    )


def test_chain_falls_through_to_second_provider():
    p1 = _StubProvider("p1", {"AAPL": [_bar("AAPL", "p1")]})
    p2 = _StubProvider("p2", {"MSFT": [_bar("MSFT", "p2")]})

    result = chain(p1, p2).fetch(["AAPL", "MSFT"], START, END)

    assert {b.symbol for b in result} == {"AAPL", "MSFT"}
    assert {b.source for b in result} == {"p1", "p2"}


def test_chain_only_asks_second_provider_for_missing_symbols():
    p1 = _StubProvider("p1", {"AAPL": [_bar("AAPL", "p1")]})
    p2 = _StubProvider("p2", {"MSFT": [_bar("MSFT", "p2")]})

    chain(p1, p2).fetch(["AAPL", "MSFT"], START, END)

    assert p1.fetch_calls == [["AAPL", "MSFT"]]
    assert p2.fetch_calls == [["MSFT"]]


def test_chain_skips_unavailable_providers():
    p1 = _StubProvider("p1", {}, available=False)
    p2 = _StubProvider("p2", {"AAPL": [_bar("AAPL", "p2")]})

    result = chain(p1, p2).fetch(["AAPL"], START, END)

    assert p1.fetch_calls == []
    assert {b.symbol for b in result} == {"AAPL"}


def test_chain_returns_empty_when_no_provider_has_the_symbol():
    p1 = _StubProvider("p1", {})
    result = chain(p1).fetch(["NOPE"], START, END)
    assert result == []


def test_chain_is_a_provider_instance():
    p1 = _StubProvider("p1", {})
    c = chain(p1)
    assert isinstance(c, ChainProvider)


# ---------------------------------------------------------------------------
# Live (network) tests — skipped unless INVESTLAB_LIVE=1
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_yfinance_pull_aapl_and_brk_b():
    provider = YFinanceProvider()
    bars = provider.fetch(["AAPL", "BRK.B"], date(2026, 8, 3), date(2026, 8, 7))
    symbols = {b.symbol for b in bars}
    assert "AAPL" in symbols
    assert "BRK.B" in symbols  # the dot came back, not a dash


@pytest.mark.live
def test_live_tiingo_skips_without_key(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    provider = TiingoProvider()
    assert provider.available() is False
