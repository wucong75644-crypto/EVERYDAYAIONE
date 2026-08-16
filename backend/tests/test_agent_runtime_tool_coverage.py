import pytest

from services.agent.runtime.catalog.consistency import (
    assert_model_tools_have_executors,
)
from services.agent.runtime.catalog.image_release import build_image_registry
from services.agent.runtime.executors.registry import ExecutorRegistry


def test_model_tool_coverage_accepts_registered_image_executor() -> None:
    assert_model_tools_have_executors(
        {"generate_image"}, build_image_registry(),
    )


def test_model_tool_coverage_fails_closed_for_missing_executor() -> None:
    with pytest.raises(
        RuntimeError,
        match="RUNTIME_MODEL_TOOL_EXECUTOR_COVERAGE_MISMATCH:generate_image",
    ):
        assert_model_tools_have_executors(
            {"generate_image"}, ExecutorRegistry(),
        )
