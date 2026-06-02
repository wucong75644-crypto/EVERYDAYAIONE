"""
文件操作 E2E 测试 — 验证文件名直接引用链路：

  file_search 返回文件名 → file_analyze/code_execute 用文件名读
  （句柄系统已移除，所有文件操作统一用文件名/相对路径；
   file_read 工具已删除，图片走 file_search 自动多模态）

模拟真实场景：用户上传文件 → AI 直接用文件名读取处理。
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def workspace(tmp_path):
    """模拟 workspace 根目录（file_workspace_root）"""
    return str(tmp_path)


@pytest.fixture
def user_workspace(workspace):
    """用户实际 workspace 目录"""
    user_dir = Path(workspace) / "org" / "test_org" / "test_user"
    user_dir.mkdir(parents=True, exist_ok=True)
    return str(user_dir)


@pytest.fixture
def tool_executor(workspace):
    """构造 ToolExecutor（mock db，真实文件系统）"""
    from services.agent.tool_executor import ToolExecutor

    mock_settings = MagicMock()
    mock_settings.file_workspace_enabled = True
    mock_settings.file_workspace_root = workspace
    mock_settings.sandbox_enabled = True
    mock_settings.sandbox_timeout = 30.0
    mock_settings.sandbox_max_result_chars = 8000
    mock_settings.oss_cdn_domain = None

    with patch("core.config.get_settings", return_value=mock_settings):
        executor = ToolExecutor(
            db=MagicMock(),
            user_id="test_user",
            conversation_id="conv_001",
            org_id="test_org",
        )
        yield executor


# ============================================================
# E2E: file_search → 文件名列表 → file_analyze/code_execute 用文件名读
# ============================================================


class TestFileSearchListDir:
    """file_search 列目录功能（替代旧 file_list）"""

    @pytest.mark.asyncio
    async def test_file_search_lists_files(self, tool_executor, user_workspace):
        """file_search 无参数列出文件"""
        Path(user_workspace, "readme.txt").write_text("hello")
        Path(user_workspace, "data.csv").write_text("a,b\n1,2")

        result = await tool_executor.execute("file_search", {})
        # AgentResult 对象，取 summary
        summary = result.summary if hasattr(result, "summary") else str(result)
        assert "readme.txt" in summary
        assert "data.csv" in summary


# ============================================================
# file_search 测试
# ============================================================


class TestFileSearch:
    """file_search 直接用文件名搜索"""

    @pytest.mark.asyncio
    async def test_search_by_filename(self, tool_executor, user_workspace):
        """按文件名搜索"""
        Path(user_workspace, "report_2026.txt").write_text("data")
        Path(user_workspace, "notes.txt").write_text("other")

        result = await tool_executor.execute(
            "file_search", {"keyword": "report"},
        )
        assert "report_2026.txt" in result
        assert "notes.txt" not in result


# TestRegisterResultFiles 已移除（SessionFileRegistry 模块已删除）
