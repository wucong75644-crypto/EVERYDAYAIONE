"""Host source scope and real incremental read boundary; no paid transports."""
import asyncio
from types import SimpleNamespace

import pytest
from schemas.message import TextPart, ImagePart
from services.knowledge_workspace.scope_selection import self_contained_reason
from services.handlers.chat.execution_engine import _read_turn
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.agent.observability.chat_stream import ModelTurnTiming


@pytest.mark.parametrize('question,images,expected', [
    ('你是谁？', False, 'self_contained_chat'),
    ('请讲个笑话', False, 'self_contained_chat'),
    ('请翻译：hello world', False, 'self_contained_transform'),
    ('仅根据这张图片分析颜色和构图', True, 'user_images_only'),
    ('分析这张图片', True, 'user_images_only'),  # Complete source-bound request, observed in production.
    ('帮我分析一下图片', True, 'user_images_only'),
    ('请描述我上传的照片。', True, 'user_images_only'),
    ('分析这张图片', False, None),
    ('分析这张图片后上网核实', True, None),
    ('分析图片中的商品价格是否合理', True, None),
    ('分析这张图片并按之前要求执行', True, None),
    ('分析这张图片中的公司报销规则', True, None),
    ('仅根据这张图片分析公司报销流程', True, None),
    ('公司有哪些规则？', False, None),
    ('帮我分析一下', False, None),
    ('润色上一版', False, None),
    ('翻译上面的内容', False, None),
    ('仅根据以下内容分析', False, None),
    ('写个程序查询库存', False, None),
])
def test_host_scope_is_conservative(question, images, expected):
    content = [TextPart(text=question)]
    if images:
        content.append(ImagePart(url='https://example.invalid/original.png'))
    assert self_contained_reason(question, content, []) == expected
    assert self_contained_reason(question, content, [], required=True) is None
    assert self_contained_reason(question, content, [{'role': 'assistant', 'content': '[来源](/knowledge?object=old)'}]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('held', [False, True])
async def test_real_read_loop_delivers_before_generation_finishes_only_without_gate(held):
    first_received = asyncio.Event()
    continue_generation = asyncio.Event()
    finished = asyncio.Event()

    async def stream(**kwargs):
        yield SimpleNamespace(content='第一段', thinking_content='', tool_calls=None,
                              prompt_tokens=0, completion_tokens=0, credits_consumed=None, finish_reason=None)
        first_received.set()
        await continue_generation.wait()
        yield SimpleNamespace(content='第二段', thinking_content='', tool_calls=None,
                              prompt_tokens=0, completion_tokens=0, credits_consumed=None, finish_reason=None)
        finished.set()

    p = SimpleNamespace(adapter=SimpleNamespace(stream_chat=stream), messages=[], stream_kwargs={},
                        knowledge_grounding_run=object() if held else None)
    sink, totals = CollectingExecutionSink(), StreamTotals()
    run = asyncio.create_task(_read_turn(p, [], asyncio.Event(), sink, totals, [], None))
    try:
        await asyncio.wait_for(first_received.wait(), 2)
        assert not finished.is_set()
        assert sink.text == ('' if held else '第一段')
        assert totals.text == sink.text
    finally:
        continue_generation.set()
        result = await asyncio.wait_for(run, 2)
    assert result[0] == '第一段第二段'
    assert sink.text == ('' if held else '第一段第二段')
    assert p.model_turn_timings[0]['body_chunks'] == 2
    assert p.model_turn_timings[0]['held_for_knowledge'] is held
    assert 'model_turn_timings' not in totals.usage  # Billing contract is unchanged.


def test_model_timing_uses_one_clock_and_fixed_scalar_state():
    clock = SimpleNamespace(now=0.0)
    timing = ModelTurnTiming(clock=lambda: clock.now)
    clock.now = .7
    timing.on_text()
    clock.now = .9
    timing.on_text()
    clock.now = 1.2
    assert timing.summary(held=False, review=False) == {
        'first_body_ms': 700.0, 'elapsed_ms': 1200.0, 'body_chunks': 2,
        'max_body_gap_ms': 200.0, 'held_for_knowledge': False, 'review': False,
    }


def test_checkpointed_user_steer_is_not_reclassified_from_original_request():
    content = [TextPart(text='你是谁？')]
    assert self_contained_reason('你是谁？', content, [
        {'role': 'user', 'content': '你是谁？'},
        {'role': 'user', 'content': '按公司规范解释'},
    ]) is None


@pytest.mark.asyncio
async def test_consumed_steer_keeps_pending_scope_refresh_until_next_model(monkeypatch):
    from unittest.mock import AsyncMock
    from services.handlers.chat.execution_engine import _inject_steer_messages, _refresh_knowledge_after_steer
    from tests.test_chat_execution_engine import _request
    p = SimpleNamespace(messages=[], text_content='你是谁？')
    h = SimpleNamespace(_knowledge_self_contained=True, _knowledge_run=None)
    run = SimpleNamespace(introduction=lambda: {'scope': 'authorized'})
    async def prepare(handler, *args, **kwargs):
        assert kwargs['question'] == '按公司规范改写'
        assert 'self_contained' not in kwargs
        handler._knowledge_run = run
    prep = AsyncMock(side_effect=prepare)
    monkeypatch.setattr('services.knowledge_workspace.runtime.prepare_knowledge_run', prep)
    # All model-loop/tool/Skill consumption points use the same injector. The
    # queue may now be empty, but its invalidation survives until the loop reads.
    _inject_steer_messages(p.messages, ['按公司规范改写'], prepared=p)
    assert p.knowledge_steer_pending
    await _refresh_knowledge_after_steer(h, _request(), p)
    assert not p.knowledge_steer_pending and not h._knowledge_self_contained
    assert p.knowledge_question == '按公司规范改写'
    assert '知识画布运行快照：' in p.messages[-1]['content']
    await _refresh_knowledge_after_steer(h, _request(), p)
    prep.assert_awaited_once()


@pytest.mark.parametrize('question', ['润色上一版', '翻译上面的内容', '仅根据以下内容分析'])
def test_legacy_scope_selection_does_not_reopen_ambiguous_exemption(question):
    from services.knowledge_workspace.scope_selection import select
    corpus = [{'id': 'authorized-personal', 'kind': 'personal', 'title': '个人资料'}]
    selected, reason = select(corpus, question)
    assert selected == corpus and reason == 'task_scope'


@pytest.mark.parametrize('question', ['你好', '请翻译：hello', '仅根据以下内容回答：hello'])
def test_host_denied_exemption_cannot_be_reopened_by_legacy_scope_rules(question):
    from services.knowledge_workspace.scope_selection import select
    corpus = [{'id': 'authorized-personal', 'kind': 'personal', 'title': '个人资料'}]
    assert select(corpus, question, allow_self_contained=False)[0] == corpus


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('question,images,reason', [
    ('你好', False, 'self_contained_chat'),
    ('你都能帮我做什么？', False, 'self_contained_chat'),
    ('分析这张图片', True, 'user_images_only'),
])
def test_real_prompt_tool_documentation_does_not_become_knowledge_evidence(monkeypatch, cached, question, images, reason):
    from config.chat_tools import TOOL_SYSTEM_PROMPT
    from services.prompt_builder.builder import PromptBuilder
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(prompt_cache_control_enabled=cached))
    assert 'knowledge_search' in TOOL_SYSTEM_PROMPT
    messages = PromptBuilder._compose_messages(
        TOOL_SYSTEM_PROMPT, '当前可用工具及权限', '当前时间', [], None,
        SimpleNamespace(workspace_system_block='', attachments_system_block='',
                        user_message={'role': 'user', 'content': question}))
    content = [TextPart(text=question)]
    if images:
        content.append(ImagePart(url='https://example.invalid/original.png'))
    assert self_contained_reason(question, content, messages) == reason
    assert self_contained_reason(question, content, messages, required=True) is None


@pytest.mark.parametrize('packet', [
    {'role': 'system', 'content': '知识画布运行快照：{"roots":[]}'},
    {'role': 'system', 'content': [{'type':'text','text':'知识库程序检索依据：实际证据'}]},
    {'role': 'system', 'content': '知识查询答复阶段：实际证据'},
    {'role': 'system', 'content': '以下是之前对话的摘要：已读取 knowledge_search'},
    {'role': 'assistant', 'content': '', 'tool_calls':[{'function':{'name':'knowledge_search'}}]},
    {'role': 'tool', 'name': 'knowledge_read', 'content': '{}'},
    {'role': 'assistant', 'content': '[来源](/knowledge?object=old)'},
])
def test_real_knowledge_provenance_still_requires_verification(packet):
    messages = [packet, {'role':'user','content':'你好'}]
    assert self_contained_reason('你好', [TextPart(text='你好')], messages) is None


@pytest.mark.asyncio
async def test_tool_round_is_not_presented_as_answer_audit_and_final_body_stays_held(monkeypatch):
    from unittest.mock import AsyncMock
    from services.agent.execution_budget import ExecutionBudget
    from services.tools.runtime_context import catalog_context
    from services.handlers.chat.execution_engine import _run_loop
    from tests.test_chat_execution_engine import _request
    rounds=[]
    sink=CollectingExecutionSink()
    blocks=[]
    async def stream(**kwargs):
        rounds.append(len(rounds))
        calls=[SimpleNamespace(index=0,id='lookup',name='erp_agent',arguments_delta='{}')] if len(rounds)==1 else None
        yield SimpleNamespace(content='未经核验的草稿',thinking_content='内部思考',tool_calls=calls,
            prompt_tokens=3,completion_tokens=2,credits_consumed=None,finish_reason='stop')
    prepared=SimpleNamespace(execution_context=catalog_context(None),
        adapter=SimpleNamespace(stream_chat=stream),stream_kwargs={},
        budget=ExecutionBudget(max_turns=4,wrap_up_turns_reserved=0),core_tools=[],messages=[],
        tool_context=SimpleNamespace(discovered_tools=set(),build_context_prompt=lambda:''),
        permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _:''),
        knowledge_grounding_run=object())
    async def tools(**kwargs):
        assert not any(b.get('tool_name')=='knowledge_answer_verification' for b in blocks)
        assert sink.text=='' and sink.thinking==''
    async def verify(run,draft,*args,**kwargs):
        assert draft=='未经核验的草稿' and sink.text=='' and sink.thinking==''
        stages=[b for b in blocks if b.get('tool_name')=='knowledge_answer_verification']
        assert len(stages)==1 and stages[0]['status']=='running'
        return '已核验的回答'
    execute=AsyncMock(side_effect=tools)
    audit=AsyncMock(side_effect=verify)
    monkeypatch.setattr('services.handlers.chat.execution_engine._execute_tools',execute)
    monkeypatch.setattr('services.handlers.chat.execution_engine._verify_knowledge_answer',audit)
    await _run_loop(handler=SimpleNamespace(org_id=None),request=_request(),prepared=prepared,
        cancellation_event=asyncio.Event(),sink=sink,totals=StreamTotals(),blocks=blocks,runtime=None)
    assert len(rounds)==2
    execute.assert_awaited_once();audit.assert_awaited_once()
    assert sink.text=='已核验的回答'
    assert [b['status'] for b in blocks if b.get('tool_name')=='knowledge_answer_verification']==['completed']
