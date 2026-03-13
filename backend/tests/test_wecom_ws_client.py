"""
WecomWSClient 单元测试

覆盖：消息去重 LRU、send_reply/send_stream_chunk 协议构建、
      连接状态管理、_handle_msg_callback 去重+分发、
      _handle_event_callback 欢迎消息
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.wecom import WecomCommand
from services.wecom.ws_client import (
    MSG_DEDUP_CAPACITY,
    WecomWSClient,
)


@pytest.fixture
def client():
    """创建未连接的 WS 客户端"""
    return WecomWSClient(
        bot_id="bot_test",
        secret="secret_test",
        on_message=AsyncMock(),
    )


@pytest.fixture
def connected_client(client):
    """模拟已连接状态的客户端"""
    client._ws = AsyncMock()
    client._is_connected = True
    return client


# ============================================================
# TestDedup — 消息去重 LRU
# ============================================================


class TestDedup:
    """消息去重 LRU 缓存"""

    def test_add_and_check(self, client):
        """添加后可检测到重复"""
        client._add_to_dedup("msg001")
        assert "msg001" in client._processed_msgs

    def test_capacity_eviction(self, client):
        """超过容量 → 淘汰最早的"""
        for i in range(MSG_DEDUP_CAPACITY + 10):
            client._add_to_dedup(f"msg_{i}")

        assert len(client._processed_msgs) == MSG_DEDUP_CAPACITY
        # 最早的应被淘汰
        assert "msg_0" not in client._processed_msgs
        # 最新的应在
        assert f"msg_{MSG_DEDUP_CAPACITY + 9}" in client._processed_msgs

    def test_empty_msgid(self, client):
        """空 msgid 也可以去重"""
        client._add_to_dedup("")
        assert "" in client._processed_msgs


# ============================================================
# TestSendReply — 回复消息协议
# ============================================================


class TestSendReply:
    """send_reply 协议构建"""

    @pytest.mark.asyncio
    async def test_send_text_reply(self, connected_client):
        """文本回复构建正确协议"""
        await connected_client.send_reply(
            req_id="req_001",
            msgtype="text",
            content={"content": "hello"},
        )

        connected_client._ws.send.assert_called_once()
        sent = json.loads(connected_client._ws.send.call_args[0][0])

        assert sent["cmd"] == WecomCommand.RESPOND_MSG
        assert sent["headers"]["req_id"] == "req_001"
        assert sent["body"]["msgtype"] == "text"
        assert sent["body"]["text"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_send_markdown_reply(self, connected_client):
        """Markdown 回复"""
        await connected_client.send_reply(
            req_id="req_002",
            msgtype="markdown",
            content={"content": "# Title"},
        )

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["body"]["msgtype"] == "markdown"
        assert sent["body"]["markdown"]["content"] == "# Title"

    @pytest.mark.asyncio
    async def test_no_send_when_disconnected(self, client):
        """未连接时不发送"""
        assert not client._is_connected
        await client.send_reply("req", "text", {"content": "hi"})
        # 没有 _ws，不应抛出


# ============================================================
# TestSendStreamChunk — 流式回复
# ============================================================


class TestSendStreamChunk:
    """send_stream_chunk 流式协议"""

    @pytest.mark.asyncio
    async def test_stream_chunk_protocol(self, connected_client):
        """流式 chunk 协议正确"""
        await connected_client.send_stream_chunk(
            req_id="req_003",
            stream_id="stream_001",
            content="部分内容",
            finish=False,
        )

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.RESPOND_MSG
        assert sent["body"]["msgtype"] == "stream"
        stream = sent["body"]["stream"]
        assert stream["id"] == "stream_001"
        assert stream["content"] == "部分内容"
        assert stream["finish"] is False

    @pytest.mark.asyncio
    async def test_stream_finish(self, connected_client):
        """流式结束标志"""
        await connected_client.send_stream_chunk(
            req_id="req_004",
            stream_id="stream_002",
            content="完整内容",
            finish=True,
        )

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["body"]["stream"]["finish"] is True

    @pytest.mark.asyncio
    async def test_no_send_when_disconnected(self, client):
        """未连接时静默跳过"""
        await client.send_stream_chunk("req", "s1", "text")
        # 不抛出异常


# ============================================================
# TestConnectionState — 连接状态管理
# ============================================================


class TestConnectionState:
    """连接状态属性"""

    def test_initially_disconnected(self, client):
        assert client.is_connected is False

    def test_connected_property(self, connected_client):
        assert connected_client.is_connected is True

    def test_should_run_initially_true(self, client):
        assert client._should_run is True


# ============================================================
# TestSafeSend — 安全发送
# ============================================================


class TestSafeSend:
    """_safe_send 异常处理"""

    @pytest.mark.asyncio
    async def test_send_success(self, connected_client):
        """正常发送"""
        await connected_client._safe_send({"cmd": "test"})
        connected_client._ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_error_marks_disconnected(self, connected_client):
        """发送失败 → 标记断开"""
        connected_client._ws.send.side_effect = RuntimeError("send failed")

        await connected_client._safe_send({"cmd": "test"})

        assert connected_client._is_connected is False


# ============================================================
# TestHandleMsgCallback — 消息回调
# ============================================================


class TestHandleMsgCallback:
    """_handle_msg_callback 去重+分发"""

    @pytest.mark.asyncio
    async def test_dispatches_to_handler(self, client):
        """正常消息 → 调用 on_message"""
        data = {
            "cmd": WecomCommand.MSG_CALLBACK,
            "body": {"msgid": "m001", "content": "hello"},
        }

        await client._handle_msg_callback(data)

        client.on_message.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_dedup_skips_duplicate(self, client):
        """重复 msgid → 跳过"""
        data = {
            "cmd": WecomCommand.MSG_CALLBACK,
            "body": {"msgid": "m002"},
        }

        await client._handle_msg_callback(data)
        await client._handle_msg_callback(data)

        # 只调用一次
        assert client.on_message.call_count == 1

    @pytest.mark.asyncio
    async def test_handler_error_no_raise(self, client):
        """handler 异常 → 记录日志不抛出"""
        client.on_message = AsyncMock(side_effect=RuntimeError("crash"))

        data = {
            "cmd": WecomCommand.MSG_CALLBACK,
            "body": {"msgid": "m003"},
        }

        # 不应抛出
        await client._handle_msg_callback(data)

    @pytest.mark.asyncio
    async def test_no_handler(self):
        """未设置 on_message → 静默跳过"""
        client = WecomWSClient("bot", "secret", on_message=None)
        data = {"body": {"msgid": "m004"}}

        await client._handle_msg_callback(data)


# ============================================================
# TestHandleEventCallback — 事件回调
# ============================================================


class TestHandleEventCallback:
    """_handle_event_callback 事件处理"""

    @pytest.mark.asyncio
    async def test_enter_chat_sends_welcome(self, connected_client):
        """enter_chat 事件 → 发送欢迎消息"""
        data = {
            "headers": {"req_id": "req_evt_001"},
            "body": {
                "event": {"eventtype": "enter_chat"},
            },
        }

        with patch(
            "services.wecom.ws_client.get_settings",
            return_value=MagicMock(),
        ):
            await connected_client._handle_event_callback(data)

        connected_client._ws.send.assert_called_once()
        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.RESPOND_WELCOME
        assert sent["headers"]["req_id"] == "req_evt_001"
        assert "你好" in sent["body"]["text"]["content"]

    @pytest.mark.asyncio
    async def test_unknown_event_no_op(self, connected_client):
        """未知事件 → 不处理"""
        data = {
            "headers": {"req_id": "req_evt_002"},
            "body": {
                "event": {"eventtype": "unknown_event"},
            },
        }

        with patch(
            "services.wecom.ws_client.get_settings",
            return_value=MagicMock(),
        ):
            await connected_client._handle_event_callback(data)

        connected_client._ws.send.assert_not_called()
