"""The write side of the loop.

`daily` only reads the portfolio file. Until `fill` existed, entering trades on
the platform left the tool believing it still held the old positions and the
old cash, so it re-proposed buys that had already been made.
"""

import json
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from investlab.cli import app

runner = CliRunner()


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = tmp_path / "portfolio_deca.json"
    path.write_text(
        json.dumps({"profile": "deca", "as_of": "2026-09-08", "cash": "100000.00", "positions": []})
    )
    monkeypatch.setattr("investlab.cli._portfolio_path", lambda profile: path)
    return path


def _run(*args):
    return runner.invoke(app, ["fill", *args])


def test_buy_reduces_cash_by_notional_plus_commission(book):
    result = _run("-s", "AAPL", "-a", "buy", "-q", "10", "-p", "100", "--commission", "5")
    assert result.exit_code == 0, result.output
    saved = json.loads(book.read_text())
    assert Decimal(saved["cash"]) == Decimal("98995.00")
    assert saved["positions"][0]["symbol"] == "AAPL"
    assert saved["positions"][0]["lots"][0]["quantity"] == 10


def test_sell_returns_cash_and_consumes_lots_fifo(book):
    _run("-s", "AAPL", "-a", "buy", "-q", "10", "-p", "100", "--commission", "5")
    result = _run("-s", "AAPL", "-a", "sell", "-q", "4", "-p", "110", "--commission", "5")
    assert result.exit_code == 0, result.output
    saved = json.loads(book.read_text())
    # 98995 + 440 - 5
    assert Decimal(saved["cash"]) == Decimal("99430.00")
    assert saved["positions"][0]["lots"][0]["quantity"] == 6


def test_selling_more_than_held_is_rejected(book):
    _run("-s", "AAPL", "-a", "buy", "-q", "10", "-p", "100")
    result = _run("-s", "AAPL", "-a", "sell", "-q", "25", "-p", "110")
    assert result.exit_code != 0
    assert "shorting is disabled" in result.output.lower()


def test_buy_beyond_cash_is_rejected(book):
    result = _run("-s", "AAPL", "-a", "buy", "-q", "10000", "-p", "500")
    assert result.exit_code != 0
    assert "out of step" in result.output.lower()


def test_position_fully_sold_is_removed(book):
    _run("-s", "AAPL", "-a", "buy", "-q", "10", "-p", "100")
    _run("-s", "AAPL", "-a", "sell", "-q", "10", "-p", "100")
    assert json.loads(book.read_text())["positions"] == []


def test_bond_etf_warns_that_it_did_not_satisfy_the_bond_leg(book):
    """DECA classifies every ETF as a stock, bond ETFs included. A student
    buying AGG or BND to cover the bond leg has covered the stock leg and
    still has an unmet bond requirement, which is a disqualification if it is
    discovered on the deadline."""
    result = _run("-s", "AGG", "-a", "buy", "-q", "100", "-p", "99")
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    assert "not bonds" in out or "did not satisfy the bond leg" in out


def test_unknown_symbol_demands_an_explicit_asset_class(book):
    """Guessing the class would corrupt the diversification check."""
    result = _run("-s", "ZZZZ", "-a", "buy", "-q", "10", "-p", "50")
    assert result.exit_code != 0
    assert "asset-class" in result.output.lower() or "asset class" in result.output.lower()


def test_quantity_must_be_positive(book):
    assert _run("-s", "AAPL", "-a", "buy", "-q", "0", "-p", "100").exit_code != 0
