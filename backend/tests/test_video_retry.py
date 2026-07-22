"""
Video 异步重试服务测试

覆盖：
- AsyncRetryService 对 video 类型的支持
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType
from services.adapters.base import VideoGenerateResult, TaskStatus
from services.async_retry_service import AsyncRetryService
from services.intent_router import RoutingDecision


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
