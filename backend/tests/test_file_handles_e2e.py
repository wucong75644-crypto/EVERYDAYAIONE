"""
文件操作 E2E 测试 — 验证文件名直接引用链路：

  file_list 返回文件名 → file_read 用文件名读 → code_execute 用文件名读
  （句柄系统已移除，所有文件操作统一用文件名/相对路径）

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
# E2E: file_list → 文件名列表 → file_read 用文件名读
# ============================================================


class TestFileListToFileRead:
    """file_list 返回文件名 → file_read 用文件名访问"""

    @pytest.mark.asyncio
    async def test_file_list_returns_filenames(self, tool_executor, user_workspace):
        """file_list 返回文件名列表，不含句柄"""
        Path(user_workspace, "readme.txt").write_text("hello")
        Path(user_workspace, "data.csv").write_text("a,b\n1,2")

        result = await tool_executor.execute("file_list", {})
        assert "readme.txt" in result
        assert "data.csv" in result
        # 不应包含句柄格式
        assert "[F1]" not in result
        assert "[F2]" not in result

    @pytest.mark.asyncio
    async def test_file_read_by_filename(self, tool_executor, user_workspace):
        """file_read 直接用文件名读取"""
        Path(user_workspace, "notes.txt").write_text("line1\nline2\nline3")

        result = await tool_executor.execute("file_read", {"path": "notes.txt"})
        assert "line1" in result
        assert "line2" in result

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_relative_path_works(self, tool_executor, user_workspace):
        """相对路径（含子目录）正常工作"""
        sub_dir = Path(user_workspace, "sub")
        sub_dir.mkdir()
        Path(sub_dir, "deep.txt").write_text("deep content")

        result = await tool_executor.execute("file_read", {"path": "sub/deep.txt"})
        assert "deep content" in result

    @pytest.mark.asyncio
    async def test_subdirectory_file_list(self, tool_executor, user_workspace):
        """子目录 file_list 正常工作"""
        sub_dir = Path(user_workspace, "reports")
        sub_dir.mkdir()
        Path(sub_dir, "q1.txt").write_text("Q1 data")
        Path(sub_dir, "q2.txt").write_text("Q2 data")

        result = await tool_executor.execute("file_list", {"path": "reports"})
        assert "q1.txt" in result
        assert "q2.txt" in result

    @pytest.mark.asyncio
    async def test_chinese_filename(self, tool_executor, user_workspace):
        """中文文件名正常工作"""
        Path(user_workspace, "利润表.txt").write_text("利润数据")

        result = await tool_executor.execute("file_read", {"path": "利润表.txt"})
        assert "利润数据" in result

    @pytest.mark.asyncio
    async def test_filename_with_spaces(self, tool_executor, user_workspace):
        """带空格的文件名正常工作"""
        Path(user_workspace, "sales report.txt").write_text("sales data")

        result = await tool_executor.execute(
            "file_read", {"path": "sales report.txt"},
        )
        assert "sales data" in result


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


# ============================================================
# _register_result_files（ToolLoopExecutor）
# ============================================================


class TestRegisterResultFiles:
    """_register_result_files 注册 file_ref 到 file_registry（不再注册句柄）"""

    def _make_loop_executor(self):
        from services.agent.tool_loop_executor import ToolLoopExecutor

        mock_executor = MagicMock(spec=[])  # spec=[] 使 MagicMock 不响应任意属性

        loop_exec = ToolLoopExecutor.__new__(ToolLoopExecutor)
        loop_exec.executor = mock_executor
        loop_exec._collected_files = []

        from services.agent.session_file_registry import SessionFileRegistry
        loop_exec._file_registry = SessionFileRegistry()

        return loop_exec

    def test_registers_file_ref_to_registry(self):
        """file_ref 注册到 _file_registry"""
        loop_exec = self._make_loop_executor()

        from services.agent.tool_output import FileRef
        ref = FileRef(
            path="/staging/trade.parquet", filename="trade.parquet",
            format="parquet", row_count=100, size_bytes=1024, columns=[],
        )
        result = MagicMock(spec=["file_ref", "source", "collected_files"])
        result.file_ref = ref
        result.source = "trade_agent"
        result.collected_files = None

        loop_exec._register_result_files(result, "erp_agent")

        assert loop_exec._file_registry.get_latest() is not None

    def test_skips_when_no_file_ref(self):
        """无 file_ref 时安全跳过"""
        loop_exec = self._make_loop_executor()

        loop_exec._register_result_files("查询成功，共10条", "some_tool")

        assert len(loop_exec._collected_files) == 0
