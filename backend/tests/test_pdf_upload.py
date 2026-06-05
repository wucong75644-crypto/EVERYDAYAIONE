"""
PDF 上传功能单元测试

测试 PDF 上传相关的所有改动点：
- StorageService.upload_file() 文件类型/大小校验
- BaseHandler._extract_file_urls() 文件 URL 提取
- ChatContextMixin._build_llm_messages() FilePart 支持
- AgentLoop PDF 检测 + 上下文注入
- Google 适配器 _detect_mime_type() PDF 扩展名
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.exceptions import ValidationError
from schemas.message import TextPart, ImagePart, FilePart
from tests.conftest import MockSupabaseClient


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    return MockSupabaseClient()


# ============ StorageService.upload_file ============


class TestStorageServiceUploadFile:
    """测试 StorageService.upload_file()"""

    @pytest.fixture
    def storage(self, mock_db):
        from services.storage_service import StorageService
        return StorageService(mock_db)

    def test_upload_pdf_success(self, storage):
        """测试：成功上传 PDF"""
        fake_oss = MagicMock()
        fake_oss.upload_bytes.return_value = {
            "url": "https://cdn.example.com/doc.pdf",
            "object_key": "documents/user1/abc.pdf",
        }

        with patch("services.storage_service.get_oss_service", return_value=fake_oss):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                storage.upload_file(
                    user_id="user1",
                    file_data=b"%PDF-1.4 fake content",
                    content_type="application/pdf",
                    filename="report.pdf",
                )
            )

        assert result["url"] == "https://cdn.example.com/doc.pdf"
        assert result["name"] == "report.pdf"
        assert result["mime_type"] == "application/pdf"
        assert result["size"] == len(b"%PDF-1.4 fake content")

    def test_upload_unsupported_type(self, storage):
        """测试：不支持的文件类型"""
        import asyncio
        with pytest.raises(ValidationError, match="不支持的文件类型"):
            asyncio.get_event_loop().run_until_complete(
                storage.upload_file(
                    user_id="user1",
                    file_data=b"fake",
                    content_type="application/x-executable",
                )
            )

    def test_upload_file_too_large(self, storage):
        """测试：文件超过 50MB"""
        big_data = b"x" * (51 * 1024 * 1024)
        import asyncio
        with pytest.raises(ValidationError, match="文件过大"):
            asyncio.get_event_loop().run_until_complete(
                storage.upload_file(
                    user_id="user1",
                    file_data=big_data,
                    content_type="application/pdf",
                )
            )

    def test_upload_default_filename(self, storage):
        """测试：未提供文件名时使用默认名"""
        fake_oss = MagicMock()
        fake_oss.upload_bytes.return_value = {
            "url": "https://cdn.example.com/doc.pdf",
            "object_key": "documents/user1/abc.pdf",
        }

        with patch("services.storage_service.get_oss_service", return_value=fake_oss):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                storage.upload_file(
                    user_id="user1",
                    file_data=b"%PDF content",
                    content_type="application/pdf",
                    filename=None,
                )
            )

        assert result["name"] == "document.pdf"


# ============ BaseHandler._extract_file_urls ============


class TestExtractFileUrls:
    """测试 BaseHandler._extract_file_urls()"""

    @pytest.fixture
    def handler(self, mock_db):
        from services.handlers.chat_handler import ChatHandler
        return ChatHandler(db=mock_db)

    # _extract_file_urls 已按 mime 分流：仅 image/* 的 FilePart 进 image_url 多模态块
    # PDF/Excel 等非图片 FilePart 不再被错塞进多模态，改走 <attachments> XML 块
    def test_extract_file_urls_pdf_filtered_out(self, handler):
        """PDF FilePart 不进 image_url 多模态块（mime 不是 image/*）"""
        content = [
            TextPart(text="分析这份报告"),
            FilePart(url="https://cdn.example.com/report.pdf", name="report.pdf", mime_type="application/pdf"),
        ]

        result = handler._extract_file_urls(content)

        assert result == []

    def test_extract_file_urls_image_filepart_kept(self, handler):
        """image/* mime 的 FilePart 保留（如用户用 FilePart 通道上传图片）"""
        content = [
            FilePart(url="https://cdn.example.com/a.png", name="a.png", mime_type="image/png"),
            FilePart(url="https://cdn.example.com/b.jpg", name="b.jpg", mime_type="image/jpeg"),
        ]

        result = handler._extract_file_urls(content)

        assert result == [
            "https://cdn.example.com/a.png",
            "https://cdn.example.com/b.jpg",
        ]

    def test_extract_file_urls_from_dict_pdf_filtered(self, handler):
        """dict 形式：非 image/* mime 的 file 也被过滤"""
        content = [
            {"type": "text", "text": "分析"},
            {"type": "file", "url": "https://cdn.example.com/doc.pdf", "mime_type": "application/pdf"},
        ]

        result = handler._extract_file_urls(content)

        assert result == []

    def test_extract_file_urls_from_dict_image_kept(self, handler):
        """dict 形式 image/* mime 保留"""
        content = [
            {"type": "file", "url": "https://cdn.example.com/x.webp", "mime_type": "image/webp"},
        ]

        result = handler._extract_file_urls(content)

        assert result == ["https://cdn.example.com/x.webp"]

    def test_extract_file_urls_empty(self, handler):
        """无文件时返回空列表"""
        content = [TextPart(text="纯文本")]

        result = handler._extract_file_urls(content)

        assert result == []

    def test_extract_file_urls_multiple_pdfs_filtered(self, handler):
        """多个 PDF 全部被过滤"""
        content = [
            FilePart(url="https://cdn.example.com/a.pdf", name="a.pdf", mime_type="application/pdf"),
            FilePart(url="https://cdn.example.com/b.pdf", name="b.pdf", mime_type="application/pdf"),
        ]

        result = handler._extract_file_urls(content)

        assert result == []

    def test_extract_file_urls_skip_none_url(self, handler):
        """跳过 URL 为 None 的 dict；非 image/* 的 url 也跳过"""
        content = [
            {"type": "file", "url": None, "mime_type": "image/png"},
            {"type": "file", "url": "https://cdn.example.com/ok.pdf", "mime_type": "application/pdf"},
            {"type": "file", "url": "https://cdn.example.com/ok.png", "mime_type": "image/png"},
        ]

        result = handler._extract_file_urls(content)

        assert result == ["https://cdn.example.com/ok.png"]


# ============ ChatContextMixin._build_llm_messages FilePart ============


class TestBuildLlmMessagesFilePart:
    """测试 _build_llm_messages 对 FilePart 的支持"""

    @pytest.fixture
    def chat_handler(self, mock_db):
        from services.handlers.chat_handler import ChatHandler
        return ChatHandler(db=mock_db)

    @pytest.mark.asyncio
    async def test_text_only(self, chat_handler, mock_db):
        """测试：纯文本消息，无媒体"""
        mock_db.set_table_data("messages", [])

        content = [TextPart(text="你好")]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="你好",
            )

        # 最后一条是用户消息
        user_msg = result[-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "你好"

    @pytest.mark.asyncio
    async def test_pdf_does_not_enter_image_url(self, chat_handler, mock_db):
        """PDF FilePart 不被错塞进 image_url 多模态块（mime 分流）。

        改造前：PDF 的 url 也被注入 image_url，视觉模型读不懂 URL 报错或忽略。
        改造后：仅 image/* 进 image_url；PDF 走 <attachments> XML 块的 status 引导。
        """
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="分析这份PDF"),
            FilePart(url="https://cdn.example.com/report.pdf", name="report.pdf", mime_type="application/pdf"),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="分析这份PDF",
            )

        user_msg = result[-1]
        # 无 workspace_path 时 content 是纯字符串（没有 image_url 多模态块）
        assert isinstance(user_msg["content"], str)
        assert "PDF" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_image_kept_pdf_filtered(self, chat_handler, mock_db):
        """图片 + PDF 混合：图片进 image_url 多模态，PDF 被过滤。"""
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="对比这些"),
            ImagePart(url="https://cdn.example.com/photo.png"),
            FilePart(url="https://cdn.example.com/doc.pdf", name="doc.pdf", mime_type="application/pdf"),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="对比这些",
            )

        user_msg = result[-1]
        # 有图片时 content 是 list；仅 1 个 image_url（图片），无 PDF
        assert isinstance(user_msg["content"], list)
        image_urls = [
            p["image_url"]["url"] for p in user_msg["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"
        ]
        assert image_urls == ["https://cdn.example.com/photo.png"]
        # PDF 的 url 不应在 image_url 块里
        assert "https://cdn.example.com/doc.pdf" not in image_urls


# ============ AgentLoop PDF 检测 ============


# ============ Google 适配器 _detect_mime_type ============


class TestGoogleAdapterDetectMimeType:
    """测试 Google 适配器的 MIME 类型检测"""

    @pytest.fixture
    def adapter(self):
        from services.adapters.google.chat_adapter import GoogleChatAdapter
        adapter = GoogleChatAdapter.__new__(GoogleChatAdapter)
        return adapter

    def test_detect_pdf(self, adapter):
        """测试：检测 .pdf 扩展名"""
        result = adapter._detect_mime_type("https://cdn.example.com/doc.pdf")
        assert result == "application/pdf"

    def test_detect_pdf_with_query_params(self, adapter):
        """测试：带查询参数的 PDF URL"""
        result = adapter._detect_mime_type("https://cdn.example.com/doc.pdf?token=abc123")
        assert result == "application/pdf"

    def test_detect_jpg(self, adapter):
        """测试：检测 .jpg"""
        result = adapter._detect_mime_type("https://cdn.example.com/photo.jpg")
        assert result == "image/jpeg"

    def test_detect_png(self, adapter):
        """测试：检测 .png"""
        result = adapter._detect_mime_type("https://cdn.example.com/image.png")
        assert result == "image/png"

    def test_detect_webp(self, adapter):
        """测试：检测 .webp"""
        result = adapter._detect_mime_type("https://cdn.example.com/photo.webp")
        assert result == "image/webp"

    def test_detect_gif(self, adapter):
        """测试：检测 .gif"""
        result = adapter._detect_mime_type("https://cdn.example.com/anim.gif")
        assert result == "image/gif"

    def test_detect_unknown_returns_default(self, adapter):
        """测试：未知扩展名返回默认值"""
        result = adapter._detect_mime_type("https://cdn.example.com/file.xyz")
        assert result == "image/png"

    def test_detect_case_insensitive(self, adapter):
        """测试：大小写不敏感"""
        result = adapter._detect_mime_type("https://cdn.example.com/DOC.PDF")
        assert result == "application/pdf"


# ============ BaseHandler._extract_workspace_files ============


class TestExtractWorkspaceFiles:
    """测试 BaseHandler._extract_workspace_files()"""

    @pytest.fixture
    def handler(self, mock_db):
        from services.handlers.chat_handler import ChatHandler
        return ChatHandler(db=mock_db)

    def test_extract_from_filepart_with_workspace_path(self, handler):
        """从 FilePart 提取含 workspace_path 的文件"""
        content = [
            TextPart(text="分析"),
            FilePart(
                url="https://cdn.example.com/ws/data.csv",
                name="data.csv",
                mime_type="text/csv",
                size=1024,
                workspace_path="uploads/data.csv",
            ),
        ]
        result = handler._extract_workspace_files(content)
        assert len(result) == 1
        assert result[0]["workspace_path"] == "uploads/data.csv"
        assert result[0]["name"] == "data.csv"
        assert result[0]["size"] == 1024

    def test_skip_filepart_without_workspace_path(self, handler):
        """无 workspace_path 的 FilePart 不提取（走 image_url 通道）"""
        content = [
            FilePart(url="https://cdn.example.com/report.pdf", name="report.pdf", mime_type="application/pdf"),
        ]
        result = handler._extract_workspace_files(content)
        assert result == []

    def test_extract_from_dict_with_workspace_path(self, handler):
        """从 dict 格式提取"""
        content = [
            {"type": "file", "url": "https://cdn.example.com/ws/report.xlsx",
             "name": "report.xlsx", "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "size": 5000, "workspace_path": "reports/report.xlsx"},
        ]
        result = handler._extract_workspace_files(content)
        assert len(result) == 1
        assert result[0]["workspace_path"] == "reports/report.xlsx"

    def test_skip_dict_without_workspace_path(self, handler):
        """dict 无 workspace_path 不提取"""
        content = [
            {"type": "file", "url": "https://cdn.example.com/doc.pdf", "name": "doc.pdf"},
        ]
        result = handler._extract_workspace_files(content)
        assert result == []

    def test_empty_content(self, handler):
        """空内容返回空列表"""
        assert handler._extract_workspace_files([]) == []

    def test_mixed_content(self, handler):
        """混合内容：只提取有 workspace_path 的文件"""
        content = [
            TextPart(text="分析这些文件"),
            FilePart(url="https://cdn.example.com/a.pdf", name="a.pdf", mime_type="application/pdf"),
            FilePart(url="https://cdn.example.com/ws/b.csv", name="b.csv", mime_type="text/csv", workspace_path="b.csv"),
        ]
        result = handler._extract_workspace_files(content)
        assert len(result) == 1
        assert result[0]["name"] == "b.csv"


# ============ ChatContextMixin workspace 文件注入 ============


class TestBuildLlmMessagesWorkspace:
    """测试 workspace 文件在 _build_llm_messages 中的注入逻辑"""

    @pytest.fixture
    def chat_handler(self, mock_db):
        from services.handlers.chat_handler import ChatHandler
        return ChatHandler(db=mock_db)

    @pytest.fixture(autouse=True)
    def _patch_workspace_root(self, tmp_path):
        """workspace_root 指向 tmp_path，避免 /mnt 只读文件系统"""
        from core.config import get_settings
        real_settings = get_settings()
        with patch.object(real_settings, "file_workspace_root", str(tmp_path)):
            yield

    @pytest.mark.asyncio
    async def test_workspace_file_injects_attachments_xml(self, chat_handler, mock_db):
        """workspace 文件通过 <attachments> XML 块注入独立 system message（Layer 6.7）

        messages 净化后（messages_attachments_as_system=True）：
        - attachments XML 在 user 前的独立 system message 中
        - user content 保持纯净（仅 text_content）
        - 设计文档：docs/document/TECH_messages数组结构净化.md
        """
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="分析这个CSV"),
            FilePart(
                url="https://cdn.example.com/ws/sales.csv",
                name="sales.csv",
                mime_type="text/csv",
                size=2048,
                workspace_path="上传/2026-06/sales.csv",
            ),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="分析这个CSV",
            )

        # user 纯净
        user_msg = result[-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "分析这个CSV"
        assert "<attachments" not in user_msg["content"]

        # 紧贴 user 前一条是独立 system 含 attachments XML
        att_msg = result[-2]
        assert att_msg["role"] == "system"
        assert "<attachments" in att_msg["content"]
        assert "<name>sales.csv</name>" in att_msg["content"]
        assert "<type>数据文件</type>" in att_msg["content"]
        assert "file_analyze" in att_msg["content"]  # status 引导

    @pytest.mark.asyncio
    async def test_mixed_pdf_and_workspace(self, chat_handler, mock_db):
        """PDF 走 <attachments> XML 块（不再错塞进 image_url）

        messages 净化后：XML 在 Layer 6.7 独立 system，user 纯净。
        """
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="对比这些"),
            FilePart(
                url="https://cdn.example.com/report.pdf",
                name="report.pdf",
                mime_type="application/pdf",
                workspace_path="上传/2026-06/report.pdf",
            ),
            FilePart(
                url="https://cdn.example.com/ws/data.csv",
                name="data.csv",
                mime_type="text/csv",
                workspace_path="上传/2026-06/data.csv",
            ),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="对比这些",
            )

        user_msg = result[-1]
        assert user_msg["role"] == "user"

        # 独立 system 含两个文件名
        att_msg = result[-2]
        assert att_msg["role"] == "system"
        assert "<name>report.pdf</name>" in att_msg["content"]
        assert "<name>data.csv</name>" in att_msg["content"]

        # user 纯净
        text = (
            user_msg["content"]
            if isinstance(user_msg["content"], str)
            else next(
                (p["text"] for p in user_msg["content"] if isinstance(p, dict) and p.get("type") == "text"),
                "",
            )
        )
        assert "<attachments" not in text

        # PDF url 不出现在 image_url 多模态块
        if isinstance(user_msg["content"], list):
            image_urls = [
                p["image_url"]["url"]
                for p in user_msg["content"]
                if isinstance(p, dict) and p.get("type") == "image_url"
            ]
            assert "https://cdn.example.com/report.pdf" not in image_urls
            assert "https://cdn.example.com/ws/data.csv" not in image_urls

    @pytest.mark.asyncio
    async def test_no_workspace_files_no_injection(self, chat_handler, mock_db):
        """无 workspace 文件时不注入额外 system prompt"""
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="普通问题"),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="普通问题",
            )

        system_prompts = [m["content"] for m in result if m["role"] == "system"]
        ws_prompt = [p for p in system_prompts if "file_read" in p]
        assert ws_prompt == []


# ============================================================
# StorageService 异常类型测试（上传失败/base64 无效）
# ============================================================


class TestStorageServiceErrorTypes:
    """验证 storage_service 抛出正确的异常类型"""

    @pytest.fixture
    def storage(self, mock_db):
        from services.storage_service import StorageService
        return StorageService(mock_db)

    def test_upload_image_oss_failure_raises_external_service_error(self, storage):
        """OSS 上传失败 → ExternalServiceError(503)"""
        from core.exceptions import ExternalServiceError
        fake_oss = MagicMock()
        fake_oss.upload_bytes.side_effect = Exception("OSS connection timeout")

        import asyncio
        with patch("services.storage_service.get_oss_service", return_value=fake_oss):
            with pytest.raises(ExternalServiceError):
                asyncio.get_event_loop().run_until_complete(
                    storage.upload_image(
                        user_id="user1",
                        file_data=b"\x89PNG\r\n\x1a\n" + b"x" * 100,
                        content_type="image/png",
                    )
                )

    def test_upload_file_oss_failure_raises_external_service_error(self, storage):
        """文件上传 OSS 失败 → ExternalServiceError(503)"""
        from core.exceptions import ExternalServiceError
        fake_oss = MagicMock()
        fake_oss.upload_bytes.side_effect = Exception("network error")

        import asyncio
        with patch("services.storage_service.get_oss_service", return_value=fake_oss):
            with pytest.raises(ExternalServiceError):
                asyncio.get_event_loop().run_until_complete(
                    storage.upload_file(
                        user_id="user1",
                        file_data=b"%PDF-1.4 fake",
                        content_type="application/pdf",
                    )
                )

    def test_upload_base64_invalid_raises_validation_error(self, storage):
        """无效 base64 → ValidationError(400)"""
        import asyncio
        with pytest.raises(ValidationError, match="无效的图片数据"):
            asyncio.get_event_loop().run_until_complete(
                storage.upload_base64_image(
                    user_id="user1",
                    base64_data="data:image/png;base64,!!!invalid!!!",
                )
            )
