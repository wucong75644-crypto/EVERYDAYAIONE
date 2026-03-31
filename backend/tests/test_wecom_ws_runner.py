"""
wecom_ws_runner 单元测试（多企业版 WecomWSManager）

覆盖：_parse_message_content 消息解析（text/voice/image/file/video/mixed/缺失字段）、
      WecomWSManager 生命周期、get_ws_client、信号处理
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wecom_ws_runner import _parse_message_content


# ============================================================
# TestParseMessageContent — 模块级函数直接测试
# ============================================================


class TestParseMessageContent:
    """_parse_message_content: 从 body 中提取消息内容"""

    def test_text_message(self):
        """text 消息 -> text_content 正确"""
        body = {
            "msgtype": "text",
            "text": {"content": "你好"},
        }
        result = _parse_message_content(body)
        assert result["text_content"] == "你好"
        assert result["msgtype"] == "text"

    def test_voice_message(self):
        """voice 消息 -> voice content 正确"""
        body = {
            "msgtype": "voice",
            "voice": {"content": "语音转文字内容"},
        }
        result = _parse_message_content(body)
        assert result["text_content"] == "语音转文字内容"
        assert result["msgtype"] == "voice"

    def test_image_with_aeskey(self):
        """image 消息 -> image_urls / aeskeys 正确"""
        body = {
            "msgtype": "image",
            "image": {
                "url": "https://img.example.com/pic.jpg",
                "aeskey": "abc123key",
            },
        }
        result = _parse_message_content(body)
        assert result["msgtype"] == "image"
        assert result["image_urls"] == ["https://img.example.com/pic.jpg"]
        assert result["aeskeys"] == {"https://img.example.com/pic.jpg": "abc123key"}

    def test_image_without_aeskey(self):
        """image 消息无 aeskey -> aeskeys 为空"""
        body = {
            "msgtype": "image",
            "image": {"url": "https://img.example.com/pic2.jpg"},
        }
        result = _parse_message_content(body)
        assert result["image_urls"] == ["https://img.example.com/pic2.jpg"]
        assert result["aeskeys"] == {}

    def test_file_message(self):
        """file 消息 -> file_url / file_name 正确"""
        body = {
            "msgtype": "file",
            "file": {
                "url": "https://file.example.com/doc.pdf",
                "name": "report.pdf",
                "aeskey": "filekey",
            },
        }
        result = _parse_message_content(body)
        assert result["msgtype"] == "file"
        assert result["file_url"] == "https://file.example.com/doc.pdf"
        assert result["file_name"] == "report.pdf"
        assert result["aeskeys"] == {"https://file.example.com/doc.pdf": "filekey"}

    def test_video_message(self):
        """video 消息 -> file_url / file_name 正确"""
        body = {
            "msgtype": "video",
            "video": {
                "url": "https://vid.example.com/clip.mp4",
                "name": "clip.mp4",
                "aeskey": "vidkey",
            },
        }
        result = _parse_message_content(body)
        assert result["msgtype"] == "video"
        assert result["file_url"] == "https://vid.example.com/clip.mp4"
        assert result["file_name"] == "clip.mp4"
        assert result["aeskeys"] == {"https://vid.example.com/clip.mp4": "vidkey"}

    def test_mixed_text_and_images(self):
        """mixed 消息 -> text_content + image_urls 合并"""
        body = {
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"type": "text", "text": {"content": "看这个图"}},
                    {
                        "type": "image",
                        "image": {
                            "url": "https://img1.example.com/a.jpg",
                            "aeskey": "key1",
                        },
                    },
                    {
                        "type": "image",
                        "image": {
                            "url": "https://img2.example.com/b.jpg",
                        },
                    },
                ],
            },
        }
        result = _parse_message_content(body)
        assert result["msgtype"] == "mixed"
        assert result["text_content"] == "看这个图"
        assert len(result["image_urls"]) == 2
        assert "https://img1.example.com/a.jpg" in result["image_urls"]
        assert "https://img2.example.com/b.jpg" in result["image_urls"]
        assert result["aeskeys"].get("https://img1.example.com/a.jpg") == "key1"
        assert "https://img2.example.com/b.jpg" not in result["aeskeys"]

    def test_missing_fields_no_crash(self):
        """缺失字段 -> 不崩溃，text_content 为 None"""
        body = {"msgid": "msg_003", "msgtype": "image"}
        result = _parse_message_content(body)
        assert result["text_content"] is None
        assert result["image_urls"] == []
        assert result["msgtype"] == "image"

    def test_empty_body(self):
        """空 body -> 不崩溃"""
        result = _parse_message_content({})
        assert result["msgtype"] == ""
        assert result["text_content"] is None


# ============================================================
# TestWecomWSManager — 多企业 WS 管理器
# ============================================================


class TestWecomWSManager:
    """WecomWSManager 生命周期测试"""

    @pytest.mark.asyncio
    async def test_start_no_orgs(self):
        """无配置企业 -> 不启动任何 client"""
        from wecom_ws_runner import WecomWSManager

        mock_db = MagicMock()
        manager = WecomWSManager(mock_db)

        with patch(
            "services.org.config_resolver.OrgConfigResolver"
        ) as MockResolver:
            MockResolver.return_value.list_orgs_with_wecom_bot.return_value = []
            await manager.start()

        assert len(manager.clients) == 0

    @pytest.mark.asyncio
    async def test_start_with_orgs(self):
        """有配置企业 -> 启动对应 client"""
        from wecom_ws_runner import WecomWSManager

        mock_db = MagicMock()
        manager = WecomWSManager(mock_db)

        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()

        orgs = [
            {"org_id": "org-1", "bot_id": "bot_123", "bot_secret": "sec_456", "corp_id": "corp_789"},
        ]

        with (
            patch("services.org.config_resolver.OrgConfigResolver") as MockResolver,
            patch("wecom_ws_runner.WecomWSClient", return_value=mock_ws_instance),
            patch("wecom_ws_runner.WecomMessageService"),
        ):
            MockResolver.return_value.list_orgs_with_wecom_bot.return_value = orgs
            await manager.start()

        assert "org-1" in manager.clients
        mock_ws_instance.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_all_clients(self):
        """stop() 停止所有 client"""
        from wecom_ws_runner import WecomWSManager

        mock_db = MagicMock()
        manager = WecomWSManager(mock_db)

        mock_client = MagicMock()
        mock_client.stop = AsyncMock()
        manager._clients["org-1"] = mock_client

        await manager.stop()
        mock_client.stop.assert_called_once()

    def test_get_client(self):
        """get_client 按 org_id 返回 client"""
        from wecom_ws_runner import WecomWSManager

        mock_db = MagicMock()
        manager = WecomWSManager(mock_db)

        mock_client = MagicMock()
        manager._clients["org-1"] = mock_client

        assert manager.get_client("org-1") is mock_client
        assert manager.get_client("org-unknown") is None


# ============================================================
# TestGetWsClient — 模块级 get_ws_client
# ============================================================


class TestGetWsClient:
    """get_ws_client: 按 org_id 获取 WS 客户端"""

    def test_no_manager_returns_none(self):
        """_manager 为 None -> 返回 None"""
        import wecom_ws_runner
        original = wecom_ws_runner._manager
        wecom_ws_runner._manager = None
        try:
            result = wecom_ws_runner.get_ws_client("org-1")
            assert result is None
        finally:
            wecom_ws_runner._manager = original

    def test_no_org_id_returns_none(self):
        """org_id=None -> 返回 None"""
        import wecom_ws_runner
        mock_manager = MagicMock()
        original = wecom_ws_runner._manager
        wecom_ws_runner._manager = mock_manager
        try:
            result = wecom_ws_runner.get_ws_client(None)
            assert result is None
        finally:
            wecom_ws_runner._manager = original

    def test_with_org_id_returns_client(self):
        """有 org_id -> 委托给 manager.get_client"""
        import wecom_ws_runner
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_manager.get_client.return_value = mock_client
        original = wecom_ws_runner._manager
        wecom_ws_runner._manager = mock_manager
        try:
            result = wecom_ws_runner.get_ws_client("org-1")
            assert result is mock_client
            mock_manager.get_client.assert_called_once_with("org-1")
        finally:
            wecom_ws_runner._manager = original


# ============================================================
# TestMessageHandler — _make_message_handler 集成
# ============================================================


class TestMessageHandler:
    """WecomWSManager._make_message_handler 创建的闭包"""

    @pytest.mark.asyncio
    async def test_handler_parses_text_and_calls_service(self):
        """text 消息 -> 正确构建 WecomIncomingMessage 并调用 handle_message"""
        from wecom_ws_runner import WecomWSManager

        mock_db = MagicMock()
        manager = WecomWSManager(mock_db)
        mock_client = MagicMock()
        manager._clients["org-1"] = mock_client

        mock_msg_svc = MagicMock()
        mock_msg_svc.handle_message = AsyncMock()

        handler = manager._make_message_handler("org-1", "corp_789", mock_msg_svc)

        text_data = {
            "headers": {"req_id": "req_001"},
            "body": {
                "msgid": "msg_001",
                "from": {"userid": "user_abc"},
                "chatid": "chat_001",
                "chattype": "single",
                "msgtype": "text",
                "text": {"content": "你好"},
            },
        }

        await handler(text_data)

        mock_msg_svc.handle_message.assert_called_once()
        msg_arg = mock_msg_svc.handle_message.call_args[0][0]
        assert msg_arg.text_content == "你好"
        assert msg_arg.msgtype == "text"
        assert msg_arg.wecom_userid == "user_abc"
        assert msg_arg.channel == "smart_robot"
        assert msg_arg.corp_id == "corp_789"
        assert msg_arg.org_id == "org-1"

        reply_arg = mock_msg_svc.handle_message.call_args[0][1]
        assert reply_arg.channel == "smart_robot"
        assert reply_arg.req_id == "req_001"


# ============================================================
# TestCardHandler — _make_card_handler 集成
# ============================================================


class TestCardHandler:
    """WecomWSManager._make_card_handler 创建的闭包"""

    @pytest.mark.asyncio
    async def test_card_event_calls_handler(self):
        """卡片事件 -> 调用 CardEventHandler.handle"""
        from wecom_ws_runner import WecomWSManager

        mock_db = MagicMock()
        manager = WecomWSManager(mock_db)
        mock_client = MagicMock()
        manager._clients["org-1"] = mock_client

        mock_msg_svc = MagicMock()
        mock_msg_svc._get_or_create_conversation = AsyncMock(return_value="conv_123")

        handler = manager._make_card_handler("org-1", "corp_789", mock_msg_svc)

        mock_handler_instance = MagicMock()
        mock_handler_instance.handle = AsyncMock()

        mock_user_svc = MagicMock()
        mock_user_svc.get_or_create_user = AsyncMock(return_value="user_123")

        data = {
            "headers": {"req_id": "req_card"},
            "body": {
                "from": {"userid": "wx_user"},
                "chatid": "chat_card",
                "chattype": "single",
                "event": {
                    "template_card_event": {
                        "event_key": "start_chat",
                        "task_id": "task_001",
                        "card_type": "button_interaction",
                    },
                },
            },
        }

        with (
            patch(
                "services.wecom.user_mapping_service.WecomUserMappingService",
                return_value=mock_user_svc,
            ),
            patch(
                "services.wecom.card_event_handler.WecomCardEventHandler",
                return_value=mock_handler_instance,
            ),
        ):
            await handler(data)

        mock_handler_instance.handle.assert_called_once()
        call_kwargs = mock_handler_instance.handle.call_args[1]
        assert call_kwargs["event_key"] == "start_chat"
        assert call_kwargs["task_id"] == "task_001"
        assert call_kwargs["org_id"] == "org-1"


# ============================================================
# TestSignalHandling — main() 信号处理
# ============================================================


class TestSignalHandling:
    """信号处理：SIGTERM/SIGINT 触发优雅关闭"""

    @pytest.mark.asyncio
    async def test_signal_sets_stop_event(self):
        """信号处理器调用 stop_event.set()"""
        mock_stop_event = MagicMock()
        mock_stop_event.wait = AsyncMock()
        mock_stop_event.set = MagicMock()

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()
        mock_manager.stop = AsyncMock()
        mock_manager.clients = {}

        registered_handlers = {}

        def fake_add_signal_handler(sig, handler):
            registered_handlers[sig] = handler

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomWSManager", return_value=mock_manager),
            patch("asyncio.Event", return_value=mock_stop_event),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.add_signal_handler = fake_add_signal_handler

            from wecom_ws_runner import main
            await main()

        # 验证注册了 SIGINT 和 SIGTERM
        assert signal.SIGINT in registered_handlers
        assert signal.SIGTERM in registered_handlers

        # 触发信号处理器 -> 应调用 stop_event.set()
        registered_handlers[signal.SIGTERM]()
        mock_stop_event.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_manager_stopped_on_shutdown(self):
        """关闭流程调用 manager.stop()"""
        mock_stop_event = MagicMock()
        mock_stop_event.wait = AsyncMock()

        mock_manager = MagicMock()
        mock_manager.start = AsyncMock()
        mock_manager.stop = AsyncMock()
        mock_manager.clients = {"org-1": MagicMock()}

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomWSManager", return_value=mock_manager),
            patch("asyncio.Event", return_value=mock_stop_event),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.add_signal_handler = MagicMock()

            from wecom_ws_runner import main
            await main()

        mock_manager.start.assert_called_once()
        mock_manager.stop.assert_called_once()
