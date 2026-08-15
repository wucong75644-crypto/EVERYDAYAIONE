"""
services/agent/sandbox_tool_mixin.py 单元测试

覆盖：code_execute 沙盒执行链与共享路径辅助。
"""

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Mock pydantic_settings 以避免环境依赖
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = MagicMock()

import pytest

from services.agent.sandbox_tool_mixin import SandboxToolMixin
from services.agent.agent_result import AgentResult


class FakeSandboxMixin(SandboxToolMixin):
    """组合 Mixin 以测试（模拟宿主类属性）"""

    def __init__(self, user_id="u1", org_id="org1", conversation_id="conv1"):
        self.user_id = user_id
        self.workspace_user_id = user_id
        self.org_id = org_id
        self.conversation_id = conversation_id
        self.db = MagicMock()


class TestCodeExecute:
    @pytest.mark.asyncio
    async def test_executes_with_actor_owned_kernel(self):
        mixin = FakeSandboxMixin()
        executor = MagicMock()
        executor.execute = AsyncMock(
            return_value=AgentResult(summary="done", status="success"),
        )
        settings = MagicMock(
            sandbox_enabled=True,
            sandbox_timeout=120,
            sandbox_max_result_chars=8000,
        )

        with (
            patch("core.config.get_settings", return_value=settings),
            patch(
                "services.sandbox.functions.build_sandbox_executor",
                return_value=executor,
            ) as build_executor,
            patch(
                "services.sandbox.kernel_manager.get_kernel_manager",
                return_value="kernel",
            ),
            patch.object(mixin, "_get_staging_dir", return_value="/tmp/staging"),
            patch.object(mixin, "_register_files_from_output") as register_files,
            patch.object(mixin, "_record_sandbox_metric") as record_metric,
        ):
            result = await mixin._code_execute(
                {"code": "print('done')", "description": "smoke"},
            )

        assert result.status == "success"
        executor.execute.assert_awaited_once_with("print('done')", "smoke")
        assert build_executor.call_args.kwargs["kernel_manager"] == "kernel"
        assert build_executor.call_args.kwargs["conversation_id"] == "conv1"
        register_files.assert_called_once_with("done")
        assert record_metric.call_args.kwargs["status"] == "success"

    @pytest.mark.asyncio
    async def test_disabled_sandbox_returns_non_retryable_error(self):
        mixin = FakeSandboxMixin()
        settings = MagicMock(sandbox_enabled=False)

        with patch("core.config.get_settings", return_value=settings):
            result = await mixin._code_execute({"code": "print(1)"})

        assert result.status == "error"
        assert result.metadata["retryable"] is False

    @pytest.mark.asyncio
    async def test_executor_failure_is_returned_and_observed(self):
        mixin = FakeSandboxMixin()
        executor = MagicMock()
        executor.execute = AsyncMock(side_effect=RuntimeError("sandbox down"))
        settings = MagicMock(
            sandbox_enabled=True,
            sandbox_timeout=120,
            sandbox_max_result_chars=8000,
        )

        with (
            patch("core.config.get_settings", return_value=settings),
            patch(
                "services.sandbox.functions.build_sandbox_executor",
                return_value=executor,
            ),
            patch.object(mixin, "_get_staging_dir", return_value="/tmp/staging"),
            patch.object(mixin, "_record_sandbox_metric") as record_metric,
            patch.object(mixin, "_record_sandbox_knowledge") as record_knowledge,
        ):
            result = await mixin._code_execute(
                {"code": "raise RuntimeError", "description": "failure"},
            )

        assert result.status == "error"
        assert "sandbox down" in result.error_message
        assert record_metric.call_args.kwargs["status"] == "failed"
        record_knowledge.assert_called_once()


# ============================================================
# _get_workspace_dir / _get_staging_dir
# ============================================================


class TestPathHelpers:
    """路径辅助方法"""

    def test_get_staging_dir_returns_string(self):
        mixin = FakeSandboxMixin()
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.file_workspace_root = "/tmp/test_ws"
            result = mixin._get_staging_dir()
            assert isinstance(result, str)
            assert "staging" in result

    def test_get_staging_dir_exception_returns_empty(self):
        mixin = FakeSandboxMixin()
        with patch("core.config.get_settings", side_effect=Exception("err")):
            assert mixin._get_staging_dir() == ""

    def test_get_workspace_dir_returns_string(self):
        mixin = FakeSandboxMixin()
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.file_workspace_root = "/tmp/test_ws"
            result = mixin._get_workspace_dir()
            assert isinstance(result, str)
            assert result

    def test_get_workspace_dir_exception_returns_empty(self):
        mixin = FakeSandboxMixin()
        with patch("core.config.get_settings", side_effect=Exception("err")):
            assert mixin._get_workspace_dir() == ""
