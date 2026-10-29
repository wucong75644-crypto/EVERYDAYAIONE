"""Current-action routing, optional-method exit and authorization regression tests."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, WorkflowBinding, classify_workflow
from tests.test_skill_runtime import Source, item, state, activate
from tests.tool_context_test_utils import parse_json_tool_content


async def test_exit_optional_method_recomputes_remaining_scope_and_replays():
    source = Source([item('a', tools=('file_search',)), item('b', tools=('web_search',))])
    runtime = state(source, authorized_tool_names={'file_search', 'web_search'})
    await runtime.initialize()
    await runtime.activate(activate('a'))
    await runtime.activate(activate('b'))
    assert not runtime.effective_allowed_tool_names
    assert (await runtime.deactivate(json.dumps({'skill_id': 'a'})))['ok']
    assert runtime.effective_allowed_tool_names == {'web_search'}
    restored = state(source, authorized_tool_names={'file_search', 'web_search', 'file_delete'})
    await restored.initialize(json.loads(json.dumps(runtime.checkpoint())))
    assert (await restored.deactivate(json.dumps({'skill_id': 'b'})))['ok']
    assert restored.effective_allowed_tool_names == {'file_search', 'web_search'}
    assert 'file_delete' not in restored.effective_allowed_tool_names


@pytest.mark.parametrize('binding', ['manual', 'session', 'scheduled'])
async def test_exit_cannot_remove_explicit_or_fixed_method(binding):
    runtime = state()
    await runtime.initialize()
    await runtime.activate(activate())
    if binding == 'manual':
        runtime.manual_skill_id = 'report'
    elif binding == 'session':
        runtime.session_skill_ids = ('report',)
    else:
        runtime.scheduled_snapshot = {}
    before = runtime.effective_allowed_tool_names
    assert (await runtime.deactivate(json.dumps({'skill_id': 'report'})))['code'] == 'SKILL_BINDING_LOCKED'
    assert 'report' in runtime.active and runtime.effective_allowed_tool_names == before


async def test_old_checkpoint_exit_never_recovers_unsaved_authority():
    runtime = state()
    await runtime.initialize()
    await runtime.activate(activate())
    checkpoint = runtime.checkpoint()
    checkpoint.pop('host_allowed_tool_names')
    restored = state()
    await restored.initialize(checkpoint)
    await restored.deactivate(json.dumps({'skill_id': 'report'}))
    assert restored.effective_allowed_tool_names == set(checkpoint['effective_allowed_tool_names'])


def flow():
    value = object.__new__(EcommerceWorkflow)
    value.value = None
    value.route = None
    value.previous = None
    value.input = [{'type': 'text', 'text': '你就用我的六个提示词开始生成就好了'}]
    value.handler = SimpleNamespace(org_id=None)
    value.runtime = SimpleNamespace(skill_runtime=None, skill_coordination=False)
    value.request = SimpleNamespace(task_id=str(uuid4()))
    value.save = AsyncMock()
    return value


async def test_ambiguous_use_defaults_to_ordinary_without_question(monkeypatch):
    value = flow()
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',
        AsyncMock(return_value={'kind': 'needs_input'}))
    await value.identify()
    assert value.value.kind == 'ordinary_image'
    assert value.entry_question() is None
    assert not await value.allows_planning()


async def test_routing_failure_does_not_become_missing_user_input(monkeypatch):
    value = flow()
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=False))
    assert await classify_workflow(value.input) == {'kind': 'unavailable'}
    await value.identify()
    assert value.value is None and value.entry_question() is None
    value.activated = AsyncMock()
    assert await value.barrier([{'name': 'generate_image'}]) is None
    assert await value.barrier([{'name': 'plan_ecommerce_images'}]) is not None


async def test_router_prompt_distinguishes_product_content_from_planning(monkeypatch):
    settings = SimpleNamespace(intent_router_enabled=True, dashscope_api_key='offline',
        intent_router_model='offline', intent_router_timeout=5)
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    answer = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [
        {'message': {'tool_calls': [{'function': {'name': 'select_image_workflow',
         'arguments': json.dumps(answer)}}]}}]}))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    assert await classify_workflow('直接使用已有六个提示词生成') == answer
    prompt = router.call_tool_model.await_args.kwargs['system_prompt']
    assert '最终提示词直接生成' in prompt and '未明确电商用途默认 ordinary_image' in prompt
    assert '本身不代表电商策划意图' in prompt


async def test_control_batch_exit_removes_instructions_and_respects_host_scope():
    from services.handlers.chat.execution_engine import _execute_skill_batch, _apply_skill_context
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from tests.test_skill_runtime_actor import prepared, actor
    runtime, p = actor(), prepared()
    skills = state(Source([item('wrong', tools=('file_search',))]))
    await skills.initialize()
    await skills.activate(activate('wrong'))
    runtime.skill_runtime = skills
    _apply_skill_context(p, skills)
    assert any('Turn Skill instructions' in str(m) for m in p.messages)
    await _execute_skill_batch(prepared=p, calls=[{'id': 'exit', 'name': 'deactivate_skill',
        'arguments': json.dumps({'skill_id': 'wrong'})}], runtime=runtime, blocks=[],
        sink=CollectingExecutionSink(), start_times={}, turn=0, next_model_round=1,
        repeated_tool_call_guard=None)
    assert not skills.active
    assert not any('Turn Skill instructions' in str(m) for m in p.messages)
    assert p.execution_context.authorized_tool_names == skills.effective_allowed_tool_names


async def test_ordinary_action_rejects_ecommerce_activation_before_loading_body():
    from services.handlers.chat.execution_engine import _execute_skill_batch
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from tests.test_skill_runtime_actor import prepared, actor
    runtime, p = actor(), prepared()
    source = Source([item('ecommerce-main-images')])
    skills = state(source)
    await skills.initialize()
    runtime.skill_runtime = skills
    p.ecom_workflow = SimpleNamespace(route={"kind": "ordinary_image"}, allows_planning=AsyncMock(return_value=False))
    await _execute_skill_batch(prepared=p, calls=[{'id': 'activate', 'name': 'activate_skill',
        'arguments': json.dumps({'skill_id': 'ecommerce-main-images'})}], runtime=runtime, blocks=[],
        sink=CollectingExecutionSink(), start_times={}, turn=0, next_model_round=1,
        repeated_tool_call_guard=None)
    source.load.assert_not_awaited()
    assert not skills.active
    assert 'SKILL_NOT_APPLICABLE' in json.dumps(p.messages)


@pytest.mark.parametrize('key,revision', [
    ('ecommerce-main-images', 'v4'), ('chat-image-orchestration', 'v2'),
    ('reference-image-prompts', 'v3'), ('ecommerce-image-prompts', 'v3'),
])
def test_new_skill_versions_validate_without_altering_old_versions(tmp_path, key, revision):
    import hashlib
    from services.skills.contracts import PackageCreate, PublishRevision
    from services.skills.storage import SkillStorage
    from services.skills.renderer import validate_resource_templates
    root = Path(__file__).parents[2] / 'examples/skills/catalog'
    raw = (root / 'platform' / key / revision / 'SKILL.md').read_bytes()
    body = raw.split(b'---\n', 2)[2]
    package = PackageCreate(skill_key=key, source='routing-review', scope_kind='platform')
    publication = PublishRevision(revision=revision, content_sha256=hashlib.sha256(raw).hexdigest(),
        body_sha256=hashlib.sha256(body).hexdigest())
    skill = SkillStorage(str(root), workspace_root=str(tmp_path / 'workspace')).validate(package, publication)
    validate_resource_templates(skill, {})
    assert '当前动作与适用边界' in skill.body
    assert skill.catalog_metadata.model_selectable == (key in {'ecommerce-main-images', 'chat-image-orchestration'})


async def test_exit_batch_defers_generation_and_keeps_explicit_host_ceiling():
    from dataclasses import replace
    from services.handlers.chat.execution_engine import _execute_tools, _apply_skill_context
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from tests.test_skill_runtime_actor import prepared, actor, handler
    from tests.test_chat_execution_engine import _request
    runtime, p, h = actor(), prepared(), handler()
    p.execution_context = replace(p.execution_context, authorized_tool_names=frozenset({'file_search', 'web_search'}))
    runtime.skill_runtime = state(Source([item('wrong', tools=('file_search',))]))
    await runtime.skill_runtime.initialize()
    await runtime.skill_runtime.activate(activate('wrong'))
    _apply_skill_context(p, runtime.skill_runtime)
    calls = [{'id': 'exit', 'name': 'deactivate_skill', 'arguments': json.dumps({'skill_id': 'wrong'})},
             {'id': 'business', 'name': 'web_search', 'arguments': '{}'}]
    await _execute_tools(handler=h, request=_request(), prepared=p, turn=0, turn_text='', calls=calls,
        previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(), blocks=[], runtime=runtime)
    h._execute_tool_calls.assert_not_awaited()
    assert p.execution_context.authorized_tool_names == {'file_search', 'web_search'}
    assert 'file_delete' not in p.execution_context.authorized_tool_names
    results = {m['tool_call_id']: parse_json_tool_content(m['content']) for m in p.messages if m['role'] == 'tool'}
    assert results['business']['code'] == 'SKILL_ACTIVATION_BARRIER'
    assert results['exit']['code'] == 'SKILL_DEACTIVATED'
