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
            patch("wecom_ws_runner.get_supabase_client") as mock_db,
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
            patch("wecom_ws_runner.get_supabase_client") as mock_db,
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
            patch("wecom_ws_runner.get_supabase_client", return_value=mock_db),
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
            patch("wecom_ws_runner.get_supabase_client", return_value=MagicMock()),
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
            patch("wecom_ws_runner.get_supabase_client", return_value=MagicMock()),
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
            patch("wecom_ws_runner.get_supabase_client", return_value=MagicMock()),
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
            patch("wecom_ws_runner.get_supabase_client", return_value=MagicMock()),
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
            patch("wecom_ws_runner.get_supabase_client", return_value=MagicMock()),
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
