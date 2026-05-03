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
