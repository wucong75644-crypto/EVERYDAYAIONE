"""
wecom_ws_runner 单元测试

覆盖：配置缺失提前退出、_on_message 消息解析（text/voice/缺失字段）、
      信号处理触发关闭
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


# ============================================================
# 公共 fixtures
# ============================================================


def _make_settings(**overrides):
    """构造 mock settings 对象"""
    defaults = {
        "wecom_bot_id": "bot_123",
        "wecom_bot_secret": "secret_456",
        "wecom_corp_id": "corp_789",
        "wecom_bot_enabled": True,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


async def _capture_callbacks():
    """启动 main() 并捕获 on_message / on_card_event 回调"""
    mock_settings = _make_settings()
    mock_msg_svc = MagicMock()
    mock_msg_svc.handle_message = AsyncMock()

    mock_ws_instance = MagicMock()
    mock_ws_instance.start = AsyncMock()
    mock_ws_instance.stop = AsyncMock()

    callbacks = {}

    def capture_ws_client(**kwargs):
        callbacks["on_message"] = kwargs.get("on_message")
        callbacks["on_card_event"] = kwargs.get("on_card_event")
        return mock_ws_instance

    with (
        patch("wecom_ws_runner.setup_logging"),
        patch("wecom_ws_runner.get_settings", return_value=mock_settings),
        patch("wecom_ws_runner.get_db", return_value=MagicMock()),
        patch("wecom_ws_runner.WecomMessageService", return_value=mock_msg_svc),
        patch("wecom_ws_runner.WecomWSClient", side_effect=capture_ws_client),
        patch("asyncio.Event") as mock_event_cls,
    ):
        mock_event = MagicMock()
        mock_event.wait = AsyncMock()
        mock_event_cls.return_value = mock_event

        with patch.object(
            asyncio.get_event_loop(), "add_signal_handler", create=True,
        ):
            from wecom_ws_runner import main
            await main()

    return callbacks, mock_msg_svc


# ============================================================
# TestMainExitEarly — 配置缺失提前退出
# ============================================================


class TestMainExitEarly:
    """main() 在配置缺失时应提前退出，不启动 WS 客户端"""

    @pytest.mark.asyncio
    async def test_exit_when_bot_id_missing(self):
        """缺少 WECOM_BOT_ID → 直接 return"""
        mock_settings = _make_settings(wecom_bot_id="", wecom_bot_secret="secret")

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db") as mock_db,
            patch("wecom_ws_runner.WecomWSClient") as mock_ws_cls,
        ):
            from wecom_ws_runner import main
            await main()

            mock_db.assert_not_called()
            mock_ws_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_exit_when_bot_secret_missing(self):
        """缺少 WECOM_BOT_SECRET → 直接 return"""
        mock_settings = _make_settings(wecom_bot_id="bot", wecom_bot_secret="")

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db") as mock_db,
            patch("wecom_ws_runner.WecomWSClient") as mock_ws_cls,
        ):
            from wecom_ws_runner import main
            await main()

            mock_db.assert_not_called()
            mock_ws_cls.assert_not_called()


# ============================================================
# TestOnMessage — _on_message 消息解析
# ============================================================


class TestOnMessage:
    """_on_message 回调：解析不同消息类型并调用 handle_message"""

    @pytest.mark.asyncio
    async def test_text_message_parsed_correctly(self):
        """text 消息 → text_content 正确提取"""
        mock_settings = _make_settings()
        mock_db = MagicMock()
        mock_msg_svc = MagicMock()
        mock_msg_svc.handle_message = AsyncMock()

        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()
        mock_ws_instance.stop = AsyncMock()

        captured_callback = None

        def capture_ws_client(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("on_message")
            return mock_ws_instance

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db", return_value=mock_db),
            patch("wecom_ws_runner.WecomMessageService", return_value=mock_msg_svc),
            patch("wecom_ws_runner.WecomWSClient", side_effect=capture_ws_client),
            patch("asyncio.Event") as mock_event_cls,
        ):
            # 让 stop_event.wait() 立即返回
            mock_event = MagicMock()
            mock_event.wait = AsyncMock()
            mock_event.set = MagicMock()
            mock_event_cls.return_value = mock_event

            # 替换 signal handler 注册（避免测试环境问题）
            with patch.object(asyncio.get_event_loop(), "add_signal_handler", create=True):
                from wecom_ws_runner import main
                await main()

        assert captured_callback is not None

        # 模拟收到 text 消息
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

        await captured_callback(text_data)

        mock_msg_svc.handle_message.assert_called_once()
        msg_arg = mock_msg_svc.handle_message.call_args[0][0]
        assert msg_arg.text_content == "你好"
        assert msg_arg.msgtype == "text"
        assert msg_arg.wecom_userid == "user_abc"
        assert msg_arg.channel == "smart_robot"
        assert msg_arg.corp_id == "corp_789"

        reply_arg = mock_msg_svc.handle_message.call_args[0][1]
        assert reply_arg.channel == "smart_robot"
        assert reply_arg.req_id == "req_001"

    @pytest.mark.asyncio
    async def test_voice_message_parsed_correctly(self):
        """voice 消息 → voice content 正确提取"""
        mock_settings = _make_settings()
        mock_msg_svc = MagicMock()
        mock_msg_svc.handle_message = AsyncMock()

        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()
        mock_ws_instance.stop = AsyncMock()

        captured_callback = None

        def capture_ws_client(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("on_message")
            return mock_ws_instance

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomMessageService", return_value=mock_msg_svc),
            patch("wecom_ws_runner.WecomWSClient", side_effect=capture_ws_client),
            patch("asyncio.Event") as mock_event_cls,
        ):
            mock_event = MagicMock()
            mock_event.wait = AsyncMock()
            mock_event_cls.return_value = mock_event

            with patch.object(asyncio.get_event_loop(), "add_signal_handler", create=True):
                from wecom_ws_runner import main
                await main()

        voice_data = {
            "headers": {"req_id": "req_002"},
            "body": {
                "msgid": "msg_002",
                "from": {"userid": "user_xyz"},
                "chatid": "chat_002",
                "chattype": "single",
                "msgtype": "voice",
                "voice": {"content": "语音转文字内容"},
            },
        }

        await captured_callback(voice_data)

        msg_arg = mock_msg_svc.handle_message.call_args[0][0]
        assert msg_arg.text_content == "语音转文字内容"
        assert msg_arg.msgtype == "voice"

    @pytest.mark.asyncio
    async def test_missing_fields_no_crash(self):
        """缺失字段 → 不崩溃，text_content 为 None"""
        mock_settings = _make_settings()
        mock_msg_svc = MagicMock()
        mock_msg_svc.handle_message = AsyncMock()

        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()
        mock_ws_instance.stop = AsyncMock()

        captured_callback = None

        def capture_ws_client(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("on_message")
            return mock_ws_instance

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomMessageService", return_value=mock_msg_svc),
            patch("wecom_ws_runner.WecomWSClient", side_effect=capture_ws_client),
            patch("asyncio.Event") as mock_event_cls,
        ):
            mock_event = MagicMock()
            mock_event.wait = AsyncMock()
            mock_event_cls.return_value = mock_event

            with patch.object(asyncio.get_event_loop(), "add_signal_handler", create=True):
                from wecom_ws_runner import main
                await main()

        # 最小化消息：缺少 from、text 等字段
        minimal_data = {
            "headers": {},
            "body": {
                "msgid": "msg_003",
                "msgtype": "image",
            },
        }

        await captured_callback(minimal_data)

        msg_arg = mock_msg_svc.handle_message.call_args[0][0]
        assert msg_arg.text_content is None
        assert msg_arg.wecom_userid == ""
        assert msg_arg.chatid == ""

    @pytest.mark.asyncio
    async def test_empty_body_no_crash(self):
        """body 为空 → 不崩溃"""
        mock_settings = _make_settings()
        mock_msg_svc = MagicMock()
        mock_msg_svc.handle_message = AsyncMock()

        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()
        mock_ws_instance.stop = AsyncMock()

        captured_callback = None

        def capture_ws_client(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("on_message")
            return mock_ws_instance

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomMessageService", return_value=mock_msg_svc),
            patch("wecom_ws_runner.WecomWSClient", side_effect=capture_ws_client),
            patch("asyncio.Event") as mock_event_cls,
        ):
            mock_event = MagicMock()
            mock_event.wait = AsyncMock()
            mock_event_cls.return_value = mock_event

            with patch.object(asyncio.get_event_loop(), "add_signal_handler", create=True):
                from wecom_ws_runner import main
                await main()

        await captured_callback({"headers": {}, "body": {}})

        msg_arg = mock_msg_svc.handle_message.call_args[0][0]
        assert msg_arg.msgid == ""
        assert msg_arg.msgtype == ""


# ============================================================
# TestSignalHandling — 信号处理
# ============================================================


class TestSignalHandling:
    """信号处理：SIGTERM/SIGINT 触发优雅关闭"""

    @pytest.mark.asyncio
    async def test_signal_sets_stop_event(self):
        """信号处理器调用 stop_event.set()"""
        mock_settings = _make_settings()
        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()
        mock_ws_instance.stop = AsyncMock()

        registered_handlers = {}

        def fake_add_signal_handler(sig, handler):
            registered_handlers[sig] = handler

        mock_stop_event = MagicMock()
        mock_stop_event.wait = AsyncMock()
        mock_stop_event.set = MagicMock()

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomMessageService", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomWSClient", return_value=mock_ws_instance),
            patch("asyncio.Event", return_value=mock_stop_event),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.add_signal_handler = fake_add_signal_handler

            from wecom_ws_runner import main
            await main()

        # 验证注册了 SIGINT 和 SIGTERM
        assert signal.SIGINT in registered_handlers
        assert signal.SIGTERM in registered_handlers

        # 触发信号处理器 → 应调用 stop_event.set()
        registered_handlers[signal.SIGTERM]()
        mock_stop_event.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_ws_client_stopped_on_shutdown(self):
        """关闭流程调用 ws_client.stop()"""
        mock_settings = _make_settings()
        mock_ws_instance = MagicMock()
        mock_ws_instance.start = AsyncMock()
        mock_ws_instance.stop = AsyncMock()

        mock_stop_event = MagicMock()
        mock_stop_event.wait = AsyncMock()

        with (
            patch("wecom_ws_runner.setup_logging"),
            patch("wecom_ws_runner.get_settings", return_value=mock_settings),
            patch("wecom_ws_runner.get_db", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomMessageService", return_value=MagicMock()),
            patch("wecom_ws_runner.WecomWSClient", return_value=mock_ws_instance),
            patch("asyncio.Event", return_value=mock_stop_event),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.add_signal_handler = MagicMock()

            from wecom_ws_runner import main
            await main()

        mock_ws_instance.start.assert_called_once()
        mock_ws_instance.stop.assert_called_once()


# ============================================================
# TestOnMessageImage — IMAGE 消息解析
# ============================================================


class TestOnMessageImage:
    """_on_message: image 消息 → image_urls + aeskeys 正确提取"""

    @pytest.mark.asyncio
    async def test_image_with_aeskey(self):
        """image 消息 → image_urls / aeskeys 正确"""
        callbacks, mock_svc = await _capture_callbacks()

        data = {
            "headers": {"req_id": "req_img"},
            "body": {
                "msgid": "msg_img",
                "from": {"userid": "u1"},
                "chatid": "c1",
                "chattype": "single",
                "msgtype": "image",
                "image": {
                    "url": "https://img.example.com/pic.jpg",
                    "aeskey": "abc123key",
                },
            },
        }

        await callbacks["on_message"](data)

        msg = mock_svc.handle_message.call_args[0][0]
        assert msg.msgtype == "image"
        assert msg.image_urls == ["https://img.example.com/pic.jpg"]
        assert msg.aeskeys == {"https://img.example.com/pic.jpg": "abc123key"}

    @pytest.mark.asyncio
    async def test_image_without_aeskey(self):
        """image 消息无 aeskey → aeskeys 为空"""
        callbacks, mock_svc = await _capture_callbacks()

        data = {
            "headers": {"req_id": "req_img2"},
            "body": {
                "msgid": "msg_img2",
                "from": {"userid": "u1"},
                "chatid": "c1",
                "chattype": "single",
                "msgtype": "image",
                "image": {"url": "https://img.example.com/pic2.jpg"},
            },
        }

        await callbacks["on_message"](data)

        msg = mock_svc.handle_message.call_args[0][0]
        assert msg.image_urls == ["https://img.example.com/pic2.jpg"]
        assert msg.aeskeys == {}


# ============================================================
# TestOnMessageFile — FILE 消息解析
# ============================================================


class TestOnMessageFile:
    """_on_message: file 消息 → file_url / file_name / aeskeys"""

    @pytest.mark.asyncio
    async def test_file_message(self):
        """file 消息 → file_url / file_name 正确"""
        callbacks, mock_svc = await _capture_callbacks()

        data = {
            "headers": {"req_id": "req_file"},
            "body": {
                "msgid": "msg_file",
                "from": {"userid": "u1"},
                "chatid": "c1",
                "chattype": "single",
                "msgtype": "file",
                "file": {
                    "url": "https://file.example.com/doc.pdf",
                    "name": "report.pdf",
                    "aeskey": "filekey",
                },
            },
        }

        await callbacks["on_message"](data)

        msg = mock_svc.handle_message.call_args[0][0]
        assert msg.msgtype == "file"
        assert msg.file_url == "https://file.example.com/doc.pdf"
        assert msg.file_name == "report.pdf"
        assert msg.aeskeys == {"https://file.example.com/doc.pdf": "filekey"}


# ============================================================
# TestOnMessageVideo — VIDEO 消息解析
# ============================================================


class TestOnMessageVideo:
    """_on_message: video 消息 → file_url / file_name / aeskeys"""

    @pytest.mark.asyncio
    async def test_video_message(self):
        """video 消息 → file_url / file_name 正确"""
        callbacks, mock_svc = await _capture_callbacks()

        data = {
            "headers": {"req_id": "req_vid"},
            "body": {
                "msgid": "msg_vid",
                "from": {"userid": "u1"},
                "chatid": "c1",
                "chattype": "single",
                "msgtype": "video",
                "video": {
                    "url": "https://vid.example.com/clip.mp4",
                    "name": "clip.mp4",
                    "aeskey": "vidkey",
                },
            },
        }

        await callbacks["on_message"](data)

        msg = mock_svc.handle_message.call_args[0][0]
        assert msg.msgtype == "video"
        assert msg.file_url == "https://vid.example.com/clip.mp4"
        assert msg.file_name == "clip.mp4"
        assert msg.aeskeys == {"https://vid.example.com/clip.mp4": "vidkey"}


# ============================================================
# TestOnMessageMixed — MIXED 消息解析
# ============================================================


class TestOnMessageMixed:
    """_on_message: mixed 消息 → text + images 正确提取"""

    @pytest.mark.asyncio
    async def test_mixed_text_and_images(self):
        """mixed 消息 → text_content + image_urls 合并"""
        callbacks, mock_svc = await _capture_callbacks()

        data = {
            "headers": {"req_id": "req_mix"},
            "body": {
                "msgid": "msg_mix",
                "from": {"userid": "u1"},
                "chatid": "c1",
                "chattype": "single",
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
            },
        }

        await callbacks["on_message"](data)

        msg = mock_svc.handle_message.call_args[0][0]
        assert msg.msgtype == "mixed"
        assert msg.text_content == "看这个图"
        assert len(msg.image_urls) == 2
        assert "https://img1.example.com/a.jpg" in msg.image_urls
        assert "https://img2.example.com/b.jpg" in msg.image_urls
        assert msg.aeskeys.get("https://img1.example.com/a.jpg") == "key1"
        assert "https://img2.example.com/b.jpg" not in msg.aeskeys


# ============================================================
# TestOnCardEvent — 卡片事件回调
# ============================================================


class TestOnCardEvent:
    """_on_card_event 回调：解析卡片事件并分发到 handler"""

    @pytest.mark.asyncio
    async def test_card_event_callback_captured(self):
        """main() 启动时 on_card_event 被注册"""
        callbacks, _ = await _capture_callbacks()
        assert callbacks["on_card_event"] is not None

    @pytest.mark.asyncio
    async def test_card_event_calls_handler(self):
        """卡片事件 → 调用 CardEventHandler.handle"""
        callbacks, mock_msg_svc = await _capture_callbacks()

        mock_handler_instance = MagicMock()
        mock_handler_instance.handle = AsyncMock()

        mock_user_svc = MagicMock()
        mock_user_svc.get_or_create_user = AsyncMock(return_value="user_123")

        # mock msg_svc 的 _get_or_create_conversation
        mock_msg_svc._get_or_create_conversation = AsyncMock(
            return_value="conv_123"
        )

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

        # _on_card_event 内部用 from ... import 导入，patch 源模块
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
            await callbacks["on_card_event"](data)

        mock_handler_instance.handle.assert_called_once()
        call_kwargs = mock_handler_instance.handle.call_args[1]
        assert call_kwargs["event_key"] == "start_chat"
        assert call_kwargs["task_id"] == "task_001"
