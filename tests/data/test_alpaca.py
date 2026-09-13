from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from investlab.data.providers import AlpacaProvider, ChainProvider

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _row(day: str, o, h, low, c, v=1000):
    return {"t": f"{day}T04:00:00Z", "o": o, "h": h, "l": low, "c": c, "v": v, "n": 10, "vw": c}


def _provider(pages, calls):
    def http_get(url, headers):
        calls.append((url, headers))
        q = parse_qs(urlparse(url).query)
        key = (q["adjustment"][0], q.get("page_token", [None])[0])
        return json.dumps(pages[key]).encode()

    return AlpacaProvider(key_id="k", secret_key="s", http_get=http_get, now=lambda: NOW)


def test_unavailable_without_keys_makes_no_request():
    calls = []
    p = AlpacaProvider(
        key_id="", secret_key="", http_get=lambda u, h: calls.append(u), now=lambda: NOW
    )
    assert not p.available()
    assert p.fetch(["AAPL"], date(2026, 9, 1), date(2026, 9, 11)) == []
    assert calls == []


def test_raw_ohlc_with_adjusted_close_and_sip_feed():
    raw = {
        "bars": {
            "AAPL": [_row("2026-09-10", 10, 11, 9, 10.5), _row("2026-09-11", 10.5, 12, 10, 11.5)]
        },
        "next_page_token": None,
    }
    adj = {
        "bars": {
            "AAPL": [_row("2026-09-10", 5, 5.5, 4.5, 5.25), _row("2026-09-11", 5.25, 6, 5, 5.75)]
        },
        "next_page_token": None,
    }
    calls = []
    p = _provider({("raw", None): raw, ("all", None): adj}, calls)
    bars = p.fetch(["AAPL"], date(2026, 9, 1), date(2026, 9, 11))
    assert [b.session for b in bars] == [date(2026, 9, 10), date(2026, 9, 11)]
    assert bars[1].close == Decimal("11.5") and bars[1].adj_close == Decimal("5.75")
    assert all(b.source == "alpaca" for b in bars)
    url, headers = calls[0]
    assert headers["APCA-API-KEY-ID"] == "k" and headers["APCA-API-SECRET-KEY"] == "s"
    assert url.startswith("https://data.alpaca.markets/v2/stocks/bars?")
    assert parse_qs(urlparse(url).query)["feed"] == ["sip"]


def test_pagination_follows_next_page_token():
    first = {"bars": {"MSFT": [_row("2026-09-10", 1, 2, 1, 1.5)]}, "next_page_token": "abc"}
    second = {"bars": {"MSFT": [_row("2026-09-11", 1.5, 2, 1, 1.8)]}, "next_page_token": None}
    empty = {"bars": {}, "next_page_token": None}
    calls = []
    p = _provider({("raw", None): first, ("raw", "abc"): second, ("all", None): empty}, calls)
    bars = p.fetch(["MSFT"], date(2026, 9, 1), date(2026, 9, 11))
    assert len(bars) == 2
    assert bars[0].adj_close == bars[0].close


def test_request_window_stops_sixteen_minutes_before_now():
    calls = []
    empty = {"bars": {}, "next_page_token": None}
    now = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)
    p = AlpacaProvider(
        key_id="k",
        secret_key="s",
        now=lambda: now,
        http_get=lambda u, h: (calls.append(u), json.dumps(empty).encode())[1],
    )
    p.fetch(["AAPL"], date(2026, 9, 1), date(2026, 9, 11))
    assert parse_qs(urlparse(calls[0]).query)["end"] == ["2026-09-11T14:44:00Z"]


def test_http_failure_returns_nothing_instead_of_raising():
    def boom(url, headers):
        raise OSError("403")

    p = AlpacaProvider(key_id="k", secret_key="s", http_get=boom, now=lambda: NOW)
    assert p.fetch(["AAPL"], date(2026, 9, 1), date(2026, 9, 11)) == []


def test_chain_falls_through_to_alpaca():
    class Empty:
        name = "empty"

        def available(self):
            return True

        def fetch(self, symbols, start, end):
            return []

    raw = {"bars": {"AAPL": [_row("2026-09-11", 10, 11, 9, 10.5)]}, "next_page_token": None}
    p = _provider({("raw", None): raw, ("all", None): raw}, [])
    bars = ChainProvider(Empty(), p).fetch(["AAPL"], date(2026, 9, 1), date(2026, 9, 11))
    assert [b.source for b in bars] == ["alpaca"]
