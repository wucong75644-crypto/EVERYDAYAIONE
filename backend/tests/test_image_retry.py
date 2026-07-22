"""
Image Handler 智能重试测试

覆盖：
异步重试（AsyncRetryService — Webhook 报告失败时）
  - smart_mode + Webhook 失败 → 换模型重提交
  - 非 smart_mode → 不重试
  - 超过重试上限 → 不重试
  - 非 image/video 类型 → 不重试
  - 路由失败 → 不重试
  - 重提交失败 → 不重试
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType
from services.adapters.base import ImageGenerateResult, TaskStatus
from services.async_retry_service import AsyncRetryService
from services.intent_router import RoutingDecision


# ============================================================
# Fixtures
# ============================================================

# ============================================================
# 异步重试测试（AsyncRetryService）
# ============================================================

class TestAsyncRetryService:

    @pytest.fixture
    def retry_db(self):
        """AsyncRetryService 用的 mock DB"""
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

    def _make_task(self, **overrides):
        """创建测试 task dict"""
        task = {
            "external_task_id": "ext_001",
            "type": "image",
            "user_id": "user_1",
            "model_id": "nano-banana",
            "credits_locked": 5,
            "credit_transaction_id": "old_tx",
            "request_params": {
                "prompt": "画一只猫",
                "_is_smart_mode": True,
                "aspect_ratio": "1:1",
                "output_format": "png",
            },
        }
        task.update(overrides)
        return task

    def _make_fail_result(self, msg="API error"):
        return ImageGenerateResult(
            task_id="ext_001",
            status=TaskStatus.FAILED,
            fail_code="ERROR",
            fail_msg=msg,
        )

    @pytest.mark.asyncio
    async def test_smart_retry_succeeds(self, svc):
        """smart_mode + Webhook 失败 → 换模型重提交 → 返回 True"""
        task = self._make_task()
        result = self._make_fail_result()

        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            recommended_model="flux-1-schnell",
            routed_by="model",
        )

        mock_adapter = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")
        mock_adapter.generate = AsyncMock(
            return_value=MagicMock(task_id="new_ext_002")
        )

        with patch("services.intent_router.IntentRouter") as MockRouter, \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
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
    async def test_not_smart_mode_no_retry(self, svc):
        """非 smart_mode → 不重试"""
        task = self._make_task(request_params={"prompt": "test"})
        result = self._make_fail_result()

        retried = await svc.attempt_retry(task, result)
        assert retried is False

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, svc):
        """已达重试上限 → 不重试"""
        task = self._make_task(request_params={
            "prompt": "test",
            "_is_smart_mode": True,
            "_retry_count": 2,
        })
        result = self._make_fail_result()

        retried = await svc.attempt_retry(task, result)
        assert retried is False

    @pytest.mark.asyncio
    async def test_non_media_type_no_retry(self, svc):
        """非 image/video 类型 → 不重试"""
        task = self._make_task(type="chat")
        result = self._make_fail_result()

        retried = await svc.attempt_retry(task, result)
        assert retried is False

    @pytest.mark.asyncio
    async def test_route_retry_returns_none(self, svc):
        """路由返回 None → 不重试"""
        task = self._make_task()
        result = self._make_fail_result()

        with patch("services.intent_router.IntentRouter") as MockRouter:
            mock_router = AsyncMock()
            mock_router.route_retry = AsyncMock(return_value=None)
            MockRouter.return_value = mock_router

            retried = await svc.attempt_retry(task, result)
            assert retried is False

    @pytest.mark.asyncio
    async def test_route_retry_exception(self, svc):
        """路由抛异常 → 不重试"""
        task = self._make_task()
        result = self._make_fail_result()

        with patch("services.intent_router.IntentRouter") as MockRouter:
            mock_router = AsyncMock()
            mock_router.route_retry = AsyncMock(
                side_effect=Exception("router error")
            )
            MockRouter.return_value = mock_router

            retried = await svc.attempt_retry(task, result)
            assert retried is False

    @pytest.mark.asyncio
    async def test_resubmit_fails(self, svc):
        """重提交失败 → 返回 False"""
        task = self._make_task()
        result = self._make_fail_result()

        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            recommended_model="flux-1-schnell",
            routed_by="model",
        )

        mock_adapter = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")
        mock_adapter.generate = AsyncMock(
            side_effect=Exception("adapter error")
        )

        with patch("services.intent_router.IntentRouter") as MockRouter, \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
             patch("services.handlers.base.BaseHandler._build_callback_url", return_value="http://cb"), \
             patch("services.handlers.mixins.CreditMixin._lock_credits", return_value="new_tx"), \
             patch("services.handlers.mixins.CreditMixin._refund_credits"):

            mock_router = AsyncMock()
            mock_router.route_retry = AsyncMock(return_value=decision)
            MockRouter.return_value = mock_router

            retried = await svc.attempt_retry(task, result)
            assert retried is False

    @pytest.mark.asyncio
    async def test_string_request_params_parsed(self, svc):
        """request_params 为 JSON 字符串时能正确解析"""
        params = json.dumps({
            "prompt": "test",
            "_is_smart_mode": True,
        })
        task = self._make_task(request_params=params)
        result = self._make_fail_result()

        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            recommended_model="flux-1-schnell",
            routed_by="model",
        )

        mock_adapter = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")
        mock_adapter.generate = AsyncMock(
            return_value=MagicMock(task_id="new_ext")
        )

        with patch("services.intent_router.IntentRouter") as MockRouter, \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
             patch("services.handlers.base.BaseHandler._build_callback_url", return_value="http://cb"), \
             patch("services.handlers.mixins.CreditMixin._lock_credits", return_value="new_tx"), \
             patch("services.handlers.mixins.CreditMixin._refund_credits"):

            mock_router = AsyncMock()
            mock_router.route_retry = AsyncMock(return_value=decision)
            MockRouter.return_value = mock_router

            retried = await svc.attempt_retry(task, result)
            assert retried is True
