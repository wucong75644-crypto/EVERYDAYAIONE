"""
services/agent/sandbox_tool_mixin.py 单元测试

覆盖：共享 staging 路径辅助；旧 code_execute 拒绝由专属测试覆盖。
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
