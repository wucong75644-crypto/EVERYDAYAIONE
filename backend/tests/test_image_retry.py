"""
Image Handler 智能重试测试

覆盖：
A. 同步重试（_attempt_image_sync_retry — API 调用失败时）
  - smart_mode + API 失败 → 换模型重试 → 成功
  - smart_mode + 重试也失败 → 返回 None
  - 非 smart_mode → 不重试
  - 重试时积分正确锁定/退回
  - 重试时 adapter 正确关闭

B. 异步重试（AsyncRetryService — Webhook 报告失败时）
  - smart_mode + Webhook 失败 → 换模型重提交
  - 非 smart_mode → 不重试
  - 超过重试上限 → 不重试
  - 非 image/video 类型 → 不重试
  - 路由失败 → 不重试
  - 重提交失败 → 不重试
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType
from services.adapters.base import ImageGenerateResult, TaskStatus
from services.async_retry_service import AsyncRetryService
from services.handlers.image_handler import ImageHandler
from services.intent_router import RetryContext, RoutingDecision


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_db():
    """简化的 mock DB"""
    db = MagicMock()
    # tasks table chain
    task_chain = MagicMock()
    task_chain.insert.return_value = task_chain
    task_chain.update.return_value = task_chain
    task_chain.eq.return_value = task_chain
    task_chain.execute.return_value = MagicMock(data=[])
    db.table.return_value = task_chain

    # users lookup (for _check_balance)
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
                data={"id": "tx_mock", "status": "pending", "amount": 5}
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
    return ImageHandler(mock_db)


# ============================================================
# A. 同步重试测试（_attempt_image_sync_retry）
# ============================================================

class TestImageSyncRetry:

    @pytest.mark.asyncio
    async def test_smart_mode_retry_succeeds(self, handler):
        """smart_mode + API 失败 → 换模型重试 → 返回 ext_task_id"""
        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            recommended_model="flux-1-schnell",
            routed_by="model",
        )

        mock_new_adapter = AsyncMock()
        mock_new_adapter.provider = MagicMock(value="kie")
        mock_new_adapter.generate = AsyncMock(
            return_value=MagicMock(task_id="new_ext_123")
        )

        with patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=decision), \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_new_adapter), \
             patch.object(handler, "_build_callback_url", return_value="http://cb"), \
             patch.object(handler, "_lock_credits", return_value="tx_new"), \
             patch.object(handler, "_update_task_by_id"):

            result = await handler._attempt_image_sync_retry(
                prompt="画一只猫",
                model_id="nano-banana",
                error="API timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "画一只猫", "size": "1:1"},
                user_id="user_1",
                per_image_credits=5,
                index=0,
                batch_id="batch_1",
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(),
                local_task_id="local_task_1",
            )

            assert result == "new_ext_123"
            mock_new_adapter.generate.assert_awaited_once()
            mock_new_adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_smart_mode_retry_also_fails(self, handler):
        """smart_mode + 重试也失败 → 返回 None"""
        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            recommended_model="flux-1-schnell",
            routed_by="model",
        )

        mock_new_adapter = AsyncMock()
        mock_new_adapter.provider = MagicMock(value="kie")
        mock_new_adapter.generate = AsyncMock(
            side_effect=Exception("retry also failed")
        )

        # route_retry 第一次返回模型，第二次返回 None（已用完）
        with patch.object(handler, "_route_retry", new_callable=AsyncMock, side_effect=[decision, None]), \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_new_adapter), \
             patch.object(handler, "_build_callback_url", return_value="http://cb"), \
             patch.object(handler, "_lock_credits", return_value="tx_new"), \
             patch.object(handler, "_refund_credits") as mock_refund:

            result = await handler._attempt_image_sync_retry(
                prompt="画一只猫",
                model_id="nano-banana",
                error="API timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "画一只猫", "size": "1:1"},
                user_id="user_1",
                per_image_credits=5,
                index=0,
                batch_id="batch_1",
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(),
                local_task_id="local_task_1",
            )

            assert result is None
            # 重试失败时积分应退回
            mock_refund.assert_called_once_with("tx_new")

    @pytest.mark.asyncio
    async def test_not_smart_mode_no_retry(self, handler):
        """非 smart_mode → 不重试，直接返回 None"""
        result = await handler._attempt_image_sync_retry(
            prompt="画一只猫",
            model_id="nano-banana",
            error="API timeout",
            params={},  # 无 _is_smart_mode
            generate_kwargs={"prompt": "画一只猫"},
            user_id="user_1",
            per_image_credits=5,
            index=0,
            batch_id="batch_1",
            message_id="msg_1",
            conversation_id="conv_1",
            metadata=MagicMock(),
            local_task_id="local_task_1",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_route_retry_returns_none_no_retry(self, handler):
        """route_retry 返回 None → 不重试"""
        with patch.object(handler, "_route_retry", new_callable=AsyncMock, return_value=None):
            result = await handler._attempt_image_sync_retry(
                prompt="画一只猫",
                model_id="nano-banana",
                error="API timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "画一只猫"},
                user_id="user_1",
                per_image_credits=5,
                index=0,
                batch_id="batch_1",
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(),
                local_task_id="local_task_1",
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_retry_adapter_closed(self, handler):
        """重试时 adapter 应被关闭（无论成功还是失败）"""
        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            recommended_model="flux-1-schnell",
            routed_by="model",
        )

        mock_new_adapter = AsyncMock()
        mock_new_adapter.provider = MagicMock(value="kie")
        mock_new_adapter.generate = AsyncMock(
            side_effect=Exception("fail")
        )

        with patch.object(handler, "_route_retry", new_callable=AsyncMock, side_effect=[decision, None]), \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_new_adapter), \
             patch.object(handler, "_build_callback_url", return_value="http://cb"), \
             patch.object(handler, "_lock_credits", return_value="tx_new"), \
             patch.object(handler, "_refund_credits"):

            await handler._attempt_image_sync_retry(
                prompt="画一只猫",
                model_id="nano-banana",
                error="timeout",
                params={"_is_smart_mode": True},
                generate_kwargs={"prompt": "画一只猫", "size": "1:1"},
                user_id="user_1",
                per_image_credits=5,
                index=0,
                batch_id="batch_1",
                message_id="msg_1",
                conversation_id="conv_1",
                metadata=MagicMock(),
                local_task_id="local_task_1",
            )

            mock_new_adapter.close.assert_awaited_once()


# ============================================================
# B. 异步重试测试（AsyncRetryService）
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
            "id": "local_task_1",
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

    @pytest.mark.asyncio
    async def test_kie_image_fetch_fallback_reuses_same_model_with_staged_urls(self, svc, retry_db):
        source_urls = [
            "https://cdn.example.com/source-1.png",
            "https://cdn.example.com/source-2.png",
        ]
        staged_urls = [
            "https://tempfile.redpandaai.co/staged-1.png",
            "https://tempfile.redpandaai.co/staged-2.png",
        ]
        task = self._make_task(
            model_id="gpt-image-2-image-to-image",
            request_params={
                "prompt": "画一只猫",
                "aspect_ratio": "1:1",
                "output_format": "png",
                "resolution": "2K",
                "image_urls": source_urls,
            },
        )
        result = ImageGenerateResult(
            task_id=task["external_task_id"],
            status=TaskStatus.FAILED,
            fail_code="400",
            fail_msg="Image fetch failed",
        )
        mock_adapter = AsyncMock()
        mock_adapter.provider = MagicMock(value="kie")
        mock_adapter.generate = AsyncMock(
            return_value=MagicMock(task_id="fallback_ext_002")
        )

        with patch.dict(os.environ, {"KIE_IMAGE_FETCH_FALLBACK_ENABLED": "true"}), \
             patch(
                 "services.adapters.kie.shadow_upload_store.get_overseas_shadow_upload_urls",
                 new_callable=AsyncMock,
                 return_value=dict(zip(source_urls, staged_urls)),
             ), \
             patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
             patch("services.handlers.base.BaseHandler._build_callback_url", return_value="http://cb"), \
             patch("services.handlers.mixins.CreditMixin._lock_credits", return_value="new_tx"), \
             patch("services.handlers.mixins.CreditMixin._refund_credits"):
            assert svc.should_attempt_kie_image_fetch_fallback(task, result) is True
            retried = await svc.attempt_kie_image_fetch_fallback(task)

        assert retried is True
        assert mock_adapter.generate.await_args.kwargs["image_urls"] == staged_urls
        assert mock_adapter.generate.await_args.kwargs["resolution"] == "2K"
        update_payload = retry_db.table.return_value.update.call_args.args[0]
        assert update_payload["external_task_id"] == "fallback_ext_002"
        assert update_payload["status"] == "pending"
        assert update_payload["request_params"]["_kie_image_fetch_fallback_attempted"] is True
        assert update_payload["request_params"]["image_urls"] == staged_urls

    @pytest.mark.asyncio
    async def test_kie_image_fetch_fallback_requires_complete_staged_urls(self, svc):
        source_url = "https://cdn.example.com/source.png"
        task = self._make_task(
            model_id="gpt-image-2-image-to-image",
            request_params={"prompt": "画一只猫", "image_urls": [source_url]},
        )

        with patch(
            "services.adapters.kie.shadow_upload_store.get_overseas_shadow_upload_urls",
            new_callable=AsyncMock,
            return_value={},
        ):
            retried = await svc.attempt_kie_image_fetch_fallback(task)

        assert retried is False

    @pytest.mark.asyncio
    async def test_kie_image_fetch_fallback_matches_kie_normalized_source_url(self, svc):
        source_url = "https://cdn.example.com/用户上传/input.png"
        normalized_url = "https://cdn.example.com/%E7%94%A8%E6%88%B7%E4%B8%8A%E4%BC%A0/input.png"
        staged_url = "https://tempfile.redpandaai.co/staged-input.png"
        task = self._make_task(
            model_id="gpt-image-2-image-to-image",
            request_params={"prompt": "画一只猫", "image_urls": [source_url]},
        )

        with patch(
            "services.adapters.kie.shadow_upload_store.get_overseas_shadow_upload_urls",
            new_callable=AsyncMock,
            return_value={normalized_url: staged_url},
        ), patch(
            "services.oss_service.normalize_external_oss_url",
            return_value=normalized_url,
        ), patch.object(
            svc, "_resubmit", new_callable=AsyncMock, return_value="fallback_ext_002"
        ) as resubmit:
            retried = await svc.attempt_kie_image_fetch_fallback(task)

        assert retried is True
        assert resubmit.await_args.kwargs["request_params"]["image_urls"] == [staged_url]

    def test_kie_image_fetch_fallback_is_not_repeated(self, svc):
        task = self._make_task(
            model_id="gpt-image-2-image-to-image",
            request_params={"_kie_image_fetch_fallback_attempted": True},
        )
        result = ImageGenerateResult(
            task_id=task["external_task_id"],
            status=TaskStatus.FAILED,
            fail_code="400",
        )

        with patch.dict(os.environ, {"KIE_IMAGE_FETCH_FALLBACK_ENABLED": "true"}):
            assert svc.should_attempt_kie_image_fetch_fallback(task, result) is False
