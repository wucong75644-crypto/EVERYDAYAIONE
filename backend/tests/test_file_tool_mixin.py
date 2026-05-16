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
        """deleted_files 记录存在 → OSS 下载 → 恢复成功"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        executor._workspace_base = str(tmp_path)

        mock_settings = MagicMock()

        fake_record = {
            "id": "rec-1",
            "oss_object_key": "deleted/report.xlsx",
            "relative_path": "workspace/report.xlsx",
        }

        with patch.object(mixin, "_find_deleted_record", new_callable=AsyncMock, return_value=fake_record), \
             patch("services.oss_service.get_oss_service") as mock_oss_svc, \
             patch.object(mixin, "_mark_restored", new_callable=AsyncMock):
            # Mock OSS download to write the file
            def fake_download(key, path):
                from pathlib import Path as _P
                _P(path).write_bytes(b"original data")
            mock_oss_svc.return_value.bucket.get_object_to_file = fake_download
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"}, mock_settings)

        assert result.status == "success"
        assert "已恢复" in result.summary

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
        """路径穿越在 filename 中 → 返回 empty（DB 查不到穿越路径记录）"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        mock_settings = MagicMock()

        # _find_deleted_record returns None for traversal filenames
        with patch.object(mixin, "_find_deleted_record", new_callable=AsyncMock, return_value=None):
            result = await mixin._restore_file(executor, {"filename": "../../etc/passwd"}, mock_settings)
        assert result.status == "empty"


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
        """path 指向已存在的文件 → 调 _describe_single_file"""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "report.txt").write_text("hello")

        mixin = FakeMixin()
        executor = MagicMock()
        executor.resolve_safe_path = lambda p: ws / p

        settings = MagicMock()
        settings.file_workspace_root = str(tmp_path)

        with patch("core.workspace.resolve_staging_dir", return_value=str(tmp_path / "staging")), \
             patch.object(mixin, "_describe_single_file", new_callable=AsyncMock,
                          return_value=AgentResult(summary="described", status="success")) as mock_desc:
            result = await mixin._file_search(executor, {"path": "report.txt"}, settings)

        mock_desc.assert_called_once()
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
# _describe_single_file
# ============================================================


class TestDescribeSingleFile:
    """单个文件描述（NAS 模式：不做转换，只返回路径）"""

    @pytest.mark.asyncio
    async def test_returns_workspace_path(self, tmp_path):
        """返回 WORKSPACE_DIR 路径"""
        ws = tmp_path / "workspace"
        ws.mkdir()
        f = ws / "report.xlsx"
        f.write_bytes(b"fake")

        mixin = FakeMixin()
        executor = MagicMock()
        executor.workspace_root = str(ws)
        result = await mixin._describe_single_file(executor, str(f))

        assert result.status == "success"
        assert "report.xlsx" in result.summary
        assert "open(" in result.summary


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
    async def test_no_record_returns_empty(self, tmp_path):
        """DB 无删除记录 → 返回 empty"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        settings = MagicMock()

        with patch.object(mixin, "_find_deleted_record", new_callable=AsyncMock, return_value=None):
            result = await mixin._restore_file(executor, {"filename": "data.csv"}, settings)

        assert result.status == "empty"

    @pytest.mark.asyncio
    async def test_restore_from_oss_success(self, tmp_path):
        """DB 有记录 + OSS 下载成功 → 恢复成功"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        executor._workspace_base = str(tmp_path)
        settings = MagicMock()

        fake_record = {
            "id": "rec-99",
            "oss_object_key": "deleted/report.xlsx",
            "relative_path": "workspace/report.xlsx",
        }

        with patch.object(mixin, "_find_deleted_record", new_callable=AsyncMock, return_value=fake_record), \
             patch("services.oss_service.get_oss_service") as mock_oss_svc, \
             patch.object(mixin, "_mark_restored", new_callable=AsyncMock):
            def fake_download(key, path):
                from pathlib import Path as _P
                _P(path).write_bytes(b"newest version")
            mock_oss_svc.return_value.bucket.get_object_to_file = fake_download
            result = await mixin._restore_file(executor, {"filename": "report.xlsx"}, settings)

        assert result.status == "success"
        assert (ws / "report.xlsx").read_bytes() == b"newest version"

    @pytest.mark.asyncio
    async def test_oss_failure_returns_error(self, tmp_path):
        """OSS 下载失败 → 返回 error"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        executor._workspace_base = str(tmp_path)
        settings = MagicMock()

        fake_record = {
            "id": "rec-1",
            "oss_object_key": "deleted/test.txt",
            "relative_path": "workspace/test.txt",
        }

        with patch.object(mixin, "_find_deleted_record", new_callable=AsyncMock, return_value=fake_record), \
             patch("services.oss_service.get_oss_service") as mock_oss_svc:
            mock_oss_svc.return_value.bucket.get_object_to_file.side_effect = Exception("OSS timeout")
            result = await mixin._restore_file(executor, {"filename": "test.txt"}, settings)

        assert result.status == "error"
        assert "失败" in result.summary

    @pytest.mark.asyncio
    async def test_mark_restored_called_after_success(self, tmp_path):
        """恢复成功后调用 _mark_restored"""
        ws = tmp_path / "workspace"
        ws.mkdir()

        mixin = FakeMixin()
        executor = self._make_executor(str(ws))
        executor._workspace_base = str(tmp_path)
        settings = MagicMock()

        fake_record = {
            "id": "rec-42",
            "oss_object_key": "deleted/test.txt",
            "relative_path": "workspace/test.txt",
        }

        with patch.object(mixin, "_find_deleted_record", new_callable=AsyncMock, return_value=fake_record), \
             patch("services.oss_service.get_oss_service") as mock_oss_svc, \
             patch.object(mixin, "_mark_restored", new_callable=AsyncMock) as mock_mark:
            def fake_download(key, path):
                from pathlib import Path as _P
                _P(path).write_bytes(b"restored")
            mock_oss_svc.return_value.bucket.get_object_to_file = fake_download
            await mixin._restore_file(executor, {"filename": "test.txt"}, settings)

        mock_mark.assert_awaited_once_with("rec-42")



# Parquet 转换限制测试已删除（NAS 模式不做转换）
