"""The DECA commands end to end, against a synthetic cache and a throwaway ledger."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

import investlab.cli as cli
from investlab.data.cache import ParquetCache
from investlab.earnings import research_root
from investlab.store import LedgerStore

AS_OF = date(2026, 9, 11)
runner = CliRunner()


@pytest.fixture
def live(monkeypatch, synth_market):
    cache = ParquetCache(Path(os.environ["INVESTLAB_CACHE_DIR"]))
    cache.write([b for bars in synth_market["bars"].values() for b in bars])
    monkeypatch.setattr(cli, "today_et", lambda: AS_OF)
    monkeypatch.setattr(cli, "now_et", lambda: datetime(2026, 9, 11, 7, 0, tzinfo=cli.EASTERN))
    monkeypatch.setattr(cli, "default_universe", lambda: synth_market["universe"])
    store = LedgerStore("deca")
    store.init(Decimal("100000"), date(2026, 9, 8))
    return store


def run(*args):
    return runner.invoke(cli.app, list(args))


def _saved_sheet():
    result = run("daily", "--save")
    assert result.exit_code == 0, result.output
    path = research_root() / "deca" / "sheets" / "2026-09-14.json"
    return json.loads(path.read_text())


def test_daily_prints_and_saves_the_sheet(live):
    sheet = _saved_sheet()
    assert sheet["buys"] and {c["bucket"] for c in sheet["compliance"]} == {"mutual_funds", "bonds"}
    assert (research_root() / "deca" / "sheets" / "2026-09-14.md").exists()


def test_daily_json_is_machine_readable(live):
    result = run("daily", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["fill_session"] == "2026-09-14"


def test_daily_refuses_other_profiles(live):
    assert run("daily", "--profile", "wharton").exit_code == cli.EXIT_RULE_UNRESOLVED


def test_fill_recovers_the_stop_from_the_saved_sheet(live):
    buy = _saved_sheet()["buys"][0]
    result = run(
        "fill",
        "-s",
        buy["symbol"],
        "-q",
        str(buy["quantity"]),
        "-p",
        buy["reference_close"],
        "--when",
        "2026-09-14",
    )
    assert result.exit_code == 0, result.output
    assert "sheet" in result.output
    assert live.entry_stops()[buy["symbol"]] == Decimal(buy["stop"])


def test_fill_without_a_sheet_computes_a_stop(live):
    result = run("fill", "-s", "S23", "-q", "10", "-p", "100", "--when", "2026-09-11")
    assert result.exit_code == 0, result.output
    assert live.entry_stops()["S23"] is not None
    assert "ATR" in result.output


def test_reconcile_passes_and_fails_loudly(live):
    run("fill", "-s", "S23", "-q", "10", "-p", "100", "--when", "2026-09-11")
    ok = run(
        "reconcile",
        "--cash",
        "98995.00",
        "--position",
        "S23:10:1005.00",
        "--save",
        "--as-of",
        "2026-09-11",
    )
    assert ok.exit_code == 0, ok.output
    assert (live.dir / "statements" / "2026-09-11.json").exists()
    assert run("reconcile", "--cash", "98990.00", "--position", "S23:10:1005.00").exit_code == 5
    assert run("reconcile", "--cash", "98995.00").exit_code == 5
    assert run("reconcile", "--cash", "98995.00", "--position", "S23:10:1000.00").exit_code == 5


def test_snapshot_refuses_non_sessions_and_backfills(live):
    assert run("snapshot", "--session", "2026-09-12").exit_code == cli.EXIT_BAD_CONFIG
    result = run("snapshot", "--session", "2026-09-11")
    assert result.exit_code == 0, result.output
    row = next(r for r in live.equity_curve_rows() if r["session"] == "2026-09-11")
    assert row["equity"] == "100000.00" and row["benchmark"]


def test_stop_set(live):
    run("fill", "-s", "S23", "-q", "10", "-p", "100", "--when", "2026-09-11")
    assert run("stop", "set", "-s", "S23", "-p", "91.50").exit_code == 0
    assert live.entry_stops()["S23"] == Decimal("91.50")
    assert run("stop", "set", "-s", "NOPE", "-p", "1").exit_code == cli.EXIT_BAD_CONFIG


def test_earnings_set_and_list(live):
    assert run("earnings", "set", "-s", "S23", "-d", "2026-10-20", "-t", "amc").exit_code == 0
    listed = run("earnings", "list")
    assert "S23" in listed.output
    assert run("earnings", "set", "-s", "S23", "-d", "2026-10-20", "-t", "noon").exit_code == 2


def test_risk_review_records_the_note(live):
    assert run("risk-review", "--note", "my words").exit_code == 0
    assert live.risk_reviews() == [(AS_OF, "my words")]
