"""Price providers: vendor adapters into `list[Bar]`.

Only this module touches the network. `YFinanceProvider` is primary,
`TiingoProvider` is the keyless-optional fallback, and `chain()` tries a
sequence of providers in order, asking each only for the symbols the
previous ones did not find. Everything downstream reads from
`data/cache.py`, never from here directly.

Design notes (see `docs/superpowers/plans/2026-09-06-core-data.md` Task 4):

- Symbol translation is one-way. `to_vendor_symbol` turns `.` into `-` for
  the outbound vendor request (`BRK.B` -> `BRK-B`); the reverse is *not* a
  global `-` -> `.` rule, because real tickers legitimately contain hyphens.
  Each `fetch` call instead builds a request-scoped `{vendor: original}`
  dict and looks the original symbol back up when building `Bar` objects.
- `YFinanceProvider.fetch` never raises for a missing symbol: an unknown
  ticker comes back from yfinance with its columns present and zero rows
  (verified 2026-09-06), so it simply contributes no bars.
- Retry: after the first download, any requested symbol that produced zero
  *parsed* bars (i.e. every row for that symbol was dropped as NaN, or the
  symbol's columns had no rows at all) is re-requested once, together, in a
  single follow-up call. A genuinely delisted symbol costs one wasted retry
  call and then yields no bars, which is correct.
- `auto_adjust=False` is always requested so both `Close` (raw) and
  `Adj Close` (adjusted) come back — ATR and breakout levels need raw OHLC,
  return series need `adj_close`, and mixing the two in one calculation is a
  documented bug class.
- Prices are rounded to `price_dp` (default 6) decimal places with
  round-half-even *before* `Decimal` conversion. Round-half-even is
  monotonic, so if the raw vendor data satisfies `low <= open <= high` the
  rounded values do too — `Bar.__post_init__` cannot be tripped by rounding.
  Rows with a NaN in any OHLC or adjusted-close field are dropped outright.
- Tiingo uses stdlib `urllib.request`, not `requests` — `pyproject.toml` is
  owned by another agent and no new dependency may be added here. Tiingo
  live verification is pending: the contract tests run against a recorded
  JSON fixture (`tests/data/fixtures/tiingo_aapl.json`); the one live call
  is `@pytest.mark.live` and additionally no-ops without `TIINGO_API_KEY`.
- No Stooq provider: since ~April 2026 Stooq requires an API key plus a
  JavaScript proof-of-work challenge, and its CSV endpoint returns "Access
  denied" even after the challenge is solved. Not worth designing around.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import pandas as pd

from investlab.contracts import Bar

DEFAULT_PRICE_DP = 6
_OHLC_FIELDS = ("Open", "High", "Low", "Close", "Adj Close")


class ProviderError(RuntimeError):
    """A provider-level failure that is not "this one symbol is missing" -
    e.g. malformed vendor output that could not be parsed at all. Never
    raised by `fetch` for an individual missing or delisted ticker; those
    simply contribute no bars."""


def to_vendor_symbol(symbol: str) -> str:
    """`.` -> `-` for the outbound vendor request (`BRK.B` -> `BRK-B`).

    One-way by design: real tickers can legitimately contain hyphens, so a
    global `-` -> `.` reverse mapping would be wrong in general. Callers
    recover the original symbol from a request-scoped dict instead.
    """
    return symbol.replace(".", "-")


def _is_nan(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _round_price(value: Any, dp: int) -> Decimal | None:
    """Round a vendor float to `dp` decimal places, half-even, before
    `Decimal` conversion. Returns `None` for NaN/missing so the caller can
    drop the row rather than fabricate a price."""
    if _is_nan(value):
        return None
    quantum = Decimal(1).scaleb(-dp)
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)


def _row_volume(value: Any) -> int:
    if _is_nan(value):
        return 0
    return int(round(float(value)))


# ---------------------------------------------------------------------------
# yfinance
# ---------------------------------------------------------------------------


class YFinanceProvider:
    """Primary provider. Wraps `yfinance.download`, or an injected
    `downloader` callable with the same `(tickers, **kwargs) -> DataFrame`
    signature for offline testing."""

    name = "yfinance"

    def __init__(
        self,
        *,
        downloader: Callable[..., pd.DataFrame] | None = None,
        retries: int = 1,
        price_dp: int = DEFAULT_PRICE_DP,
    ) -> None:
        self._downloader = downloader
        self.retries = retries
        self.price_dp = price_dp

    def available(self) -> bool:
        # yfinance needs no credentials; a network outage is handled per
        # call in fetch(), not here.
        return True

    def fetch(self, symbols: list[str], start: date, end: date) -> list[Bar]:
        if not symbols:
            return []

        vendor_to_original: dict[str, str] = {to_vendor_symbol(s): s for s in symbols}
        pending = list(vendor_to_original.keys())
        bars_by_vendor_symbol: dict[str, list[Bar]] = {}

        for _attempt in range(1 + self.retries):
            if not pending:
                break
            frame = self._safe_download(pending, start, end)
            for vendor_symbol in list(pending):
                original = vendor_to_original[vendor_symbol]
                parsed = self._parse_symbol(frame, vendor_symbol, original) if frame is not None else []
                if parsed:
                    bars_by_vendor_symbol[vendor_symbol] = parsed
                    pending.remove(vendor_symbol)
            # Anything still in `pending` produced zero bars this round and
            # is retried on the next loop iteration (at most `self.retries`
            # further times).

        result: list[Bar] = []
        for vendor_symbol in vendor_to_original:
            result.extend(bars_by_vendor_symbol.get(vendor_symbol, []))
        return result

    def _safe_download(self, vendor_symbols: list[str], start: date, end: date) -> pd.DataFrame | None:
        try:
            return self._download(vendor_symbols, start, end)
        except Exception:
            return None

    def _download(self, vendor_symbols: list[str], start: date, end: date) -> pd.DataFrame:
        if self._downloader is not None:
            return self._downloader(vendor_symbols, start=start, end=end, auto_adjust=False)
        import yfinance as yf

        # yfinance's `end` is exclusive; add a day so `end` itself is included.
        return yf.download(
            vendor_symbols,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )

    def _parse_symbol(self, frame: pd.DataFrame, vendor_symbol: str, original_symbol: str) -> list[Bar]:
        try:
            sub = frame.xs(vendor_symbol, axis=1, level=1)
        except (KeyError, ValueError):
            return []

        bars: list[Bar] = []
        for idx, row in sub.iterrows():
            session = idx.date() if hasattr(idx, "date") else idx
            values = {field: row.get(field) for field in _OHLC_FIELDS}
            if any(_is_nan(v) for v in values.values()):
                continue
            try:
                bar = Bar(
                    symbol=original_symbol,
                    session=session,
                    open=_round_price(values["Open"], self.price_dp),
                    high=_round_price(values["High"], self.price_dp),
                    low=_round_price(values["Low"], self.price_dp),
                    close=_round_price(values["Close"], self.price_dp),
                    adj_close=_round_price(values["Adj Close"], self.price_dp),
                    volume=_row_volume(row.get("Volume")),
                    source=self.name,
                )
            except ValueError:
                continue
            bars.append(bar)
        return bars


# ---------------------------------------------------------------------------
# Tiingo
# ---------------------------------------------------------------------------


class TiingoProvider:
    """Fallback provider. Reads `TIINGO_API_KEY` from the environment;
    `available()` is False without a key so `ChainProvider` falls through.
    Uses stdlib `urllib.request` — no `requests` dependency is added.
    """

    name = "tiingo"
    _BASE_URL = "https://api.tiingo.com/tiingo/daily"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_get: Callable[[str], bytes] | None = None,
        price_dp: int = DEFAULT_PRICE_DP,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("TIINGO_API_KEY")
        self._http_get = http_get
        self.price_dp = price_dp

    def available(self) -> bool:
        return bool(self.api_key)

    def fetch(self, symbols: list[str], start: date, end: date) -> list[Bar]:
        if not self.available():
            return []
        bars: list[Bar] = []
        for symbol in symbols:
            bars.extend(self._fetch_one(symbol, start, end))
        return bars

    def _fetch_one(self, symbol: str, start: date, end: date) -> list[Bar]:
        vendor_symbol = to_vendor_symbol(symbol)
        url = (
            f"{self._BASE_URL}/{vendor_symbol}/prices"
            f"?startDate={start.isoformat()}&endDate={end.isoformat()}"
            f"&format=json&token={self.api_key}"
        )
        try:
            raw = self._get(url)
            rows = json.loads(raw)
        except Exception:
            return []
        if not isinstance(rows, list):
            return []

        bars: list[Bar] = []
        for row in rows:
            bar = self._row_to_bar(symbol, row)
            if bar is not None:
                bars.append(bar)
        return bars

    def _row_to_bar(self, symbol: str, row: dict) -> Bar | None:
        try:
            session = date.fromisoformat(str(row["date"])[:10])
            values = {
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "adj_close": row.get("adjClose", row.get("close")),
            }
        except (KeyError, TypeError, ValueError):
            return None
        if any(_is_nan(v) for v in values.values()):
            return None
        try:
            return Bar(
                symbol=symbol,
                session=session,
                open=_round_price(values["open"], self.price_dp),
                high=_round_price(values["high"], self.price_dp),
                low=_round_price(values["low"], self.price_dp),
                close=_round_price(values["close"], self.price_dp),
                adj_close=_round_price(values["adj_close"], self.price_dp),
                volume=_row_volume(row.get("volume")),
                source=self.name,
            )
        except ValueError:
            return None

    def _get(self, url: str) -> bytes:
        if self._http_get is not None:
            return self._http_get(url)
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            return resp.read()


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------


class ChainProvider:
    """Tries each provider in order, asking each only for the symbols the
    earlier ones did not return bars for."""

    def __init__(self, *providers: Any) -> None:
        self.providers: Sequence[Any] = providers

    @property
    def name(self) -> str:
        return "chain(" + ",".join(p.name for p in self.providers) + ")"

    def available(self) -> bool:
        return any(p.available() for p in self.providers)

    def fetch(self, symbols: list[str], start: date, end: date) -> list[Bar]:
        remaining = list(symbols)
        bars: list[Bar] = []
        for provider in self.providers:
            if not remaining:
                break
            if not provider.available():
                continue
            got = provider.fetch(remaining, start, end)
            bars.extend(got)
            found = {b.symbol for b in got}
            remaining = [s for s in remaining if s not in found]
        return bars


def chain(*providers: Any) -> ChainProvider:
    return ChainProvider(*providers)
