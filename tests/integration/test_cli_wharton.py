"""The `investlab wharton` commands run end to end against an empty cache."""

from __future__ import annotations

from typer.testing import CliRunner

from investlab.cli import app

runner = CliRunner()


def test_cashflows_prints_the_case():
    r = runner.invoke(app, ["wharton", "cashflows"])
    assert r.exit_code == 0, r.output
    assert "2042" in r.output and "operating payment" in r.output


def test_project_compares_every_strategy():
    r = runner.invoke(app, ["wharton", "project", "--paths", "500"], env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "Side by side" in r.output and "Placeholder" in r.output


def test_project_rejects_an_unknown_strategy():
    r = runner.invoke(app, ["wharton", "project", "--strategy", "nope", "--paths", "100"])
    assert r.exit_code == 2


def test_plan_runs_without_prices():
    r = runner.invoke(app, ["wharton", "plan"], env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "Sleeves" in r.output
