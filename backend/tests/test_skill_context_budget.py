"""Final request accounting must never mutate instructions or call the provider on refusal."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.skills.context_budget import assess_context, SkillContextBudgetError
from services.model_gateway import ModelCallRequest, ModelGateway
from core.error_classifier import classify_error, ErrorCategory
from tests.test_model_gateway import _chunk, _SamplingEvents


def test_context_budget_counts_tools_and_output_without_mutating_inputs():
    messages = [{'role': 'system', 'content': 'Core method'}, {'role': 'user', 'content': 'question'}]
    tools = [{'type': 'function', 'function': {'name': 'long_tool', 'description': 'x' * 6000}}]
    before = copy.deepcopy((messages, tools))
    small = assess_context(messages, [], 4000, 256)
    large = assess_context(messages, tools, None, 256)
    assert large['estimated_input_tokens'] > small['estimated_input_tokens'] + 2000
    with pytest.raises(SkillContextBudgetError):
        assess_context(messages, tools, 1024, 256)
    assert (messages, tools) == before
    assert assess_context(messages, [], None)['context_window'] == 0


async def test_context_refusal_occurs_before_slot_and_provider_and_is_not_an_outage():
    provider = Mock(side_effect=AssertionError('no paid call'))
    adapter = SimpleNamespace(stream_chat=provider, close=AsyncMock(), effective_context_window=100)
    events = _SamplingEvents()
    gateway = ModelGateway(adapter_factory=Mock(return_value=adapter), event_publisher=events)
    session = gateway.open_chat(ModelCallRequest(model_id='model', task_id='task'))
    try:
        with pytest.raises(SkillContextBudgetError) as caught:
            async for _ in session.stream_chat(messages=[{'role': 'system', 'content': 'x' * 3000}],
                                               tools=[], skill_context=True):
                pass
        error = classify_error(caught.value, model_call=True)
        assert error.category == ErrorCategory.BUSINESS
        assert not error.is_retryable and not error.should_record_breaker and not error.should_refund
        assert session._acquire_task is None
        provider.assert_not_called()
    finally:
        await session.close()


@pytest.mark.parametrize('guard', [False, True])
async def test_unknown_or_unguarded_context_preserves_provider_contract(guard):
    received = []
    async def stream_chat(**kwargs):
        received.append(kwargs)
        yield _chunk('answer')
    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock(), effective_context_window=None)
    gateway = ModelGateway(adapter_factory=Mock(return_value=adapter))
    session = gateway.open_chat(ModelCallRequest(model_id='model'))
    try:
        chunks = [chunk async for chunk in session.stream_chat(messages=[], tools=[], skill_context=guard)]
        assert chunks[0].content == 'answer'
        assert 'skill_context' not in received[0]
    finally:
        await session.close()


async def test_retry_revalidates_the_smaller_actual_model_before_provider_call():
    from tests.test_model_gateway_retry import adapter, setup
    from services.model_gateway import ModelGatewayError
    from services.adapters.types import StreamChunk
    first = adapter(ConnectionError('initial provider unavailable'))
    second = adapter(StreamChunk(content='must not be reached'))
    first.effective_context_window = 10000
    second.effective_context_window = 100
    session, handler, factory, events = setup(first, second)
    try:
        with pytest.raises(ModelGatewayError) as caught:
            async for _ in session.stream_chat(messages=[{'role': 'user', 'content': 'x' * 3000}],
                                               tools=[], skill_context=True):
                pass
        assert caught.value.result.error_code == 'SKILL_CONTEXT_WINDOW_EXCEEDED'
        first.stream_chat.assert_called_once()
        second.stream_chat.assert_not_called()
        assert handler._route_retry.await_count == 1
    finally:
        await session.close()


async def test_scheduled_loop_and_wrap_up_guard_the_actual_adapter():
    from services.agent.tool_loop_executor import ToolLoopExecutor
    from services.agent.loop_types import LoopConfig, LoopStrategy
    from services.agent.stop_policy import synthesize_wrap_up
    provider = Mock(side_effect=AssertionError('no paid call'))
    adapter = SimpleNamespace(stream_chat=provider, close=AsyncMock(), effective_context_window=100)
    gateway = ModelGateway(adapter_factory=Mock(return_value=adapter))
    session = gateway.open_chat(ModelCallRequest(model_id='model'))
    messages = [{'role': 'system', 'content': 'Skill core ' * 1000}]
    loop = ToolLoopExecutor(adapter=session, executor=Mock(), all_tools=[],
                            config=LoopConfig(max_turns=3, context_window=10000, tool_timeout=5),
                            strategy=LoopStrategy(), skill_context=True)
    try:
        with pytest.raises(SkillContextBudgetError):
            await loop._stream_one_turn(messages, [])
        assert await synthesize_wrap_up(model_gateway=session, messages=messages,
                                         skill_context=True) is None
        provider.assert_not_called()
    finally:
        await session.close()


@pytest.mark.parametrize('kind', ['active', 'resident', 'inactive', 'no_runtime'])
@pytest.mark.parametrize('window', [100, 20000])
async def test_chat_budget_wrap_up_checks_final_skill_request_and_preserves_fallback(kind, window):
    from services.handlers.chat.execution_engine import _apply_budget_stop
    from services.handlers.chat.stream_session import StreamTotals
    from tests.test_skill_runtime import state, Source, activate
    skills = state(Source(body='Core method'))
    await skills.initialize()
    if kind in {'active', 'resident'}:
        assert (await skills.activate(activate()))['ok']
        if kind == 'resident':
            skills.resident, skills.active = skills.active, {}
    runtime = SimpleNamespace(skill_runtime=skills) if kind != 'no_runtime' else None
    received = []
    async def stream_chat(**kwargs):
        received.append(kwargs)
        yield _chunk('final answer')
    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock(), effective_context_window=window)
    session = ModelGateway(adapter_factory=Mock(return_value=adapter),
                           event_publisher=_SamplingEvents()).open_chat(ModelCallRequest(model_id='model'))
    messages = [{'role': 'system', 'content': 'Skill instructions ' * 1000}]
    before = copy.deepcopy(messages)
    prepared = SimpleNamespace(model_gateway=session, budget=SimpleNamespace(stop_reason='max_turns'),
                               messages=messages)
    totals, blocks = StreamTotals(text='existing partial answer'), []
    sink = SimpleNamespace(on_block=AsyncMock())
    try:
        await _apply_budget_stop(prepared, totals, blocks, sink, runtime=runtime)
        if kind in {'active', 'resident'} and window == 100:
            assert received == []
            assert session._acquire_task is None
            assert totals.text.startswith('existing partial answer')
            assert '已达到执行上限' in totals.text
        else:
            assert len(received) == 1
            assert 'skill_context' not in received[0]  # Private flag never reaches the provider.
            assert received[0]['tools'] is None
            assert totals.text == 'final answer'
        assert prepared.messages == before
        sink.on_block.assert_awaited_once()
    finally:
        await session.close()


async def test_chat_context_refusal_without_partial_output_keeps_existing_failure_contract():
    from services.handlers.chat.execution_engine import _apply_budget_stop
    from services.handlers.chat.stream_session import StreamTotals
    provider = Mock(side_effect=AssertionError('no paid call'))
    adapter = SimpleNamespace(stream_chat=provider, close=AsyncMock(), effective_context_window=100)
    session = ModelGateway(adapter_factory=Mock(return_value=adapter),
                           event_publisher=_SamplingEvents()).open_chat(ModelCallRequest(model_id='model'))
    prepared = SimpleNamespace(model_gateway=session, budget=SimpleNamespace(stop_reason='max_turns'),
        messages=[{'role': 'system', 'content': 'Skill instructions ' * 1000}])
    runtime = SimpleNamespace(skill_runtime=SimpleNamespace(has_active_skills=True))
    sink = SimpleNamespace(on_block=AsyncMock())
    blocks = []
    try:
        with pytest.raises(RuntimeError, match='CHAT_BUDGET_EXHAUSTED_WITHOUT_OUTPUT'):
            await _apply_budget_stop(prepared, StreamTotals(), blocks, sink, runtime=runtime)
        provider.assert_not_called()
        sink.on_block.assert_not_awaited()
        assert blocks == []
    finally:
        await session.close()
