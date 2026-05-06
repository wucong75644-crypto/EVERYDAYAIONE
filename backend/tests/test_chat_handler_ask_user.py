"""ChatHandler ask_user 冻结/恢复单元测试

覆盖：_freeze_for_ask_user, _check_pending_interaction, _restore_from_pending
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_handler():
    """构造最小 ChatHandler 实例（mock 所有外部依赖）"""
    from services.handlers.chat_handler import ChatHandler
    handler = MagicMock(spec=ChatHandler)
    handler.db = MagicMock()
    handler.org_id = "org_test"
    # 绑定真实方法
    handler._freeze_for_ask_user = ChatHandler._freeze_for_ask_user.__get__(handler)
    handler._check_pending_interaction = ChatHandler._check_pending_interaction.__get__(handler)
    handler._restore_from_pending = ChatHandler._restore_from_pending.__get__(handler)
    return handler


# ============================================================
# _check_pending_interaction
# ============================================================


class TestCheckPendingInteraction:

    def test_returns_data_when_pending_exists(self):
        handler = _make_handler()
        pending_data = {
            "id": "int_1",
            "frozen_messages": "[]",
            "tool_call_id": "tc_1",
            "loop_snapshot": "{}",
        }
        handler.db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value = \
            MagicMock(data=pending_data)

        result = handler._check_pending_interaction("conv_1")
        assert result == pending_data

    def test_returns_none_when_no_pending(self):
        handler = _make_handler()
        handler.db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value = \
            MagicMock(data=None)

        result = handler._check_pending_interaction("conv_1")
        assert result is None

    def test_returns_none_on_db_error(self):
        handler = _make_handler()
        handler.db.table.side_effect = Exception("DB error")

        result = handler._check_pending_interaction("conv_1")
        assert result is None

    def test_rejects_mock_data(self):
        """MagicMock 等非法数据不被当作有效 pending"""
        handler = _make_handler()
        handler.db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.maybe_single.return_value.execute.return_value = \
            MagicMock(data=MagicMock())  # 不是 dict

        result = handler._check_pending_interaction("conv_1")
        assert result is None


# ============================================================
# _restore_from_pending
# ============================================================


class TestRestoreFromPending:

    def _make_pending(self, **overrides):
        base = {
            "id": "int_1",
            "frozen_messages": json.dumps([
                {"role": "user", "content": "查销量"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "tc_1", "type": "function",
                     "function": {"name": "erp_agent", "arguments": "{}"}}
                ]},
                {"role": "tool", "tool_call_id": "tc_1", "content": "结果..."},
            ]),
            "tool_call_id": "tc_ask",
            "loop_snapshot": json.dumps({
                "content_blocks": [{"type": "text"}],
                "tool_context_state": {"discovered_tools": ["web_search"]},
                "budget_snapshot": {"turns_used": 3},
            }),
        }
        base.update(overrides)
        return base

    def test_restores_messages_with_user_answer(self):
        handler = _make_handler()
        pending = self._make_pending()

        messages, blocks, tc_state, budget, _fr, _dp = handler._restore_from_pending(
            pending, "查最近7天的"
        )

        # 原始 3 条 + 用户回答 1 条
        assert len(messages) == 4
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "tc_ask"
        assert "用户回答: 查最近7天的" in messages[-1]["content"]

    def test_restores_content_blocks(self):
        handler = _make_handler()
        pending = self._make_pending()

        _, blocks, _, _, _, _ = handler._restore_from_pending(pending, "ok")
        assert blocks == [{"type": "text"}]

    def test_restores_tool_context_state(self):
        handler = _make_handler()
        pending = self._make_pending()

        _, _, tc_state, _, _, _ = handler._restore_from_pending(pending, "ok")
        assert "web_search" in tc_state["discovered_tools"]

    def test_restores_budget_snapshot(self):
        handler = _make_handler()
        pending = self._make_pending()

        _, _, _, budget, _, _ = handler._restore_from_pending(pending, "ok")
        assert budget["turns_used"] == 3

    def test_marks_pending_as_resumed_atomically(self):
        handler = _make_handler()
        pending = self._make_pending()

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[{"id": "int_1"}])
        handler.db.table.return_value.update.return_value = update_chain

        handler._restore_from_pending(pending, "ok")

        # 验证原子更新：同时匹配 id 和 status=pending
        eq_calls = update_chain.eq.call_args_list
        eq_args = [(c.args[0], c.args[1]) for c in eq_calls]
        assert ("id", "int_1") in eq_args
        assert ("status", "pending") in eq_args

    def test_empty_snapshot_fallback(self):
        """loop_snapshot 为空时使用默认值"""
        handler = _make_handler()
        pending = self._make_pending(loop_snapshot="{}")

        _, blocks, tc_state, budget, file_reg, dag_prog = handler._restore_from_pending(pending, "ok")
        assert blocks == []
        assert tc_state == {}
        assert len(file_reg.list_all()) == 0  # 空 snapshot → 空 Registry
        assert dag_prog is None
        assert budget == {}


# ============================================================
# _freeze_for_ask_user
# ============================================================


class TestFreezeForAskUser:

    @pytest.mark.asyncio
    async def test_inserts_pending_interaction(self):
        handler = _make_handler()
        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock(data=[])
        handler.db.table.return_value.insert.return_value = insert_chain

        with patch("services.handlers.chat_handler.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()

            await handler._freeze_for_ask_user(
                ask_info={"message": "请选择店铺", "tool_call_id": "tc_1"},
                messages=[{"role": "user", "content": "查销量"}],
                task_id="task_1",
                conversation_id="conv_1",
                message_id="msg_1",
                user_id="user_1",
                model_id="gemini-3-pro",
                content_blocks=[],
                tool_context_state={},
                budget_snapshot={},
            )

        # 验证 DB insert
        insert_call = handler.db.table.return_value.insert.call_args
        data = insert_call.args[0]
        assert data["conversation_id"] == "conv_1"
        assert data["user_id"] == "user_1"
        assert data["question"] == "请选择店铺"
        assert data["status"] == "pending"
        assert data["tool_call_id"] == "tc_1"

    @pytest.mark.asyncio
    async def test_sends_ws_ask_user_request(self):
        handler = _make_handler()
        handler.db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("services.handlers.chat_handler.ws_manager") as mock_ws:
            mock_ws.send_to_task_or_user = AsyncMock()

            await handler._freeze_for_ask_user(
                ask_info={"message": "选时间", "tool_call_id": "tc_1"},
                messages=[],
                task_id="task_1",
                conversation_id="conv_1",
                message_id="msg_1",
                user_id="user_1",
                model_id="gemini-3-pro",
                content_blocks=[],
                tool_context_state={},
                budget_snapshot={},
            )

        mock_ws.send_to_task_or_user.assert_called_once()
        ws_msg = mock_ws.send_to_task_or_user.call_args.args[2]
        assert ws_msg["type"] == "ask_user_request"
        assert ws_msg["payload"]["question"] == "选时间"

    @pytest.mark.asyncio
    async def test_db_error_does_not_crash(self):
        """DB 插入失败时不抛异常，静默跳过"""
        handler = _make_handler()
        handler.db.table.return_value.insert.return_value.execute.side_effect = \
            Exception("DB down")

        # 不应抛异常
        await handler._freeze_for_ask_user(
            ask_info={"message": "q", "tool_call_id": "tc_1"},
            messages=[], task_id="t", conversation_id="c",
            message_id="m", user_id="u", model_id="m",
            content_blocks=[], tool_context_state={}, budget_snapshot={},
        )
