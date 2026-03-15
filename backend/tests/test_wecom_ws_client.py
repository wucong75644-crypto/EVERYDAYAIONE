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

import asyncio
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
    async def test_stream_with_feedback_id(self, connected_client):
        """流式带 feedback_id → 包含 feedback.id 字段"""
        await connected_client.send_stream_chunk(
            req_id="req_005",
            stream_id="stream_003",
            content="回复内容",
            finish=True,
            feedback_id="msg_abc123",
        )

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        stream = sent["body"]["stream"]
        assert stream["feedback"] == {"id": "msg_abc123"}

    @pytest.mark.asyncio
    async def test_stream_without_feedback_id(self, connected_client):
        """流式无 feedback_id → 不含 feedback 字段"""
        await connected_client.send_stream_chunk(
            req_id="req_006",
            stream_id="stream_004",
            content="内容",
            finish=False,
        )

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert "feedback" not in sent["body"]["stream"]

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

    @pytest.mark.asyncio
    async def test_send_error_closes_ws(self, connected_client):
        """发送失败 → 主动关闭WS触发重连"""
        connected_client._ws.send.side_effect = RuntimeError("send failed")
        connected_client._ws.close = AsyncMock()

        await connected_client._safe_send({"cmd": "test"})

        connected_client._ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_error_close_also_fails(self, connected_client):
        """发送失败 + close也失败 → 不抛出"""
        connected_client._ws.send.side_effect = RuntimeError("send failed")
        connected_client._ws.close = AsyncMock(side_effect=RuntimeError("close failed"))

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
        """enter_chat 事件 → 发送欢迎卡片"""
        data = {
            "headers": {"req_id": "req_evt_001"},
            "body": {
                "event": {"eventtype": "enter_chat"},
            },
        }

        await connected_client._handle_event_callback(data)

        connected_client._ws.send.assert_called_once()
        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.RESPOND_WELCOME
        assert sent["headers"]["req_id"] == "req_evt_001"
        assert sent["body"]["msgtype"] == "template_card"
        assert "template_card" in sent["body"]

    @pytest.mark.asyncio
    async def test_unknown_event_no_op(self, connected_client):
        """未知事件 → 不处理"""
        data = {
            "headers": {"req_id": "req_evt_002"},
            "body": {
                "event": {"eventtype": "unknown_event"},
            },
        }

        await connected_client._handle_event_callback(data)

        connected_client._ws.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_feedback_event_like(self, connected_client):
        """feedback_event 赞 → 记录日志，不发送消息"""
        data = {
            "headers": {"req_id": "req_fb_001"},
            "body": {
                "from": {"userid": "test_user"},
                "event": {
                    "eventtype": "feedback_event",
                    "feedback": {
                        "type": 1,
                        "id": "fb_msg_001",
                        "content": "",
                    },
                },
            },
        }

        await connected_client._handle_event_callback(data)
        # feedback_event 只记日志，不发送消息
        connected_client._ws.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_feedback_event_dislike(self, connected_client):
        """feedback_event 踩 → 记录日志（含 reasons）"""
        data = {
            "headers": {"req_id": "req_fb_002"},
            "body": {
                "from": {"userid": "test_user"},
                "event": {
                    "eventtype": "feedback_event",
                    "feedback": {
                        "type": 2,
                        "id": "fb_msg_002",
                        "content": "回答不准确",
                        "inaccurate_reason_list": ["事实错误", "过时信息"],
                    },
                },
            },
        }

        await connected_client._handle_event_callback(data)
        connected_client._ws.send.assert_not_called()


# ============================================================
# TestHeartbeatLoop — 心跳连续失败检测
# ============================================================


class TestHeartbeatLoop:
    """_heartbeat_loop 连续失败检测"""

    @pytest.mark.asyncio
    async def test_heartbeat_resets_on_success(self, connected_client):
        """心跳成功 → 计数器归零，循环继续"""
        call_count = 0

        async def _mock_send(data):
            nonlocal call_count
            call_count += 1
            # 第2次心跳后断开循环
            if call_count >= 2:
                connected_client._is_connected = False

        connected_client._ws.send = _mock_send

        with patch("services.wecom.ws_client.HEARTBEAT_INTERVAL", 0.01):
            await connected_client._heartbeat_loop()

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_heartbeat_2_failures_closes_ws(self, connected_client):
        """连续2次心跳失败 → 主动关闭WS"""
        connected_client._ws.send = AsyncMock(
            side_effect=RuntimeError("timeout")
        )
        connected_client._ws.close = AsyncMock()

        with patch("services.wecom.ws_client.HEARTBEAT_INTERVAL", 0.01):
            await connected_client._heartbeat_loop()

        assert connected_client._is_connected is False
        connected_client._ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_single_failure_continues(self, connected_client):
        """单次心跳失败 → 继续尝试"""
        call_count = 0

        async def _flaky_send(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            # 第二次成功后停止
            if call_count >= 3:
                connected_client._is_connected = False

        connected_client._ws.send = _flaky_send

        with patch("services.wecom.ws_client.HEARTBEAT_INTERVAL", 0.01):
            await connected_client._heartbeat_loop()

        # 至少发送了3次（1次失败 + 后续成功）
        assert call_count >= 3


# ============================================================
# TestReceiveLoopNonBlocking — 接收循环非阻塞
# ============================================================


class TestReceiveLoopNonBlocking:
    """_receive_loop 使用 create_task 不阻塞"""

    @pytest.mark.asyncio
    async def test_msg_callback_uses_create_task(self, client):
        """消息回调通过 create_task 分发，不阻塞接收循环"""
        msg_data = json.dumps({
            "cmd": WecomCommand.MSG_CALLBACK,
            "body": {"msgid": "m_task_001", "content": "hi"},
        })

        # 模拟 WS 只产出一条消息后结束
        client._ws = AsyncMock()
        client._ws.__aiter__ = lambda self: self
        messages = [msg_data]

        async def _anext(self):
            if messages:
                return messages.pop(0)
            raise StopAsyncIteration

        client._ws.__anext__ = _anext
        client._is_connected = True

        # 用真实 create_task 包装，避免协程未消费警告
        tasks_created = []
        original_create_task = asyncio.create_task

        def _tracking_create_task(coro):
            task = original_create_task(coro)
            tasks_created.append(task)
            return task

        with patch("asyncio.create_task", side_effect=_tracking_create_task):
            await client._receive_loop()
            # 等待所有创建的任务完成
            for t in tasks_created:
                await t

        assert len(tasks_created) == 1

    @pytest.mark.asyncio
    async def test_event_callback_uses_create_task(self, connected_client):
        """事件回调通过 create_task 分发"""
        event_data = json.dumps({
            "cmd": WecomCommand.EVENT_CALLBACK,
            "body": {"event": {"eventtype": "enter_chat"}},
        })

        connected_client._ws.__aiter__ = lambda self: self
        messages = [event_data]

        async def _anext(self):
            if messages:
                return messages.pop(0)
            raise StopAsyncIteration

        connected_client._ws.__anext__ = _anext

        tasks_created = []
        original_create_task = asyncio.create_task

        def _tracking_create_task(coro):
            task = original_create_task(coro)
            tasks_created.append(task)
            return task

        with patch("asyncio.create_task", side_effect=_tracking_create_task):
            await connected_client._receive_loop()
            for t in tasks_created:
                await t

        assert len(tasks_created) == 1


# ============================================================
# TestSendTemplateCard — 模板卡片发送
# ============================================================


class TestSendTemplateCard:
    """send_template_card 协议构建"""

    @pytest.mark.asyncio
    async def test_template_card_protocol(self, connected_client):
        """模板卡片 → 正确协议格式"""
        card = {"card_type": "text_notice", "main_title": {"title": "Hello"}}
        await connected_client.send_template_card("req_tc_001", card)

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.RESPOND_MSG
        assert sent["headers"]["req_id"] == "req_tc_001"
        assert sent["body"]["msgtype"] == "template_card"
        assert sent["body"]["template_card"] == card

    @pytest.mark.asyncio
    async def test_template_card_disconnected(self, client):
        """未连接 → 不发送"""
        await client.send_template_card("req", {"card": "data"})
        # 无 _ws，不抛出


# ============================================================
# TestSendUpdateCard — 更新卡片
# ============================================================


class TestSendUpdateCard:
    """send_update_card 协议构建"""

    @pytest.mark.asyncio
    async def test_update_card_protocol(self, connected_client):
        """更新卡片 → 正确协议格式"""
        card = {"card_type": "text_notice", "main_title": {"title": "Updated"}}
        await connected_client.send_update_card("req_uc_001", card)

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.RESPOND_UPDATE
        assert sent["headers"]["req_id"] == "req_uc_001"
        assert sent["body"]["response_type"] == "update_template_card"
        assert sent["body"]["template_card"] == card

    @pytest.mark.asyncio
    async def test_update_card_disconnected(self, client):
        """未连接 → 不发送"""
        await client.send_update_card("req", {"card": "data"})


# ============================================================
# TestDisconnectedEvent — disconnected_event 强制关闭重连
# ============================================================


class TestDisconnectedEvent:
    """_handle_event_callback disconnected_event"""

    @pytest.mark.asyncio
    async def test_disconnected_event_force_closes(self, connected_client):
        """disconnected_event → 主动关闭连接"""
        connected_client._ws.close = AsyncMock()
        data = {
            "headers": {"req_id": "req_dc_001"},
            "body": {
                "event": {"eventtype": "disconnected_event"},
            },
        }

        await connected_client._handle_event_callback(data)

        assert connected_client._is_connected is False
        connected_client._ws.close.assert_called_once()
        # 不发送任何消息
        connected_client._ws.send.assert_not_called()


# ============================================================
# TestTemplateCardEvent — template_card_event 分发
# ============================================================


class TestTemplateCardEvent:
    """_handle_event_callback template_card_event"""

    @pytest.mark.asyncio
    async def test_card_event_dispatches_to_handler(self):
        """template_card_event → 调用 on_card_event"""
        on_card = AsyncMock()
        client = WecomWSClient("bot", "secret", on_card_event=on_card)

        data = {
            "headers": {"req_id": "req_ce_001"},
            "body": {
                "event": {
                    "eventtype": "template_card_event",
                    "template_card_event": {"event_key": "start_chat"},
                },
            },
        }

        await client._handle_event_callback(data)
        on_card.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_card_event_handler_error_no_raise(self):
        """on_card_event 异常 → 不抛出"""
        on_card = AsyncMock(side_effect=RuntimeError("handler crash"))
        client = WecomWSClient("bot", "secret", on_card_event=on_card)

        data = {
            "headers": {"req_id": "req_ce_002"},
            "body": {
                "event": {"eventtype": "template_card_event"},
            },
        }

        # 不应抛出
        await client._handle_event_callback(data)

    @pytest.mark.asyncio
    async def test_card_event_no_handler(self):
        """无 on_card_event 处理器 → 静默跳过"""
        client = WecomWSClient("bot", "secret", on_card_event=None)

        data = {
            "headers": {"req_id": "req_ce_003"},
            "body": {
                "event": {"eventtype": "template_card_event"},
            },
        }

        await client._handle_event_callback(data)


# ============================================================
# TestSubscribe — 订阅协议
# ============================================================


class TestSubscribe:
    """_subscribe 认证协议"""

    @pytest.mark.asyncio
    async def test_subscribe_success(self, connected_client):
        """订阅成功 → 正常返回"""
        connected_client._ws.recv = AsyncMock(
            return_value=json.dumps({"errcode": 0, "errmsg": "ok"})
        )

        await connected_client._subscribe()

        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.SUBSCRIBE
        assert sent["body"]["bot_id"] == "bot_test"
        assert sent["body"]["secret"] == "secret_test"

    @pytest.mark.asyncio
    async def test_subscribe_error_raises(self, connected_client):
        """订阅失败 → 抛出 ConnectionError"""
        connected_client._ws.recv = AsyncMock(
            return_value=json.dumps({"errcode": 40001, "errmsg": "invalid secret"})
        )

        with pytest.raises(ConnectionError, match="errcode=40001"):
            await connected_client._subscribe()

    @pytest.mark.asyncio
    async def test_subscribe_body_errcode(self, connected_client):
        """errcode 在 body 内 → 也能正确解析"""
        connected_client._ws.recv = AsyncMock(
            return_value=json.dumps({
                "body": {"errcode": 40002, "errmsg": "bad bot_id"},
            })
        )

        with pytest.raises(ConnectionError, match="errcode=40002"):
            await connected_client._subscribe()


# ============================================================
# TestRecvTimeout — 心跳接收超时检测
# ============================================================


class TestRecvTimeout:
    """_heartbeat_loop 接收超时检测"""

    @pytest.mark.asyncio
    async def test_recv_timeout_force_closes(self, connected_client):
        """超过 RECV_TIMEOUT 未收到数据 → 强制关闭"""
        connected_client._ws.close = AsyncMock()
        # 设置 last_recv_time 为很久以前
        connected_client._last_recv_time = (
            asyncio.get_event_loop().time() - 100
        )

        with patch("services.wecom.ws_client.HEARTBEAT_INTERVAL", 0.01):
            with patch("services.wecom.ws_client.RECV_TIMEOUT", 1):
                await connected_client._heartbeat_loop()

        assert connected_client._is_connected is False
        connected_client._ws.close.assert_called_once()
