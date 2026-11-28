"""Customer old-draft requests expose only actions whose prerequisites are met."""
import copy
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from schemas.message import TextPart
from services.handlers.chat import content_tools
from services.task_execution import content_objects as co, content_revision, request_resolution as rr
from services.task_execution import saved_image_revision
from services.task_execution.image_reference_catalog import for_runtime
from services.tools import build_legacy_catalog
from tests.semantic_fixtures import semantic_request
from tests.test_content_objects import registered, ref
from tests.test_content_revision_contract import read
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


async def customer_case(indices=(3,), **fields):
    r, p, h, q, row = await registered()
    r.checkpoint_base.pop(content_revision.KEY, None)
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    target = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': row['artifact_id'],
              'revision': row['revision'], 'item_indices': list(indices)}
    r.checkpoint_base[rr.KEY] = {'status': 'resolved', 'target': target}
    q = semantic_request(q, 'execute_saved', target=target, **fields)
    q = replace(q, content=[TextPart(text='第三个清楚点，改成两千，直接做成图。')])
    rr.freeze(r, q, target)
    p.content_controls_available = True
    return r, p, h, q, row


def tools_for(r, q):
    refs = for_runtime(r)
    tools = content_tools.model_tools([co.save_schema(r), build_legacy_catalog().require('generate_image').to_schema()],
        reuse_source=True, saved_text_available=True)
    tools = content_revision.offer(r, q, [*tools, content_tools.READ_SCHEMA])
    return {t['function']['name']: t['function'] for t in refs.tools(tools)}


def workflow(function):
    text = function['description'].split('当前已选稿件的修订流程（后台核验状态，不是新的选图建议）：')[1]
    return json.JSONDecoder().raw_decode(text)[0]


@pytest.mark.asyncio
async def test_customer_reads_then_saves_then_can_generate_without_first_old_draft_rejection(monkeypatch):
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    original = copy.deepcopy(r.checkpoint_base)
    offered = tools_for(r, q)
    assert 'generate_image' not in offered and co.SAVE not in offered
    step = workflow(offered[content_tools.READ])
    assert step['stage'] == 'read_required'
    args = step['read_calls'][0]['arguments']
    assert args['content_source']['content_ref'].startswith('doc_')
    assert row['artifact_id'] not in json.dumps(step)
    Draft202012Validator(offered[content_tools.READ]['parameters']).validate(args)
    assert r.checkpoint_base == original  # Offering tools never grants approval or counts rejections.
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    await read(r, p, h, q, row, 3)
    offered = tools_for(r, q)
    assert co.SAVE in offered and 'generate_image' not in offered
    assert workflow(offered[co.SAVE])['stage'] == 'revision_required'
    p.content_visible_tools = offered
    old = co.load(r.task_execution_state, ref(row, 3))[2]
    await execute(r, p, h, q, [call(co.SAVE, {'base': for_runtime(r).encode(ref(row, 3)),
        'updates': [{'item_index': 3, 'item': {'body': old.body + '\n2K清晰度。',
            'options': {**old.options, 'resolution': '2K'}}}], 'continue_execution': True}, 'customer-revision')])
    assert json.loads(p.messages[-1]['content'])['ok']
    latest, = co.catalog(r.task_execution_state)
    assert latest['revision'] == row['revision'] + 1
    assert 'generate_image' in tools_for(r, q)
    assert saved_image_revision.check(r, q, [call('generate_image', {'content_source': ref(latest, 3)})],
        r.task_execution_state) == {}
    assert not (r.checkpoint_base.get(saved_image_revision.KEY) or {}).get('rejections')
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_spec_original_execution_stays_available_without_reading():
    r, p, h, q, row = await customer_case(size={'aspect_ratio': '1:1', 'resolution': '1K'})
    offered = tools_for(r, q)
    assert 'generate_image' in offered
    assert '当前已选稿件的修订流程' not in offered[content_tools.READ]['description']
    assert not saved_image_revision.pending_execution(r, q)


@pytest.mark.asyncio
async def test_mixed_selection_keeps_ready_item_but_rejects_pending_item_in_public_schema():
    r, p, h, q, row = await customer_case(indices=(1, 3), size_rules=[
        {'scope': 'items', 'item_indices': [3], 'size': {'resolution': '2K'}}])
    offered = tools_for(r, q)
    schema = offered['generate_image']['parameters']
    refs = for_runtime(r)
    assert Draft202012Validator(schema).is_valid({'content_source': refs.encode({'artifact_id': row['artifact_id'], 'item_index': 1})})
    rejected = {'content_source': refs.encode({'artifact_id': row['artifact_id'], 'item_index': 3})}
    assert not Draft202012Validator(schema).is_valid(rejected)
    from services.tools.argument_validation import _issues
    assert _issues(rejected, schema, image_hints=False)
    for step in workflow(offered[content_tools.READ])['read_calls']:
        Draft202012Validator(offered[content_tools.READ]['parameters']).validate(step['arguments'])


@pytest.mark.asyncio
async def test_history_metadata_before_adoption_uses_same_requirement_as_backend():
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    state = r.task_execution_state
    native_groups = rr.groups(r)
    before = saved_image_revision.check(r, q, [call('generate_image', {'content_source': ref(row, 3)}, 'old')], state)
    r.task_content_groups = native_groups
    r.task_execution_state = None
    pending = saved_image_revision.pending_execution(r, q)
    assert pending[0]['required_changes'] == before['old']['required_changes']
    assert 'generate_image' not in tools_for(r, q)


@pytest.mark.asyncio
async def test_discovery_cannot_offer_generation_before_saved_revision(monkeypatch):
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    p.content_visible_tools = tools_for(r, q)
    h.db = None
    monkeypatch.setattr('services.tools.runtime_context._check_identity', lambda *args: None)
    await execute(r, p, h, q, [call('find_available_tools', {'query': 'generate_image'}, 'discover')])
    result = json.loads(p.messages[-1]['content'])
    assert result['status'] == 'revision_before_execution' and result['tools'] == []
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['image', 'current', 'request'])
async def test_other_image_entry_targets_do_not_enter_saved_revision_workflow(kind):
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    r.checkpoint_base[rr.KEY]['target'] = {'kind': kind}
    assert not saved_image_revision.pending_execution(r, q)
    assert 'generate_image' in tools_for(r, q)


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', ['write', 'write_then_submit_images', 'edit'])
async def test_content_only_revision_without_size_changes_still_reads_before_saving(intent):
    r, p, h, q, row = await customer_case()
    q = semantic_request(q, intent, target=content_tools.semantic_context(q, r).target.model_dump(exclude_none=True))
    offered = tools_for(r, q)
    assert 'generate_image' not in offered
    assert workflow(offered[content_tools.READ])['stage'] == 'read_required'
    assert saved_image_revision.check(r, q, [call('generate_image', {'content_source': ref(row, 3)}, 'old')],
        r.task_execution_state)['old']['code'] == saved_image_revision.CODE


def test_customer_error_audit_includes_both_chat_and_responses_protocols():
    from tests.manual_two_bug_customer_acceptance import wire_errors
    failed = {'ok': False, 'code': 'MODEL_TOOL_ARGUMENTS_INVALID'}
    chat = {'kind': 'formal_model_request', 'body': {'messages': [{'role': 'tool',
        'tool_call_id': 'first', 'content': json.dumps(failed) + '\n平台元数据'}]}}
    response = {'kind': 'formal_model_request', 'body': {'input': [{'type': 'function_call_output',
        'call_id': 'first', 'output': json.dumps(failed)}]}}
    assert wire_errors([chat, response]) == [{'call_id': 'first', 'result': failed}]
