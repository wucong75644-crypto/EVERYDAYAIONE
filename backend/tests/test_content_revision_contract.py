"""Real read controls, immutable saved formats and model revision admission."""
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import content_objects as co, content_revision as revision
from services.task_execution.methods import content_format_contract, REFERENCE_PROMPTS_PROFILE, valid_reference_prompts_presentation
from services.task_execution.image_reference_catalog import ImageReferences
from tests.test_content_objects import registered, ref
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


def configure_reference(runtime, row):
    """Stored old method output, with NO active Skill in the current input."""
    item = co.ContentItem(title='绿叶封面', **co.revision_examples()[0]['updates'][0]['item'])
    artifacts = list(runtime.task_execution_state.artifacts)
    pos = next(i for i, a in enumerate(artifacts) if a.id == row['artifact_id'])
    original = artifacts[pos]
    record = co.ContentRecord.model_validate_json(original.inline_text)
    record = record.model_copy(update={'presentation_profile': REFERENCE_PROMPTS_PROFILE,
        'document': co.ContentDocument(title='旧参考图稿', display_prefix='# 参考图判断\n产品摄影参考图。', items=(item, item, item))})
    raw = record.model_dump_json()
    artifacts[pos] = original.model_copy(update={'inline_text': raw, 'sha256': hashlib.sha256(raw.encode()).hexdigest()})
    runtime.task_execution_state = runtime.task_execution_state.model_copy(update={'artifacts': tuple(artifacts)})
    return co.catalog(runtime.task_execution_state)[0]


async def read(runtime, prepared, handler, request, row, index):
    handler.db = None
    await execute(runtime, prepared, handler, request, [call('read_conversation_text', {'content_source': {
        'artifact_id': row['artifact_id'], 'item_index': index}}, f'read-{row["artifact_id"]}-{index}')])
    return json.loads(prepared.messages[-1]['content'])


@pytest.fixture(autouse=True)
def scoped_read_identity(monkeypatch):
    # Identity SQL is covered by content/history PG tests; exercise the real
    # read loader, tool dispatch, checkpoint and save here without a fake read.
    monkeypatch.setattr('services.handlers.chat.content_tools.check_source_scope', lambda *args: None)


@pytest.mark.asyncio
async def test_model_cannot_save_or_adopt_without_read_then_actual_read_unlocks_save():
    r, p, h, q, row = await registered()
    r.checkpoint_base.pop(revision.KEY, None)
    before = r.task_execution_state
    r.task_followup_adopt = AsyncMock()
    save = call(co.SAVE, {'base': ref(row, 3), 'updates': [{'item_index': 3, 'item': {'body': '第三项修订后的完整正文。'}}]}, 'revision')
    await execute(r, p, h, q, [save])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'CONTENT_READ_REQUIRED'
    assert result['recovery']['calls'] == [{'content_source': {'artifact_id': row['artifact_id'], 'item_index': 3}}]
    assert r.task_execution_state == before
    r.task_followup_adopt.assert_not_awaited()
    h._execute_tool_calls.assert_not_awaited()
    result = await read(r, p, h, q, row, 3)
    assert result['ok'] and result['format_contract']['profile_id'] is None
    assert result['item']['body'] == '完整正文3，文字 ORIGINAL-3\n保持原产品结构，禁止水印。\n不能变形，条目限制3。'
    await execute(r, p, h, q, [save])
    assert json.loads(p.messages[-1]['content'])['ok']
    latest, = co.catalog(r.task_execution_state)
    _, document, item = co.load(r.task_execution_state, ref(latest, 3))
    assert item.body == '第三项修订后的完整正文。'
    assert latest['object_key'] == row['object_key']
    assert document.items[:2] == co.load(before, ref(row, 1))[1].items[:2]
    # A stale original is rejected with the current version as a candidate,
    # never silently substituted or sent back into a read-old loop.
    stale = revision.check_call(r, q, {**save, 'id': 'stale-revision'})
    assert stale['code'] == 'CONTENT_BASE_VERSION_CHANGED'
    assert stale['recovery']['available_content_sources'] == [{'artifact_id': latest['artifact_id'], 'item_index': 3}]
    # New immutable version must be read for another edit, not for execution.
    assert revision.check_call(r, q, call(co.SAVE, {'base': ref(latest, 3), 'updates': [{'item_index': 3, 'item': {'body': '再次修订'}}]}))['code'] == 'CONTENT_READ_REQUIRED'
    assert revision.check_call(r, q, call('generate_image', {'content_source': ref(latest, 3)})) is None


@pytest.mark.asyncio
async def test_read_of_wrong_item_or_previous_input_cannot_authorize_revision():
    r, p, h, q, row = await registered()
    r.checkpoint_base.pop(revision.KEY, None)
    await read(r, p, h, q, row, 1)
    save = call(co.SAVE, {'base': ref(row, 3), 'updates': [{'item_index': 3, 'item': {'body': '新正文'}}]})
    assert revision.check_call(r, q, save)['code'] == 'CONTENT_READ_REQUIRED'
    await read(r, p, h, q, row, 3)
    assert revision.check_call(r, q, save) is None
    next_request = replace(q, context_anchor=SimpleNamespace(input_message_id='new-input', base_revision=99))
    assert revision.check_call(r, next_request, save)['code'] == 'CONTENT_READ_REQUIRED'
    r.checkpoint_base = json.loads(json.dumps(r.checkpoint_base))
    assert revision.check_call(r, q, save) is None  # Same input restored.
    save['arguments'] = json.dumps({'base': {**ref(row, 3), 'sha256': '0' * 64},
        'updates': [{'item_index': 3, 'item': {'body': '新正文'}}]})
    assert revision.check_call(r, q, save)['code'] == 'CONTENT_READ_REQUIRED'


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', ['write', 'execute_saved'])
async def test_old_reference_contract_is_visible_without_active_skill_and_repair_is_specific(intent):
    from services.handlers.chat import content_tools
    from services.task_execution import request_resolution as rr
    r, p, h, q, row = await registered()
    row = configure_reference(r, row)
    r.checkpoint_base.pop(revision.KEY, None)
    r.checkpoint_base[rr.KEY] = {'target': {'kind': 'content', 'artifact_id': row['artifact_id'], 'revision': 1}}
    # Do not install request context here; target scope integration has its own tests.
    schema = co.save_schema(r)
    assert schema['function']['description'].startswith('按当前参考图提示词方法')
    offered = content_tools.model_tools([schema], saved_text_available=True,
        reuse_source=intent == 'execute_saved', revise_saved=intent == 'write')[0]
    projected = ImageReferences({'input_message_id': 'current'}).schema(offered)['function']
    params = projected['parameters']
    for example in params['examples']:
        Draft202012Validator(params).validate(example)
        assert json.dumps(example, ensure_ascii=False) in projected['description']
        item = co.ContentItem(title='案例', **example['updates'][0]['item'])
        assert valid_reference_prompts_presentation(co.ContentDocument(title='案例', display_prefix='# 参考图判断\n摄影参考。', items=(item,)))
    result = await read(r, p, h, q, row, 3)
    assert result['format_contract'] == content_format_contract(REFERENCE_PROMPTS_PROFILE)
    bad = {'base': ref(row, 3), 'updates': [{'item_index': 3, 'item': {'body': '漏掉标题的正文\n\n## 负面提示词\n不要水印。'}}]}
    before = r.task_execution_state
    await execute(r, p, h, q, [call(co.SAVE, bad, 'bad-presentation')])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == co.PRESENTATION_INCOMPLETE
    assert result['issues'] == [{'path': '$.updates[0].item.body', 'reason': '缺少必需章节：完整生图提示词'}]
    assert result['format_contract']['profile_id'] == REFERENCE_PROMPTS_PROFILE
    assert r.task_execution_state == before
    fixed = copy.deepcopy(bad)
    fixed['updates'][0]['item']['body'] = '## 完整生图提示词\n修订后的完整封面提示词。\n\n## 负面提示词\n不要水印。'
    await execute(r, p, h, q, [call(co.SAVE, fixed, 'fixed-presentation')])
    assert json.loads(p.messages[-1]['content'])['ok']
    latest, = co.catalog(r.task_execution_state)
    _, doc, _ = co.load(r.task_execution_state, ref(latest, 3))
    assert valid_reference_prompts_presentation(doc)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_scope_survives_authenticated_adoption_but_not_new_steer():
    from uuid import uuid4
    from services.task_execution.user_changes import UserChange, UserChanges
    r, p, h, q, row = await registered()
    r.checkpoint_base.pop(revision.KEY, None)
    await read(r, p, h, q, row, 3)
    save = call(co.SAVE, {'base': ref(row, 3), 'updates': [{'item_index': 3, 'item': {'body': '修订正文'}}]})
    original = copy.deepcopy(r.checkpoint_base)
    text = '第三个改成正方形，一千就行。'
    synthetic = UserChange(event_id=uuid4(), owner_task_id=r.task_id, message=text,
        message_sha256=hashlib.sha256(text.encode()).hexdigest(), delivered=True)
    r.checkpoint_base['_task_followup'] = {'input_sha256': synthetic.message_sha256}
    r.checkpoint_base['_task_followup_input_checkpoint'] = original
    r.task_user_changes = UserChanges(root_task_id=r.task_id, items=(synthetic,))
    assert revision.check_call(r, q, save) is None
    # Even an identical later steer is a separate authenticated input event.
    later = synthetic.model_copy(update={'event_id': uuid4()})
    r.task_user_changes = r.task_user_changes.model_copy(update={'items': (synthetic, later)})
    assert revision.check_call(r, q, save)['code'] == 'CONTENT_READ_REQUIRED'


@pytest.mark.asyncio
async def test_stored_generic_format_does_not_gain_an_unrelated_active_image_profile(monkeypatch):
    r, _, _, _, row = await registered()
    from services.task_execution import request_resolution as rr
    r.checkpoint_base[rr.KEY] = {'target': {'kind': 'content', 'artifact_id': row['artifact_id'], 'revision': 1}}
    monkeypatch.setattr('services.task_execution.direct_loop.active_image_methods',
        lambda _: {'reference': (REFERENCE_PROMPTS_PROFILE, True, 'reviewed')})
    schema = co.save_schema(r)['function']
    assert not schema['description'].startswith('按当前参考图提示词方法')
    assert all('完整生图提示词' not in e['updates'][0]['item']['body'] for e in schema['parameters']['examples'])
    params = schema['parameters']
    valid = {'document': {'title': '普通稿件', 'items': [{'title': '通知', 'body': '正文。'}]},
        'confirmation': {'tool': 'generate_image', 'wait_all': True}}
    Draft202012Validator(params).validate(valid)
    invalid = copy.deepcopy(valid)
    invalid['confirmation']['approved'] = [1]
    assert not Draft202012Validator(params).is_valid(invalid)


@pytest.mark.asyncio
async def test_tool_offer_requires_read_without_hiding_execution_or_changing_global_read_schema(monkeypatch):
    from services.handlers.chat.content_tools import READ_SCHEMA
    from services.task_execution import request_resolution as rr
    r, p, h, q, row = await registered()
    r.checkpoint_base.pop(revision.KEY, None)
    r.checkpoint_base[rr.KEY] = {'target': {'kind': 'content', 'artifact_id': row['artifact_id'],
        'revision': 1, 'item_indices': [3]}}
    tools = [co.save_schema(r), READ_SCHEMA, {'type': 'function', 'function': {'name': 'generate_image'}}]
    original = copy.deepcopy(READ_SCHEMA)
    offered = revision.offer(r, q, tools)
    assert [t['function']['name'] for t in offered] == ['read_conversation_text', 'generate_image']
    assert row['artifact_id'] in offered[0]['function']['description']
    assert READ_SCHEMA == original
    # Discovery cannot expose the hidden mutation tool before the source read.
    h.db = None
    monkeypatch.setattr('services.tools.runtime_context._check_identity', lambda *args: None)
    await execute(r, p, h, q, [call('find_available_tools', {'query': 'save_task_text'}, 'discover-save')])
    assert json.loads(p.messages[-1]['content'])['status'] == 'read_before_revision'
    await read(r, p, h, q, row, 3)
    assert revision.offer(r, q, tools) == tools
