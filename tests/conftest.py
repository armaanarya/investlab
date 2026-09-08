"""Global test guards."""

import pytest

from investlab.store import LEDGER_ROOT_ENV


@pytest.fixture(autouse=True)
def isolate_ledger(tmp_path, monkeypatch):
    """Point every test at a throwaway ledger root.

    Autouse and unconditional: the real ledger under `ledger/` is git-tracked
    competition state, and a test run once appended live trades to it. No test
    may reach it, whether or not it remembers to ask.
    """
    monkeypatch.setenv(LEDGER_ROOT_ENV, str(tmp_path / "ledger"))
