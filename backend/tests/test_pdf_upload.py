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
        with pytest.raises(ValueError, match="不支持的文件类型"):
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
        with pytest.raises(ValueError, match="文件过大"):
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

    def test_extract_file_urls_from_filepart(self, handler):
        """测试：从 FilePart 提取 URL"""
        content = [
            TextPart(text="分析这份报告"),
            FilePart(url="https://cdn.example.com/report.pdf", name="report.pdf", mime_type="application/pdf"),
        ]

        result = handler._extract_file_urls(content)

        assert result == ["https://cdn.example.com/report.pdf"]

    def test_extract_file_urls_from_dict(self, handler):
        """测试：从 dict 格式提取 URL"""
        content = [
            {"type": "text", "text": "分析"},
            {"type": "file", "url": "https://cdn.example.com/doc.pdf"},
        ]

        result = handler._extract_file_urls(content)

        assert result == ["https://cdn.example.com/doc.pdf"]

    def test_extract_file_urls_empty(self, handler):
        """测试：无文件时返回空列表"""
        content = [TextPart(text="纯文本")]

        result = handler._extract_file_urls(content)

        assert result == []

    def test_extract_file_urls_multiple(self, handler):
        """测试：多个文件"""
        content = [
            FilePart(url="https://cdn.example.com/a.pdf", name="a.pdf", mime_type="application/pdf"),
            FilePart(url="https://cdn.example.com/b.pdf", name="b.pdf", mime_type="application/pdf"),
        ]

        result = handler._extract_file_urls(content)

        assert len(result) == 2

    def test_extract_file_urls_skip_none_url(self, handler):
        """测试：跳过 URL 为 None 的 dict"""
        content = [
            {"type": "file", "url": None},
            {"type": "file", "url": "https://cdn.example.com/ok.pdf"},
        ]

        result = handler._extract_file_urls(content)

        assert result == ["https://cdn.example.com/ok.pdf"]


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
    async def test_with_file_urls(self, chat_handler, mock_db):
        """测试：带 PDF 的消息包含 image_url 格式的媒体部分"""
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
        assert isinstance(user_msg["content"], list)
        # 第一个是文本
        assert user_msg["content"][0] == {"type": "text", "text": "分析这份PDF"}
        # 第二个是 PDF 的 image_url
        assert user_msg["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.com/report.pdf"},
        }

    @pytest.mark.asyncio
    async def test_with_image_and_file(self, chat_handler, mock_db):
        """测试：图片 + PDF 混合"""
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
        assert isinstance(user_msg["content"], list)
        assert len(user_msg["content"]) == 3  # text + image + file
        # 图片
        assert user_msg["content"][1]["image_url"]["url"] == "https://cdn.example.com/photo.png"
        # PDF
        assert user_msg["content"][2]["image_url"]["url"] == "https://cdn.example.com/doc.pdf"


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

    @pytest.mark.asyncio
    async def test_workspace_file_injects_system_prompt(self, chat_handler, mock_db):
        """workspace 文件注入 system prompt，不作为 image_url"""
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="分析这个CSV"),
            FilePart(
                url="https://cdn.example.com/ws/sales.csv",
                name="sales.csv",
                mime_type="text/csv",
                size=2048,
                workspace_path="uploads/sales.csv",
            ),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="分析这个CSV",
            )

        # 用户消息应该是纯文本（workspace 文件不走 image_url）
        user_msg = result[-1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "分析这个CSV"

        # 应该有一个 system prompt 包含 file_read 提示
        system_prompts = [m["content"] for m in result if m["role"] == "system"]
        ws_prompt = [p for p in system_prompts if "file_read" in p]
        assert len(ws_prompt) == 1
        assert "uploads/sales.csv" in ws_prompt[0]
        assert "2.0KB" in ws_prompt[0]

    @pytest.mark.asyncio
    async def test_mixed_pdf_and_workspace(self, chat_handler, mock_db):
        """PDF（无 workspace_path）走 image_url，workspace 文件走提示注入"""
        mock_db.set_table_data("messages", [])

        content = [
            TextPart(text="对比这些"),
            FilePart(url="https://cdn.example.com/report.pdf", name="report.pdf", mime_type="application/pdf"),
            FilePart(url="https://cdn.example.com/ws/data.csv", name="data.csv", mime_type="text/csv", workspace_path="data.csv"),
        ]
        with patch.object(chat_handler, '_build_memory_prompt', new_callable=AsyncMock, return_value=None):
            result = await chat_handler._build_llm_messages(
                content=content,
                user_id="u1",
                conversation_id="c1",
                text_content="对比这些",
            )

        user_msg = result[-1]
        # 用户消息应该包含 image_url（仅 PDF，不含 workspace 文件）
        assert isinstance(user_msg["content"], list)
        image_urls = [
            p["image_url"]["url"]
            for p in user_msg["content"]
            if isinstance(p, dict) and p.get("type") == "image_url"
        ]
        assert "https://cdn.example.com/report.pdf" in image_urls
        assert "https://cdn.example.com/ws/data.csv" not in image_urls

        # workspace 文件通过 system prompt 注入
        system_prompts = [m["content"] for m in result if m["role"] == "system"]
        ws_prompt = [p for p in system_prompts if "file_read" in p]
        assert len(ws_prompt) == 1
        assert "data.csv" in ws_prompt[0]

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
