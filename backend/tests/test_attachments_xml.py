"""
ChatContextMixin._format_attachments 专项测试(路径协议 v2:纯状态)

新协议要点(对齐 OpenAI Assistants / Claude / Gemini):
- <status> 纯状态枚举:raw/analyzed/parquet/image/doc/text/binary
- <path> 相对 workspace 路径,LLM 字面 copy 用
- <parquet> 仅 analyzed 数据文件,LLM 直接 pd.read_parquet 读
- 不再有 <type>/<source>/<hint>(老的"教 AI 工作"字段已删,LLM 看 tools 自主决策)

依据:Anthropic prompt engineering 推荐 XML 锚点,但内容应是声明而非指令。
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

    def test_each_file_has_file_tag(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png"), _file("b.png", "image/png")],
            conversation_id=_CONV,
        )
        assert out.count("<file>") == 2
        assert out.count("</file>") == 2


class TestFileTypeRouting:
    """按扩展名分流的 status 枚举(纯状态,LLM 看 tools 自主决策)"""

    def test_image_type_status(self):
        out = ChatContextMixin._format_attachments(
            [_file("photo.png", "image/png", width=1920, height=1080)],
            conversation_id=_CONV,
        )
        assert "<status>image</status>" in out
        assert "<dimensions>1920×1080</dimensions>" in out

    def test_xlsx_unanalyzed_status(self):
        out = ChatContextMixin._format_attachments(
            [_file("sales.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV,
        )
        assert "<status>raw</status>" in out
        assert "<name>sales.xlsx</name>" in out

    def test_csv_unanalyzed_status(self):
        out = ChatContextMixin._format_attachments(
            [_file("data.csv", "text/csv")], conversation_id=_CONV,
        )
        assert "<status>raw</status>" in out

    def test_pdf_routes_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("doc.pdf", "application/pdf")], conversation_id=_CONV,
        )
        assert "<status>doc</status>" in out

    def test_word_routes_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("方案.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
            conversation_id=_CONV,
        )
        assert "<status>doc</status>" in out

    def test_pptx_routes_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")],
            conversation_id=_CONV,
        )
        assert "<status>doc</status>" in out

    def test_text_routes_to_open(self):
        out = ChatContextMixin._format_attachments(
            [_file("readme.md", "text/markdown")], conversation_id=_CONV,
        )
        assert "<status>text</status>" in out

    def test_unknown_binary_fallback(self):
        """未知扩展名落 binary 状态"""
        out = ChatContextMixin._format_attachments(
            [_file("data.bin", "application/octet-stream")], conversation_id=_CONV,
        )
        assert "<status>binary</status>" in out


class TestAnalyzedStateSwitch:
    """analyzed 状态驱动数据文件 status 切换 raw→analyzed,并暴露 parquet 相对路径"""

    def test_unanalyzed_status_is_raw(self):
        cache = get_file_cache(_CONV + "-state-a")
        cache.register("report.xlsx", workspace="/abs/report.xlsx")
        # 未调 set_analyzed,应仍是未分析

        out = ChatContextMixin._format_attachments(
            [_file("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV + "-state-a",
        )
        assert "<status>raw</status>" in out
        # 未分析时不应该有 parquet 字段
        assert "<parquet>" not in out

    def test_analyzed_status_exposes_parquet_path(self):
        cache = get_file_cache(_CONV + "-state-b")
        cache.register(
            "report.xlsx",
            workspace="/abs/report.xlsx",
            parquet="/host/staging/conv-x/report.parquet",
        )
        cache.set_analyzed("report.xlsx", True)

        out = ChatContextMixin._format_attachments(
            [_file("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")],
            conversation_id=_CONV + "-state-b",
        )
        assert "<status>analyzed</status>" in out
        assert "<parquet>staging/report.parquet</parquet>" in out


class TestPathField:
    """<path> 字段渲染 workspace 相对路径(沙盒 cwd=/workspace,LLM 字面 copy)"""

    def test_upload_path_rendered(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png", wp="上传/2026-06/a.png")],
            conversation_id=_CONV,
        )
        assert "<path>上传/2026-06/a.png</path>" in out

    def test_workspace_subdir_path_rendered(self):
        out = ChatContextMixin._format_attachments(
            [_file("a.png", "image/png", wp="销售/Q1/a.png")],
            conversation_id=_CONV,
        )
        assert "<path>销售/Q1/a.png</path>" in out


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
        # 三种 status 状态都正确出现
        assert "<status>image</status>" in out
        assert "<status>raw</status>" in out
        assert "<status>doc</status>" in out
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
