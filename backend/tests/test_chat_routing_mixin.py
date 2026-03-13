"""
ChatRoutingMixin 单元测试

覆盖：_route_and_stream 路由分发、记忆并行预取、Agent Loop 失败降级、
      _apply_agent_result 参数注入、_reroute_to_media 媒体重路由
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart
from services.agent_types import AgentResult
from services.handlers.chat_handler import ChatHandler
from services.handlers.chat_routing_mixin import ChatRoutingMixin


# ============================================================
# Helpers
# ============================================================


def _make_agent_result(
    gen_type: GenerationType = GenerationType.CHAT,
    model: str = "gemini-3-pro",
    system_prompt: Optional[str] = None,
    search_context: Optional[str] = None,
    direct_reply: Optional[str] = None,
    tool_params: Optional[Dict] = None,
    batch_prompts: Optional[List] = None,
    render_hints: Optional[Dict] = None,
) -> AgentResult:
    return AgentResult(
        generation_type=gen_type,
        model=model,
        system_prompt=system_prompt,
        search_context=search_context,
        direct_reply=direct_reply,
        tool_params=tool_params or {},
        batch_prompts=batch_prompts,
        render_hints=render_hints,
        turns_used=1,
        total_tokens=100,
    )


def _make_db_mock():
    """创建按表名隔离的 DB mock，避免 table('tasks') 和 table('messages') 共用同一个 mock"""
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)
    db._table_mocks = table_mocks  # 测试中可按名访问
    return db


class FakeMixin(ChatRoutingMixin):
    """可测试的 mixin 子类，提供所需的 stub 方法"""

    def __init__(self):
        self.db = _make_db_mock()
        self._extract_text_content = MagicMock(return_value="你好")
        self._build_memory_prompt = AsyncMock(return_value="记忆内容")
        self._stream_generate = AsyncMock()

    def _extract_image_urls(self, content):
        return []


def _text_content(text: str = "你好"):
    return [TextPart(text=text)]


# ============================================================
# TestApplyAgentResult
# ============================================================


class TestApplyAgentResult:
    """_apply_agent_result 将 Agent Loop 结果注入 params"""

    def test_basic_model_injection(self):
        result = _make_agent_result(model="gemini-3-pro")
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "gemini-3-pro")
        assert params["model"] == "gemini-3-pro"

    def test_system_prompt_injection(self):
        result = _make_agent_result(system_prompt="你是一个助手")
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["_router_system_prompt"] == "你是一个助手"

    def test_search_context_injection(self):
        result = _make_agent_result(search_context="搜索结果...")
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["_router_search_context"] == "搜索结果..."

    def test_direct_reply_injection(self):
        result = _make_agent_result(direct_reply="直接回复内容")
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["_direct_reply"] == "直接回复内容"

    def test_render_hints_injection(self):
        hints = {"placeholder_text": "生成中..."}
        result = _make_agent_result(render_hints=hints)
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["_render"] == hints

    def test_tool_params_google_search(self):
        result = _make_agent_result(
            tool_params={"_needs_google_search": True}
        )
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["_needs_google_search"] is True

    def test_tool_params_image_fields(self):
        result = _make_agent_result(
            tool_params={"prompt": "画猫", "aspect_ratio": "16:9"}
        )
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["prompt"] == "画猫"
        assert params["aspect_ratio"] == "16:9"

    def test_batch_prompts_injection(self):
        prompts = [
            {"prompt": "猫", "aspect_ratio": "1:1"},
            {"prompt": "狗", "aspect_ratio": "1:1"},
        ]
        result = _make_agent_result(batch_prompts=prompts)
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params["_batch_prompts"] == prompts
        assert params["num_images"] == 2
        assert params["aspect_ratio"] == "1:1"

    def test_no_optional_fields_leaves_params_minimal(self):
        result = _make_agent_result()
        params: Dict[str, Any] = {}
        ChatRoutingMixin._apply_agent_result(result, params, "m1")
        assert params == {"model": "m1"}


# ============================================================
# TestRouteAndStream
# ============================================================


class TestRouteAndStream:
    """_route_and_stream 路由分发 + 记忆并行"""

    @pytest.mark.asyncio
    async def test_chat_routing_calls_stream_generate(self):
        """Agent Loop 路由到 chat → 调用 _stream_generate"""
        mixin = FakeMixin()
        agent_result = _make_agent_result(gen_type=GenerationType.CHAT)

        with patch.object(
            mixin, "_run_agent_loop", new=AsyncMock(return_value=agent_result),
        ), patch(
            "services.handlers.chat_routing_mixin.ws_manager",
            new=MagicMock(send_to_task_subscribers=AsyncMock()),
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="gemini-3-pro",
        ):
            await mixin._route_and_stream(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), _params={}, metadata=MagicMock(),
            )

        mixin._stream_generate.assert_called_once()
        call_kwargs = mixin._stream_generate.call_args
        assert call_kwargs.kwargs["model_id"] == "gemini-3-pro"

    @pytest.mark.asyncio
    async def test_image_routing_calls_reroute_to_media(self):
        """Agent Loop 路由到 image → 调用 _reroute_to_media"""
        mixin = FakeMixin()
        agent_result = _make_agent_result(
            gen_type=GenerationType.IMAGE, model="flux-1",
        )
        mixin._reroute_to_media = AsyncMock()

        with patch.object(
            mixin, "_run_agent_loop", new=AsyncMock(return_value=agent_result),
        ), patch(
            "services.handlers.chat_routing_mixin.ws_manager",
            new=MagicMock(send_to_task_subscribers=AsyncMock()),
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="flux-1",
        ):
            await mixin._route_and_stream(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), _params={}, metadata=MagicMock(),
            )

        mixin._reroute_to_media.assert_called_once()
        call_kwargs = mixin._reroute_to_media.call_args.kwargs
        assert call_kwargs["gen_type"] == GenerationType.IMAGE

    @pytest.mark.asyncio
    async def test_memory_prefetch_injected_to_params(self):
        """记忆并行预取 → 注入到 _params["_prefetched_memory"]"""
        mixin = FakeMixin()
        mixin._build_memory_prompt = AsyncMock(return_value="用户喜欢Python")
        agent_result = _make_agent_result(gen_type=GenerationType.CHAT)
        params: Dict[str, Any] = {}

        with patch.object(
            mixin, "_run_agent_loop", new=AsyncMock(return_value=agent_result),
        ), patch(
            "services.handlers.chat_routing_mixin.ws_manager",
            new=MagicMock(send_to_task_subscribers=AsyncMock()),
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="gemini-3-pro",
        ):
            await mixin._route_and_stream(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), _params=params, metadata=MagicMock(),
            )

        assert params.get("_prefetched_memory") == "用户喜欢Python"

    @pytest.mark.asyncio
    async def test_memory_failure_degrades_to_none(self):
        """记忆预取失败 → 降级为 None，不影响主流程"""
        mixin = FakeMixin()
        mixin._build_memory_prompt = AsyncMock(
            side_effect=RuntimeError("Mem0 timeout")
        )
        agent_result = _make_agent_result(gen_type=GenerationType.CHAT)
        params: Dict[str, Any] = {}

        with patch.object(
            mixin, "_run_agent_loop", new=AsyncMock(return_value=agent_result),
        ), patch(
            "services.handlers.chat_routing_mixin.ws_manager",
            new=MagicMock(send_to_task_subscribers=AsyncMock()),
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="gemini-3-pro",
        ):
            await mixin._route_and_stream(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), _params=params, metadata=MagicMock(),
            )

        # 记忆未注入（None 不会设置 key）
        assert "_prefetched_memory" not in params
        # 但 _stream_generate 仍然被调用
        mixin._stream_generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_loop_failure_fallback_to_default(self):
        """Agent Loop 失败 → 降级到默认模型聊天"""
        mixin = FakeMixin()

        with patch.object(
            mixin, "_run_agent_loop",
            new=AsyncMock(side_effect=RuntimeError("Agent crash")),
        ), patch(
            "services.adapters.factory.DEFAULT_MODEL_ID", "fallback-model",
        ):
            await mixin._route_and_stream(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), _params={}, metadata=MagicMock(),
            )

        mixin._stream_generate.assert_called_once()
        call_kwargs = mixin._stream_generate.call_args.kwargs
        assert call_kwargs["model_id"] == "fallback-model"

    @pytest.mark.asyncio
    async def test_routing_complete_ws_sent(self):
        """路由完成后发送 routing_complete WS 事件"""
        mixin = FakeMixin()
        agent_result = _make_agent_result(gen_type=GenerationType.CHAT)
        mock_ws = MagicMock(send_to_task_subscribers=AsyncMock())

        with patch.object(
            mixin, "_run_agent_loop", new=AsyncMock(return_value=agent_result),
        ), patch(
            "services.websocket_manager.ws_manager", new=mock_ws,
        ), patch(
            "services.intent_router.resolve_auto_model",
            return_value="gemini-3-pro",
        ):
            await mixin._route_and_stream(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), _params={}, metadata=MagicMock(),
            )

        mock_ws.send_to_task_subscribers.assert_called_once()
        ws_args = mock_ws.send_to_task_subscribers.call_args
        assert ws_args[0][0] == "t1"  # task_id
        msg = ws_args[0][1]
        assert msg["type"] == "routing_complete"


# ============================================================
# TestRerouteToMedia
# ============================================================


class TestRerouteToMedia:
    """_reroute_to_media 媒体重路由"""

    @pytest.mark.asyncio
    async def test_marks_chat_task_completed(self):
        """标记原 chat task 为 completed"""
        mixin = FakeMixin()
        mock_handler = AsyncMock()

        with patch(
            "services.handlers.get_handler",
            return_value=mock_handler,
        ):
            await mixin._reroute_to_media(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), params={},
                metadata=MagicMock(client_task_id="t1", placeholder_created_at=None),
                gen_type=GenerationType.IMAGE, model_id="flux-1",
            )

        # DB update called for task completion
        mixin.db.table("tasks").update.assert_called_once_with(
            {"status": "completed"}
        )

    @pytest.mark.asyncio
    async def test_inserts_media_placeholder(self):
        """插入媒体占位符消息到 DB"""
        mixin = FakeMixin()
        mock_handler = AsyncMock()

        with patch(
            "services.handlers.get_handler",
            return_value=mock_handler,
        ):
            await mixin._reroute_to_media(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), params={},
                metadata=MagicMock(client_task_id="t1", placeholder_created_at=None),
                gen_type=GenerationType.IMAGE, model_id="flux-1",
            )

        insert_call = mixin.db.table("messages").insert
        insert_call.assert_called_once()
        inserted = insert_call.call_args[0][0]
        assert inserted["id"] == "m1"
        assert inserted["status"] == "pending"
        assert inserted["content"] == [{"type": "text", "text": "图片生成中"}]

    @pytest.mark.asyncio
    async def test_delegates_to_media_handler(self):
        """委派给对应的 media handler"""
        mixin = FakeMixin()
        mock_handler = AsyncMock()

        with patch(
            "services.handlers.get_handler",
            return_value=mock_handler,
        ) as get_handler_mock:
            await mixin._reroute_to_media(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), params={},
                metadata=MagicMock(client_task_id="t1", placeholder_created_at=None),
                gen_type=GenerationType.IMAGE, model_id="flux-1",
            )

        get_handler_mock.assert_called_once_with(GenerationType.IMAGE, mixin.db)
        mock_handler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_video_placeholder_text(self):
        """视频路由使用正确的占位符文本"""
        mixin = FakeMixin()
        mock_handler = AsyncMock()

        with patch(
            "services.handlers.get_handler",
            return_value=mock_handler,
        ):
            await mixin._reroute_to_media(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), params={},
                metadata=MagicMock(client_task_id="t1", placeholder_created_at=None),
                gen_type=GenerationType.VIDEO, model_id="kling-2",
            )

        inserted = mixin.db.table("messages").insert.call_args[0][0]
        assert inserted["content"] == [{"type": "text", "text": "视频生成中"}]

    @pytest.mark.asyncio
    async def test_gen_params_include_render_hints(self):
        """_render 参数正确传递到 gen_params"""
        mixin = FakeMixin()
        mock_handler = AsyncMock()
        params = {"_render": {"placeholder_text": "自定义..."}, "aspect_ratio": "16:9"}

        with patch(
            "services.handlers.get_handler",
            return_value=mock_handler,
        ):
            await mixin._reroute_to_media(
                task_id="t1", message_id="m1",
                conversation_id="c1", user_id="u1",
                content=_text_content(), params=params,
                metadata=MagicMock(client_task_id="t1", placeholder_created_at=None),
                gen_type=GenerationType.IMAGE, model_id="flux-1",
            )

        inserted = mixin.db.table("messages").insert.call_args[0][0]
        gen_params = inserted["generation_params"]
        assert gen_params["_render"] == {"placeholder_text": "自定义..."}
        assert gen_params["aspect_ratio"] == "16:9"


# -- TestStartRouting（start() 路由分发）--


class TestStartRouting:

    @pytest.mark.asyncio
    @patch("services.handlers.chat_handler.asyncio.create_task")
    async def test_needs_routing_dispatches_to_route_and_stream(self, mock_create_task):
        """_needs_routing=True → 调用 _route_and_stream"""
        handler = ChatHandler(db=MagicMock())
        handler._save_task = MagicMock()
        handler._route_and_stream = AsyncMock()
        handler._stream_generate = AsyncMock()

        from services.handlers.base import TaskMetadata
        metadata = TaskMetadata(client_task_id="t1", placeholder_created_at=None)

        task_id = await handler.start(
            message_id="m1", conversation_id="c1", user_id="u1",
            content=[TextPart(text="hi")],
            params={"_needs_routing": True}, metadata=metadata,
        )

        assert task_id == "t1"
        handler._save_task.assert_called_once()
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.handlers.chat_handler.asyncio.create_task")
    async def test_no_routing_dispatches_to_stream_generate(self, mock_create_task):
        """_needs_routing=False → 调用 _stream_generate"""
        handler = ChatHandler(db=MagicMock())
        handler._save_task = MagicMock()
        handler._route_and_stream = AsyncMock()
        handler._stream_generate = AsyncMock()

        from services.handlers.base import TaskMetadata
        metadata = TaskMetadata(client_task_id="t2", placeholder_created_at=None)

        task_id = await handler.start(
            message_id="m1", conversation_id="c1", user_id="u1",
            content=[TextPart(text="hi")],
            params={}, metadata=metadata,
        )

        assert task_id == "t2"
        mock_create_task.assert_called_once()
