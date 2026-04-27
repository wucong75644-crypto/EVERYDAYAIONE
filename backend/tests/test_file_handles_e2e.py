"""
文件句柄 E2E 测试 — 验证完整链路：

  file_list 注册 → file_read 用句柄读 → code_execute 用 FILES 字典读
  staging 文件注册 → 也在 FILES 里 → 沙盒可跨源读取

模拟真实场景：用户上传 Excel + ERP 查询产出 parquet → 沙盒 merge 两个文件。
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.agent.workspace_file_handles import WorkspaceFileHandles
from services.file_executor import FileExecutor


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def workspace(tmp_path):
    """模拟 workspace 根目录（file_workspace_root）"""
    return str(tmp_path)


@pytest.fixture
def user_workspace(workspace):
    """用户实际 workspace 目录（org 模式：workspace/org/{org_id}/{user_id}/）"""
    user_dir = Path(workspace) / "org" / "test_org" / "test_user"
    user_dir.mkdir(parents=True, exist_ok=True)
    return str(user_dir)


@pytest.fixture
def tool_executor(workspace):
    """构造真实 ToolExecutor（mock db，真实文件系统）

    使用 org_id + user_id → workspace 目录 = workspace/org/test_org/test_user/
    get_settings 全局 mock 确保 _file_dispatch 和 _code_execute 都能拿到配置。
    """
    from services.agent.tool_executor import ToolExecutor

    mock_settings = MagicMock()
    mock_settings.file_workspace_enabled = True
    mock_settings.file_workspace_root = workspace
    mock_settings.sandbox_enabled = True
    mock_settings.sandbox_timeout = 10.0
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
# E2E: file_list → 句柄注册 → file_read 用句柄
# ============================================================


class TestFileListToFileRead:
    """file_list 注册句柄 → file_read/file_info 用句柄访问"""

    @pytest.mark.asyncio
    async def test_file_list_registers_handles(self, tool_executor, user_workspace):
        """file_list 后 handles 字典有数据"""
        Path(user_workspace, "报表.xlsx").write_bytes(b"fake excel")
        Path(user_workspace, "数据.csv").write_text("a,b\n1,2")

        result = await tool_executor.execute("file_list", {})

        assert "F1" in result
        assert "F2" in result
        assert "报表.xlsx" in result
        assert "数据.csv" in result
        assert tool_executor.file_handles.count == 2

    @pytest.mark.asyncio
    async def test_file_read_by_handle(self, tool_executor, user_workspace):
        """用句柄 F1 读取文件内容"""
        Path(user_workspace, "hello.txt").write_text("Hello World 你好世界")

        # 先 file_list 注册
        await tool_executor.execute("file_list", {})
        # 再用句柄读
        result = await tool_executor.execute("file_read", {"path": "F1"})

        assert "Hello World 你好世界" in result

    @pytest.mark.asyncio
    async def test_file_info_by_handle(self, tool_executor, user_workspace):
        """用句柄 F1 获取文件信息"""
        Path(user_workspace, "data.json").write_text('{"key": "val"}')

        await tool_executor.execute("file_list", {})
        result = await tool_executor.execute("file_info", {"path": "F1"})

        assert "文件" in result

    @pytest.mark.asyncio
    async def test_relative_path_still_works(self, tool_executor, user_workspace):
        """相对路径不受影响"""
        Path(user_workspace, "readme.md").write_text("# Title")

        result = await tool_executor.execute("file_read", {"path": "readme.md"})
        assert "# Title" in result

    @pytest.mark.asyncio
    async def test_handle_dedup_across_multiple_list_calls(self, tool_executor, user_workspace):
        """多次 file_list 同一文件不重复分配句柄"""
        Path(user_workspace, "report.csv").write_text("a,b")

        await tool_executor.execute("file_list", {})
        await tool_executor.execute("file_list", {})

        assert tool_executor.file_handles.count == 1  # 不是 2

    @pytest.mark.asyncio
    async def test_subdirectory_file_list(self, tool_executor, user_workspace):
        """子目录的文件也能注册句柄"""
        sub = Path(user_workspace, "reports")
        sub.mkdir()
        (sub / "q1.xlsx").write_bytes(b"excel")
        (sub / "q2.xlsx").write_bytes(b"excel")

        result = await tool_executor.execute("file_list", {"path": "reports"})

        assert "F1" in result
        assert "F2" in result
        assert tool_executor.file_handles.count == 2


# ============================================================
# E2E: code_execute + FILES 字典
# ============================================================


class TestCodeExecuteWithFILES:
    """code_execute 沙盒内可用 FILES 字典读取文件"""

    @pytest.mark.asyncio
    async def test_sandbox_reads_csv_via_files_handle(self, tool_executor, user_workspace):
        """沙盒用 FILES['F1'] 读 CSV"""
        Path(user_workspace, "data.csv").write_text("name,value\nalice,100\nbob,200")

        # 注册句柄
        await tool_executor.execute("file_list", {})

        # 沙盒读取
        code = (
            "import pandas as pd\n"
            "df = pd.read_csv(FILES['F1'])\n"
            "print(f'rows={len(df)}, cols={list(df.columns)}')"
        )
        result = await tool_executor.execute("code_execute", {
            "code": code,
            "description": "test FILES dict CSV read",
        })

        assert "rows=2" in result
        assert "name" in result

    @pytest.mark.asyncio
    async def test_sandbox_reads_txt_via_files_handle(self, tool_executor, user_workspace):
        """沙盒用 FILES['F1'] 读文本文件"""
        Path(user_workspace, "config.txt").write_text("key=value")

        await tool_executor.execute("file_list", {})

        code = (
            "with open(FILES['F1']) as f:\n"
            "    content = f.read()\n"
            "print(content)"
        )
        result = await tool_executor.execute("code_execute", {
            "code": code,
            "description": "test FILES dict text read",
        })

        assert "key=value" in result

    @pytest.mark.asyncio
    async def test_files_not_injected_without_file_list(self, tool_executor, user_workspace):
        """未调 file_list 时 FILES 未注入（空字典不注入，避免污染命名空间）"""
        Path(user_workspace, "data.csv").write_text("a,b\n1,2")

        code = (
            "try:\n"
            "    print(f'files_count={len(FILES)}')\n"
            "except Exception:\n"
            "    print('FILES_NOT_DEFINED')"
        )
        result = await tool_executor.execute("code_execute", {
            "code": code,
            "description": "test no FILES",
        })

        assert "FILES_NOT_DEFINED" in result


# ============================================================
# E2E: staging 文件注册到统一字典
# ============================================================


class TestStagingFileRegistration:
    """工具产出的 staging 文件也注册到 file_handles"""

    def test_staging_file_registered_via_handles(self, workspace):
        """模拟工具产出 staging parquet → 注册到 handles"""
        handles = WorkspaceFileHandles()

        # 模拟 workspace 文件
        handles.register(f"{workspace}/利润表.xlsx", "利润表.xlsx")

        # 模拟 staging 文件（工具产出）
        staging_path = f"{workspace}/staging/conv_001/trade_data.parquet"
        handles.register(staging_path, "trade_data.parquet")

        staging_path2 = f"{workspace}/staging/conv_001/stock_summary.parquet"
        handles.register(staging_path2, "stock_summary.parquet")

        # 统一字典
        d = handles.to_sandbox_dict()
        assert len(d) == 3
        assert d["F1"].endswith("利润表.xlsx")
        assert d["F2"].endswith("trade_data.parquet")
        assert d["F3"].endswith("stock_summary.parquet")

    @pytest.mark.asyncio
    async def test_mixed_workspace_and_staging_in_sandbox(self, tool_executor, user_workspace):
        """workspace 文件 + staging 文件混合在 FILES 字典里"""
        # workspace 文件
        Path(user_workspace, "report.csv").write_text("name,score\nAlice,95\nBob,87")

        # 注册 workspace 文件
        await tool_executor.execute("file_list", {})

        # 模拟 staging 文件注册（正常流程由 ToolLoopExecutor 完成）
        staging = Path(user_workspace, "staging", "conv_001")
        staging.mkdir(parents=True)
        staging_file = staging / "erp_data.csv"
        staging_file.write_text("product,amount\nA,1000\nB,2000")
        tool_executor.file_handles.register(str(staging_file), "erp_data.csv")

        # 沙盒同时读两个来源的文件
        code = (
            "import pandas as pd\n"
            "df1 = pd.read_csv(FILES['F1'])  # workspace\n"
            "df2 = pd.read_csv(FILES['F2'])  # staging\n"
            "print(f'workspace_rows={len(df1)}, staging_rows={len(df2)}')\n"
            "print(f'total_files={len(FILES)}')"
        )
        result = await tool_executor.execute("code_execute", {
            "code": code,
            "description": "test mixed sources",
        })

        assert "workspace_rows=2" in result
        assert "staging_rows=2" in result
        assert "total_files=2" in result


# ============================================================
# E2E: 边界场景
# ============================================================


class TestEdgeCases:
    """边界场景"""

    @pytest.mark.asyncio
    async def test_unknown_handle_falls_through(self, tool_executor, user_workspace):
        """未注册的句柄不翻译，当作相对路径处理"""
        result = await tool_executor.execute("file_read", {"path": "F99"})
        # F99 不是注册的句柄 → 当作相对路径 → 文件不存在
        assert "不存在" in result or "路径越界" in result

    @pytest.mark.asyncio
    async def test_handle_case_insensitive(self, tool_executor, user_workspace):
        """句柄大小写不敏感"""
        Path(user_workspace, "test.txt").write_text("case test")

        await tool_executor.execute("file_list", {})
        # 用小写 f1 读取
        result = await tool_executor.execute("file_read", {"path": "f1"})
        assert "case test" in result

    @pytest.mark.asyncio
    async def test_chinese_filename(self, tool_executor, user_workspace):
        """中文文件名正常注册和读取"""
        Path(user_workspace, "销售报表 2026年.csv").write_text("店铺,金额\n淘宝,1000")

        await tool_executor.execute("file_list", {})

        result = await tool_executor.execute("file_read", {"path": "F1"})
        assert "店铺" in result
        assert "淘宝" in result

    @pytest.mark.asyncio
    async def test_filename_with_spaces(self, tool_executor, user_workspace):
        """文件名含空格正常处理"""
        Path(user_workspace, "my report (final).txt").write_text("done")

        await tool_executor.execute("file_list", {})
        result = await tool_executor.execute("file_read", {"path": "F1"})
        assert "done" in result


# ============================================================
# _register_result_files（ToolLoopExecutor 提取方法）
# ============================================================


class TestRegisterResultFiles:
    """验证 _register_result_files 统一注册 file_ref + collected_files"""

    def _make_loop_executor(self):
        """构造最小 ToolLoopExecutor 实例（只需注册相关属性）"""
        from services.agent.workspace_file_handles import WorkspaceFileHandles
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.session_file_registry import SessionFileRegistry

        mock_executor = MagicMock()
        mock_executor.file_handles = WorkspaceFileHandles()

        loop_exec = ToolLoopExecutor.__new__(ToolLoopExecutor)
        loop_exec.executor = mock_executor
        loop_exec._file_registry = SessionFileRegistry()
        loop_exec._collected_files = []
        return loop_exec, mock_executor.file_handles

    def _make_file_ref(self, path: str, filename: str):
        from services.agent.tool_output import FileRef
        import time
        return FileRef(
            path=path, filename=filename, format="parquet",
            row_count=100, size_bytes=5000, columns=[],
            created_at=time.time(),
        )

    def test_registers_tooloutput_file_ref(self):
        """ToolOutput 的 file_ref 注册到 handles"""
        loop_exec, handles = self._make_loop_executor()
        ref = self._make_file_ref("/staging/trade.parquet", "trade.parquet")

        # 模拟 ToolOutput（只需 file_ref/source/collected_files 属性）
        result = MagicMock(spec=["file_ref", "source", "collected_files"])
        result.file_ref = ref
        result.source = "trade_agent"
        result.collected_files = None

        loop_exec._register_result_files(result, "erp_agent")

        assert loop_exec._file_registry.get_latest() is not None
        assert handles.resolve("F1") == "/staging/trade.parquet"

    def test_registers_agentresult_file_ref(self):
        """AgentResult 的 file_ref 也能注册"""
        loop_exec, handles = self._make_loop_executor()
        ref = self._make_file_ref("/staging/stock.parquet", "stock.parquet")

        # 模拟 AgentResult
        result = MagicMock(spec=["file_ref", "source", "collected_files"])
        result.file_ref = ref
        result.source = "erp_agent"
        result.collected_files = [{"url": "https://cdn/f.xlsx", "name": "f.xlsx", "mime_type": "x", "size": 1}]

        loop_exec._register_result_files(result, "erp_agent")

        assert handles.resolve("F1") == "/staging/stock.parquet"
        assert len(loop_exec._collected_files) == 1

    def test_skips_when_no_file_ref(self):
        """无 file_ref 时安全跳过"""
        loop_exec, handles = self._make_loop_executor()

        loop_exec._register_result_files("查询成功，共10条", "some_tool")

        assert handles.count == 0
        assert len(loop_exec._collected_files) == 0


# ============================================================
# build_sandbox_executor files_dict 透传
# ============================================================


class TestSandboxFilesDict:
    """验证 SandboxExecutor 的 files_dict 注入"""

    @pytest.mark.asyncio
    async def test_files_dict_injected_into_sandbox(self):
        """files_dict 注入为 FILES 全局变量"""
        from services.sandbox.executor import SandboxExecutor

        executor = SandboxExecutor(
            timeout=5.0, max_result_chars=2000,
            files_dict={"F1": "/tmp/a.csv", "F2": "/tmp/b.parquet"},
        )

        result = await executor.execute(
            "print(f'F1={FILES[\"F1\"]}, count={len(FILES)}')",
            "test FILES injection",
        )

        assert "F1=/tmp/a.csv" in result
        assert "count=2" in result

    @pytest.mark.asyncio
    async def test_empty_files_dict_not_injected(self):
        """空 files_dict 不注入 FILES"""
        from services.sandbox.executor import SandboxExecutor

        executor = SandboxExecutor(
            timeout=5.0, max_result_chars=2000,
            files_dict={},
        )

        code = (
            "try:\n"
            "    print(len(FILES))\n"
            "except Exception:\n"
            "    print('NO_FILES')"
        )
        result = await executor.execute(code, "test no FILES")
        assert "NO_FILES" in result

    @pytest.mark.asyncio
    async def test_files_dict_is_isolated_copy(self):
        """沙盒内修改 FILES 不影响原始字典"""
        from services.sandbox.executor import SandboxExecutor

        original = {"F1": "/tmp/test.csv"}
        executor = SandboxExecutor(
            timeout=5.0, max_result_chars=2000,
            files_dict=original,
        )

        code = "FILES['F99'] = '/hack'; print(f'sandbox_count={len(FILES)}')"
        result = await executor.execute(code, "test isolation")

        assert "sandbox_count=2" in result
        assert "F99" not in original
