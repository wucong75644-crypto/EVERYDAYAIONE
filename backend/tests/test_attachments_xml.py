"""
ChatContextMixin._format_attachments 专项测试

覆盖 P0 重写的 <attachments> XML 渲染：
- 多文件 / 空列表
- 类型分流（图片 / 数据文件 / PDF / Word / PPT / 文本 / 二进制）
- analyzed 状态切换（数据文件 未分析 → 已分析 status 文本变化）
- source 推断（"本轮上传" / "工作区引用"）
- XML 转义安全（特殊字符不破坏结构）

依据：Anthropic prompt engineering 文档推荐 XML 标签，本测试守护输出结构。
"""

import pytest

from services.agent.file_path_cache import get_file_cache
from services.handlers.chat_context_mixin import ChatContextMixin

# 模块级独立 conv_id，避免与其他测试 cache 串扰
_CONV = "test-attachments-xml-conv"


def _file(name, mime, *, wp=None, size=None, **extra):
    """构造一个 workspace_file dict"""
    return {
        "name": name,
        "workspace_path": wp or f"上传/2026-06/{name}",
        "size": size or 1024,
        "mime_type": mime,
        "url": f"https://cdn.example.com/{name}",
        **extra,
    }


class TestEmptyAttachments:
    """空列表场景"""

    def test_empty_list_returns_empty_string(self):
        assert ChatContextMixin._format_attachments([]) == ""

    def test_none_conversation_id_ok(self):
        """conversation_id=None 不应崩溃（cache 查询走 fallback）"""
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png")], conversation_id=None,
        )
        assert "<attachments" in out


class TestXmlStructure:
    """XML 结构守护：标签 + count + hint 字段"""

    def test_root_attachments_tag(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png")], conversation_id=_CONV,
        )
        assert "<attachments" in out
        assert "</attachments>" in out

    def test_count_attribute(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png"),
             _file("b.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
             _file("c.pdf", "application/pdf")],
            conversation_id=_CONV,
        )
        assert 'count="3"' in out

    def test_hint_attribute_present(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png")], conversation_id=_CONV,
        )
        assert "hint=" in out
        assert "行动指引" in out  # 强调 status 是行动指引

    def test_each_file_has_file_tag(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png"), _file("b.png", "image/png")],
            conversation_id=_CONV,
        )
        assert out.count("<file>") == 2
        assert out.count("</file>") == 2


class TestFileTypeRouting:
    """按扩展名分流的 type/status 行动指引"""

    def test_image_type_status(self):
        out = ChatContextMixin._format_attachments(
            [_file("photo.png", "image/png", width=1920, height=1080)],
            conversation_id=_CONV,
        )
        assert "<type>图片</type>" in out
        assert "<dimensions>1920×1080</dimensions>" in out
        assert "已自动注入视觉" in out
        assert "不要调用" in out  # 明确禁止 file_read

    def test_xlsx_unanalyzed_status(self):
        out = ChatContextMixin._format_attachments(
            [_file("sales.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV,
        )
        assert "<type>数据文件</type>" in out
        assert "未分析" in out
        assert 'file_analyze("sales.xlsx")' in out

    def test_csv_unanalyzed_status(self):
        out = ChatContextMixin._format_attachments(
            [_file("data.csv", "text/csv")], conversation_id=_CONV,
        )
        assert "<type>数据文件</type>" in out
        assert "file_analyze" in out

    def test_pdf_routes_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("doc.pdf", "application/pdf")], conversation_id=_CONV,
        )
        assert "<type>文档</type>" in out
        assert "pdfplumber" in out
        assert "get_file" in out

    def test_word_routes_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("方案.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
            conversation_id=_CONV,
        )
        assert "<type>文档</type>" in out
        assert "python-docx" in out

    def test_pptx_routes_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")],
            conversation_id=_CONV,
        )
        assert "<type>文档</type>" in out
        assert "python-pptx" in out

    def test_text_routes_to_open(self):
        out = ChatContextMixin._format_attachments(
            [_file("readme.md", "text/markdown")], conversation_id=_CONV,
        )
        assert "<type>文本</type>" in out

    def test_unknown_binary_fallback(self):
        """未知二进制扩展名走 get_file 兜底引导"""
        out = ChatContextMixin._format_attachments(
            [_file("data.bin", "application/octet-stream")], conversation_id=_CONV,
        )
        assert "<type>二进制</type>" in out
        assert "get_file" in out


class TestAnalyzedStateSwitch:
    """analyzed 状态驱动数据文件 status 切换（核心跨轮持久行为）"""

    def test_unanalyzed_status_calls_file_analyze(self):
        cache = get_file_cache(_CONV + "-state-a")
        cache.register("report.xlsx", workspace="/abs/report.xlsx")
        # 未调 set_analyzed，应仍是未分析

        out = ChatContextMixin._format_attachments(
            [_file("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV + "-state-a",
        )
        assert "未分析" in out
        assert "file_analyze" in out

    def test_analyzed_status_calls_duckdb(self):
        cache = get_file_cache(_CONV + "-state-b")
        cache.register("report.xlsx", workspace="/abs/report.xlsx")
        cache.set_analyzed("report.xlsx", True)

        out = ChatContextMixin._format_attachments(
            [_file("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV + "-state-b",
        )
        assert "已分析" in out
        assert "duckdb" in out
        assert 'get_file("report.xlsx")' in out


class TestSourceInference:
    """source 字段根据 workspace_path 前缀推断"""

    def test_upload_prefix_means_uploaded(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png", wp="上传/2026-06/a.png")],
            conversation_id=_CONV,
        )
        assert "<source>本轮上传</source>" in out

    def test_other_path_means_workspace_ref(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png", wp="销售/Q1/a.png")],
            conversation_id=_CONV,
        )
        assert "<source>工作区引用</source>" in out


class TestXmlEscapeSafety:
    """文件名/字段含特殊字符不破坏 XML 结构"""

    def test_filename_with_angle_brackets(self):
        """< 和 > 必须转义"""
        out = ChatContextMixin._format_attachments(
            [_file("<script>.png", "image/png")], conversation_id=_CONV,
        )
        # 实际文件名出现时应该被转义
        assert "&lt;script&gt;.png" in out
        # 不能让原始 < script > 出现在标签外
        assert "<script>" not in out.replace("&lt;script&gt;", "")

    def test_filename_with_ampersand(self):
        out = ChatContextMixin._format_attachments(
            [_file("a&b.png", "image/png")], conversation_id=_CONV,
        )
        assert "a&amp;b.png" in out

    def test_xml_well_formed_count_balanced(self):
        """整体标签开闭对称"""
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png"),
             _file("b.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV,
        )
        # <file> 数 == </file> 数
        assert out.count("<file>") == out.count("</file>")
        # <name> 数 == </name> 数
        assert out.count("<name>") == out.count("</name>") == 2
        # <status> 数 == </status> 数
        assert out.count("<status>") == out.count("</status>") == 2


class TestMultiFileRendering:
    """多文件场景（每个文件独立 <file> 块）"""

    def test_three_different_types(self):
        out = ChatContextMixin._format_attachments(
            [
                _file("photo.png", "image/png", width=800, height=600),
                _file("sales.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                _file("doc.pdf", "application/pdf"),
            ],
            conversation_id=_CONV,
        )
        # 三种类型都正确出现
        assert "<type>图片</type>" in out
        assert "<type>数据文件</type>" in out
        assert "<type>文档</type>" in out
        # 三个文件名都正确出现
        assert "<name>photo.png</name>" in out
        assert "<name>sales.xlsx</name>" in out
        assert "<name>doc.pdf</name>" in out
        # count 正确
        assert 'count="3"' in out


# ============ messages 净化：Layer 6.7 独立 system 注入 ============
# 设计文档：docs/document/TECH_messages数组结构净化.md


class TestAttachmentsAsSystem:
    """flag=True 时：attachments XML 走 Layer 6.7 独立 system，user content 纯净"""

    @pytest.fixture
    def chat_handler_db(self):
        from tests.conftest import MockSupabaseClient
        from services.handlers.chat_handler import ChatHandler
        db = MockSupabaseClient()
        db.set_table_data("messages", [])
        return ChatHandler(db=db)

    @pytest.mark.asyncio
    async def test_layer67_system_injected_user_pure(self, chat_handler_db):
        """flag=True：messages 中存在独立 system attachments，user content 等于 text_content"""
        from unittest.mock import AsyncMock, patch
        from schemas.message import FilePart

        content = [
            {"type": "text", "text": "分析下"},
            FilePart(
                type="file", url="https://x/a.xlsx", name="账单.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                size=2048, workspace_path="上传/2026-06/账单.xlsx",
            ),
        ]
        with patch.object(
            chat_handler_db, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler_db, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler_db._build_llm_messages(
                content=content, user_id="u1",
                conversation_id="conv-att-system", text_content="分析下",
            )

        # user 必须纯净
        last = messages[-1]
        assert last["role"] == "user"
        assert last["content"] == "分析下"
        assert "<attachments" not in last["content"]

        # 紧贴 user 前必有一条独立 system 含 attachments XML
        prev = messages[-2]
        assert prev["role"] == "system"
        assert "<attachments" in prev["content"]
        assert "账单.xlsx" in prev["content"]

    @pytest.mark.asyncio
    async def test_no_files_no_system_injected(self, chat_handler_db):
        """无附件时不注入空 attachments system"""
        from unittest.mock import AsyncMock, patch

        with patch.object(
            chat_handler_db, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler_db, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler_db._build_llm_messages(
                content=[{"type": "text", "text": "你好"}],
                user_id="u1", conversation_id="conv-att-empty", text_content="你好",
            )
        # 不应该有 attachments system
        assert not any(
            "<attachments" in str(m.get("content", "")) for m in messages
        )

    @pytest.mark.asyncio
    async def test_multimodal_user_text_pure(self, chat_handler_db):
        """图片 + 附件：user 多模态 text part 仍然纯净（不含 XML）"""
        from unittest.mock import AsyncMock, patch
        from schemas.message import FilePart, ImagePart

        content = [
            {"type": "text", "text": "对比这两个"},
            ImagePart(
                type="image", url="https://x/a.png",
                width=100, height=100, name="截图.png", mime_type="image/png",
                workspace_path="上传/2026-06/截图.png",
            ),
            FilePart(
                type="file", url="https://x/b.xlsx", name="账单.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                workspace_path="上传/2026-06/账单.xlsx",
            ),
        ]
        with patch.object(
            chat_handler_db, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            chat_handler_db, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await chat_handler_db._build_llm_messages(
                content=content, user_id="u1",
                conversation_id="conv-att-mm", text_content="对比这两个",
            )

        last = messages[-1]
        assert last["role"] == "user"
        # 多模态 list 形式
        assert isinstance(last["content"], list)
        text_parts = [p for p in last["content"] if p.get("type") == "text"]
        assert len(text_parts) == 1
        assert text_parts[0]["text"] == "对比这两个"
        assert "<attachments" not in text_parts[0]["text"]


class TestAttachmentsLegacyPath:
    """flag=False 回滚路径：attachments XML 走 Layer 7 user 末尾（向后兼容）"""

    @pytest.mark.asyncio
    async def test_legacy_appends_to_user_text(self, monkeypatch):
        """flag=False：旧行为 — XML 拼到 user content 末尾，无独立 system"""
        from unittest.mock import AsyncMock, patch
        from tests.conftest import MockSupabaseClient
        from services.handlers.chat_handler import ChatHandler
        from schemas.message import FilePart
        from core import config as _cfg_mod

        # 关 flag
        _cfg_mod.get_settings.cache_clear()
        monkeypatch.setattr(
            _cfg_mod.get_settings(), "messages_attachments_as_system", False,
        )

        db = MockSupabaseClient()
        db.set_table_data("messages", [])
        handler = ChatHandler(db=db)

        content = [
            {"type": "text", "text": "分析下"},
            FilePart(
                type="file", url="https://x/a.xlsx", name="账单.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                workspace_path="上传/2026-06/账单.xlsx",
            ),
        ]
        with patch.object(
            handler, "_build_memory_prompt",
            new_callable=AsyncMock, return_value=None,
        ), patch.object(
            handler, "_get_context_summary",
            new_callable=AsyncMock, return_value=None,
        ):
            messages = await handler._build_llm_messages(
                content=content, user_id="u1",
                conversation_id="conv-att-legacy", text_content="分析下",
            )

        last = messages[-1]
        assert last["role"] == "user"
        # 旧路径：user content 含 XML
        assert "<attachments" in last["content"]
        assert "账单.xlsx" in last["content"]
        # 不存在独立 system 形式
        assert not any(
            m.get("role") == "system" and "<attachments" in str(m.get("content", ""))
            for m in messages[:-1]
        )
