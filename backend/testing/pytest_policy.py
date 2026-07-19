"""Central classification for legacy tests that cannot be edited safely."""

from __future__ import annotations

from collections.abc import Iterable
import os
from typing import Protocol

import pytest


class PytestItem(Protocol):
    """Minimal pytest item surface used by the classification hook."""

    nodeid: str

    def add_marker(self, marker: pytest.MarkDecorator) -> None: ...


_LARGE_NODE_PREFIXES = (
    "tests/test_file_scanners.py::TestRealDataRegression::",
)


def pytest_configure() -> None:
    """Establish a deterministic test environment before app imports."""
    os.environ["APP_ENV"] = "testing"
    os.environ["DB_POOL_MIN"] = "0"


def classify_nodeid(nodeid: str) -> str | None:
    """Return the execution tier for a legacy test node."""
    if nodeid.startswith(_LARGE_NODE_PREFIXES):
        return "large"
    return None


def pytest_collection_modifyitems(items: Iterable[PytestItem]) -> None:
    """Apply centralized tiers without modifying oversized legacy files."""
    for item in items:
        tier = classify_nodeid(item.nodeid)
        if tier is not None:
            item.add_marker(getattr(pytest.mark, tier))
