"""Shared pytest handling for optional, locally generated corpus fixtures."""

from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip corpus regressions when ignored local data is absent in CI.

    These tests use no network or credentials, but the quote database and raw
    corpus are intentionally gitignored generated artifacts. Developers with
    the local corpus still run them normally.
    """
    root = Path(__file__).resolve().parent.parent
    required_paths = (
        root / "data" / "renovai.db",
        root / "data" / "raw" / "inflation" / "materials_cpi.csv",
        root / "data" / "raw" / "inflation" / "labor_cpi.csv",
    )
    if all(path.exists() for path in required_paths):
        return

    reason = (
        "requires locally generated data/renovai.db and data/raw inflation "
        "fixtures; these are gitignored and unavailable in a clean checkout"
    )
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "requires_local_corpus" in item.keywords:
            item.add_marker(skip)
