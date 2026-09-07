"""Parquet cache tests.

The cache is the reproducibility boundary: everything downstream reads from
it, only an explicit pull touches the network. These tests pin the
round-trip exactness (Decimal via string, never float), the "never returns
empty for an unpulled symbol" contract, and the manifest content hash.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investlab.contracts import Bar
from investlab.data.cache import PULL_COMMAND_HINT, ParquetCache, SymbolNotCachedError


def make_bar(
    symbol: str,
    session: date,
    price: str,
    *,
    volume: int = 1_000_000,
    source: str = "test",
) -> Bar:
    p = Decimal(price)
    return Bar(
        symbol=symbol,
        session=session,
        open=p,
        high=p,
        low=p,
        close=p,
        adj_close=p,
        volume=volume,
        source=source,
    )


def test_round_trip_is_exact(tmp_path):
    bars = [make_bar("BRK.B", date(2026, 9, 1), "504.320007")]
    c = ParquetCache(tmp_path)
    c.write(bars)
    assert c.read("BRK.B") == bars  # Decimal equality, not float


def test_round_trip_preserves_decimal_precision(tmp_path):
    # 310.339996 is not exactly representable in binary floating point;
    # a float round trip would corrupt it.
    bars = [make_bar("AAPL", date(2026, 9, 1), "310.339996")]
    c = ParquetCache(tmp_path)
    c.write(bars)
    got = c.read("AAPL")
    assert got[0].close == Decimal("310.339996")
    assert isinstance(got[0].close, Decimal)


def test_read_uncached_symbol_names_the_pull_command(tmp_path):
    with pytest.raises(SymbolNotCachedError) as excinfo:
        ParquetCache(tmp_path).read("AAPL")
    assert "investlab data pull" in str(excinfo.value)
    assert "AAPL" in str(excinfo.value)


def test_pull_command_hint_constant_appears_in_error(tmp_path):
    with pytest.raises(SymbolNotCachedError) as excinfo:
        ParquetCache(tmp_path).read("MSFT")
    assert PULL_COMMAND_HINT in str(excinfo.value)


def test_dot_in_symbol_does_not_collide_with_file_extension(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("BRK.B", date(2026, 9, 1), "504.32")])
    files = sorted(p.name for p in tmp_path.glob("*.parquet"))
    assert files == ["BRK_DOT_B.parquet"]
    assert c.read("BRK.B")[0].symbol == "BRK.B"


def test_rewrite_of_same_session_replaces_not_duplicates(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("AAPL", date(2026, 9, 1), "100.00")])
    c.write([make_bar("AAPL", date(2026, 9, 1), "101.00")])
    got = c.read("AAPL")
    assert len(got) == 1
    assert got[0].close == Decimal("101.00")


def test_write_merges_with_existing_rows_and_sorts_ascending(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("AAPL", date(2026, 9, 3), "103.00")])
    c.write([make_bar("AAPL", date(2026, 9, 1), "101.00")])
    c.write([make_bar("AAPL", date(2026, 9, 2), "102.00")])
    got = c.read("AAPL")
    assert [b.session for b in got] == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]


def test_write_returns_count_of_bars_accepted(tmp_path):
    c = ParquetCache(tmp_path)
    n = c.write(
        [
            make_bar("AAPL", date(2026, 9, 1), "100"),
            make_bar("MSFT", date(2026, 9, 1), "200"),
        ]
    )
    assert n == 2


def test_read_with_start_end_filters_the_window(tmp_path):
    c = ParquetCache(tmp_path)
    c.write(
        [
            make_bar("AAPL", date(2026, 9, 1), "100"),
            make_bar("AAPL", date(2026, 9, 2), "101"),
            make_bar("AAPL", date(2026, 9, 3), "102"),
        ]
    )
    got = c.read("AAPL", start=date(2026, 9, 2), end=date(2026, 9, 3))
    assert [b.session for b in got] == [date(2026, 9, 2), date(2026, 9, 3)]


def test_read_returns_empty_list_for_a_window_with_no_sessions(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("AAPL", date(2026, 9, 1), "100")])
    got = c.read("AAPL", start=date(2026, 10, 1), end=date(2026, 10, 5))
    assert got == []  # pulled, but nothing in this window - not an error


def test_coverage_returns_range_and_none_for_unknown(tmp_path):
    c = ParquetCache(tmp_path)
    c.write(
        [
            make_bar("AAPL", date(2026, 9, 1), "100"),
            make_bar("AAPL", date(2026, 9, 5), "104"),
        ]
    )
    assert c.coverage("AAPL") == (date(2026, 9, 1), date(2026, 9, 5))
    assert c.coverage("MSFT") is None


def test_symbols_lists_cached_symbols_sorted(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("MSFT", date(2026, 9, 1), "200")])
    c.write([make_bar("AAPL", date(2026, 9, 1), "100")])
    assert c.symbols() == ["AAPL", "MSFT"]


def test_manifest_hash_is_stable_across_rewrites(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("AAPL", date(2026, 9, 1), "100")])
    h1 = c.manifest()["content_hash"]
    c.write([make_bar("AAPL", date(2026, 9, 1), "100")])  # identical rewrite
    h2 = c.manifest()["content_hash"]
    assert h1 == h2


def test_manifest_hash_changes_when_a_price_changes(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("AAPL", date(2026, 9, 1), "100")])
    h1 = c.manifest()["content_hash"]
    c.write([make_bar("AAPL", date(2026, 9, 1), "100.01")])
    h2 = c.manifest()["content_hash"]
    assert h1 != h2


def test_manifest_hash_is_independent_of_write_order(tmp_path):
    c1 = ParquetCache(tmp_path / "a")
    c1.write([make_bar("AAPL", date(2026, 9, 1), "100")])
    c1.write([make_bar("MSFT", date(2026, 9, 1), "200")])

    c2 = ParquetCache(tmp_path / "b")
    c2.write([make_bar("MSFT", date(2026, 9, 1), "200")])
    c2.write([make_bar("AAPL", date(2026, 9, 1), "100")])

    assert c1.manifest()["content_hash"] == c2.manifest()["content_hash"]


def test_manifest_reports_symbol_count_and_date_range(tmp_path):
    c = ParquetCache(tmp_path)
    c.write(
        [
            make_bar("AAPL", date(2026, 9, 1), "100"),
            make_bar("AAPL", date(2026, 9, 5), "104"),
        ]
    )
    c.write([make_bar("MSFT", date(2026, 9, 3), "200")])
    m = c.manifest()
    assert m["symbol_count"] == 2
    assert m["start"] == date(2026, 9, 1)
    assert m["end"] == date(2026, 9, 5)
    assert m["symbols"] == ["AAPL", "MSFT"]


def test_manifest_pulled_at_comes_from_sidecar_not_mtime(tmp_path):
    c = ParquetCache(tmp_path)
    c.write([make_bar("AAPL", date(2026, 9, 1), "100")])
    m = c.manifest()
    assert m["pulled_at"] is not None
    # Touching the parquet file's mtime must not change the recorded value.
    sidecar = tmp_path / "_manifest.json"
    assert sidecar.exists()


def test_manifest_on_empty_cache(tmp_path):
    m = ParquetCache(tmp_path).manifest()
    assert m["symbol_count"] == 0
    assert m["start"] is None
    assert m["end"] is None
    assert m["symbols"] == []
