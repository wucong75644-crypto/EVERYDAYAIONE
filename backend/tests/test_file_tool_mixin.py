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
            side_effect=FileOperationError("文件不存在: test.txt")
        )

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "test.txt"})

        assert isinstance(result, AgentResult)
        assert result.status == "error"
        assert result.metadata.get("retryable") is True
        assert "文件不存在" in result.summary

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
        """正常 str 结果 → AgentResult(success)"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(return_value="file content here")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor):
            result = await mixin._file_dispatch("file_read", {"path": "x.txt"})

        assert isinstance(result, AgentResult)
        assert result.status == "success"
        assert "file content here" in result.summary

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
        """备份存在 → 恢复成功"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_output import FileRef

        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        stg.mkdir()

        # 创建备份文件
        backup_file = stg / "_bak_1700000000_report.xlsx"
        backup_file.write_bytes(b"original data")

        # 当前文件（被修改后的）
        (ws / "report.xlsx").write_bytes(b"modified data")

        # 注册到 registry
        registry = SessionFileRegistry()
        ref = FileRef(
            path=str(backup_file), filename=backup_file.name,
            format="xlsx", row_count=0, size_bytes=13, columns=[],
        )
        registry.register("backup:report.xlsx", "code_execute", ref)

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        with patch(
            "services.agent.session_file_registry.get_conversation_registry",
            return_value=registry,
        ):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"})

        assert result.status == "success"
        assert "已恢复" in result.summary
        assert (ws / "report.xlsx").read_bytes() == b"original data"

    @pytest.mark.asyncio
    async def test_restore_no_backup(self, tmp_path):
        """无备份 → 返回 empty"""
        from services.agent.session_file_registry import SessionFileRegistry

        ws = tmp_path / "workspace"
        ws.mkdir()

        registry = SessionFileRegistry()
        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        with patch(
            "services.agent.session_file_registry.get_conversation_registry",
            return_value=registry,
        ):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"})

        assert result.status == "empty"
        assert "未找到" in result.summary

    @pytest.mark.asyncio
    async def test_restore_expired_backup(self, tmp_path):
        """备份文件已被清理 → 返回 error"""
        from services.agent.session_file_registry import SessionFileRegistry
        from services.agent.tool_output import FileRef

        ws = tmp_path / "workspace"
        ws.mkdir()

        # 注册一个指向不存在文件的 ref
        registry = SessionFileRegistry()
        ref = FileRef(
            path="/nonexistent/_bak_1700000000_report.xlsx",
            filename="_bak_1700000000_report.xlsx",
            format="xlsx", row_count=0, size_bytes=100, columns=[],
        )
        registry.register("backup:report.xlsx", "code_execute", ref)

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        with patch(
            "services.agent.session_file_registry.get_conversation_registry",
            return_value=registry,
        ):
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"})

        assert result.status == "error"
        assert "过期" in result.summary

    @pytest.mark.asyncio
    async def test_restore_empty_filename(self, tmp_path):
        """空文件名 → 返回 error"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        result = await mixin._restore_file(executor, {"filename": ""})
        assert result.status == "error"
        assert "请指定" in result.summary

    @pytest.mark.asyncio
    async def test_restore_path_traversal_blocked(self, tmp_path):
        """路径穿越攻击 → PermissionError"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))

        # ../etc/passwd 应该被 resolve_safe_path 拦截
        with pytest.raises(ValueError):
            await mixin._restore_file(executor, {"filename": "../../etc/passwd"})


# ============================================================
# file_read 数据文件分支路由
# ============================================================


class TestFileReadDataRouting:
    """file_read 对数据文件的路由：Excel → excel_reader / CSV → DataQueryExecutor"""

    @pytest.mark.asyncio
    async def test_xlsx_routes_to_data_branch(self, mixin):
        """Excel 文件走数据文件分支"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_result = AgentResult(summary="structured output", status="success")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor), \
             patch.object(mixin, "_file_read_data", new_callable=AsyncMock, return_value=mock_result) as mock_data:
            result = await mixin._file_dispatch("file_read", {"path": "report.xlsx"})

        mock_data.assert_called_once()
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_csv_routes_to_data_branch(self, mixin):
        """CSV 文件走数据文件分支"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_result = AgentResult(summary="csv profile", status="success")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor), \
             patch.object(mixin, "_file_read_data", new_callable=AsyncMock, return_value=mock_result) as mock_data:
            result = await mixin._file_dispatch("file_read", {"path": "data.csv"})

        mock_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_txt_does_not_route_to_data(self, mixin):
        """.txt 不走数据文件分支，走 FileExecutor"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(return_value="text content")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor), \
             patch.object(mixin, "_file_read_data", new_callable=AsyncMock) as mock_data:
            result = await mixin._file_dispatch("file_read", {"path": "notes.txt"})

        mock_data.assert_not_called()
        assert isinstance(result, AgentResult)
        assert "text content" in result.summary

    @pytest.mark.asyncio
    async def test_pdf_does_not_route_to_data(self, mixin):
        """.pdf 不走数据文件分支"""
        settings = MagicMock()
        settings.file_workspace_enabled = True
        settings.file_workspace_root = "/tmp"

        mock_executor = MagicMock()
        mock_executor.file_read = AsyncMock(return_value="pdf text")

        with patch("core.config.get_settings", create=True, return_value=settings), \
             patch("services.file_executor.FileExecutor", return_value=mock_executor), \
             patch.object(mixin, "_file_read_data", new_callable=AsyncMock) as mock_data:
            result = await mixin._file_dispatch("file_read", {"path": "doc.pdf"})

        mock_data.assert_not_called()
