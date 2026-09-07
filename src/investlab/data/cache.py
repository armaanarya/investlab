"""On-disk parquet cache: the reproducibility boundary.

Every downstream module reads bars from here. Only an explicit pull (via
`data/providers.py`, driven by the CLI) touches the network. A backtest run
records `manifest()["content_hash"]` so its inputs can be verified offline,
even after Yahoo (or Tiingo) changes or goes away.

Layout: one parquet file per symbol at `<root>/<safe_symbol>.parquet`, where
`safe_symbol` replaces `.` with `_DOT_` so `BRK.B` does not collide with the
`.parquet` extension. A `_manifest.json` sidecar at `<root>/_manifest.json`
records, per symbol, the UTC timestamp of the most recent `write()` call —
`manifest()["pulled_at"]` comes from this sidecar, never from filesystem
mtimes, which a `git clone` or a backup restore can silently rewrite.

Prices persist as **strings**, not floats and not a parquet decimal type:
`Decimal("310.339996")` round-trips through `str(Decimal(...))` byte-exact
with no scale to pin in advance, and parquet's fixed-scale decimal would
need one.

`write()` merges incoming bars with whatever is already on disk for that
symbol, de-duplicating on `session` and keeping the newest write, then sorts
ascending. Re-pulling a day overwrites it rather than appending a duplicate
row.

`read()` on a symbol with no file on disk raises `SymbolNotCachedError`
naming the pull command — it must never return an empty list to mean "this
was never pulled". An empty list from `read()` means "this symbol has been
pulled, but no session falls inside the requested window".

`manifest()["content_hash"]` is a sha256 over per-symbol digests of
canonical row text, each digest keyed by symbol and sorted before hashing.
That makes the hash deterministic and independent of file mtime and of the
order symbols were written in — see `test_manifest_hash_is_independent_of_write_order`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from investlab.contracts import Bar

PULL_COMMAND_HINT = "investlab data pull --symbols"

_MANIFEST_SIDECAR = "_manifest.json"
_DOT_MARKER = "_DOT_"

# Columns persisted to each per-symbol parquet file, in canonical order.
# Prices are strings; volume is int64; session is an ISO-8601 date string.
_COLUMNS = ["session", "open", "high", "low", "close", "adj_close", "volume", "source"]


class SymbolNotCachedError(LookupError):
    """Raised by `ParquetCache.read` for a symbol that has never been
    written to the cache. Never returned as an empty list — an empty read
    result must mean "pulled, but empty window", not "never pulled"."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        super().__init__(
            f"{symbol!r} has never been pulled into the cache. Run: {PULL_COMMAND_HINT} {symbol}"
        )


def _safe_symbol(symbol: str) -> str:
    return symbol.replace(".", _DOT_MARKER)


def _unsafe_symbol(safe_symbol: str) -> str:
    return safe_symbol.replace(_DOT_MARKER, ".")


def _bar_to_row(bar: Bar) -> dict:
    return {
        "session": bar.session.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "adj_close": str(bar.adj_close),
        "volume": int(bar.volume),
        "source": bar.source,
    }


def _row_to_bar(symbol: str, row: dict) -> Bar:
    return Bar(
        symbol=symbol,
        session=date.fromisoformat(row["session"]),
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        adj_close=Decimal(row["adj_close"]),
        volume=int(row["volume"]),
        source=row["source"],
    )


def _canonical_row_text(symbol: str, row: dict) -> str:
    return "|".join(
        [
            symbol,
            row["session"],
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["adj_close"],
            str(row["volume"]),
            row["source"],
        ]
    )


class ParquetCache:
    """A per-symbol parquet cache under `root`."""

    def __init__(self, root: Path = Path("data_cache")) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ----------------------------------------------------------

    def _path_for(self, symbol: str) -> Path:
        return self.root / f"{_safe_symbol(symbol)}.parquet"

    def _sidecar_path(self) -> Path:
        return self.root / _MANIFEST_SIDECAR

    # -- sidecar ----------------------------------------------------------

    def _load_sidecar(self) -> dict:
        path = self._sidecar_path()
        if not path.exists():
            return {"pulled_at": {}}
        with path.open("r") as f:
            return json.load(f)

    def _save_sidecar(self, data: dict) -> None:
        with self._sidecar_path().open("w") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    # -- write ----------------------------------------------------------

    def write(self, bars: Iterable[Bar]) -> int:
        """Merge `bars` into the cache, one file per symbol.

        Rows are de-duplicated on `session`, keeping the newest write (later
        bars in `bars`, for a repeated session, replace earlier ones; new
        bars always replace whatever was already on disk for that session).
        Returns the number of bars accepted in this call.
        """
        bars = list(bars)
        by_symbol: dict[str, list[Bar]] = {}
        for bar in bars:
            by_symbol.setdefault(bar.symbol, []).append(bar)

        sidecar = self._load_sidecar()
        now = datetime.now(UTC).isoformat()

        for symbol, new_bars in by_symbol.items():
            existing_rows = self._read_raw_rows(symbol)
            merged: dict[str, dict] = {r["session"]: r for r in existing_rows}
            for bar in new_bars:
                row = _bar_to_row(bar)
                merged[row["session"]] = row
            ordered = [merged[k] for k in sorted(merged.keys())]
            self._write_raw_rows(symbol, ordered)
            sidecar.setdefault("pulled_at", {})[symbol] = now

        self._save_sidecar(sidecar)
        return len(bars)

    def _write_raw_rows(self, symbol: str, rows: list[dict]) -> None:
        df = pd.DataFrame(rows, columns=_COLUMNS)
        df.to_parquet(self._path_for(symbol), engine="pyarrow", index=False)

    def _read_raw_rows(self, symbol: str) -> list[dict]:
        path = self._path_for(symbol)
        if not path.exists():
            return []
        df = pd.read_parquet(path, engine="pyarrow")
        return df.to_dict(orient="records")

    # -- read ----------------------------------------------------------

    def read(self, symbol: str, start: date | None = None, end: date | None = None) -> list[Bar]:
        path = self._path_for(symbol)
        if not path.exists():
            raise SymbolNotCachedError(symbol)
        rows = self._read_raw_rows(symbol)
        bars = [_row_to_bar(symbol, r) for r in rows]
        bars.sort(key=lambda b: b.session)
        if start is not None:
            bars = [b for b in bars if b.session >= start]
        if end is not None:
            bars = [b for b in bars if b.session <= end]
        return bars

    def coverage(self, symbol: str) -> tuple[date, date] | None:
        path = self._path_for(symbol)
        if not path.exists():
            return None
        rows = self._read_raw_rows(symbol)
        if not rows:
            return None
        sessions = [date.fromisoformat(r["session"]) for r in rows]
        return (min(sessions), max(sessions))

    def symbols(self) -> list[str]:
        return sorted(_unsafe_symbol(p.stem) for p in self.root.glob("*.parquet"))

    # -- manifest ----------------------------------------------------------

    def manifest(self) -> dict:
        symbols = self.symbols()
        sidecar = self._load_sidecar()
        pulled_at_map = sidecar.get("pulled_at", {})

        digests: list[str] = []
        all_sessions: list[date] = []
        for symbol in symbols:
            rows = self._read_raw_rows(symbol)
            rows_sorted = sorted(rows, key=lambda r: r["session"])
            row_text = "\n".join(_canonical_row_text(symbol, r) for r in rows_sorted)
            digest = hashlib.sha256(row_text.encode("utf-8")).hexdigest()
            digests.append(f"{symbol}:{digest}")
            all_sessions.extend(date.fromisoformat(r["session"]) for r in rows_sorted)

        content_hash = hashlib.sha256("\n".join(sorted(digests)).encode("utf-8")).hexdigest()

        pulled_at_values = [pulled_at_map[s] for s in symbols if s in pulled_at_map]
        pulled_at = max(pulled_at_values) if pulled_at_values else None

        return {
            "content_hash": content_hash,
            "symbol_count": len(symbols),
            "start": min(all_sessions) if all_sessions else None,
            "end": max(all_sessions) if all_sessions else None,
            "pulled_at": pulled_at,
            "symbols": symbols,
        }
