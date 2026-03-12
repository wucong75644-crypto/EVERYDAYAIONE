"""
意图学习服务单元测试

覆盖：record_ask_user_context、check_and_record_intent、_find_recent_pending、_write_intent_pattern
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.intent_learning import (
    _PENDING_TTL_SECONDS,
    _find_recent_pending,
    _write_intent_pattern,
    check_and_record_intent,
    record_ask_user_context,
)


# ============================================================
# TestRecordAskUserContext
# ============================================================


class TestRecordAskUserContext:

    @pytest.mark.asyncio
    @patch("services.intent_learning.record_metric")
    async def test_records_pending_metric(self, mock_record):
        """ask_user 触发时写入 intent_pending 信号"""
        mock_record.return_value = None
        await record_ask_user_context(
            conversation_id="conv-1",
            user_id="user-1",
            original_message="修正图片",
            ask_options="1.编辑 2.生成",
        )
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["task_type"] == "intent_pending"
        assert call_kwargs["status"] == "pending"
        assert call_kwargs["user_id"] == "user-1"
        assert call_kwargs["params"]["conversation_id"] == "conv-1"
        assert call_kwargs["params"]["original_message"] == "修正图片"

    @pytest.mark.asyncio
    @patch("services.intent_learning.record_metric")
    async def test_truncates_long_message(self, mock_record):
        """超长消息被截断到 500 字符"""
        mock_record.return_value = None
        long_msg = "x" * 1000
        await record_ask_user_context(
            conversation_id="conv-1",
            user_id="user-1",
            original_message=long_msg,
            ask_options="options",
        )
        params = mock_record.call_args[1]["params"]
        assert len(params["original_message"]) == 500


# ============================================================
# TestFindRecentPending
# ============================================================


class TestFindRecentPending:

    @pytest.mark.asyncio
    @patch("services.intent_learning.get_pg_connection")
    async def test_no_connection_returns_none(self, mock_conn):
        """数据库不可用→返回 None"""
        mock_conn.return_value = None
        result = await _find_recent_pending("conv-1")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.intent_learning.get_pg_connection")
    async def test_no_rows_returns_none(self, mock_get_conn):
        """无匹配记录→返回 None"""
        mock_cur = AsyncMock()
        mock_cur.fetchone.return_value = None

        # cursor() 是同步调用，返回异步上下文管理器
        cursor_ctx = MagicMock()
        cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
        cursor_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock(return_value=cursor_ctx)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        mock_get_conn.return_value = mock_ctx

        result = await _find_recent_pending("conv-1")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.intent_learning.get_pg_connection")
    async def test_found_row_returns_params(self, mock_get_conn):
        """匹配到记录→返回 params dict"""
        params_data = {
            "conversation_id": "conv-1",
            "original_message": "修正图片",
            "ask_options": "1.编辑 2.生成",
        }
        mock_cur = AsyncMock()
        mock_cur.fetchone.return_value = (params_data,)

        cursor_ctx = MagicMock()
        cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
        cursor_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock(return_value=cursor_ctx)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        mock_get_conn.return_value = mock_ctx

        result = await _find_recent_pending("conv-1")
        assert result == params_data

    @pytest.mark.asyncio
    @patch("services.intent_learning.get_pg_connection")
    async def test_json_string_params_parsed(self, mock_get_conn):
        """params 为 JSON 字符串时正确解析"""
        params_json = json.dumps({
            "conversation_id": "conv-1",
            "original_message": "test",
        })
        mock_cur = AsyncMock()
        mock_cur.fetchone.return_value = (params_json,)

        cursor_ctx = MagicMock()
        cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
        cursor_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock(return_value=cursor_ctx)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        mock_get_conn.return_value = mock_ctx

        result = await _find_recent_pending("conv-1")
        assert result["conversation_id"] == "conv-1"

    @pytest.mark.asyncio
    @patch("services.intent_learning.get_pg_connection")
    async def test_db_error_returns_none(self, mock_get_conn):
        """数据库异常→返回 None（不抛异常）"""
        mock_get_conn.side_effect = Exception("DB error")
        result = await _find_recent_pending("conv-1")
        assert result is None


# ============================================================
# TestWriteIntentPattern
# ============================================================


class TestWriteIntentPattern:

    @pytest.mark.asyncio
    @patch("services.knowledge_service.add_knowledge")
    async def test_writes_correct_knowledge(self, mock_add):
        """写入正确的知识节点结构"""
        mock_add.return_value = "node-123"
        result = await _write_intent_pattern(
            original_expression="修正图片",
            confirmed_tool="route_to_image",
            user_response="1",
            ask_options="1.编辑 2.生成",
        )
        assert result == "node-123"
        call_kwargs = mock_add.call_args[1]
        assert call_kwargs["category"] == "experience"
        assert call_kwargs["subcategory"] == "route_to_image"
        assert call_kwargs["node_type"] == "intent_pattern"
        assert call_kwargs["source"] == "user_confirmed"
        assert call_kwargs["confidence"] == 0.8
        assert call_kwargs["scope"] == "global"
        assert "修正图片" in call_kwargs["title"]
        assert "route_to_image" in call_kwargs["content"]

    @pytest.mark.asyncio
    @patch("services.knowledge_service.add_knowledge")
    async def test_tool_label_mapping(self, mock_add):
        """工具名正确映射为中文"""
        mock_add.return_value = "node-456"
        await _write_intent_pattern(
            original_expression="聊一下",
            confirmed_tool="route_to_chat",
            user_response="1",
            ask_options="options",
        )
        title = mock_add.call_args[1]["title"]
        assert "文字对话" in title

    @pytest.mark.asyncio
    @patch("services.knowledge_service.add_knowledge")
    async def test_video_tool_label(self, mock_add):
        """视频工具名映射"""
        mock_add.return_value = "node-789"
        await _write_intent_pattern(
            original_expression="做个视频",
            confirmed_tool="route_to_video",
            user_response="2",
            ask_options="options",
        )
        title = mock_add.call_args[1]["title"]
        assert "视频" in title


# ============================================================
# TestCheckAndRecordIntent
# ============================================================


class TestCheckAndRecordIntent:

    @pytest.mark.asyncio
    @patch("services.intent_learning.is_kb_available")
    async def test_kb_unavailable_returns_early(self, mock_avail):
        """知识库不可用→直接返回"""
        mock_avail.return_value = False
        # 不应抛异常
        await check_and_record_intent(
            conversation_id="conv-1",
            user_id="user-1",
            user_response="1",
            confirmed_tool="route_to_image",
        )

    @pytest.mark.asyncio
    @patch("services.intent_learning.record_metric")
    @patch("services.intent_learning._write_intent_pattern")
    @patch("services.intent_learning._find_recent_pending")
    @patch("services.intent_learning.is_kb_available")
    async def test_no_pending_skips(
        self, mock_avail, mock_find, mock_write, mock_record,
    ):
        """无 pending 记录→不写入知识"""
        mock_avail.return_value = True
        mock_find.return_value = None
        await check_and_record_intent(
            conversation_id="conv-1",
            user_id="user-1",
            user_response="1",
            confirmed_tool="route_to_image",
        )
        mock_write.assert_not_called()
        mock_record.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.intent_learning.record_metric")
    @patch("services.intent_learning._write_intent_pattern")
    @patch("services.intent_learning._find_recent_pending")
    @patch("services.intent_learning.is_kb_available")
    async def test_pending_found_writes_knowledge(
        self, mock_avail, mock_find, mock_write, mock_record,
    ):
        """有 pending 记录→写入知识 + 记录确认信号"""
        mock_avail.return_value = True
        mock_find.return_value = {
            "original_message": "修正图片",
            "ask_options": "1.编辑 2.生成",
        }
        mock_write.return_value = "node-123"
        mock_record.return_value = None

        await check_and_record_intent(
            conversation_id="conv-1",
            user_id="user-1",
            user_response="1",
            confirmed_tool="route_to_image",
        )

        # 验证写入知识
        mock_write.assert_called_once()
        write_kwargs = mock_write.call_args[1]
        assert write_kwargs["original_expression"] == "修正图片"
        assert write_kwargs["confirmed_tool"] == "route_to_image"

        # 验证记录确认信号
        mock_record.assert_called_once()
        record_kwargs = mock_record.call_args[1]
        assert record_kwargs["task_type"] == "intent_confirmed"
        assert record_kwargs["status"] == "confirmed"

    @pytest.mark.asyncio
    @patch("services.intent_learning.record_metric")
    @patch("services.intent_learning._write_intent_pattern")
    @patch("services.intent_learning._find_recent_pending")
    @patch("services.intent_learning.is_kb_available")
    async def test_empty_original_message_skips(
        self, mock_avail, mock_find, mock_write, mock_record,
    ):
        """pending 的 original_message 为空→跳过"""
        mock_avail.return_value = True
        mock_find.return_value = {
            "original_message": "",
            "ask_options": "options",
        }
        await check_and_record_intent(
            conversation_id="conv-1",
            user_id="user-1",
            user_response="1",
            confirmed_tool="route_to_image",
        )
        mock_write.assert_not_called()
