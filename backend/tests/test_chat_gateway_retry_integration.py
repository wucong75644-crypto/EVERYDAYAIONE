"""Web/Actor → shared Chat kernel → real Gateway/RetryContext → original terminal owners."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from schemas.message import GenerationType, TextPart
from services.adapters.types import StreamChunk, CostEstimate
from services.conversation_execution import ConversationExecutionService, GenerationClaim
from services.handlers.chat.executor import ChatGenerationExecutor
from services.handlers.chat.stream_runner import LegacyStreamRequest, run_legacy_chat_stream
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat_handler import ChatHandler
from services.intent_router import RoutingDecision
from services.model_gateway import ModelGateway

A, B = "gemini-3-pro", "qwen3.5-plus"


def make_adapter(*steps, price=9):
    async def stream(**_kwargs):
        for step in steps:
            if isinstance(step, Exception):
                raise step
            yield step
    return SimpleNamespace(
        stream_chat=Mock(side_effect=stream), close=AsyncMock(),
        supports_google_search=False,
        estimate_cost_unified=Mock(return_value=CostEstimate(B, Decimal(0), price)),
    )


@pytest.fixture
def environment(monkeypatch):
    from services.handlers.chat import stream_setup
    handler = ChatHandler(MagicMock())
    handler._build_llm_messages = AsyncMock(return_value=[{"role": "user", "content": "fixed"}])
    handler._record_breaker_result = Mock()
    handler._send_retry_notification = AsyncMock()
    handler._save_accumulated_content = AsyncMock()
    handler._save_accumulated_blocks = AsyncMock()
    handler._route_retry = AsyncMock(return_value=RoutingDecision(
        generation_type=GenerationType.CHAT, recommended_model=B,
    ))
    handler._calculate_credits = Mock(wraps=handler._calculate_credits)
    handler._deduct_directly = Mock()
    handler._dispatch_post_tasks = Mock()
    handler._record_knowledge_metric = AsyncMock()
    handler._extract_failure_knowledge = AsyncMock()
    handler.on_error = AsyncMock()
    async def complete(**kwargs):
        await handler._handle_credits_on_complete(
            {"user_id": "user", "model_id": B}, kwargs["credits_consumed"],
        )
    handler.on_complete = AsyncMock(side_effect=complete)
    monkeypatch.setattr(stream_setup, "_prepare_request_context", lambda *_args: SimpleNamespace(
        discovered_tools=set(), build_context_prompt=lambda: "",
    ))
    from services.handlers.permission_mode import PermissionMode
    monkeypatch.setattr(stream_setup, "_prepare_permission_and_tools", lambda *_args: (PermissionMode("ask"), []))
    events, sessions = [], []
    def configure(*adapters, max_concurrency=None):
        factory = Mock(side_effect=adapters)
        gateway = ModelGateway(adapter_factory=factory, event_publisher=SimpleNamespace(publish=events.append), max_concurrency=max_concurrency)
        original = gateway.open_chat
        def open_chat(request):
            session = original(request)
            sessions.append(session)
            return session
        gateway.open_chat = open_chat
        monkeypatch.setattr("services.model_gateway.get_model_gateway", lambda: gateway)
        return factory
    return SimpleNamespace(handler=handler, events=events, sessions=sessions, configure=configure)


def web_request():
    return LegacyStreamRequest(
        task_id="task", message_id="message", conversation_id="conv", user_id="user",
        content=[TextPart(text="hello")], model_id=A, params={"_is_smart_mode": True},
        permission_mode="ask", context_anchor=object(), thinking_mode="deep",
    )


def websocket():
    return SimpleNamespace(
        register_steer_listener=Mock(), register_cancel_listener=Mock(),
        unregister_steer_listener=Mock(), unregister_cancel_listener=Mock(),
        is_cancelled=Mock(return_value=False), check_steer=Mock(return_value=None),
        send_to_task_or_user=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_credits,expected", [(1.25, 3), (None, 7)])
async def test_web_fallback_settles_once_using_successful_model_usage(environment, api_credits, expected):
    first = make_adapter(ConnectionError("down"), price=99)
    second = make_adapter(StreamChunk(content="ok", prompt_tokens=4, completion_tokens=2, credits_consumed=api_credits), price=7)
    factory = environment.configure(first, second)
    handler = environment.handler
    request = web_request()
    await run_legacy_chat_stream(handler=handler, request=request, websocket=websocket())
    assert factory.call_count == 2
    assert len(environment.sessions) == 1
    handler._build_llm_messages.assert_awaited_once()
    assert handler._build_llm_messages.call_args.kwargs["context_anchor"] is request.context_anchor
    assert handler._build_llm_messages.call_args.kwargs["permission_mode"] == "ask"
    assert second.stream_chat.call_args.kwargs["thinking_mode"] == "deep"
    handler.on_complete.assert_awaited_once()
    handler.on_error.assert_not_awaited()
    handler._deduct_directly.assert_called_once()
    assert handler._deduct_directly.call_args.kwargs["amount"] == expected
    handler._calculate_credits.assert_called_once_with({
        "prompt_tokens": 4, "completion_tokens": 2,
        **({"api_credits": api_credits} if api_credits is not None else {}),
    })
    first.estimate_cost_unified.assert_not_called()
    assert handler._dispatch_post_tasks.call_args.kwargs["model_id"] == B
    assert handler._dispatch_post_tasks.call_args.kwargs["retry_context"].failed_models == [A]
    assert handler._record_breaker_result.call_count == 2  # no duplicate Web finalizer record


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_web_failure_saves_partial_without_retry_or_charge(environment, partial):
    first = make_adapter(*([StreamChunk(content="partial", prompt_tokens=10, credits_consumed=2)] if partial else []), ConnectionError("down"))
    factory = environment.configure(first, make_adapter(ConnectionError("down again")))
    handler = environment.handler
    await run_legacy_chat_stream(handler=handler, request=web_request(), websocket=websocket())
    handler.on_complete.assert_not_awaited()
    handler.on_error.assert_awaited_once()
    handler._deduct_directly.assert_not_called()
    handler._calculate_credits.assert_not_called()
    assert factory.call_count == (1 if partial else 2)
    assert handler.on_error.call_args.kwargs["error_code"] == ("MODEL_PARTIAL_OUTPUT" if partial else "GENERATION_FAILED")
    if partial:
        handler._save_accumulated_content.assert_awaited_once_with("task", "partial")
        assert environment.sessions[0].last_result.usage["api_credits"] == 2


def actor_service(environment, monkeypatch, owned=True):
    from services.handlers.chat import executor as executor_module
    monkeypatch.setattr(executor_module, "resolve_execution_scope", AsyncMock(return_value=SimpleNamespace(
        workspace_owner_id="user", personal_context_allowed=True,
    )))
    db = MagicMock()
    query = db.table.return_value
    query.update.return_value = query
    query.eq.return_value = query
    query.gt.return_value = query
    query.execute = AsyncMock(return_value=SimpleNamespace(data=[{"id": "task"}] if owned else []))
    sink = CollectingExecutionSink()
    sink.flush_progress = AsyncMock()
    executor = ChatGenerationExecutor(
        db, handler_factory=lambda _db: environment.handler, handler_db_factory=lambda: environment.handler.db,
        sink_factory=lambda *_args: sink,
    )
    executor._load_input_content = AsyncMock(return_value=[TextPart(text="hello")])
    claim = GenerationClaim(
        task_id="task", execution_token="token", conversation_id="conv", turn_id="turn",
        input_message_id="input", base_context_revision=0, context_through_message_id=None,
        execution_attempt=1, execution_mode="serial",
    )
    task = {
        "id": "task", "conversation_id": "conv", "assistant_message_id": "message", "user_id": "user",
        "model_id": A, "request_params": {"_is_smart_mode": True, "permission_mode": "ask"},
    }
    service = ConversationExecutionService(db, executor)
    service._load_task = AsyncMock(return_value=task)
    service._rpc = AsyncMock(return_value={"outcome": "committed"})
    return service, claim, db, sink


@pytest.mark.asyncio
async def test_actor_uses_same_gateway_policy_and_commits_usage_once(environment, monkeypatch):
    factory = environment.configure(
        make_adapter(ConnectionError("down")),
        make_adapter(StreamChunk(content="ok", prompt_tokens=4, completion_tokens=2, credits_consumed=1.25)),
    )
    service, claim, db, sink = actor_service(environment, monkeypatch)
    assert await service.execute_claim(claim) == {"outcome": "committed"}
    assert factory.call_count == 2 and sink.text == "ok"
    assert len(environment.sessions) == 1
    assert environment.sessions[0].retry_context.failed_models == [A]
    service._rpc.assert_awaited_once()
    name, params = service._rpc.call_args.args
    assert name == "commit_generation_turn"
    assert params["p_usage"].obj == {"prompt_tokens": 4, "completion_tokens": 2, "api_credits": 1.25}
    assert params["p_credits_cost"] == 3
    assert params["p_execution_token"] == "token"
    environment.handler.on_complete.assert_not_awaited()
    environment.handler._deduct_directly.assert_not_called()
    environment.handler._send_retry_notification.assert_not_awaited()
    query = db.table.return_value
    query.update.assert_called_once_with({"model_id": B})
    assert ("execution_token", "token") in [call.args for call in query.eq.call_args_list]
    assert ("status", "running") in [call.args for call in query.eq.call_args_list]
    assert query.gt.call_args.args[0] == "lease_expires_at"


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_actor_final_failure_uses_web_error_code_without_commit(environment, monkeypatch, partial):
    factory = environment.configure(
        make_adapter(*([StreamChunk(content="partial", credits_consumed=1.5)] if partial else []), ConnectionError("down")),
        make_adapter(ConnectionError("down again")),
    )
    service, claim, db, sink = actor_service(environment, monkeypatch)
    service._rpc.return_value = {"outcome": "failed"}
    assert await service.execute_claim(claim) == {"outcome": "failed"}
    service._rpc.assert_awaited_once()
    name, params = service._rpc.call_args.args
    assert name == "fail_generation_turn"
    assert params["p_error_code"] == ("MODEL_PARTIAL_OUTPUT" if partial else "GENERATION_FAILED")
    assert factory.call_count == (1 if partial else 2)
    environment.handler._deduct_directly.assert_not_called()
    environment.handler._calculate_credits.assert_not_called()
    if partial:
        sink.flush_progress.assert_awaited_once()
        assert sink.text == "partial"


@pytest.mark.asyncio
async def test_actor_ownership_loss_during_fallback_prevents_provider_and_terminal_write(environment, monkeypatch):
    factory = environment.configure(make_adapter(ConnectionError("down")))
    service, claim, db, sink = actor_service(environment, monkeypatch, owned=False)
    assert await service.execute_claim(claim) == {"outcome": "ownership_lost"}
    assert factory.call_count == 1
    service._rpc.assert_not_awaited()
    environment.handler._deduct_directly.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize("first_search,second_search", [(True, False), (False, True)])
async def test_fallback_rebuilds_provider_search_tools_without_mutating_common_tools(environment, first_search, second_search):
    from dataclasses import replace
    first = make_adapter(ConnectionError("down"))
    second = make_adapter(StreamChunk(content="ok"))
    search = {"type": "function", "function": {"name": "googleSearch"}}
    for provider, enabled in ((first, first_search), (second, second_search)):
        provider.supports_google_search = enabled
        provider.create_google_search_tool = Mock(return_value=search)
    environment.configure(first, second)
    await run_legacy_chat_stream(
        handler=environment.handler,
        request=replace(web_request(), needs_google_search=True),
        websocket=websocket(),
    )
    assert first.stream_chat.call_args.kwargs["tools"] == ([search] if first_search else [])
    assert second.stream_chat.call_args.kwargs["tools"] == ([search] if second_search else [])
