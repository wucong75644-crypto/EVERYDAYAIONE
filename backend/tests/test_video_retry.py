"""
Video Handler 智能重试测试

覆盖：
- smart_mode + API 失败 → 换模型重试 → 成功
- smart_mode + 重试也失败 → 抛出原始异常
- 非 smart_mode → 不重试
- route_retry 返回 None → 不重试
- 重试时 adapter 正确关闭 + 积分正确退回
- AsyncRetryService 对 video 类型的支持
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType
from services.adapters.base import VideoGenerateResult, TaskStatus
from services.async_retry_service import AsyncRetryService
from services.handlers.video_handler import VideoHandler
from services.intent_router import RoutingDecision


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_db():
    db = MagicMock()
    task_chain = MagicMock()
    task_chain.insert.return_value = task_chain
    task_chain.update.return_value = task_chain
    task_chain.eq.return_value = task_chain
    task_chain.execute.return_value = MagicMock(data=[])

    def table_dispatch(name):
        if name == "users":
            user_chain = MagicMock()
            user_chain.select.return_value = user_chain
            user_chain.eq.return_value = user_chain
            user_chain.single.return_value = user_chain
            user_chain.maybe_single.return_value = user_chain
            user_chain.execute.return_value = MagicMock(
                data={"id": "user_1", "credits": 1000, "status": "active"}
            )
            return user_chain
        if name == "credit_transactions":
            tx_chain = MagicMock()
            tx_chain.insert.return_value = tx_chain
            tx_chain.update.return_value = tx_chain
            tx_chain.select.return_value = tx_chain
            tx_chain.eq.return_value = tx_chain
            tx_chain.single.return_value = tx_chain
            tx_chain.maybe_single.return_value = tx_chain
            tx_chain.execute.return_value = MagicMock(
                data={"id": "tx_mock", "status": "pending", "amount": 10}
            )
            return tx_chain
        return task_chain

    db.table.side_effect = table_dispatch
    db.rpc.return_value.execute.return_value = MagicMock(
        data={"success": True, "new_balance": 90}
    )
    return db


@pytest.fixture
def handler(mock_db):
    return VideoHandler(mock_db)


# ============================================================
# A. 同步重试测试（_attempt_video_sync_retry）
# ============================================================


class TestVideoSyncRetry:

    @pytest.mark.asyncio
    async def test_smart_mode_retry_succeeds(self, handler):
        """smart_mode + API 失败 → 换模型重试 → 返回 client_task_id"""
        decision = RoutingDecision(
            generation_type=GenerationType.VIDEO,
            recommended_model="kling-video",
            routed_by="model",
        )

        mock_new_adapter = AsyncMock()
        mock_new_adapter.provider = MagicMock(value="kie")
        mock_new_adapter.generate = AsyncMock(
            return_value=MagicMock(task_id="new_video_ext")
        )

        with patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=decision), \
             patch("services.adapters.factory.create_video_adapter", return_value=mock_new_adapter), \
             patch.object(handler, "_build_callback_url", return_value="http://cb"), \
             patch.object(handler, "_lock_credits", return_value="tx_new"), \
             patch.object(handler, "_save_task"):

            result = await handler._attempt_video_sync_retry(
                prompt="一只猫在跳舞",
                model_id="sora-2-text-to-video",
                error="API timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "一只猫在跳舞", "aspect_ratio": "16:9"},
                user_id="user_1",
                credits_to_lock=10,
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(client_task_id="client_task_1"),
            )

            assert result == "client_task_1"
            mock_new_adapter.generate.assert_awaited_once()
            mock_new_adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_smart_mode_retry_also_fails(self, handler):
        """smart_mode + 重试也失败 → 返回 None"""
        decision = RoutingDecision(
            generation_type=GenerationType.VIDEO,
            recommended_model="kling-video",
            routed_by="model",
        )

        mock_new_adapter = AsyncMock()
        mock_new_adapter.provider = MagicMock(value="kie")
        mock_new_adapter.generate = AsyncMock(
            side_effect=Exception("retry also failed")
        )

        with patch.object(handler, "_route_retry", new_callable=AsyncMock, side_effect=[decision, None]), \
             patch("services.adapters.factory.create_video_adapter", return_value=mock_new_adapter), \
             patch.object(handler, "_build_callback_url", return_value="http://cb"), \
             patch.object(handler, "_lock_credits", return_value="tx_new"), \
             patch.object(handler, "_refund_credits") as mock_refund:

            result = await handler._attempt_video_sync_retry(
                prompt="一只猫在跳舞",
                model_id="sora-2-text-to-video",
                error="API timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "一只猫在跳舞"},
                user_id="user_1",
                credits_to_lock=10,
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(client_task_id="client_task_1"),
            )

            assert result is None
            mock_refund.assert_called_once_with("tx_new")

    @pytest.mark.asyncio
    async def test_not_smart_mode_no_retry(self, handler):
        """非 smart_mode → 直接返回 None"""
        result = await handler._attempt_video_sync_retry(
            prompt="test",
            model_id="sora-2",
            error="error",
            params={},
            generate_kwargs={"prompt": "test"},
            user_id="user_1",
            credits_to_lock=10,
            message_id="msg_1",
            conversation_id="conv_1",
            metadata=MagicMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_route_retry_returns_none(self, handler):
        """route_retry 返回 None → 不重试"""
        with patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=None):
            result = await handler._attempt_video_sync_retry(
                prompt="test",
                model_id="sora-2",
                error="error",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "test"},
                user_id="user_1",
                credits_to_lock=10,
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(),
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_retry_adapter_closed(self, handler):
        """重试时 adapter 应被关闭"""
        decision = RoutingDecision(
            generation_type=GenerationType.VIDEO,
            recommended_model="kling-video",
            routed_by="model",
        )

        mock_new_adapter = AsyncMock()
        mock_new_adapter.provider = MagicMock(value="kie")
        mock_new_adapter.generate = AsyncMock(
            side_effect=Exception("fail")
        )

        with patch.object(handler, "_route_retry", new_callable=AsyncMock, side_effect=[decision, None]), \
             patch("services.adapters.factory.create_video_adapter", return_value=mock_new_adapter), \
             patch.object(handler, "_build_callback_url", return_value="http://cb"), \
             patch.object(handler, "_lock_credits", return_value="tx_new"), \
             patch.object(handler, "_refund_credits"):

            await handler._attempt_video_sync_retry(
                prompt="test",
                model_id="sora-2",
                error="timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "test"},
                user_id="user_1",
                credits_to_lock=10,
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(),
            )

            mock_new_adapter.close.assert_awaited_once()


# ============================================================
# B. 异步重试测试（AsyncRetryService — video 类型）
# ============================================================


class TestAsyncRetryServiceVideo:

    @pytest.fixture
    def retry_db(self):
        db = MagicMock()
        task_chain = MagicMock()
        task_chain.update.return_value = task_chain
        task_chain.eq.return_value = task_chain
        task_chain.execute.return_value = MagicMock(data=[])
        db.table.return_value = task_chain
        db.rpc.return_value.execute.return_value = MagicMock(
            data={"success": True}
        )
        return db

    @pytest.fixture
    def svc(self, retry_db):
        return AsyncRetryService(retry_db)

    def _make_video_task(self, **overrides):
        task = {
            "external_task_id": "vid_001",
            "type": "video",
            "user_id": "user_1",
            "model_id": "sora-2-text-to-video",
            "credits_locked": 10,
            "credit_transaction_id": "old_tx",
            "request_params": {
                "prompt": "一只猫在跳舞",
                "_is_smart_mode": True,
                "aspect_ratio": "16:9",
            },
        }
        task.update(overrides)
        return task

    def _make_fail_result(self):
        return VideoGenerateResult(
            task_id="vid_001",
            status=TaskStatus.FAILED,
            fail_code="ERROR",
            fail_msg="generation failed",
        )

    @pytest.mark.asyncio
    async def test_video_async_retry_succeeds(self, svc):
        """video 类型 Webhook 失败 → 换模型重提交"""
        task = self._make_video_task()
        result = self._make_fail_result()

        decision = RoutingDecision(
            generation_type=GenerationType.VIDEO,
            recommended_model="kling-video",
            routed_by="model",
        )

        mock_adapter = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")
        mock_adapter.generate = AsyncMock(
            return_value=MagicMock(task_id="new_vid_002")
        )

        with patch("services.intent_router.IntentRouter") as MockRouter, \
             patch("services.adapters.factory.create_video_adapter", return_value=mock_adapter), \
             patch("services.handlers.base.BaseHandler._build_callback_url", return_value="http://cb"), \
             patch("services.handlers.mixins.CreditMixin._lock_credits", return_value="new_tx"), \
             patch("services.handlers.mixins.CreditMixin._refund_credits"):

            mock_router = AsyncMock()
            mock_router.route_retry = AsyncMock(return_value=decision)
            MockRouter.return_value = mock_router

            retried = await svc.attempt_retry(task, result)
            assert retried is True
            mock_adapter.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_video_not_smart_mode(self, svc):
        """非 smart_mode video → 不重试"""
        task = self._make_video_task(request_params={
            "prompt": "test",
        })
        result = self._make_fail_result()

        retried = await svc.attempt_retry(task, result)
        assert retried is False
