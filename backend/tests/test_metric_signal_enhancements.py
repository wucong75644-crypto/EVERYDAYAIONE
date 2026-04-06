"""
信号接入增强测试

验证知识系统信号全链路接入：
1. _calc_task_elapsed_ms：各种输入场景
2. 成功/失败路径：cost_time_ms、retried、retry_from_model 正确传递
3. 重试时 retry_params 正确注入
4. 路由决策信号（IntentRouter / AgentLoop）
5. 用户反馈信号（retry/regenerate）
6. 记忆检索效果信号
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.mixins.message_mixin import MessageMixin


# ============ _calc_task_elapsed_ms 测试 ============


class TestCalcTaskElapsedMs:
    """测试任务耗时计算"""

    def test_string_timestamp(self):
        """ISO 字符串时间戳正常计算"""
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        task = {"created_at": past}
        result = MessageMixin._calc_task_elapsed_ms(task)
        assert result is not None
        assert 4000 <= result <= 7000  # 5秒 ± 容差

    def test_datetime_object(self):
        """datetime 对象正常计算"""
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        task = {"created_at": past}
        result = MessageMixin._calc_task_elapsed_ms(task)
        assert result is not None
        assert 9000 <= result <= 12000

    def test_z_suffix_timestamp(self):
        """Z 后缀的 UTC 时间戳正常解析"""
        past = (datetime.now(timezone.utc) - timedelta(seconds=3)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        ) + "Z"
        task = {"created_at": past}
        result = MessageMixin._calc_task_elapsed_ms(task)
        assert result is not None
        assert 2000 <= result <= 5000

    def test_none_created_at(self):
        """created_at 为 None 返回 None"""
        assert MessageMixin._calc_task_elapsed_ms({"created_at": None}) is None

    def test_missing_created_at(self):
        """无 created_at 字段返回 None"""
        assert MessageMixin._calc_task_elapsed_ms({}) is None

    def test_invalid_string(self):
        """无效字符串返回 None"""
        assert MessageMixin._calc_task_elapsed_ms({"created_at": "not-a-date"}) is None


# ============ 成功路径 retried/retry_from_model 测试 ============


class TestCompleteCommonMetricParams:
    """测试 _handle_complete_common 中 record_metric 参数"""

    def _create_handler(self, request_params: dict):
        """创建 ImageHandler 并 mock 依赖"""
        from services.handlers.image_handler import ImageHandler

        db = MagicMock()

        task_data = {
            "external_task_id": "task_123",
            "status": "running",
            "version": 1,
            "type": "image",
            "user_id": "user_123",
            "conversation_id": "conv_123",
            "placeholder_message_id": "msg_123",
            "model_id": "test-model",
            "client_task_id": "client_123",
            "request_params": request_params,
            "credits_locked": 10,
            "credit_transaction_id": "tx_123",
            "created_at": (
                datetime.now(timezone.utc) - timedelta(seconds=2)
            ).isoformat(),
        }

        # tasks 查询
        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.execute.return_value = MagicMock(data=task_data)

        # messages upsert
        msg_data = {
            "id": "msg_123",
            "conversation_id": "conv_123",
            "role": "assistant",
            "content": [{"type": "image", "url": "https://example.com/img.png"}],
            "status": "completed",
            "credits_cost": 10,
            "created_at": "2026-03-01T12:00:00+00:00",
        }
        messages_chain = MagicMock()
        messages_chain.select.return_value = messages_chain
        messages_chain.eq.return_value = messages_chain
        messages_chain.maybe_single.return_value = messages_chain
        messages_chain.upsert.return_value = messages_chain
        messages_chain.execute.return_value = MagicMock(data=[msg_data])

        def table_dispatch(name):
            if name == "tasks":
                return tasks_chain
            if name == "messages":
                return messages_chain
            chain = MagicMock()
            chain.update.return_value = chain
            chain.eq.return_value = chain
            chain.execute.return_value = MagicMock(data=[{}])
            return chain

        db.table = MagicMock(side_effect=table_dispatch)
        db.rpc = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(return_value=MagicMock(data={}))
            )
        )

        handler = ImageHandler(db)
        return handler

    @pytest.mark.asyncio
    async def test_non_retried_task_metric(self):
        """非重试任务：retried=False, retry_from_model=None"""
        handler = self._create_handler({"aspect_ratio": "1:1"})

        with patch.object(handler, "_complete_task"), \
             patch.object(handler, "_record_knowledge_metric",
                          new_callable=AsyncMock) as mock_metric, \
             patch("services.task_stream.publish", new_callable=AsyncMock), \
             patch("schemas.websocket.build_message_done"):


            await handler.on_complete(
                task_id="task_123",
                result=[{
                    "type": "image", "url": "https://example.com/img.png",
                    "width": 1024, "height": 1024,
                }],
            )

            mock_metric.assert_called_once()
            call_kwargs = mock_metric.call_args[1]
            assert call_kwargs["retried"] is False
            assert call_kwargs["retry_from_model"] is None
            assert call_kwargs["cost_time_ms"] is not None

    @pytest.mark.asyncio
    async def test_retried_task_metric(self):
        """重试任务：retried=True, retry_from_model=原始模型"""
        handler = self._create_handler({
            "aspect_ratio": "1:1",
            "_retried": True,
            "_retry_from_model": "original-model",
        })

        with patch.object(handler, "_complete_task"), \
             patch.object(handler, "_record_knowledge_metric",
                          new_callable=AsyncMock) as mock_metric, \
             patch("services.task_stream.publish", new_callable=AsyncMock), \
             patch("schemas.websocket.build_message_done"):


            await handler.on_complete(
                task_id="task_123",
                result=[{
                    "type": "image", "url": "https://example.com/img.png",
                    "width": 1024, "height": 1024,
                }],
            )

            mock_metric.assert_called_once()
            call_kwargs = mock_metric.call_args[1]
            assert call_kwargs["retried"] is True
            assert call_kwargs["retry_from_model"] == "original-model"


# ============ 失败路径 retried/retry_from_model 测试 ============


class TestErrorCommonMetricParams:
    """测试 _handle_error_common 中 record_metric 参数"""

    def _create_handler(self, request_params: dict):
        """创建 ImageHandler 并 mock 依赖（失败路径）"""
        from services.handlers.image_handler import ImageHandler

        db = MagicMock()

        task_data = {
            "external_task_id": "task_123",
            "status": "running",
            "version": 1,
            "type": "image",
            "user_id": "user_123",
            "conversation_id": "conv_123",
            "placeholder_message_id": "msg_123",
            "model_id": "test-model",
            "client_task_id": "client_123",
            "request_params": request_params,
            "credits_locked": 10,
            "credit_transaction_id": "tx_123",
            "created_at": (
                datetime.now(timezone.utc) - timedelta(seconds=2)
            ).isoformat(),
        }

        tasks_chain = MagicMock()
        tasks_chain.select.return_value = tasks_chain
        tasks_chain.eq.return_value = tasks_chain
        tasks_chain.maybe_single.return_value = tasks_chain
        tasks_chain.execute.return_value = MagicMock(data=task_data)

        msg_data = {
            "id": "msg_123",
            "conversation_id": "conv_123",
            "role": "assistant",
            "content": [{"type": "text", "text": "错误"}],
            "status": "failed",
            "credits_cost": 0,
            "is_error": True,
            "created_at": "2026-03-01T12:00:00+00:00",
        }
        messages_chain = MagicMock()
        messages_chain.select.return_value = messages_chain
        messages_chain.eq.return_value = messages_chain
        messages_chain.maybe_single.return_value = messages_chain
        messages_chain.upsert.return_value = messages_chain
        messages_chain.execute.return_value = MagicMock(data=[msg_data])

        ct_chain = MagicMock()
        ct_chain.select.return_value = ct_chain
        ct_chain.update.return_value = ct_chain
        ct_chain.eq.return_value = ct_chain
        ct_chain.maybe_single.return_value = ct_chain
        ct_chain.execute.return_value = MagicMock(data={
            "id": "tx_123", "user_id": "user_123",
            "amount": 10, "status": "pending",
        })

        def table_dispatch(name):
            if name == "tasks":
                return tasks_chain
            if name == "messages":
                return messages_chain
            if name == "credit_transactions":
                return ct_chain
            chain = MagicMock()
            chain.update.return_value = chain
            chain.eq.return_value = chain
            chain.execute.return_value = MagicMock(data=[{}])
            return chain

        db.table = MagicMock(side_effect=table_dispatch)
        db.rpc = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(return_value=MagicMock(data={}))
            )
        )

        handler = ImageHandler(db)
        return handler

    @pytest.mark.asyncio
    async def test_error_with_retry_info(self):
        """失败路径也传递重试信息"""
        handler = self._create_handler({
            "_retried": True,
            "_retry_from_model": "failed-model",
        })

        with patch.object(handler, "_fail_task"), \
             patch.object(handler, "_record_knowledge_metric",
                          new_callable=AsyncMock) as mock_metric, \
             patch.object(handler, "_extract_failure_knowledge",
                          new_callable=AsyncMock), \
             patch("services.task_stream.publish", new_callable=AsyncMock), \
             patch("schemas.websocket.build_message_error"):


            await handler.on_error(
                task_id="task_123",
                error_code="TEST_ERROR",
                error_message="测试错误",
            )

            mock_metric.assert_called_once()
            call_kwargs = mock_metric.call_args[1]
            assert call_kwargs["retried"] is True
            assert call_kwargs["retry_from_model"] == "failed-model"
            assert call_kwargs["cost_time_ms"] is not None
            assert call_kwargs["params"]["_retried"] is True

    @pytest.mark.asyncio
    async def test_error_without_retry_info(self):
        """非重试失败：retried=False"""
        handler = self._create_handler({"aspect_ratio": "16:9"})

        with patch.object(handler, "_fail_task"), \
             patch.object(handler, "_record_knowledge_metric",
                          new_callable=AsyncMock) as mock_metric, \
             patch.object(handler, "_extract_failure_knowledge",
                          new_callable=AsyncMock), \
             patch("services.task_stream.publish", new_callable=AsyncMock), \
             patch("schemas.websocket.build_message_error"):


            await handler.on_error(
                task_id="task_123",
                error_code="API_ERROR",
                error_message="API 失败",
            )

            mock_metric.assert_called_once()
            call_kwargs = mock_metric.call_args[1]
            assert call_kwargs["retried"] is False
            assert call_kwargs["retry_from_model"] is None


# ============ 路由决策信号测试（IntentRouter） ============


class TestRoutingSignal:
    """测试 IntentRouter._record_routing_signal"""

    @pytest.mark.asyncio
    async def test_routing_signal_params(self):
        """路由信号携带正确的 params"""
        from services.intent_router import IntentRouter, RoutingDecision
        from schemas.message import GenerationType

        decision = RoutingDecision(
            generation_type=GenerationType.IMAGE,
            raw_tool_name="generate_image",
            routed_by="qwen3.5-plus",
            recommended_model="flux-schnell",
        )

        with patch(
            "services.knowledge_service.record_metric",
            new_callable=AsyncMock,
        ) as mock_metric:
            IntentRouter._record_routing_signal(
                decision=decision,
                user_id="user_1",
                input_length=50,
                has_image=False,
                router_model="qwen3.5-plus",
            )
            # 等待 fire-and-forget 任务完成
            import asyncio
            await asyncio.sleep(0.05)

            mock_metric.assert_called_once()
            kw = mock_metric.call_args[1]
            assert kw["task_type"] == "routing"
            assert kw["model_id"] == "qwen3.5-plus"
            assert kw["params"]["routing_tool"] == "generate_image"
            assert kw["params"]["recommended_model"] == "flux-schnell"
            assert kw["params"]["input_length"] == 50
            assert kw["params"]["has_image"] is False


# ============ Agent Loop 路由信号测试 ============


# ============ 用户反馈信号测试 ============


class TestUserFeedbackSignal:
    """测试 message.py _record_user_feedback_signal"""

    @pytest.mark.asyncio
    async def test_retry_feedback_signal(self):
        """retry 操作记录 feedback_type=retry"""
        from api.routes.message import _record_user_feedback_signal

        mock_db = MagicMock()
        # 模拟原消息查询：返回 generation_params 含 model
        msg_chain = MagicMock()
        msg_chain.select.return_value = msg_chain
        msg_chain.eq.return_value = msg_chain
        msg_chain.maybe_single.return_value = msg_chain
        msg_chain.execute.return_value = MagicMock(data={
            "generation_params": {"type": "image", "model": "old-model"},
        })
        mock_db.table.return_value = msg_chain

        with patch(
            "services.knowledge_service.record_metric",
            new_callable=AsyncMock,
        ) as mock_metric:
            _record_user_feedback_signal(
                db=mock_db,
                user_id="user_3",
                operation="retry",
                model="new-model",
                gen_type="image",
                original_message_id="msg_orig",
                conversation_id="conv_1",
            )
            import asyncio
            await asyncio.sleep(0.05)

            mock_metric.assert_called_once()
            kw = mock_metric.call_args[1]
            assert kw["task_type"] == "user_feedback"
            assert kw["params"]["feedback_type"] == "retry"
            assert kw["params"]["original_model"] == "old-model"
            assert kw["params"]["new_model"] == "new-model"
            assert kw["params"]["original_message_id"] == "msg_orig"

    @pytest.mark.asyncio
    async def test_regenerate_feedback_signal(self):
        """regenerate 操作记录 feedback_type=regenerate"""
        from api.routes.message import _record_user_feedback_signal

        mock_db = MagicMock()
        msg_chain = MagicMock()
        msg_chain.select.return_value = msg_chain
        msg_chain.eq.return_value = msg_chain
        msg_chain.maybe_single.return_value = msg_chain
        msg_chain.execute.return_value = MagicMock(data=None)
        mock_db.table.return_value = msg_chain

        with patch(
            "services.knowledge_service.record_metric",
            new_callable=AsyncMock,
        ) as mock_metric:
            _record_user_feedback_signal(
                db=mock_db,
                user_id="user_4",
                operation="regenerate",
                model="model-x",
                gen_type="chat",
                original_message_id=None,
                conversation_id="conv_2",
            )
            import asyncio
            await asyncio.sleep(0.05)

            mock_metric.assert_called_once()
            kw = mock_metric.call_args[1]
            assert kw["params"]["feedback_type"] == "regenerate"
            assert kw["params"]["original_model"] is None


# ============ 记忆检索效果信号测试 ============


class TestMemorySearchSignal:
    """测试 MemoryService._record_memory_search_signal"""

    @pytest.mark.asyncio
    async def test_memory_search_signal_params(self):
        """记忆检索信号携带正确的指标"""
        from services.memory_service import MemoryService

        with patch(
            "services.knowledge_service.record_metric",
            new_callable=AsyncMock,
        ) as mock_metric:
            MemoryService._record_memory_search_signal(
                user_id="user_5",
                mem0_returned=12,
                filtered_count=3,
                filter_latency_ms=450,
                query_length=60,
            )
            import asyncio
            await asyncio.sleep(0.05)

            mock_metric.assert_called_once()
            kw = mock_metric.call_args[1]
            assert kw["task_type"] == "memory_search"
            assert kw["model_id"] == "mem0"
            assert kw["params"]["mem0_returned"] == 12
            assert kw["params"]["filtered_count"] == 3
            assert kw["params"]["filter_latency_ms"] == 450
            assert kw["params"]["query_length"] == 60


# ============ 集成测试：信号在业务流程中被调用 ============


class TestRouteSignalIntegration:
    """验证 IntentRouter.route() 完成后 _record_routing_signal 被调用"""

    @pytest.mark.asyncio
    async def test_route_calls_record_signal(self):
        """成功路由后触发信号记录"""
        import json
        from services.intent_router import IntentRouter

        router = IntentRouter()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "text_chat",
                            "arguments": json.dumps({"system_prompt": "助手"}),
                        }
                    }]
                }
            }]
        }

        with patch("core.config.get_settings") as mock_settings, \
             patch.object(
                 IntentRouter, "_record_routing_signal"
             ) as mock_signal:
            mock_settings.return_value = MagicMock(
                intent_router_enabled=True,
                dashscope_api_key="sk-test",
                intent_router_model="qwen-plus",
                intent_router_fallback_model="qwen3-flash",
                intent_router_timeout=5.0,
            )
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=mock_response)
            router._client = mock_client

            from schemas.message import TextPart
            result = await router.route(
                [TextPart(text="你好")], "user-1", "conv-1"
            )

            mock_signal.assert_called_once()
            call_args = mock_signal.call_args[0]
            # 位置参数：decision, user_id, input_length, has_image, router_model
            assert call_args[1] == "user-1"  # user_id
            assert call_args[4] == "qwen-plus"  # router_model


class TestMemorySearchSignalIntegration:
    """验证 get_relevant_memories() 完成后 _record_memory_search_signal 被调用"""

    @pytest.mark.asyncio
    async def test_search_calls_record_signal(self):
        """语义搜索完成后触发信号记录"""
        import services.memory_config as cfg
        from services.memory_service import MemoryService

        # 注入 mock Mem0
        mock_mem0 = AsyncMock()
        mock_mem0.search = AsyncMock(return_value=[
            {"id": "m1", "memory": "用户是程序员", "metadata": {}},
            {"id": "m2", "memory": "用户喜欢Python", "metadata": {}},
        ])
        cfg._mem0_instance = mock_mem0
        cfg._mem0_available = True

        service = MemoryService(db=MagicMock())

        with patch(
            "services.memory_service.filter_memories",
            new_callable=AsyncMock,
            return_value=[
                {"id": "m1", "memory": "用户是程序员", "metadata": {}},
            ],
        ), patch.object(
            MemoryService, "_record_memory_search_signal"
        ) as mock_signal:
            result = await service.get_relevant_memories(
                user_id="u1", query="用户的职业是什么"
            )

            assert len(result) == 1
            mock_signal.assert_called_once()
            kw = mock_signal.call_args[1]
            assert kw["mem0_returned"] == 2
            assert kw["filtered_count"] == 1
            assert kw["query_length"] == len("用户的职业是什么")
            assert isinstance(kw["filter_latency_ms"], int)

        # 清理
        cfg._mem0_instance = None
        cfg._mem0_available = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
