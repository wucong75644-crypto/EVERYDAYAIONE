"""
services/agent/sandbox_tool_mixin.py 单元测试

覆盖：_register_files_from_output / _register_staging_files
沙盒执行 (_code_execute) 的集成测试在 test_sandbox_executor.py 中。
"""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Mock pydantic_settings 以避免环境依赖
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = MagicMock()

import pytest

from services.agent.sandbox_tool_mixin import SandboxToolMixin


class FakeSandboxMixin(SandboxToolMixin):
    """组合 Mixin 以测试（模拟宿主类属性）"""

    def __init__(self, user_id="u1", org_id="org1", conversation_id="conv1"):
        self.user_id = user_id
        self.workspace_user_id = user_id
        self.org_id = org_id
        self.conversation_id = conversation_id


# ============================================================
# _register_files_from_output
# ============================================================


class TestRegisterFilesFromOutput:
    """_register_files_from_output 已简化为空操作（workspace_file_handles 模块已删除），
    仅验证调用不抛异常。"""

    def test_call_does_not_raise(self):
        """任意 stdout 输入 → 不抛异常"""
        mixin = FakeSandboxMixin()
        mixin._register_files_from_output("Found file: 'sales.xlsx' in directory")

    def test_empty_string_does_not_raise(self):
        """空字符串 → 不抛异常"""
        mixin = FakeSandboxMixin()
        mixin._register_files_from_output("")


# ============================================================
# _get_workspace_dir / _get_staging_dir
# ============================================================


class TestPathHelpers:
    """路径辅助方法"""

    def test_get_workspace_dir_returns_string(self):
        mixin = FakeSandboxMixin()
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.file_workspace_root = "/tmp/test_ws"
            result = mixin._get_workspace_dir()
            assert isinstance(result, str)
            assert len(result) > 0

    def test_get_workspace_dir_exception_returns_empty(self):
        mixin = FakeSandboxMixin()
        with patch("core.config.get_settings", side_effect=Exception("err")):
            assert mixin._get_workspace_dir() == ""

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
