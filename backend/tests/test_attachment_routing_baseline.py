"""附件路由 Baseline 测试套件

目的：在修改提示词之前建立 baseline，断言「修复后期望行为」。
- 通过的测试 = 当前已经正常工作的功能（需要继续保留，防回归）
- 失败的测试 = 当前 bug 复现的精确证据（修复目标）

按行业标准（Anthropic Tool Use Best Practices + OpenAI Prompt Engineering）：
1. tool description 必须包含 What / When to use / When NOT to use / Returns
2. attachments XML 每个 status 必须有配对的 inline 行动指引
3. system prompt 不重复 description 已声明的工具规则（DRY）
4. 同一指令在 messages 数组里只出现一次（避免双重注入）
"""

import pytest

from services.agent.file_path_cache import get_file_cache
from services.handlers.chat_context_mixin import ChatContextMixin

_CONV = "test-attachment-routing-baseline"


def _file(name, mime, *, wp=None, size=None, **extra):
    return {
        "name": name,
        "workspace_path": wp or f"上传/2026-06/{name}",
        "size": size or 1024,
        "mime_type": mime,
        "url": f"https://cdn.example.com/{name}",
        **extra,
    }


# ============================================================
# 1. attachments XML status × action 配对断言
# ============================================================


class TestImageAttachmentBaseline:
    """图片附件：已视觉注入，XML 应明示无需工具"""

    def test_image_xml_has_action_visual_injected(self):
        """图片块应包含 <action> 指明已视觉注入"""
        out = ChatContextMixin._format_attachments(
            [_file("photo.png", "image/png", width=1920, height=1080)],
            conversation_id=_CONV,
        )
        assert "<status>image</status>" in out
        assert "<action>" in out, "图片块缺 <action> 字段（行动指引）"
        assert "视觉" in out or "看图" in out, "图片 action 应说明已视觉注入"
        assert "无需" in out or "不需要" in out, "图片 action 应说明无需工具"

    def test_image_xml_excludes_file_analyze(self):
        """图片块的 action 不应引向 file_analyze"""
        out = ChatContextMixin._format_attachments(
            [_file("chart.png", "image/png")], conversation_id=_CONV,
        )
        # 提取 image 块内容
        img_block_start = out.find("<status>image</status>")
        img_block_end = out.find("</file>", img_block_start)
        img_block = out[img_block_start:img_block_end]
        assert "file_analyze" not in img_block, \
            "图片块不应提及 file_analyze（它仅支持 .xlsx/.xls/.csv/.tsv）"


class TestRawDataAttachmentBaseline:
    """未分析的 Excel/CSV：XML 应指向 file_analyze"""

    def test_xlsx_raw_action_points_to_file_analyze(self):
        out = ChatContextMixin._format_attachments(
            [_file(
                "sales.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )],
            conversation_id=_CONV,
        )
        assert "<status>raw</status>" in out
        assert "<action>" in out, "raw 块缺 <action>"
        assert "file_analyze" in out, "raw 块 action 应指向 file_analyze"

    def test_csv_raw_action_points_to_file_analyze(self):
        out = ChatContextMixin._format_attachments(
            [_file("data.csv", "text/csv")], conversation_id=_CONV,
        )
        assert "<status>raw</status>" in out
        # 当前实现：raw 块没有 action 字段（修复目标）
        assert "<action>" in out


class TestAnalyzedDataAttachmentBaseline:
    """已分析的 xlsx：XML 应指向 pd.read_parquet + 给出 parquet 路径"""

    def test_analyzed_action_points_to_pd_read_parquet(self):
        cache = get_file_cache(_CONV + "-analyzed")
        cache.register(
            "report.xlsx",
            workspace="/abs/report.xlsx",
            parquet="/host/staging/x/report.parquet",
        )
        cache.set_analyzed("report.xlsx", True)

        out = ChatContextMixin._format_attachments(
            [_file(
                "report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )],
            conversation_id=_CONV + "-analyzed",
        )
        assert "<status>analyzed</status>" in out
        assert "<parquet>staging/report.parquet</parquet>" in out
        assert "<action>" in out
        assert "pd.read_parquet" in out or "read_parquet" in out


class TestDocAttachmentBaseline:
    """PDF/Word/PPT：XML 应指向 code_execute + 对应库"""

    def test_pdf_action_points_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file("doc.pdf", "application/pdf")], conversation_id=_CONV,
        )
        assert "<status>doc</status>" in out
        assert "<action>" in out
        assert "code_execute" in out, "PDF action 应指向 code_execute"

    def test_docx_action_points_to_code_execute(self):
        out = ChatContextMixin._format_attachments(
            [_file(
                "方案.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )],
            conversation_id=_CONV,
        )
        assert "<status>doc</status>" in out
        assert "<action>" in out
        assert "code_execute" in out


class TestTextAttachmentBaseline:
    """文本文件：XML 应指向 code_execute + open()"""

    def test_text_action_points_to_open(self):
        out = ChatContextMixin._format_attachments(
            [_file("readme.md", "text/markdown")], conversation_id=_CONV,
        )
        assert "<status>text</status>" in out
        assert "<action>" in out
        assert "code_execute" in out or "open(" in out


class TestBinaryAttachmentBaseline:
    """未知类型：XML 应建议询问用户"""

    def test_binary_action_asks_user(self):
        out = ChatContextMixin._format_attachments(
            [_file("x.bin", "application/octet-stream")], conversation_id=_CONV,
        )
        assert "<status>binary</status>" in out
        assert "<action>" in out
        assert "询问" in out or "确认" in out


# ============================================================
# 2. 状态枚举完备性（单一事实来源）
# ============================================================


class TestStatusActionEnumComplete:
    """所有 status 必须有对应 action（单一事实来源 _STATUS_ACTIONS dict）"""

    def test_status_actions_dict_exists(self):
        from services.handlers.chat_context import attachments
        assert hasattr(attachments, "_STATUS_ACTIONS"), \
            "应存在 _STATUS_ACTIONS dict 作为单一事实来源"

    def test_status_actions_covers_all_statuses(self):
        from services.handlers.chat_context import attachments
        actions = getattr(attachments, "_STATUS_ACTIONS", {})
        expected = {"image", "raw", "analyzed", "parquet",
                    "doc", "text", "binary"}
        assert set(actions.keys()) == expected, \
            f"状态枚举不完备：缺 {expected - set(actions.keys())}"


# ============================================================
# 3. 双重注入 / DRY 检查
# ============================================================


class TestNoDoubleInjection:
    """单一事实来源：同一文件 messages 数组里不应出现两次工具引导"""

    @pytest.fixture
    def chat_handler_db(self):
        from tests.conftest import MockSupabaseClient
        from services.handlers.chat_handler import ChatHandler
        db = MockSupabaseClient()
        db.set_table_data("messages", [])
        return ChatHandler(db=db)

    @pytest.mark.asyncio
    async def test_xlsx_file_analyze_hint_appears_only_once(
        self, chat_handler_db,
    ):
        """上传一个 xlsx，messages 里 'file_analyze' 引导只应在 attachments XML 出现一次"""
        from unittest.mock import AsyncMock, patch
        from schemas.message import FilePart

        content = [
            {"type": "text", "text": "帮我分析"},
            FilePart(
                type="file", url="https://x/a.xlsx", name="账单.xlsx",
                mime_type=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
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
                conversation_id="conv-dry-xlsx", text_content="帮我分析",
            )

        # 统计所有 system message 里 "file_analyze" 出现次数
        # 排除 chat_tools.py 主提示词（它在另一个 system message，是工具列表声明，OK）
        system_msgs = [
            m for m in messages if m.get("role") == "system"
        ]
        # 找包含具体文件名的 system message（即 attachments XML 或
        # build_workspace_prompt 输出）
        file_related_systems = [
            str(m.get("content", "")) for m in system_msgs
            if "账单.xlsx" in str(m.get("content", ""))
        ]
        # 统计含文件名的 system messages 中 file_analyze 引导出现的总次数
        total = sum(s.count("file_analyze") for s in file_related_systems)
        # 期望：只在 attachments XML 的 <action> 里出现一次
        assert total <= 1, (
            f"file_analyze 引导在文件相关的 system messages 里出现 {total} 次，"
            f"期望 ≤ 1（DRY 原则）。当前疑似 build_workspace_prompt 重复注入。"
        )

    @pytest.mark.asyncio
    async def test_xlsx_file_in_at_most_2_system_messages(
        self, chat_handler_db,
    ):
        """文件名最多出现在 2 条 system message（build_workspace_prompt + attachments XML）。

        F+C' 设计：build_workspace_prompt 是"附件存在锚点"，attachments XML 是"状态+action"，
        两者维度不同，不是 DRY 违规。但仍应控制在 2 条以内，防止其他地方意外重复注入。
        """
        from unittest.mock import AsyncMock, patch
        from schemas.message import FilePart

        content = [
            {"type": "text", "text": "ok"},
            FilePart(
                type="file", url="https://x/a.xlsx",
                name="独特文件名_baseline.xlsx",
                mime_type=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
                size=2048,
                workspace_path="上传/2026-06/独特文件名_baseline.xlsx",
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
                conversation_id="conv-dry-unique", text_content="ok",
            )

        file_mentions = sum(
            1 for m in messages
            if m.get("role") == "system"
            and "独特文件名_baseline.xlsx" in str(m.get("content", ""))
        )
        assert file_mentions <= 2, (
            f"文件名在 {file_mentions} 条 system messages 里出现，期望 ≤ 2 "
            f"(workspace_prompt 锚点 + attachments XML 状态)"
        )


class TestBuildWorkspacePromptStateAware:
    """build_workspace_prompt 状态感知版（F+C' 修复）：声明附件存在 + 状态分类。

    关键约束：不应硬编码工具名（file_analyze/code_execute 等），
    工具调用方式由 attachments XML 的 <action> 字段单一声明（避免冲突）。
    """

    def test_function_exists(self):
        from services.handlers.chat_context.attachments import (
            build_workspace_prompt,
        )
        assert callable(build_workspace_prompt)

    def test_no_hardcoded_tool_names(self):
        """状态感知版禁止硬编码工具名（防止回退到 DRY 违规的旧版）"""
        from services.handlers.chat_context.attachments import (
            build_workspace_prompt,
        )
        from services.agent.file_path_cache import get_file_cache
        conv = "test-no-hardcode"
        cache = get_file_cache(conv)
        cache.register(
            "report.xlsx", workspace="/abs/report.xlsx",
            parquet="/staging/r.parquet",
        )
        cache.set_analyzed("report.xlsx", True)

        out = build_workspace_prompt(
            [
                {"name": "report.xlsx",
                 "workspace_path": "上传/report.xlsx", "size": 89000},
                {"name": "sales.xlsx",
                 "workspace_path": "上传/sales.xlsx", "size": 156000},
                {"name": "chart.png",
                 "workspace_path": "上传/chart.png", "size": 234000},
                {"name": "合同.pdf",
                 "workspace_path": "上传/合同.pdf", "size": 1200000},
            ],
            conv,
        )
        # 禁止出现工具名硬编码
        for tool_name in ("file_analyze", "file_search", "code_execute",
                          "pd.read_parquet", "duckdb"):
            assert tool_name not in out, (
                f"build_workspace_prompt 不应硬编码工具名 '{tool_name}'，"
                f"工具调用方式应由 attachments XML 的 <action> 单一声明"
            )

    def test_state_aware_for_analyzed_xlsx(self):
        """analyzed xlsx 应标记「已分析」"""
        from services.handlers.chat_context.attachments import (
            build_workspace_prompt,
        )
        from services.agent.file_path_cache import get_file_cache
        conv = "test-state-aware-analyzed"
        cache = get_file_cache(conv)
        cache.register(
            "report.xlsx", workspace="/abs/report.xlsx",
            parquet="/staging/r.parquet",
        )
        cache.set_analyzed("report.xlsx", True)

        out = build_workspace_prompt(
            [{"name": "report.xlsx",
              "workspace_path": "上传/report.xlsx", "size": 89000}],
            conv,
        )
        assert "已分析" in out
        assert "待治理" not in out

    def test_state_aware_for_raw_xlsx(self):
        """raw xlsx 应标记「待治理」"""
        from services.handlers.chat_context.attachments import (
            build_workspace_prompt,
        )
        out = build_workspace_prompt(
            [{"name": "sales.xlsx",
              "workspace_path": "上传/sales.xlsx", "size": 156000}],
            "test-raw-state",
        )
        assert "待治理" in out
        assert "已分析" not in out


# ============================================================
# 4. 工具 description 行业标准合约（Anthropic）
# ============================================================


class TestFileAnalyzeDescriptionContract:
    """file_analyze description 应符合 Anthropic 四段标准结构"""

    def _get_desc(self):
        from config.file_tools import build_file_tools
        return next(
            t["function"]["description"] for t in build_file_tools()
            if t["function"]["name"] == "file_analyze"
        )

    def test_has_when_to_use_section(self):
        assert "When to use" in self._get_desc(), \
            "description 应含 When to use 段"

    def test_has_when_not_to_use_section(self):
        assert "When NOT to use" in self._get_desc(), \
            "description 应含 When NOT to use 段（Anthropic 标准）"

    def test_explicitly_excludes_image_extensions(self):
        desc = self._get_desc()
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            assert ext in desc, \
                f"description 应显式排除图片扩展名 {ext}"

    def test_mentions_image_visual_injection(self):
        desc = self._get_desc()
        assert "视觉" in desc or "图片" in desc, \
            "description 应说明图片走视觉通道"

    def test_within_anthropic_1024_char_limit(self):
        desc = self._get_desc()
        assert len(desc) <= 1024, \
            f"description {len(desc)} 字符超过 Anthropic 1024 限制"


@pytest.mark.xfail(
    reason="P3 范围：file_search description 加图片多模态说明（B 方案未含）",
    strict=False,
)
class TestFileSearchDescriptionContract:
    """file_search description 应说明图片自动多模态行为"""

    def _get_desc(self):
        from config.file_tools import build_file_tools
        return next(
            t["function"]["description"] for t in build_file_tools()
            if t["function"]["name"] == "file_search"
        )

    def test_mentions_image_multimodal(self):
        desc = self._get_desc()
        assert "图片" in desc, "description 应提及图片"
        assert "多模态" in desc or "视觉" in desc, \
            "description 应说明命中图片返回多模态/视觉通道"


@pytest.mark.xfail(
    reason="额外 finding：erp_execute/local_data/code_execute/erp_agent description 超 1024 字符，留待后续优化",
    strict=False,
)
class TestAllToolDescriptionsContract:
    """所有工具 description 都应符合 Anthropic 限制"""

    def test_all_under_1024_chars(self):
        from config.chat_tools import get_chat_tools
        violations = []
        for t in get_chat_tools(org_id="test-org"):
            desc = t["function"]["description"]
            if len(desc) > 1024:
                violations.append(
                    f"{t['function']['name']}: {len(desc)} chars"
                )
        assert not violations, \
            f"以下工具 description 超过 1024 字符:\n" + "\n".join(violations)


# ============================================================
# 5. system prompt 模态边界
# ============================================================


@pytest.mark.xfail(
    reason="P4 范围：system prompt 加独立「模态感知」段（B 方案未含）",
    strict=False,
)
class TestSystemPromptModalityBoundary:
    """主 Agent system prompt 应有独立的「模态感知」段，
    而不是把图片处理引导藏在 file_search 工具说明里"""

    def _prompt(self):
        from config.chat_tools import TOOL_SYSTEM_PROMPT
        return TOOL_SYSTEM_PROMPT

    def test_has_modality_section_or_image_guidance_centralized(self):
        """要么有独立的「模态感知」段，要么图片处理引导集中在数据来源判断段"""
        prompt = self._prompt()
        # 期望（修复后）：有独立模态段，或在数据来源判断段集中
        has_modality_section = "模态感知" in prompt or "模态边界" in prompt
        has_data_source_image = (
            "## 数据来源判断" in prompt
            and "视觉通道" in prompt.split("## 数据来源判断")[1].split("##")[0]
        )
        assert has_modality_section or has_data_source_image, (
            "图片处理引导应在「模态感知」独立段或「数据来源判断」段集中，"
            "不应藏在 file_search 工具说明里"
        )

    def test_no_image_guidance_buried_in_file_search_only(self):
        """图片视觉注入引导不应只藏在 file_search 工具说明里"""
        prompt = self._prompt()
        if "### file_search" in prompt:
            file_search_section = prompt.split("### file_search")[1].split(
                "### "
            )[0]
            # 如果只在 file_search 段提了视觉注入，没有别处提，就是反模式
            in_file_search = "视觉" in file_search_section
            other_sections = prompt.replace(file_search_section, "")
            in_other = "视觉" in other_sections or "模态" in other_sections
            if in_file_search:
                assert in_other, (
                    "图片视觉注入引导不应只藏在 file_search 段，"
                    "应在更通用的位置（数据来源判断/模态感知）声明"
                )


# ============================================================
# 6. 完整 messages 数组结构（end-to-end 验证）
# ============================================================


class TestE2EImageOnlyScenario:
    """E2E：用户只上传图片说'帮我提取内容'，messages 数组应让 LLM 直接看图"""

    @pytest.fixture
    def chat_handler_db(self):
        from tests.conftest import MockSupabaseClient
        from services.handlers.chat_handler import ChatHandler
        db = MockSupabaseClient()
        db.set_table_data("messages", [])
        return ChatHandler(db=db)

    @pytest.mark.asyncio
    async def test_image_user_content_has_image_url_block(
        self, chat_handler_db,
    ):
        """图片应通过 image_url block 注入到 user content"""
        from unittest.mock import AsyncMock, patch
        from schemas.message import ImagePart

        content = [
            {"type": "text", "text": "帮我提取一下这个图片的内容"},
            ImagePart(
                type="image", url="https://x/chart.png",
                width=1920, height=1080,
                name="chart.png", mime_type="image/png",
                workspace_path="上传/2026-06/chart.png",
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
                conversation_id="conv-img-only",
                text_content="帮我提取一下这个图片的内容",
            )

        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        assert isinstance(user_msg["content"], list), \
            "图片场景 user content 应为 list（多模态）"
        image_parts = [
            p for p in user_msg["content"] if p.get("type") == "image_url"
        ]
        assert len(image_parts) == 1, \
            "应有且仅有 1 个 image_url block"

    @pytest.mark.asyncio
    async def test_image_attachments_xml_action_field_present(
        self, chat_handler_db,
    ):
        """图片场景，attachments XML 的 image 块应有 <action>"""
        from unittest.mock import AsyncMock, patch
        from schemas.message import ImagePart

        content = [
            {"type": "text", "text": "提取内容"},
            ImagePart(
                type="image", url="https://x/chart.png",
                width=1920, height=1080,
                name="chart.png", mime_type="image/png",
                workspace_path="上传/2026-06/chart.png",
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
                conversation_id="conv-img-action",
                text_content="提取内容",
            )

        # 找 attachments XML 那条 system message
        att_systems = [
            str(m.get("content", "")) for m in messages
            if m.get("role") == "system"
            and "<attachments" in str(m.get("content", ""))
        ]
        assert len(att_systems) == 1, \
            f"应有 1 条 attachments XML system message，实际 {len(att_systems)}"
        att_xml = att_systems[0]
        assert "<status>image</status>" in att_xml
        assert "<action>" in att_xml, "图片场景 attachments XML 缺 <action>"
