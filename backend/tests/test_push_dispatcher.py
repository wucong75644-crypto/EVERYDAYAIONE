"""测试 PushDispatcher（定时任务推送分发器）"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler.push_dispatcher import (
    PushDispatcher,
    WECOM_PROACTIVE_CHANNEL,
)


@pytest.fixture
def dispatcher():
    return PushDispatcher()


# ════════════════════════════════════════════════════════
# 1. dispatch — 路由
# ════════════════════════════════════════════════════════

class TestDispatch:

    @pytest.mark.asyncio
    async def test_unknown_target_skipped(self, dispatcher):
        result = await dispatcher.dispatch(
            org_id="org_1",
            target={"type": "unknown_type"},
            text="hi",
            files=[],
        )
        assert result == "skipped"

    @pytest.mark.asyncio
    async def test_wecom_group_calls_publish(self, dispatcher):
        with patch.object(
            dispatcher, "_publish_to_ws_runner", new=AsyncMock(return_value=True)
        ) as mock_pub:
            result = await dispatcher.dispatch(
                org_id="org_1",
                target={"type": "wecom_group", "chatid": "chat_xxx"},
                text="日报内容",
                files=[],
            )

        assert result == "pushed"
        mock_pub.assert_called_once()
        payload = mock_pub.call_args[0][0]
        assert payload["org_id"] == "org_1"
        assert payload["chatid"] == "chat_xxx"
        # 企微 aibot_send_msg 不需要 chat_type，服务器通过 chatid 自动判断
        assert "chat_type" not in payload

    @pytest.mark.asyncio
    async def test_wecom_user_uses_userid_as_chatid(self, dispatcher):
        """单聊场景：userid 作为 chatid"""
        with patch.object(
            dispatcher, "_publish_to_ws_runner", new=AsyncMock(return_value=True)
        ) as mock_pub:
            await dispatcher.dispatch(
                org_id="org_1",
                target={"type": "wecom_user", "wecom_userid": "user_xxx"},
                text="hi",
                files=[],
            )

        payload = mock_pub.call_args[0][0]
        assert payload["chatid"] == "user_xxx"
        assert "chat_type" not in payload

    @pytest.mark.asyncio
    async def test_files_appended_to_markdown(self, dispatcher):
        with patch.object(
            dispatcher, "_publish_to_ws_runner", new=AsyncMock(return_value=True)
        ) as mock_pub:
            await dispatcher.dispatch(
                org_id="org_1",
                target={"type": "wecom_group", "chatid": "chat_xxx"},
                text="日报正文",
                files=[
                    {"url": "https://cdn.x.com/a.xlsx", "name": "销售日报.xlsx"},
                ],
            )

        payload = mock_pub.call_args[0][0]
        body = payload["content"]["content"]
        assert "日报正文" in body
        assert "📎 **附件：**" in body
        assert "[销售日报.xlsx](https://cdn.x.com/a.xlsx)" in body

    @pytest.mark.asyncio
    async def test_missing_chatid_returns_failed(self, dispatcher):
        result = await dispatcher.dispatch(
            org_id="org_1",
            target={"type": "wecom_group"},  # 没 chatid
            text="hi",
            files=[],
        )
        assert result == "push_failed"

    @pytest.mark.asyncio
    async def test_publish_failure_returns_failed(self, dispatcher):
        with patch.object(
            dispatcher, "_publish_to_ws_runner", new=AsyncMock(return_value=False)
        ):
            result = await dispatcher.dispatch(
                org_id="org_1",
                target={"type": "wecom_group", "chatid": "chat_xxx"},
                text="hi",
                files=[],
            )
        assert result == "push_failed"

    @pytest.mark.asyncio
    async def test_multi_target(self, dispatcher):
        """多目标：任一成功即视为整体成功"""
        # mock 第一个成功，第二个失败
        call_count = {"n": 0}

        async def fake_publish(payload):
            call_count["n"] += 1
            return call_count["n"] == 1

        with patch.object(
            dispatcher, "_publish_to_ws_runner", side_effect=fake_publish
        ):
            result = await dispatcher.dispatch(
                org_id="org_1",
                target={
                    "type": "multi",
                    "targets": [
                        {"type": "wecom_group", "chatid": "chat_a"},
                        {"type": "wecom_group", "chatid": "chat_b"},
                    ],
                },
                text="hi",
                files=[],
            )
        assert result == "pushed"

    @pytest.mark.asyncio
    async def test_web_target_calls_ws_manager(self, dispatcher):
        with patch("services.websocket_manager.ws_manager") as mock_wm:
            mock_wm.send_to_user = AsyncMock()
            result = await dispatcher.dispatch(
                org_id="org_1",
                target={"type": "web", "user_id": "user_1"},
                text="hi",
                files=[],
            )

        assert result == "pushed"
        mock_wm.send_to_user.assert_called_once()
        args = mock_wm.send_to_user.call_args
        assert args[0][0] == "user_1"
        assert args[0][1]["type"] == "scheduled_task_result"


# ════════════════════════════════════════════════════════
# 2. _publish_to_ws_runner — Redis 集成
# ════════════════════════════════════════════════════════

class TestPublishToWsRunner:

    @pytest.mark.asyncio
    async def test_redis_publish_called(self):
        dispatcher = PushDispatcher()

        # mock RedisClient.get_client
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()

        with patch("core.redis.RedisClient.get_client", new=AsyncMock(return_value=mock_client)):
            ok = await dispatcher._publish_to_ws_runner({
                "org_id": "org_1",
                "chatid": "chat_xxx",
                "msgtype": "markdown",
                "content": {"content": "hi"},
            })

        assert ok is True
        mock_client.publish.assert_called_once()
        channel = mock_client.publish.call_args[0][0]
        assert channel == WECOM_PROACTIVE_CHANNEL

    @pytest.mark.asyncio
    async def test_redis_failure_returns_false(self):
        dispatcher = PushDispatcher()
        with patch("core.redis.RedisClient.get_client", side_effect=RuntimeError("redis down")):
            ok = await dispatcher._publish_to_ws_runner({})
        assert ok is False
