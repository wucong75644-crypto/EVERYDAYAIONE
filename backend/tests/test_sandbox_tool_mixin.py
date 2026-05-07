"""
services/agent/sandbox_tool_mixin.py 单元测试

覆盖：_register_workspace_backups / _register_files_from_output / _register_staging_files
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
        self.org_id = org_id
        self.conversation_id = conversation_id


# ============================================================
# _register_workspace_backups
# ============================================================


class TestRegisterWorkspaceBackups:
    """workspace 备份注册到 session_file_registry"""

    def test_registers_backup_to_registry(self, tmp_path):
        """备份文件存在 → 注册到 registry"""
        backup_file = tmp_path / "_bak_1700000000_report.xlsx"
        backup_file.write_bytes(b"original data")

        mixin = FakeSandboxMixin()

        saved = {}

        def mock_save(conv_id, tmp_reg):
            saved["conv_id"] = conv_id
            saved["entries"] = tmp_reg.list_all()

        with patch(
            "services.agent.session_file_registry.save_conversation_registry",
            side_effect=mock_save,
        ):
            mixin._register_workspace_backups({
                "report.xlsx": str(backup_file),
            })

        assert saved["conv_id"] == "conv1"
        assert len(saved["entries"]) == 1
        key, ref = saved["entries"][0]
        assert key.startswith("backup:report.xlsx:")
        assert ref.path == str(backup_file)
        assert ref.format == "xlsx"

    def test_skips_nonexistent_backup(self, tmp_path):
        """备份文件不存在 → 跳过，不注册"""
        saved = {}

        def mock_save(conv_id, tmp_reg):
            saved["entries"] = tmp_reg.list_all()

        with patch(
            "services.agent.session_file_registry.save_conversation_registry",
            side_effect=mock_save,
        ):
            mixin = FakeSandboxMixin()
            mixin._register_workspace_backups({
                "report.xlsx": "/nonexistent/path/_bak_123_report.xlsx",
            })

        assert len(saved["entries"]) == 0

    def test_multiple_backups_registered(self, tmp_path):
        """多个备份文件 → 全部注册"""
        (tmp_path / "_bak_1_a.xlsx").write_bytes(b"a")
        (tmp_path / "_bak_1_b.csv").write_bytes(b"b")

        saved = {}

        def mock_save(conv_id, tmp_reg):
            saved["entries"] = tmp_reg.list_all()

        with patch(
            "services.agent.session_file_registry.save_conversation_registry",
            side_effect=mock_save,
        ):
            mixin = FakeSandboxMixin()
            mixin._register_workspace_backups({
                "a.xlsx": str(tmp_path / "_bak_1_a.xlsx"),
                "b.csv": str(tmp_path / "_bak_1_b.csv"),
            })

        assert len(saved["entries"]) == 2
        keys = {k for k, _ in saved["entries"]}
        assert any("backup:a.xlsx:" in k for k in keys)
        assert any("backup:b.csv:" in k for k in keys)


# ============================================================
# _register_files_from_output
# ============================================================


class TestRegisterFilesFromOutput:
    """从 code_execute stdout 提取文件名注册到路径缓存"""

    def test_registers_existing_file(self, tmp_path):
        """stdout 中引用的存在文件 → 注册到缓存"""
        (tmp_path / "sales.xlsx").write_bytes(b"data")

        mixin = FakeSandboxMixin()

        registered = {}

        class FakeCache:
            def register(self, name, path):
                registered[name] = path

        with patch.object(mixin, "_get_workspace_dir", return_value=str(tmp_path)), \
             patch("services.agent.workspace_file_handles.get_file_cache", return_value=FakeCache()):
            mixin._register_files_from_output("Found file: 'sales.xlsx' in directory")

        assert "sales.xlsx" in registered

    def test_skips_nonexistent_file(self, tmp_path):
        """stdout 中引用但不存在的文件 → 不注册"""
        mixin = FakeSandboxMixin()

        registered = {}

        class FakeCache:
            def register(self, name, path):
                registered[name] = path

        with patch.object(mixin, "_get_workspace_dir", return_value=str(tmp_path)), \
             patch("services.agent.workspace_file_handles.get_file_cache", return_value=FakeCache()):
            mixin._register_files_from_output("File: 'nonexistent.xlsx'")

        assert len(registered) == 0

    def test_no_workspace_dir_returns_early(self):
        """无 workspace_dir → 直接返回"""
        mixin = FakeSandboxMixin()
        with patch.object(mixin, "_get_workspace_dir", return_value=""):
            # 不应抛异常
            mixin._register_files_from_output("'test.xlsx'")


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
