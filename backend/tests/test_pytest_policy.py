"""Tests for centralized legacy test classification."""

from testing.pytest_policy import classify_nodeid, pytest_collection_modifyitems


def test_pytest_disables_idle_database_connections() -> None:
    from core.config import get_settings

    assert get_settings().app_env == "testing"
    assert get_settings().db_pool_min == 0


def test_real_data_scanner_regressions_are_large() -> None:
    nodeid = (
        "tests/test_file_scanners.py::TestRealDataRegression::"
        "test_large_file_path_a"
    )

    assert classify_nodeid(nodeid) == "large"


def test_regular_unit_test_has_no_forced_tier() -> None:
    assert classify_nodeid(
        "tests/test_memory_tools.py::test_memory_search"
    ) is None


def test_collection_hook_marks_only_classified_items() -> None:
    class FakeItem:
        def __init__(self, nodeid: str) -> None:
            self.nodeid = nodeid
            self.marker_names: list[str] = []

        def add_marker(self, marker) -> None:
            self.marker_names.append(marker.mark.name)

    large_item = FakeItem(
        "tests/test_file_scanners.py::TestRealDataRegression::test_small_file_path_a"
    )
    regular_item = FakeItem(
        "tests/test_memory_tools.py::test_memory_search"
    )

    pytest_collection_modifyitems([large_item, regular_item])

    assert large_item.marker_names == ["large"]
    assert regular_item.marker_names == []
