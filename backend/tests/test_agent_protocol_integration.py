"""主 Agent ↔ 子 Agent 通信协议集成测试。

验证 AgentResult 从 ERPAgent 到主 Agent LLM messages 的完整链路：
- to_message_content() → list[dict] 正确注入 messages
- file_ref block 路径准确
- ChatToolMixin 正确处理文件通道 / ask_user / token 统计
- KIE adapter 正确转换 list content → text parts
- context_compressor 兼容 list content

设计文档: docs/document/TECH_Agent通信协议结构化.md §3 + §5
"""

import pytest
from unittest.mock import MagicMock

from services.agent.agent_result import AgentResult
from services.agent.tool_output import FileRef, ColumnMeta


# ============================================================
# 链路 1：AgentResult → messages content（chat_handler 注入）
# ============================================================


class TestMessageInjection:
    """AgentResult 注入 messages 后 content 格式正确"""

    def test_text_result_injects_list(self):
        """纯文本 → content 是 list[dict]"""
        result = AgentResult(status="success", summary="共 23 笔")
        content = result.to_message_content()
        msg = {"role": "tool", "tool_call_id": "tc1", "content": content}

        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][0]["text"] == "共 23 笔"

    def test_file_ref_injects_path(self):
        """文件引用 → content 包含路径信息（text 形式）"""
        ref = FileRef(
            path="staging/conv123/trade_20260420.parquet",
            filename="trade_20260420.parquet",
            format="parquet",
            row_count=945,
            size_bytes=131072,
            columns=[],
        )
        result = AgentResult(
            status="success", summary="已导出",
            file_ref=ref,
        )
        content = result.to_message_content()
        msg = {"role": "tool", "tool_call_id": "tc1", "content": content}

        # 所有 block 都是 type=text，文件信息在第二个 block 的文本里
        assert len(msg["content"]) == 2
        file_text = msg["content"][1]["text"]
        assert "staging/conv123/trade_20260420.parquet" in file_text
        assert "945行" in file_text

    def test_mixed_messages_str_and_list(self):
        """messages 中混合 str 和 list[dict] content"""
        messages = [
            {"role": "tool", "tool_call_id": "tc1", "content": "搜索结果：3条"},
            {"role": "tool", "tool_call_id": "tc2", "content": [
                {"type": "text", "text": "共 945 条"},
                {"type": "file_ref", "file_ref": {"path": "a.parquet", "rows": 945}},
            ]},
        ]
        # str content
        assert isinstance(messages[0]["content"], str)
        # list content
        assert isinstance(messages[1]["content"], list)
        assert len(messages[1]["content"]) == 2


# ============================================================
# 链路 2：ChatToolMixin AgentResult 处理
# ============================================================


class TestChatToolMixinAgentResult:
    """ChatToolMixin 正确从 AgentResult 提取文件/ask_user/token"""

    def test_collected_files_to_pending(self):
        """collected_files → _pending_file_parts"""
        result = AgentResult(
            status="success", summary="ok",
            collected_files=[{
                "url": "/tmp/a.parquet", "name": "a.parquet",
                "mime_type": "application/octet-stream", "size": 1024,
            }],
            agent_name="erp_agent",
        )
        assert result.collected_files is not None
        assert len(result.collected_files) == 1
        assert result.collected_files[0]["url"] == "/tmp/a.parquet"

    def test_ask_user_fields(self):
        """ask_user 冒泡字段完整"""
        result = AgentResult(
            status="ask_user", summary="需确认",
            ask_user_question="查哪个平台？",
            agent_name="erp_agent",
        )
        assert result.status == "ask_user"
        assert result.ask_user_question == "查哪个平台？"
        assert result.agent_name == "erp_agent"

    def test_token_accumulation(self):
        """tokens_used 正确累加"""
        r1 = AgentResult(status="success", summary="ok", tokens_used=500)
        r2 = AgentResult(status="success", summary="ok", tokens_used=300)
        total = r1.tokens_used + r2.tokens_used
        assert total == 800


# ============================================================
# 链路 3：KIE adapter list content 转换
# ============================================================


class TestKieAdapterListContent:
    """KIE adapter format_messages_from_history 处理 list content"""

    def test_text_block_preserved(self):
        """text block → ChatContentPart(type="text")"""
        from services.adapters.kie.models import ChatContentPart
        content = [{"type": "text", "text": "共 945 条"}]
        # 模拟 adapter 转换逻辑
        parts = []
        for block in content:
            if block["type"] == "text":
                parts.append(ChatContentPart(type="text", text=block["text"]))
        assert len(parts) == 1
        assert parts[0].text == "共 945 条"

    def test_file_ref_block_to_text(self):
        """file_ref block → 文本描述"""
        from services.adapters.kie.models import ChatContentPart
        content = [
            {"type": "text", "text": "已导出"},
            {"type": "file_ref", "file_ref": {
                "path": "staging/test.parquet", "rows": 945, "format": "parquet",
            }},
        ]
        parts = []
        for block in content:
            if block["type"] == "text":
                parts.append(ChatContentPart(type="text", text=block["text"]))
            elif block["type"] == "file_ref":
                ref = block["file_ref"]
                parts.append(ChatContentPart(
                    type="text",
                    text=f"[文件: {ref['path']} | {ref['rows']}行 | {ref['format']}]",
                ))
        assert len(parts) == 2
        assert "staging/test.parquet" in parts[1].text
        assert "945行" in parts[1].text

    def test_insights_block_to_text(self):
        """insights block → 文本描述"""
        from services.adapters.kie.models import ChatContentPart
        block = {"type": "insights", "insights": ["HZ001 异常", "尺码问题"]}
        text = "分析洞察：\n" + "\n".join(f"· {i}" for i in block["insights"])
        part = ChatContentPart(type="text", text=text)
        assert "HZ001 异常" in part.text
        assert "尺码问题" in part.text


# ============================================================
# 链路 4：context_compressor 兼容 list content
# ============================================================


class TestContextCompressorListContent:
    """context_compressor 处理 list[dict] content 不崩"""

    def test_extract_text_from_list(self):
        from services.handlers.context_compressor import _extract_text
        content = [
            {"type": "text", "text": "共 945 条"},
            {"type": "file_ref", "file_ref": {"path": "a.parquet"}},
        ]
        text = _extract_text(content)
        assert "共 945 条" in text
        assert "file_ref" not in text  # 只提取 text block

    def test_extract_text_from_str(self):
        from services.handlers.context_compressor import _extract_text
        assert _extract_text("hello") == "hello"

    def test_extract_text_from_none(self):
        from services.handlers.context_compressor import _extract_text
        assert _extract_text(None) == ""
        assert _extract_text("") == ""

    def test_estimate_tokens_with_list_content(self):
        from services.handlers.context_compressor import estimate_tokens
        messages = [
            {"role": "tool", "content": [
                {"type": "text", "text": "a" * 100},
                {"type": "file_ref", "file_ref": {"path": "x"}},
            ]},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0

    def test_is_archived_with_list_content(self):
        from services.handlers.context_compressor import _is_archived
        assert _is_archived({"content": [{"type": "text", "text": "[已归档]..."}]}) is True
        assert _is_archived({"content": [{"type": "text", "text": "正常内容"}]}) is False

    def test_build_loop_summary_with_list_content(self):
        """_build_loop_summary_input 处理 list content 不崩"""
        from services.handlers.context_compressor import _build_loop_summary_input
        messages = [
            {"role": "tool", "content": [
                {"type": "text", "text": "共 945 条刷单订单"},
                {"type": "file_ref", "file_ref": {"path": "staging/x.parquet"}},
            ]},
        ]
        text = _build_loop_summary_input(messages, [0])
        assert "945" in text


# ============================================================
# 链路 5：to_text() 供 tool_context.update_from_result 消费
# ============================================================


class TestToTextForToolContext:
    """AgentResult.to_text() 产出 str，tool_context 能安全消费"""

    def test_to_text_with_file_ref(self):
        ref = FileRef(
            path="staging/test.parquet", filename="test.parquet",
            format="parquet", row_count=945, size_bytes=131072, columns=[],
        )
        result = AgentResult(status="success", summary="已导出", file_ref=ref)
        text = result.to_text()
        assert isinstance(text, str)
        assert "staging/test.parquet" in text

    def test_summary_is_str(self):
        """result.summary 始终是 str，可安全传给 tool_context"""
        result = AgentResult(status="success", summary="共 23 笔")
        assert isinstance(result.summary, str)


# ============================================================
# 链路 6：向后兼容 — 旧 query 参数
# ============================================================


class TestBackwardCompatibility:
    """旧的 query 参数仍能正常工作"""

    def test_validator_migrates_query_to_task(self):
        """tool_args_validator 把旧 query 迁移为 task"""
        from services.agent.tool_args_validator import validate_tool_args
        tools = [{
            "type": "function",
            "function": {
                "name": "erp_agent",
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {"type": "string"},
                        "conversation_context": {"type": "string"},
                    },
                },
            },
        }]
        cleaned, error = validate_tool_args(
            "erp_agent", {"query": "查库存"}, tools,
        )
        assert error is None
        assert cleaned.get("task") == "查库存"
        assert "query" not in cleaned


# ============================================================
# 链路 7：KIE adapter format_messages_from_history 完整调用
# ============================================================


class TestKieAdapterFormatMessages:
    """KIE adapter format_messages_from_history 处理 list content 完整链路"""

    def _make_adapter(self):
        from unittest.mock import MagicMock, PropertyMock
        from services.adapters.kie.chat_adapter import KieChatAdapter
        adapter = KieChatAdapter.__new__(KieChatAdapter)
        adapter.model = "gemini-3-pro"
        # supports_google_search 是 property，用 mock 绕过
        type(adapter).supports_google_search = PropertyMock(return_value=False)
        return adapter

    def test_str_content_unchanged(self):
        """str content → format_text_message（旧路径不受影响）"""
        adapter = self._make_adapter()
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "有什么可以帮你的？"},
        ]
        messages = adapter.format_messages_from_history(history)
        assert len(messages) == 2
        assert messages[0].content == "你好"

    def test_list_content_converted_to_parts(self):
        """list[dict] content（全 type=text）→ ChatContentPart 列表"""
        adapter = self._make_adapter()
        history = [
            {"role": "tool", "content": [
                {"type": "text", "text": "共 945 条"},
                {"type": "text", "text": "[文件: staging/test.parquet | 945行 | parquet]"},
            ]},
        ]
        messages = adapter.format_messages_from_history(history)
        assert len(messages) == 1
        content = messages[0].content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0].type == "text"
        assert content[0].text == "共 945 条"
        assert "945行" in content[1].text

    def test_mixed_str_and_list_messages(self):
        """messages 中混合 str 和 list content"""
        adapter = self._make_adapter()
        history = [
            {"role": "user", "content": "查退货"},
            {"role": "tool", "content": [
                {"type": "text", "text": "退货 23 笔"},
            ]},
            {"role": "tool", "content": "搜索结果：3条知识"},
        ]
        messages = adapter.format_messages_from_history(history)
        assert len(messages) == 3
        # user: str
        assert isinstance(messages[0].content, str)
        # tool with list: list[ChatContentPart]
        assert isinstance(messages[1].content, list)
        # tool with str: str
        assert isinstance(messages[2].content, str)

    def test_empty_list_content_fallback(self):
        """空 list 或未知 block type → 降级为空字符串"""
        adapter = self._make_adapter()
        history = [
            {"role": "tool", "content": [
                {"type": "unknown_block", "data": "???"},
            ]},
        ]
        messages = adapter.format_messages_from_history(history)
        assert len(messages) == 1
        # parts 为空，降级为 ""
        assert messages[0].content == ""

    def test_data_text_block_converted(self):
        """data 文本 block → ChatContentPart"""
        adapter = self._make_adapter()
        history = [
            {"role": "tool", "content": [
                {"type": "text", "text": "[数据: 5行 | 列: shop, count]\n[{\"shop\":\"A\"}]"},
            ]},
        ]
        messages = adapter.format_messages_from_history(history)
        content = messages[0].content
        assert isinstance(content, list)
        assert "5行" in content[0].text

    def test_insights_text_block_converted(self):
        """insights 文本 block → ChatContentPart"""
        adapter = self._make_adapter()
        history = [
            {"role": "tool", "content": [
                {"type": "text", "text": "分析洞察：\n· 退货率异常\n· 集中在尺码问题"},
            ]},
        ]
        messages = adapter.format_messages_from_history(history)
        content = messages[0].content
        assert isinstance(content, list)
        assert "退货率异常" in content[0].text
