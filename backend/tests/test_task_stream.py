"""Redis Streams 任务消息总线 (task_stream) 单元测试"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.task_stream import (
    STREAM_KEY_PREFIX,
    STREAM_MAXLEN,
    STREAM_TTL_SECONDS,
    _TERMINAL_TYPES,
    _parse_entry,
    publish,
    consume,
    set_stream_expire,
)


# ============================================================
# _parse_entry 测试
# ============================================================


class TestParseEntry:
    """_parse_entry: Stream entry 解析 + user_id 鉴权"""

    def test_valid_entry(self):
        """正常 entry → (stream_id, message_dict)"""
        msg = {"type": "message_chunk", "payload": {"chunk": "你好"}}
        fields = {"user_id": "u1", "data": json.dumps(msg)}

        result = _parse_entry("1234-0", fields, "u1")

        assert result is not None
        stream_id, message = result
        assert stream_id == "1234-0"
        assert message["type"] == "message_chunk"
        assert message["stream_id"] == "1234-0"

    def test_user_id_mismatch_returns_none(self):
        """user_id 不匹配 → None（鉴权拒绝）"""
        fields = {"user_id": "u1", "data": json.dumps({"type": "x"})}

        result = _parse_entry("1234-0", fields, "u2")

        assert result is None

    def test_missing_data_returns_none(self):
        """缺少 data 字段 → None"""
        fields = {"user_id": "u1"}

        result = _parse_entry("1234-0", fields, "u1")

        assert result is None

    def test_invalid_json_returns_none(self):
        """JSON 解析失败 → None"""
        fields = {"user_id": "u1", "data": "not-json{{{"}

        result = _parse_entry("1234-0", fields, "u1")

        assert result is None

    def test_stream_id_attached_to_message(self):
        """message 中附加 stream_id 字段"""
        msg = {"type": "message_done"}
        fields = {"user_id": "u1", "data": json.dumps(msg)}

        _, message = _parse_entry("9999-5", fields, "u1")

        assert message["stream_id"] == "9999-5"


# ============================================================
# publish 测试
# ============================================================


class TestPublish:

    @pytest.mark.asyncio
    async def test_publish_success(self):
        """正常写入 → 返回 entry_id"""
        mock_client = AsyncMock()
        mock_client.xadd.return_value = "1712438000000-0"

        with patch("core.redis.RedisClient.get_client", return_value=mock_client):
            result = await publish("task1", "u1", {"type": "message_chunk"})

        assert result == "1712438000000-0"
        mock_client.xadd.assert_awaited_once()

        # 验证 xadd 参数
        call_args = mock_client.xadd.call_args
        assert call_args.args[0] == f"{STREAM_KEY_PREFIX}task1"
        entry_data = call_args.args[1]
        assert entry_data["user_id"] == "u1"
        assert json.loads(entry_data["data"])["type"] == "message_chunk"
        assert call_args.kwargs["maxlen"] == STREAM_MAXLEN

    @pytest.mark.asyncio
    async def test_publish_redis_failure_fallback(self):
        """Redis 异常 → fallback 到 Pub/Sub"""
        with patch("core.redis.RedisClient.get_client", side_effect=Exception("连接失败")), \
             patch("services.task_stream._fallback_pubsub", new_callable=AsyncMock) as mock_fallback:
            result = await publish("task1", "u1", {"type": "x"})

        assert result is None
        mock_fallback.assert_awaited_once_with("task1", "u1", {"type": "x"})

    @pytest.mark.asyncio
    async def test_publish_data_serialization(self):
        """中文消息正确序列化（ensure_ascii=False）"""
        mock_client = AsyncMock()
        mock_client.xadd.return_value = "123-0"

        with patch("core.redis.RedisClient.get_client", return_value=mock_client):
            await publish("t1", "u1", {"text": "你好世界"})

        data = mock_client.xadd.call_args.args[1]["data"]
        assert "你好世界" in data  # 不是 \u4f60\u597d


# ============================================================
# set_stream_expire 测试
# ============================================================


class TestSetStreamExpire:

    @pytest.mark.asyncio
    async def test_expire_success(self):
        """正常设置过期时间"""
        mock_client = AsyncMock()

        with patch("core.redis.RedisClient.get_client", return_value=mock_client):
            await set_stream_expire("task1")

        mock_client.expire.assert_awaited_once_with(
            f"{STREAM_KEY_PREFIX}task1",
            STREAM_TTL_SECONDS,
        )

    @pytest.mark.asyncio
    async def test_expire_custom_ttl(self):
        """自定义 TTL"""
        mock_client = AsyncMock()

        with patch("core.redis.RedisClient.get_client", return_value=mock_client):
            await set_stream_expire("task1", ttl_seconds=120)

        mock_client.expire.assert_awaited_once_with(
            f"{STREAM_KEY_PREFIX}task1", 120,
        )

    @pytest.mark.asyncio
    async def test_expire_redis_failure_no_raise(self):
        """Redis 异常不抛出（fire-and-forget）"""
        with patch("core.redis.RedisClient.get_client", side_effect=Exception("down")):
            await set_stream_expire("task1")  # 不应抛异常


# ============================================================
# consume 测试
# ============================================================


class TestConsume:

    @pytest.mark.asyncio
    async def test_replay_history_messages(self):
        """Phase 1: XRANGE 补发历史消息"""
        mock_client = AsyncMock()
        mock_client.xrange.return_value = [
            ("100-0", {"user_id": "u1", "data": json.dumps({"type": "message_chunk", "chunk": "你"})}),
            ("200-0", {"user_id": "u1", "data": json.dumps({"type": "message_done"})}),
        ]

        with patch("services.task_stream._create_block_client", return_value=mock_client):
            messages = []
            async for stream_id, msg in consume("task1", "u1", "0"):
                messages.append((stream_id, msg))

        assert len(messages) == 2
        assert messages[0][0] == "100-0"
        assert messages[1][1]["type"] == "message_done"
        # message_done 是 terminal，不应进入 XREAD 阶段
        mock_client.xread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exclusive_range_after_cursor(self):
        """last_stream_id != "0" → XRANGE 用排他 min"""
        mock_client = AsyncMock()
        mock_client.xrange.return_value = [
            ("300-0", {"user_id": "u1", "data": json.dumps({"type": "message_done"})}),
        ]

        with patch("services.task_stream._create_block_client", return_value=mock_client):
            async for _ in consume("task1", "u1", "200-0"):
                pass

        # XRANGE min 应该是 "(200-0"（排他）
        call_args = mock_client.xrange.call_args
        assert call_args.kwargs.get("min") == "(200-0" or call_args.args[1] == "(200-0"

    @pytest.mark.asyncio
    async def test_live_tail_xread(self):
        """Phase 2: 历史为空时进入 XREAD BLOCK 实时监听"""
        mock_client = AsyncMock()
        mock_client.xrange.return_value = []  # 无历史
        # 第一次 XREAD 返回新消息，第二次返回 done
        mock_client.xread.side_effect = [
            [("stream:task:t1", [
                ("500-0", {"user_id": "u1", "data": json.dumps({"type": "message_chunk", "chunk": "hi"})}),
            ])],
            [("stream:task:t1", [
                ("600-0", {"user_id": "u1", "data": json.dumps({"type": "message_done"})}),
            ])],
        ]

        with patch("services.task_stream._create_block_client", return_value=mock_client):
            messages = []
            async for stream_id, msg in consume("t1", "u1"):
                messages.append(msg["type"])

        assert messages == ["message_chunk", "message_done"]

    @pytest.mark.asyncio
    async def test_stream_expired_exits(self):
        """Stream 已过期（exists=False）→ consumer 退出"""
        mock_client = AsyncMock()
        mock_client.xrange.return_value = []
        mock_client.xread.return_value = []  # 超时无消息
        mock_client.exists.return_value = 0  # Stream 不存在

        with patch("services.task_stream._create_block_client", return_value=mock_client):
            messages = []
            async for stream_id, msg in consume("t1", "u1"):
                messages.append(msg)

        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_user_id_filter(self):
        """消费时过滤 user_id 不匹配的消息"""
        mock_client = AsyncMock()
        mock_client.xrange.return_value = [
            ("100-0", {"user_id": "other_user", "data": json.dumps({"type": "message_chunk"})}),
            ("200-0", {"user_id": "u1", "data": json.dumps({"type": "message_done"})}),
        ]

        with patch("services.task_stream._create_block_client", return_value=mock_client):
            messages = []
            async for stream_id, msg in consume("t1", "u1"):
                messages.append(msg)

        # 只收到 user_id=u1 的消息
        assert len(messages) == 1
        assert messages[0]["type"] == "message_done"

    @pytest.mark.asyncio
    async def test_redis_error_exits_gracefully(self):
        """Redis 异常 → 静默退出（不抛异常）"""
        with patch("core.redis.RedisClient.get_client", side_effect=Exception("连接断开")):
            messages = []
            async for stream_id, msg in consume("t1", "u1"):
                messages.append(msg)

        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_message_error_is_terminal(self):
        """message_error 也是终止类型"""
        mock_client = AsyncMock()
        mock_client.xrange.return_value = [
            ("100-0", {"user_id": "u1", "data": json.dumps({"type": "message_error"})}),
        ]

        with patch("services.task_stream._create_block_client", return_value=mock_client):
            messages = []
            async for stream_id, msg in consume("t1", "u1"):
                messages.append(msg["type"])

        assert messages == ["message_error"]


# ============================================================
# _fallback_pubsub 测试
# ============================================================


class TestFallbackPubsub:

    @pytest.mark.asyncio
    async def test_fallback_calls_ws_manager(self):
        """降级时调用 ws_manager.send_to_task_or_user"""
        from services.task_stream import _fallback_pubsub

        mock_ws = MagicMock()
        mock_ws.send_to_task_or_user = AsyncMock()

        with patch("services.websocket_manager.ws_manager", mock_ws):
            await _fallback_pubsub("t1", "u1", {"type": "x"})

        mock_ws.send_to_task_or_user.assert_awaited_once_with("t1", "u1", {"type": "x"})

    @pytest.mark.asyncio
    async def test_fallback_exception_no_raise(self):
        """降级也失败时不抛异常"""
        from services.task_stream import _fallback_pubsub

        mock_ws = MagicMock()
        mock_ws.send_to_task_or_user = AsyncMock(side_effect=Exception("全挂了"))

        with patch("services.websocket_manager.ws_manager", mock_ws):
            await _fallback_pubsub("t1", "u1", {"type": "x"})  # 不应抛异常


# ============================================================
# 常量 / 配置 测试
# ============================================================


class TestConstants:

    def test_terminal_types(self):
        """终止类型包含 message_done 和 message_error"""
        assert "message_done" in _TERMINAL_TYPES
        assert "message_error" in _TERMINAL_TYPES
        assert "message_chunk" not in _TERMINAL_TYPES

    def test_stream_key_prefix(self):
        """Stream key 前缀格式"""
        assert STREAM_KEY_PREFIX == "stream:task:"
