"""测试 file_tool_mixin — FileToolMixin + CrawlerToolMixin"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Mock pydantic_settings 以避免环境依赖
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = MagicMock()

import pytest

from services.agent.agent_result import AgentResult
from services.agent.file_tool_mixin import CrawlerToolMixin, FileToolMixin
from services.file_executor import FileOperationError


# ── Fixtures ──


class FakeMixin(FileToolMixin, CrawlerToolMixin):
    """组合 Mixin 以测试（模拟宿主类属性）"""

    def __init__(self, user_id="u1", org_id="org1", conversation_id="conv1"):
        self.user_id = user_id
        self.org_id = org_id
        self.conversation_id = conversation_id
        self._workspace_dir_override = ""

    def _get_workspace_dir(self) -> str:
        return self._workspace_dir_override


@pytest.fixture
def mixin():
    return FakeMixin()


# ============================================================
# FileToolMixin._file_dispatch
# ============================================================


class TestFileDispatchDisabled:
    """功能关闭时返回 error"""

    @pytest.mark.asyncio
    async def test_disabled_returns_error(self, mixin):
        settings = MagicMock()
        settings.file_workspace_enabled = False

        with patch("core.config.get_settings", create=True, return_value=settings):
            result = await mixin._file_dispatch("file_read", {"path": "x.txt"})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert result.metadata.get("retryable") is False


class TestFileDispatchErrors:
    """各类异常正确映射为 AgentResult"""

    @pytest.mark.asyncio
    async def test_file_operation_error_retryable(self, mixin):
        """FileOperationError → status=error, retryable=True"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(
            side_effect=FileOperationError("文件不存在: test.png")
        )

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "test.png"})

        assert isinstance(result, AgentResult)
        assert result.status == "error"
        assert result.metadata.get("retryable") is True
        assert "图片读取失败" in result.summary

    @pytest.mark.asyncio
    async def test_permission_error_not_retryable(self, mixin):
        """PermissionError → status=error, retryable=False"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_write = AsyncMock(
            side_effect=PermissionError("access denied")
        )

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_write", {"path": "x", "content": "y"})

        assert isinstance(result, AgentResult)
        assert result.status == "error"
        assert result.metadata.get("retryable") is False

    @pytest.mark.asyncio
    async def test_unknown_tool_error(self, mixin):
        """未知工具名 → error"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_nonexistent", {})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "Unknown" in result.summary


class TestFileDispatchSuccess:
    """成功路径"""

    @pytest.mark.asyncio
    async def test_str_result_wrapped_as_success(self, mixin):
        """正常 str 结果（图片路径）→ AgentResult(success)"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(return_value="image content here")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "x.png"})

        assert isinstance(result, AgentResult)
        assert result.status == "success"
        assert "image content here" in result.summary

    @pytest.mark.asyncio
    async def test_file_read_result_passthrough(self, mixin):
        """FileReadResult（图片多模态）直接透传，不包装为 AgentResult"""
        from services.file_executor import FileReadResult

        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        fr = FileReadResult(type="image", text="", image_url="https://cdn/img.png")
        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(return_value=fr)

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "img.png"})

        assert isinstance(result, FileReadResult)
        assert result.image_url == "https://cdn/img.png"


# ============================================================
# CrawlerToolMixin._social_crawler
# ============================================================


class TestSocialCrawlerDisabled:
    """爬虫功能关闭"""

    @pytest.mark.asyncio
    async def test_disabled_returns_error(self, mixin):
        settings = MagicMock()
        settings.crawler_enabled = False

        with patch("core.config.get_settings", create=True, return_value=settings):
            result = await mixin._social_crawler({"keywords": "test"})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "未启用" in result.summary


class TestSocialCrawlerValidation:
    """参数校验"""

    @pytest.mark.asyncio
    async def test_empty_keywords_error(self, mixin):
        settings = MagicMock()
        settings.crawler_enabled = True

        mock_service = MagicMock()
        mock_service.is_available.return_value = True

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.crawler.service.CrawlerService", return_value=mock_service):
            result = await mixin._social_crawler({"keywords": ""})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "不能为空" in result.summary
        assert result.metadata.get("retryable") is True


class TestSocialCrawlerExecution:
    """执行路径"""

    @pytest.mark.asyncio
    async def test_crawl_error_returns_error(self, mixin):
        settings = MagicMock()
        settings.crawler_enabled = True

        mock_result = MagicMock()
        mock_result.error = "network timeout"
        mock_result.items = []

        mock_service = MagicMock()
        mock_service.is_available.return_value = True
        mock_service.execute = AsyncMock(return_value=mock_result)

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.crawler.service.CrawlerService", return_value=mock_service):
            result = await mixin._social_crawler({"keywords": "测试", "platform": "xhs"})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "network timeout" in result.summary

    @pytest.mark.asyncio
    async def test_crawl_success(self, mixin):
        settings = MagicMock()
        settings.crawler_enabled = True

        mock_result = MagicMock()
        mock_result.error = None
        mock_result.items = [{"title": "小红书笔记"}]

        mock_service = MagicMock()
        mock_service.is_available.return_value = True
        mock_service.execute = AsyncMock(return_value=mock_result)
        mock_service.format_for_brain.return_value = "搜索结果: 小红书笔记"

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.crawler.service.CrawlerService", return_value=mock_service):
            result = await mixin._social_crawler({"keywords": "测试"})

        assert isinstance(result, AgentResult)
        assert result.status == "success"
        assert "小红书笔记" in result.summary


# ============================================================
# FileToolMixin._restore_file
# ============================================================


class TestRestoreFile:
    """restore_file 工具测试"""

    @staticmethod
    def _make_executor(ws_path):
        """创建真实 FileExecutor（复用项目路径安全基础设施）"""
        from services.file_executor import FileExecutor
        # FileExecutor 需要 workspace_root，_root 会解析为 ws_path
        executor = MagicMock()
        # 用真实的 resolve_safe_path 逻辑：realpath + workspace 白名单
        from pathlib import Path as _Path
        _root = _Path(ws_path).resolve()

        def _resolve(path_input):
            p = path_input.strip()
            if _Path(p).is_absolute():
                target = _Path(p).resolve()
            else:
                target = (_root / p.lstrip("/").lstrip("\\")).resolve()
            target.relative_to(_root)  # ValueError → PermissionError
            return target

        executor.resolve_safe_path = _resolve
        return executor

    @pytest.mark.asyncio
    async def test_restore_success(self, tmp_path):
        """备份存在 → 恢复成功（glob 扫描 staging 目录）"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        # 创建备份文件
        backup_file = stg / "_bak_1700000000_report.xlsx"
        backup_file.write_bytes(b"original data")

        # 当前文件（被修改后的）
        (ws / "report.xlsx").write_bytes(b"modified data")

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        mock_settings = MagicMock()
        mock_settings.file_workspace_root = str(tmp_path)

        with patch(
            "core.workspace.resolve_staging_dir",
            return_value=str(stg),
        ):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"}, mock_settings)

        assert result.status == "success"
        assert "已恢复" in result.summary
        assert (ws / "report.xlsx").read_bytes() == b"original data"

    @pytest.mark.asyncio
    async def test_restore_no_backup(self, tmp_path):
        """无备份 → 返回 empty"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        mock_settings = MagicMock()
        mock_settings.file_workspace_root = str(tmp_path)

        with patch(
            "core.workspace.resolve_staging_dir",
            return_value=str(stg),
        ):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"}, mock_settings)

        assert result.status == "empty"
        assert "未找到" in result.summary

    @pytest.mark.asyncio
    async def test_restore_no_backup_empty_staging(self, tmp_path):
        """staging 目录不存在 → 返回 empty"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        mock_settings = MagicMock()
        mock_settings.file_workspace_root = str(tmp_path)

        with patch(
            "core.workspace.resolve_staging_dir",
            return_value=str(tmp_path / "nonexistent_staging"),
        ):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"}, mock_settings)

        assert result.status == "empty"
        assert "未找到" in result.summary

    @pytest.mark.asyncio
    async def test_restore_empty_filename(self, tmp_path):
        """空文件名 → 返回 error"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        mock_settings = MagicMock()
        result = await mixin._restore_file(executor, {"filename": ""}, mock_settings)
        assert result.status == "error"
        assert "请指定" in result.summary

    @pytest.mark.asyncio
    async def test_restore_path_traversal_blocked(self, tmp_path):
        """路径穿越攻击 → PermissionError"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        mock_settings = MagicMock()

        # ../etc/passwd 应该被 resolve_safe_path 拦截
        with pytest.raises(ValueError):
            await mixin._restore_file(executor, {"filename": "../../etc/passwd"}, mock_settings)


# ============================================================
# file_read 数据文件分支路由
# ============================================================


class TestFileReadImageOnly:
    """file_read 仅接受图片文件"""

    @pytest.mark.asyncio
    async def test_xlsx_rejected(self, mixin):
        """Excel 文件被拒绝（应使用 file_search + code_execute）"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "report.xlsx"})

        assert result.status == "error"
        assert "仅支持图片" in result.summary

    @pytest.mark.asyncio
    async def test_csv_rejected(self, mixin):
        """CSV 文件被拒绝"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "data.csv"})

        assert result.status == "error"
        assert "仅支持图片" in result.summary

    @pytest.mark.asyncio
    async def test_txt_rejected(self, mixin):
        """文本文件被拒绝"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "notes.txt"})

        assert result.status == "error"
        assert "仅支持图片" in result.summary

    @pytest.mark.asyncio
    async def test_png_accepted(self, mixin):
        """PNG 图片正常通过"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(return_value="image data")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "photo.png"})

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_no_extension_rejected(self, mixin):
        """无扩展名文件被拒绝"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"
        mock_executor = MagicMock()

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "README"})

        assert result.status == "error"
        assert "仅支持图片" in result.summary

    @pytest.mark.asyncio
    async def test_empty_path_rejected(self, mixin):
        """空路径 → 返回 error"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"
        mock_executor = MagicMock()

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": ""})

        assert result.status == "error"
        assert "请指定" in result.summary


# ============================================================
# _sanitize_filename
# ============================================================


class TestSanitizeFilename:
    """安全文件名转换"""

    def test_ascii_name_preserved(self):
        from services.agent.file_tool_mixin import _sanitize_filename
        assert _sanitize_filename("sales_2024.xlsx", 1) == "sales_2024_001.parquet"

    def test_chinese_name_becomes_file(self):
        """中文名全部移除后为空 → 兜底 'file'"""
        from services.agent.file_tool_mixin import _sanitize_filename
        result = _sanitize_filename("销售数据.xlsx", 2)
        assert result == "file_002.parquet"

    def test_mixed_name_keeps_ascii(self):
        """中英混合保留英文部分（下划线也保留）"""
        from services.agent.file_tool_mixin import _sanitize_filename
        result = _sanitize_filename("Q1_销售报告_final.csv", 3)
        # Q1_ 保留，中文移除，_final 保留 → Q1__final
        assert result == "Q1__final_003.parquet"
        assert ".parquet" in result

    def test_special_chars_removed(self):
        """特殊字符（空格、括号、全角）被移除"""
        from services.agent.file_tool_mixin import _sanitize_filename
        result = _sanitize_filename("data (copy) [v2].xlsx", 4)
        assert result == "datacopyv2_004.parquet"

    def test_long_name_truncated(self):
        """超长名截断到 30 字符"""
        from services.agent.file_tool_mixin import _sanitize_filename
        long_name = "a" * 50 + ".xlsx"
        result = _sanitize_filename(long_name, 5)
        stem = result.replace("_005.parquet", "")
        assert len(stem) <= 30

    def test_index_formatting(self):
        """序号格式：3位补零"""
        from services.agent.file_tool_mixin import _sanitize_filename
        assert _sanitize_filename("x.csv", 99).endswith("_099.parquet")
        assert _sanitize_filename("x.csv", 1).endswith("_001.parquet")


# ============================================================
# _fmt_size
# ============================================================


class TestFmtSize:
    """文件大小格式化"""

    def test_bytes(self):
        assert FileToolMixin._fmt_size(500) == "500 B"

    def test_kilobytes(self):
        assert FileToolMixin._fmt_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert FileToolMixin._fmt_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert FileToolMixin._fmt_size(2 * 1024 * 1024 * 1024) == "2.0 GB"


# ============================================================
# _file_search 路由逻辑
# ============================================================


class TestFileSearchRouting:
    """file_search 的模式判断：单文件 / 目录 / 关键词搜索 / 无参数"""

    @pytest.mark.asyncio
    async def test_path_to_existing_file(self, tmp_path):
        """path 指向已存在的文件 → 调 _prepare_single_file"""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "report.txt").write_text("hello")

        mixin = FakeMixin()
        executor = MagicMock()
        executor.resolve_safe_path = lambda p: ws / p

        settings = MagicMock()
        settings.file_workspace_root = str(tmp_path)

        with patch("core.workspace.resolve_staging_dir", return_value=str(tmp_path / "staging")), \
             patch.object(mixin, "_prepare_single_file", new_callable=AsyncMock,
                          return_value=AgentResult(summary="prepared", status="success")) as mock_prep:
            result = await mixin._file_search(executor, {"path": "report.txt"}, settings)

        mock_prep.assert_called_once()
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_path_to_existing_dir(self, tmp_path):
        """path 指向目录 → 调 _list_directory"""
        ws = tmp_path / "workspace"
        sub = ws / "reports"
        sub.mkdir(parents=True)

        mixin = FakeMixin()
        executor = MagicMock()
        executor.resolve_safe_path = lambda p: ws / p

        settings = MagicMock()
        settings.file_workspace_root = str(tmp_path)

        with patch("core.workspace.resolve_staging_dir", return_value=str(tmp_path / "staging")), \
             patch.object(mixin, "_list_directory", new_callable=AsyncMock,
                          return_value=AgentResult(summary="listed", status="success")) as mock_list:
            result = await mixin._file_search(executor, {"path": "reports"}, settings)

        mock_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_path_not_found_returns_error(self, tmp_path):
        """path 指向不存在的文件/目录 → 返回明确错误"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = MagicMock()
        executor.resolve_safe_path = lambda p: ws / p  # 不存在但不抛异常

        settings = MagicMock()
        settings.file_workspace_root = str(tmp_path)

        with patch("core.workspace.resolve_staging_dir", return_value=str(tmp_path / "staging")):
            result = await mixin._file_search(executor, {"path": "nonexistent.xlsx"}, settings)

        assert result.status == "error"
        assert "未找到" in result.summary

    @pytest.mark.asyncio
    async def test_keyword_routes_to_search(self, mixin):
        """有 keyword → 调 _search_files"""
        settings = MagicMock()
        settings.file_workspace_root = "/tmp"

        with patch("core.workspace.resolve_staging_dir", return_value="/tmp/staging"), \
             patch.object(mixin, "_search_files", new_callable=AsyncMock,
                          return_value=AgentResult(summary="found", status="success")) as mock_search:
            result = await mixin._file_search(MagicMock(), {"keyword": "sales"}, settings)

        mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_params_lists_root(self, mixin):
        """无参数 → 调 _list_directory"""
        settings = MagicMock()
        settings.file_workspace_root = "/tmp"

        with patch("core.workspace.resolve_staging_dir", return_value="/tmp/staging"), \
             patch.object(mixin, "_list_directory", new_callable=AsyncMock,
                          return_value=AgentResult(summary="root dir", status="success")) as mock_list:
            result = await mixin._file_search(MagicMock(), {}, settings)

        mock_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_path_exception_returns_error(self, mixin):
        """resolve_safe_path 抛异常 → 返回路径无效错误"""
        executor = MagicMock()
        executor.resolve_safe_path = MagicMock(side_effect=PermissionError("blocked"))

        settings = MagicMock()
        settings.file_workspace_root = "/tmp"

        with patch("core.workspace.resolve_staging_dir", return_value="/tmp/staging"):
            result = await mixin._file_search(executor, {"path": "../../etc/passwd"}, settings)

        assert result.status == "error"
        assert "路径无效" in result.summary


# ============================================================
# _prepare_single_file
# ============================================================


class TestPrepareSingleFile:
    """单个文件准备：数据文件 vs 非数据文件"""

    @pytest.mark.asyncio
    async def test_pdf_returns_pdfplumber_hint(self, tmp_path):
        """PDF → 返回 pdfplumber 使用提示"""
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"fake pdf")

        mixin = FakeMixin()
        executor = MagicMock()
        result = await mixin._prepare_single_file(executor, str(pdf), str(tmp_path / "staging"))

        assert result.status == "success"
        assert "pdfplumber" in result.summary

    @pytest.mark.asyncio
    async def test_docx_returns_docx_hint(self, tmp_path):
        """DOCX → 返回 docx.Document 使用提示"""
        doc = tmp_path / "report.docx"
        doc.write_bytes(b"fake docx")

        mixin = FakeMixin()
        executor = MagicMock()
        result = await mixin._prepare_single_file(executor, str(doc), str(tmp_path / "staging"))

        assert result.status == "success"
        assert "Document" in result.summary

    @pytest.mark.asyncio
    async def test_txt_returns_open_hint(self, tmp_path):
        """TXT → 返回 open() 使用提示"""
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")

        mixin = FakeMixin()
        executor = MagicMock()
        result = await mixin._prepare_single_file(executor, str(txt), str(tmp_path / "staging"))

        assert result.status == "success"
        assert "open(" in result.summary

    @pytest.mark.asyncio
    async def test_csv_triggers_parquet_conversion(self, tmp_path):
        """CSV → 触发 Parquet 转换"""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("a,b\n1,2")

        stg = tmp_path / "staging"
        stg.mkdir()

        mixin = FakeMixin()
        executor = MagicMock()

        with patch.object(mixin, "_batch_prepare_parquet", new_callable=AsyncMock,
                          return_value=[{"original": "data.csv", "parquet": "data_001.parquet", "rows": 1, "cols": 2}]):
            result = await mixin._prepare_single_file(executor, str(csv_file), str(stg))

        assert result.status == "success"
        assert "data_001.parquet" in result.summary
        assert "duckdb.sql" in result.summary


# ============================================================
# _batch_prepare_parquet
# ============================================================


class TestBatchPrepareParquet:
    """批量 Parquet 转换 + manifest"""

    @pytest.mark.asyncio
    async def test_manifest_written(self, tmp_path):
        """转换后写入 _manifest.json"""
        stg = tmp_path / "staging"
        stg.mkdir()

        # Mock ensure_parquet_cache 返回一个假的 cache 路径
        cache_file = stg / "cached.parquet"
        cache_file.write_bytes(b"fake parquet")

        mixin = FakeMixin()

        with patch("services.agent.data_query_cache.ensure_parquet_cache",
                    new_callable=AsyncMock, return_value=(str(cache_file), None)), \
             patch.object(FileToolMixin, "_parquet_shape", return_value=(100, 5)):
            entries = await mixin._batch_prepare_parquet(
                [{"name": "test.xlsx", "abs_path": "/fake/test.xlsx", "size": 1024}],
                str(stg),
            )

        assert len(entries) == 1
        assert entries[0]["original"] == "test.xlsx"
        assert entries[0]["rows"] == 100
        assert entries[0]["cols"] == 5

        manifest_path = stg / "_manifest.json"
        assert manifest_path.exists()
        import json
        manifest = json.loads(manifest_path.read_text())
        assert len(manifest["files"]) == 1
        assert "updated_at" in manifest

    @pytest.mark.asyncio
    async def test_incremental_update(self, tmp_path):
        """已存在的 manifest 条目不重复转换"""
        stg = tmp_path / "staging"
        stg.mkdir()

        # 预先写入 manifest + 对应的 parquet 文件
        existing_pq = stg / "test_001.parquet"
        existing_pq.write_bytes(b"existing")
        import json
        manifest = {"files": [{"original": "test.xlsx", "parquet": "test_001.parquet", "rows": 50, "cols": 3}]}
        (stg / "_manifest.json").write_text(json.dumps(manifest))

        mixin = FakeMixin()

        # ensure_parquet_cache 不应被调用
        with patch("services.agent.data_query_cache.ensure_parquet_cache",
                    new_callable=AsyncMock) as mock_cache:
            entries = await mixin._batch_prepare_parquet(
                [{"name": "test.xlsx", "abs_path": "/fake/test.xlsx", "size": 1024}],
                str(stg),
            )

        mock_cache.assert_not_called()
        assert len(entries) == 1
        assert entries[0]["rows"] == 50  # 复用旧值

    @pytest.mark.asyncio
    async def test_conversion_error_skipped(self, tmp_path):
        """转换失败 → 跳过该文件，不影响其他文件"""
        stg = tmp_path / "staging"
        stg.mkdir()

        mixin = FakeMixin()

        with patch("services.agent.data_query_cache.ensure_parquet_cache",
                    new_callable=AsyncMock, side_effect=Exception("conversion failed")):
            entries = await mixin._batch_prepare_parquet(
                [{"name": "bad.xlsx", "abs_path": "/fake/bad.xlsx", "size": 1024}],
                str(stg),
            )

        assert len(entries) == 0
        # manifest 仍然被写入（空文件列表）
        assert (stg / "_manifest.json").exists()

    @pytest.mark.asyncio
    async def test_parquet_file_copied_directly(self, tmp_path):
        """Parquet 文件直接 copy 到 staging，不需要转换"""
        stg = tmp_path / "staging"
        stg.mkdir()

        src = tmp_path / "data.parquet"
        src.write_bytes(b"parquet content")

        mixin = FakeMixin()

        with patch.object(FileToolMixin, "_parquet_shape", return_value=(200, 8)):
            entries = await mixin._batch_prepare_parquet(
                [{"name": "data.parquet", "abs_path": str(src), "size": 1024}],
                str(stg),
            )

        assert len(entries) == 1
        # 文件已 copy 到 staging
        dst = stg / entries[0]["parquet"]
        assert dst.exists()
        assert dst.read_bytes() == b"parquet content"


# ============================================================
# _restore_file 精确匹配
# ============================================================


class TestRestoreFilePreciseMatch:
    """restore_file 精确匹配避免误命中"""

    @staticmethod
    def _make_executor(ws_path):
        executor = MagicMock()
        from pathlib import Path as _Path
        _root = _Path(ws_path).resolve()

        def _resolve(path_input):
            p = path_input.strip()
            target = (_root / p).resolve()
            target.relative_to(_root)
            return target

        executor.resolve_safe_path = _resolve
        return executor

    @pytest.mark.asyncio
    async def test_no_cross_match_similar_names(self, tmp_path):
        """_bak_123_old_data.csv 不会被 restore_file(filename='data.csv') 误匹配"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        # 创建两个备份：data.csv 和 old_data.csv
        (stg / "_bak_1700000001_data.csv").write_bytes(b"correct backup")
        (stg / "_bak_1700000002_old_data.csv").write_bytes(b"wrong backup")

        # 当前文件
        (ws / "data.csv").write_bytes(b"modified")

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        settings = MagicMock()

        with patch("core.workspace.resolve_staging_dir", return_value=str(stg)):
            result = await mixin._restore_file(executor, {"filename": "data.csv"}, settings)

        assert result.status == "success"
        # 恢复的是 data.csv 的备份，不是 old_data.csv 的
        assert (ws / "data.csv").read_bytes() == b"correct backup"

    @pytest.mark.asyncio
    async def test_picks_newest_backup(self, tmp_path):
        """多个备份时取最新的（时间戳最大）"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        (stg / "_bak_1700000001_report.xlsx").write_bytes(b"old version")
        (stg / "_bak_1700000099_report.xlsx").write_bytes(b"newest version")
        (ws / "report.xlsx").write_bytes(b"current")

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        settings = MagicMock()

        with patch("core.workspace.resolve_staging_dir", return_value=str(stg)):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"}, settings)

        assert result.status == "success"
        assert (ws / "report.xlsx").read_bytes() == b"newest version"

    @pytest.mark.asyncio
    async def test_non_numeric_timestamp_ignored(self, tmp_path):
        """非数字时间戳的文件不会被匹配"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        # 这个文件名中 "abc" 不是有效时间戳
        (stg / "_bak_abc_data.csv").write_bytes(b"bad format")
        (ws / "data.csv").write_bytes(b"current")

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        settings = MagicMock()

        with patch("core.workspace.resolve_staging_dir", return_value=str(stg)):
            result = await mixin._restore_file(executor, {"filename": "data.csv"}, settings)

        assert result.status == "empty"  # 没有有效备份

    @pytest.mark.asyncio
    async def test_backup_deleted_after_restore(self, tmp_path):
        """恢复后备份文件被删除（一次性使用）"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        bak = stg / "_bak_1700000000_test.txt"
        bak.write_bytes(b"backup data")
        (ws / "test.txt").write_bytes(b"modified")

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        settings = MagicMock()

        with patch("core.workspace.resolve_staging_dir", return_value=str(stg)):
            await mixin._restore_file(executor, {"filename": "test.txt"}, settings)

        assert not bak.exists(), "backup should be deleted after restore"


# ============================================================
# _list_directory MAX_AUTO_CONVERT 限制
# ============================================================


class TestListDirectoryAutoConvert:
    """列目录自动 Parquet 转换限制"""

    @pytest.mark.asyncio
    async def test_max_auto_convert_limit(self, mixin):
        """超过 5 个数据文件时只转换前 5 个"""
        executor = MagicMock()
        executor._format_size = MagicMock(return_value="10 KB")
        executor.workspace_root = "/tmp/ws"
        executor.file_list_entries = AsyncMock(return_value={
            "error": None,
            "path": ".",
            "dirs": [],
            "files": [
                {"name": f"file{i}.xlsx", "abs_path": f"/tmp/ws/file{i}.xlsx", "size": 2000}
                for i in range(8)  # 8 个 Excel
            ],
        })

        with patch.object(mixin, "_batch_prepare_parquet", new_callable=AsyncMock,
                          return_value=[]) as mock_batch:
            result = await mixin._list_directory(executor, {}, "/tmp/staging")

        # 应该只传入 5 个文件
        called_files = mock_batch.call_args[0][0]
        assert len(called_files) == 5
        # 结果应提示还有 3 个未转换
        assert "3 个数据文件未自动转换" in result.summary

    @pytest.mark.asyncio
    async def test_small_files_skipped(self, mixin):
        """小于 1KB 的数据文件不自动转换"""
        executor = MagicMock()
        executor._format_size = MagicMock(return_value="500 B")
        executor.workspace_root = "/tmp/ws"
        executor.file_list_entries = AsyncMock(return_value={
            "error": None,
            "path": ".",
            "dirs": [],
            "files": [
                {"name": "tiny.csv", "abs_path": "/tmp/ws/tiny.csv", "size": 500},  # < 1KB
                {"name": "big.csv", "abs_path": "/tmp/ws/big.csv", "size": 5000},   # > 1KB
            ],
        })

        with patch.object(mixin, "_batch_prepare_parquet", new_callable=AsyncMock,
                          return_value=[]) as mock_batch:
            await mixin._list_directory(executor, {}, "/tmp/staging")

        called_files = mock_batch.call_args[0][0]
        names = [f["name"] for f in called_files]
        assert "big.csv" in names
        assert "tiny.csv" not in names
