"""Phase 4 单元测试 — send_msg / chatid 注册 / 推送 API"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from schemas.wecom import WecomCommand
from services.wecom.ws_client import WecomWSClient


# ============================================================
# TestSendMsg — ws_client 主动发送
# ============================================================


@pytest.fixture
def connected_client():
    client = WecomWSClient(bot_id="bot", secret="sec")
    client._ws = AsyncMock()
    client._is_connected = True
    return client


class TestSendMsg:
    """send_msg 主动推送协议"""

    @pytest.mark.asyncio
    async def test_send_msg_protocol(self, connected_client):
        """发送 markdown 消息 → 协议格式正确"""
        ok = await connected_client.send_msg(
            chatid="user_abc",
            msgtype="markdown",
            content={"content": "# Hello"},
        )

        assert ok is True
        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["cmd"] == WecomCommand.SEND_MSG
        assert sent["body"]["chatid"] == "user_abc"
        assert sent["body"]["chattype"] == "single"
        assert sent["body"]["msgtype"] == "markdown"
        assert sent["body"]["markdown"]["content"] == "# Hello"

    @pytest.mark.asyncio
    async def test_send_msg_group(self, connected_client):
        """群聊推送 → chattype=group"""
        ok = await connected_client.send_msg(
            chatid="group_123",
            msgtype="text",
            content={"content": "hello group"},
            chattype="group",
        )

        assert ok is True
        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert sent["body"]["chattype"] == "group"

    @pytest.mark.asyncio
    async def test_send_msg_disconnected(self):
        """未连接 → 返回 False"""
        client = WecomWSClient(bot_id="bot", secret="sec")
        ok = await client.send_msg("user", "text", {"content": "hi"})
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_msg_has_req_id(self, connected_client):
        """发送消息包含唯一 req_id"""
        await connected_client.send_msg("user", "text", {"content": "hi"})
        sent = json.loads(connected_client._ws.send.call_args[0][0])
        assert "req_id" in sent["headers"]
        assert sent["headers"]["req_id"].startswith("send_msg_")


# ============================================================
# TestChatidRegistry — chatid 注册
# ============================================================


class TestChatidRegistry:
    """update_last_chatid / get_chatid_by_user_id"""

    @pytest.mark.asyncio
    async def test_update_last_chatid(self):
        """更新 chatid → 调用 DB update"""
        from services.wecom.user_mapping_service import WecomUserMappingService

        db = MagicMock()
        chain = MagicMock()
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock()
        db.table = MagicMock(return_value=chain)

        svc = WecomUserMappingService(db)
        await svc.update_last_chatid("user_abc", "corp1", "chat_123", "single")

        chain.update.assert_called_once()
        update_data = chain.update.call_args[0][0]
        assert update_data["last_chatid"] == "chat_123"
        assert update_data["last_chat_type"] == "single"

    @pytest.mark.asyncio
    async def test_update_last_chatid_error_no_raise(self):
        """DB 异常 → 不抛出"""
        from services.wecom.user_mapping_service import WecomUserMappingService

        db = MagicMock()
        db.table = MagicMock(side_effect=RuntimeError("DB down"))
        svc = WecomUserMappingService(db)
        # 不应抛出
        await svc.update_last_chatid("user", "corp", "chat", "single")

    @pytest.mark.asyncio
    async def test_get_chatid_found(self):
        """查找 chatid → 返回结果"""
        from services.wecom.user_mapping_service import WecomUserMappingService

        db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{
            "wecom_userid": "wx_user",
            "last_chatid": "chat_456",
            "last_chat_type": "group",
        }]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        svc = WecomUserMappingService(db)
        result = await svc.get_chatid_by_user_id("user_id_1")

        assert result == {
            "chatid": "chat_456",
            "chattype": "group",
            "wecom_userid": "wx_user",
        }

    @pytest.mark.asyncio
    async def test_get_chatid_no_mapping(self):
        """无映射 → None"""
        from services.wecom.user_mapping_service import WecomUserMappingService

        db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = []
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        svc = WecomUserMappingService(db)
        result = await svc.get_chatid_by_user_id("unknown")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_chatid_null_chatid(self):
        """有映射但 chatid 为空 → None"""
        from services.wecom.user_mapping_service import WecomUserMappingService

        db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        mock_result = MagicMock()
        mock_result.data = [{"wecom_userid": "wx", "last_chatid": None, "last_chat_type": None}]
        chain.execute.return_value = mock_result
        db.table = MagicMock(return_value=chain)

        svc = WecomUserMappingService(db)
        result = await svc.get_chatid_by_user_id("user_id_1")
        assert result is None


# ============================================================
# TestPushAPI — POST /api/wecom/push
# ============================================================


class TestPushAPI:
    """push_message 端点测试"""

    @pytest.mark.asyncio
    async def test_push_ws_disconnected(self):
        """WS 未就绪 → 返回失败"""
        from api.routes.wecom import push_message, WecomPushRequest

        req = WecomPushRequest(user_id="u1", message="hello")

        with patch("wecom_ws_runner.get_ws_client", return_value=None):
            result = await push_message(req, MagicMock())

        assert result["success"] is False
        assert "未就绪" in result["error"]

    @pytest.mark.asyncio
    async def test_push_with_explicit_chatid(self):
        """指定 chatid → 直接发送"""
        from api.routes.wecom import push_message, WecomPushRequest

        req = WecomPushRequest(
            user_id="u1", message="hello", chatid="chat_abc",
        )

        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_msg = AsyncMock(return_value=True)

        with patch("wecom_ws_runner.get_ws_client", return_value=mock_ws):
            result = await push_message(req, MagicMock())

        assert result["success"] is True
        mock_ws.send_msg.assert_called_once_with(
            chatid="chat_abc",
            msgtype="markdown",
            content={"content": "hello"},
            chattype="single",
        )

    @pytest.mark.asyncio
    async def test_push_lookup_chatid(self):
        """未指定 chatid → 查找映射"""
        from api.routes.wecom import push_message, WecomPushRequest

        req = WecomPushRequest(user_id="u1", message="hi")

        mock_ws = MagicMock()
        mock_ws.is_connected = True
        mock_ws.send_msg = AsyncMock(return_value=True)

        mock_user_svc = MagicMock()
        mock_user_svc.get_chatid_by_user_id = AsyncMock(
            return_value={"chatid": "chat_found", "chattype": "group", "wecom_userid": "wx"},
        )

        with (
            patch("wecom_ws_runner.get_ws_client", return_value=mock_ws),
            patch(
                "services.wecom.user_mapping_service.WecomUserMappingService",
                return_value=mock_user_svc,
            ),
        ):
            result = await push_message(req, MagicMock())

        assert result["success"] is True
        mock_ws.send_msg.assert_called_once()
        assert mock_ws.send_msg.call_args[1]["chatid"] == "chat_found"
        assert mock_ws.send_msg.call_args[1]["chattype"] == "group"

    @pytest.mark.asyncio
    async def test_push_no_chatid_found(self):
        """未指定 chatid + 查找失败 → 返回错误"""
        from api.routes.wecom import push_message, WecomPushRequest

        req = WecomPushRequest(user_id="u1", message="hi")

        mock_ws = MagicMock()
        mock_ws.is_connected = True

        mock_user_svc = MagicMock()
        mock_user_svc.get_chatid_by_user_id = AsyncMock(return_value=None)

        with (
            patch("wecom_ws_runner.get_ws_client", return_value=mock_ws),
            patch(
                "services.wecom.user_mapping_service.WecomUserMappingService",
                return_value=mock_user_svc,
            ),
        ):
            result = await push_message(req, MagicMock())

        assert result["success"] is False
        assert "chatid" in result["error"]
