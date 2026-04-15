"""AI 主动沟通 — pending_interaction 冻结/恢复 单元测试

覆盖：
- ChatHandler._freeze_for_ask_user: 序列化 messages → DB + WS 推送
- ChatHandler._check_pending_interaction: 查询 + 类型校验
- ChatHandler._restore_from_pending: 反序列化 + tool_result 注入 + 状态更新
- ChatToolMixin ask_user 短路
- ERP Agent ask_user 冒泡
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
# _check_pending_interaction
# ============================================================


class TestCheckPendingInteraction:
    """_check_pending_interaction 查询 + 类型校验"""

    def _make_handler(self, db_return=None):
        """构造最小化 ChatHandler mock"""
        from services.handlers.chat_handler import ChatHandler

        handler = ChatHandler.__new__(ChatHandler)
        handler.db = MagicMock()
        handler.org_id = "test-org"

        # 链式调用 mock
        chain = handler.db.table.return_value \
            .select.return_value \
            .eq.return_value \
            .eq.return_value \
            .maybe_single.return_value \
            .execute.return_value
        chain.data = db_return
        return handler

    def test_returns_none_when_no_pending(self):
        handler = self._make_handler(db_return=None)
        result = handler._check_pending_interaction("conv-1")
        assert result is None

    def test_returns_none_for_mock_data(self):
        """MagicMock 数据不应被误判为 pending"""
        handler = self._make_handler(db_return=MagicMock())
        result = handler._check_pending_interaction("conv-1")
        assert result is None

    def test_returns_data_when_valid_pending(self):
        pending = {
            "id": "int-1",
            "conversation_id": "conv-1",
            "frozen_messages": "[]",
            "question": "需要排除刷单吗？",
            "tool_call_id": "tc-1",
            "loop_snapshot": "{}",
            "status": "pending",
        }
        handler = self._make_handler(db_return=pending)
        result = handler._check_pending_interaction("conv-1")
        assert result is not None
        assert result["id"] == "int-1"

    def test_returns_none_on_db_exception(self):
        handler = self._make_handler()
        handler.db.table.side_effect = Exception("DB error")
        result = handler._check_pending_interaction("conv-1")
        assert result is None


# ============================================================
# _restore_from_pending
# ============================================================


class TestRestoreFromPending:
    """_restore_from_pending 反序列化 + tool_result 注入"""

    def _make_handler(self):
        from services.handlers.chat_handler import ChatHandler

        handler = ChatHandler.__new__(ChatHandler)
        handler.db = MagicMock()
        handler.org_id = "test-org"
        return handler

    def test_restores_messages_with_user_answer(self):
        frozen = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "查上周销售额"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "tc-1", "type": "function",
                             "function": {"name": "ask_user", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc-1", "content": "OK"},
        ]
        pending = {
            "id": "int-1",
            "frozen_messages": json.dumps(frozen),
            "tool_call_id": "tc-1",
            "loop_snapshot": json.dumps({
                "content_blocks": [],
                "tool_context_state": {"discovered_tools": ["erp_agent"]},
                "budget_snapshot": {"turns_used": 2, "tokens_used": 5000},
            }),
        }

        handler = self._make_handler()
        messages, blocks, tc_state, bs = handler._restore_from_pending(
            pending, "排除刷单",
        )

        # messages 应包含原始 4 条 + 用户回答 tool_result
        assert len(messages) == 5
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "tc-1"
        assert "排除刷单" in messages[-1]["content"]

        # 快照恢复
        assert tc_state["discovered_tools"] == ["erp_agent"]
        assert bs["turns_used"] == 2
        assert bs["tokens_used"] == 5000

    def test_marks_pending_as_resumed(self):
        pending = {
            "id": "int-1",
            "frozen_messages": "[]",
            "tool_call_id": "tc-1",
            "loop_snapshot": "{}",
        }
        handler = self._make_handler()
        handler._restore_from_pending(pending, "回答")

        # 应调用 update status=resumed
        handler.db.table.assert_called_with("pending_interaction")


# ============================================================
# ChatToolMixin ask_user 短路
# ============================================================


class TestAskUserShortCircuit:
    """_execute_single_tool 遇到 ask_user 时短路"""

    @pytest.mark.asyncio
    async def test_ask_user_sets_pending_info(self):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin.db = MagicMock()
        mixin.org_id = "test-org"
        mixin._pending_file_parts = []

        tc = {
            "name": "ask_user",
            "id": "tc-ask-1",
            "arguments": json.dumps({
                "message": "需要排除刷单吗？",
                "reason": "need_info",
            }),
        }

        result_tc, result_text, is_error = await mixin._execute_single_tool(
            tc, MagicMock(), "task-1", "conv-1", "msg-1", "user-1", 1,
        )

        assert result_text == "OK"
        assert is_error is False
        assert mixin._ask_user_pending["message"] == "需要排除刷单吗？"
        assert mixin._ask_user_pending["tool_call_id"] == "tc-ask-1"

    @pytest.mark.asyncio
    async def test_ask_user_with_bad_json(self):
        from services.handlers.chat_tool_mixin import ChatToolMixin

        mixin = ChatToolMixin.__new__(ChatToolMixin)
        mixin.db = MagicMock()
        mixin.org_id = "test-org"
        mixin._pending_file_parts = []

        tc = {"name": "ask_user", "id": "tc-2", "arguments": "bad json"}
        result_tc, result_text, is_error = await mixin._execute_single_tool(
            tc, MagicMock(), "task-1", "conv-1", "msg-1", "user-1", 1,
        )

        assert result_text == "OK"
        # 降级为默认消息
        assert mixin._ask_user_pending["message"] == "请补充更多信息"


# ============================================================
# ERP Agent ask_user 冒泡
# ============================================================


class TestERPAgentAskUserBubble:
    """ERP Agent ask_user → status="ask_user" → tool_executor 标记"""

    def test_erp_result_ask_user_status(self):
        from services.agent.erp_agent_types import ERPAgentResult

        result = ERPAgentResult(
            text="需要排除刷单吗？",
            status="ask_user",
            ask_user_question="需要排除刷单吗？",
        )
        assert result.status == "ask_user"
        assert result.ask_user_question == "需要排除刷单吗？"

    def test_erp_result_default_no_ask(self):
        from services.agent.erp_agent_types import ERPAgentResult

        result = ERPAgentResult(text="查询结果: 356笔")
        assert result.status == "success"
        assert result.ask_user_question == ""
