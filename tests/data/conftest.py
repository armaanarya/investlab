"""Shared fixtures for tests/data/.

Registers the `live` marker and auto-skips it unless `INVESTLAB_LIVE=1`, so
the default suite runs fully offline. This lives here rather than in a
root-level `tests/conftest.py` because `pytest_addoption` (a `--live` CLI
flag) is root-conftest-only, and the root `conftest.py` belongs to no agent
in `docs/superpowers/plans/AGENT-FILE-OWNERSHIP.md` — an env var avoids
needing it.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: hits the network; requires INVESTLAB_LIVE=1 to run"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("INVESTLAB_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live network test; set INVESTLAB_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
