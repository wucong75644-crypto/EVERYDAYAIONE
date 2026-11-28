"""Authority, version lineage and confirmation survive real owner transitions."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.handlers.chat import content_tools
from services.task_execution import content_objects as co, content_revision as cr
from services.task_execution import content_completion, request_resolution as rr, request_window as window
from services.task_execution.content_delivery import input_revision as response_identity
from services.task_execution.content_action import evaluate
from services.task_execution.contracts import TaskError
from services.task_execution.image_reference_catalog import for_runtime
from tests.test_content_revision_contract import read
from tests.test_saved_revision_workflow import customer_case, tools_for
from tests.test_model_directed_loop import execute
from tests.test_content_objects import ref
from tests.model_update_fixtures import call


def freeze(r, q, **media):
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    before = content_tools.semantic_context(q, r)
    frame = ImageRequestContext.model_validate(before.model_dump(mode='json'))
    if media:
        from services.task_execution.item_confirmation import MediaDirective
        frame.media = MediaDirective.model_validate(media)
    return rr.freeze_frame(r, q, frame, 'execute_saved')


async def test_bound_action_wins_over_all_mutable_workflow_copies():
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    selected = freeze(r, q)
    assert 'target' not in selected['action']['request_context']
    assert 'focus' not in selected['action']['request_context']
    r.workflow_projection = {'intent': 'edit', 'request_context': {'size': {'resolution': '4K'}}}
    q.params['_ecom_workflow'] = {'intent': 'generate', 'request_context': {}}
    assert content_tools.semantic_intent(q, r) == 'execute_saved'
    frame = content_tools.semantic_context(q, r)
    assert frame.target.artifact_id == row['artifact_id'] and frame.size_for(3)['resolution'] == '2K'
    assert window.projection(r, q)['intent'] == 'execute_saved'
    assert evaluate(r, q).completion == content_completion.phase(r, q)


async def test_same_input_adoption_has_one_identity_but_real_steering_invalidates_it():
    r, p, h, q, _ = await customer_case()
    original = response_identity(r, q)
    assert original == cr.input_revision(r, q)
    r.checkpoint_base['_task_followup_input_checkpoint'] = {'_task_user_changes': {'items': []}}
    r.checkpoint_base['_task_followup'] = {'input_sha256': 'same-message'}
    event = SimpleNamespace(event_id=uuid4(), owner_task_id=r.task_id, message_sha256='same-message', delivered=True)
    r.task_user_changes = SimpleNamespace(items=(event,))
    assert response_identity(r, q) == cr.input_revision(r, q) == original
    additional = SimpleNamespace(event_id=uuid4(), owner_task_id=r.task_id, message_sha256='new-attachment', delivered=True)
    r.task_user_changes.items = (event, additional)
    assert response_identity(r, q) == cr.input_revision(r, q) != original
    other = SimpleNamespace(event_id=uuid4(), owner_task_id=str(uuid4()), message_sha256='unrelated', delivered=True)
    changed = response_identity(r, q)
    r.task_user_changes.items += (other,)
    assert response_identity(r, q) == changed


async def test_stale_action_never_falls_back_to_an_old_workflow():
    r, p, h, q, _ = await customer_case()
    freeze(r, q)
    r.workflow_projection = {'intent': 'execute_saved', 'request_context': {}}
    newer = replace(q, context_anchor=SimpleNamespace(input_message_id=str(uuid4()), base_revision=1))
    assert content_tools.semantic_intent(newer, r) is None
    assert content_tools.semantic_context(newer, r).target is None


@pytest.mark.parametrize('new_wait', [False, True])
async def test_ready_saved_revision_cannot_acquire_waits_from_its_execution_directive(monkeypatch, new_wait):
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    # Reproduce the real provider's contradictory ready-plan declaration.
    selected = freeze(r, q, tool='generate_image', wait_for=[3], approved=[3],
        artifact_id=row['artifact_id'], revision=row['revision'], confirmation_only=False)
    frame = content_tools.semantic_context(q, r)
    assert not frame.media.wait_for and not frame.media.approved
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    await read(r, p, h, q, row, 3)
    old = co.load(r.task_execution_state, ref(row, 3))[2]
    args = {'base': ref(row, 3), 'updates': [{'item_index': 3,
        'item': {'body': old.body + '\n2K清晰度。', 'options': {**old.options, 'resolution': '2K'}}}],
        'continue_execution': not new_wait}
    if new_wait:
        args['confirmation'] = {'tool': 'generate_image', 'wait_for': [3]}
    await execute(r, p, h, q, [call(co.SAVE, args, 'ready-revision')])
    assert json.loads(p.messages[-1]['content'])['ok']
    latest, = co.catalog(r.task_execution_state)
    record = co._load_record(r.task_execution_state, ref(latest, 3))[1]
    assert record.confirmation_items == ((3,) if new_wait else ())
    assert record.confirmation_tool == ('generate_image' if new_wait else None)
    assert cr.saved_reference(r, q, selected['target'])['artifact_id'] == latest['artifact_id']
    if not new_wait:
        from services.task_execution.content_source import prepare_selected
        await prepare_selected(r, q, p.execution_context)
        assert 'generate_image' in tools_for(r, q)
        assert evaluate(r, q).completion['content_source']['artifact_id'] == latest['artifact_id']


async def test_same_turn_unrelated_document_cannot_impersonate_a_saved_revision():
    r, p, h, q, row = await customer_case()
    freeze(r, q)
    original = r.task_execution_state
    from tests.test_content_objects import document
    from services.tools.catalog import build_capability_catalog
    unrelated, artifact, *_ = co.save(r, call(co.SAVE, {'document': document()}, 'another-object'), build_capability_catalog())
    r.task_execution_state = unrelated
    rejection = rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': artifact.id, 'item_index': 3}}))
    assert rejection[0] == 'REQUEST_TARGET_CONTENT_MISMATCH'
    assert r.task_execution_state.operations == original.operations


async def test_waiting_approval_must_match_selected_object_and_indices():
    r, p, h, q, row = await customer_case()
    target = {k: v for k, v in rr.binding(r)['target'].items() if k in rr.Target.model_fields}
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    from services.task_execution.item_confirmation import MediaDirective
    frame = ImageRequestContext(target=target, media=MediaDirective(tool='generate_image', approved=[1]))
    with pytest.raises(TaskError, match='CONTENT_CONFIRMATION_DIFFERENT_ITEM'):
        window.normalize_media(frame, row)


def test_handoff_call_recovery_uses_durable_proposal_not_chat_transcript():
    from services.task_execution.direct_loop import restored_content_calls
    root, owner = str(uuid4()), str(uuid4())
    chosen = {'id': 'saved-choice', 'name': 'generate_video', 'arguments': '{"content_source":{"artifact_id":"a","item_index":3}}'}
    initial = {'messages': [{'role': 'assistant', 'tool_calls': [{'id': 'fake', 'function': {
        'name': 'generate_image', 'arguments': '{}'}}]}], '_content_response_v1': {
        'phase': 'proposed', 'owner_task_id': owner, 'handoff_root_task_id': root, 'calls': [chosen],
        'calls_sha256': __import__('services.task_execution.reducer', fromlist=['digest']).digest([chosen])}}
    runtime = SimpleNamespace(task_id=owner, task_execution_state=SimpleNamespace(root_task_id=root),
        task_user_changes=SimpleNamespace(items=[SimpleNamespace(event_id=uuid4(), delivered=True,
            owner_task_id=owner, message_sha256='new-message')]), task_direct_seen=set(),
        checkpoint_base={'_task_followup': {'source_root_task_id': root, 'input_sha256': 'new-message'},
            '_task_followup_input_checkpoint': initial})
    resumed, = restored_content_calls(runtime, {'generate_video'})
    assert resumed['name'] == 'generate_video' and resumed['id'] == 'saved-choice:continued'
    assert restored_content_calls(runtime, {'generate_image'}) == []
    initial['_content_response_v1']['owner_task_id'] = str(uuid4())
    assert restored_content_calls(runtime, {'generate_video'}) == []


async def test_actual_workflow_save_only_projects_the_bound_decision():
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    freeze(r, q)
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, WorkflowBinding
    flow = EcommerceWorkflow.__new__(EcommerceWorkflow)
    flow.runtime, flow.request = r, q
    r.skill_coordination, r.skill_runtime = True, None
    flow.value = WorkflowBinding(kind='ordinary_image', root_task_id=str(r.task_id),
        window_task_id=str(r.task_id), generation_run_id=str(uuid4()), intent='edit', image_count=8)
    await flow.save()
    assert flow.value.intent == 'execute_saved'
    assert flow.value.image_count is None
    assert flow.value.request_context.target.artifact_id == row['artifact_id']
    assert r.workflow_projection['input_revision'] == window.input_revision(r, q)


async def test_real_steer_keeps_workflow_stale_until_reclassified():
    from unittest.mock import AsyncMock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, WorkflowBinding
    r, p, h, q, _ = await customer_case()
    freeze(r, q)
    prior = window.input_revision(r, q)
    q = replace(q, context_anchor=SimpleNamespace(input_message_id=str(uuid4()), base_revision=2))
    flow = EcommerceWorkflow.__new__(EcommerceWorkflow)
    flow.runtime, flow.request = r, q
    r.skill_coordination, r.skill_runtime = True, None
    flow.value = WorkflowBinding(kind='ordinary_image', root_task_id=str(r.task_id),
        window_task_id=str(r.task_id), generation_run_id=str(uuid4()), intent='execute_saved', input_revision=prior)
    flow.identify = AsyncMock()
    await flow.save()
    assert flow.value.input_revision == prior
    assert content_tools.semantic_intent(q, r) is None
    await flow.refresh_action()
    flow.identify.assert_awaited_once()


async def test_failed_checkpoint_restores_request_decision_and_all_projections(monkeypatch):
    from unittest.mock import AsyncMock
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    # Exercise the real tool transaction wrapper, not only its snapshot helper.
    r, p, h, q, _ = await customer_case()
    freeze(r, q)
    p.ecom_workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=str(r.task_id),
        window_task_id=str(r.task_id), generation_run_id=str(uuid4()), intent='execute_saved'), route=None)
    before = copy.deepcopy(r.checkpoint_base)
    projection = window.capture_projection(r, p, q)
    async def fail(**kwargs):
        r.checkpoint_base[rr.KEY]['action']['intent'] = 'edit'
        r.workflow_projection = {'intent': 'edit'}
        q.params['_ecom_workflow'] = {'intent': 'edit'}
        p.ecom_workflow.value = p.ecom_workflow.value.model_copy(update={'intent': 'edit'})
        raise RuntimeError('checkpoint rejected')
    monkeypatch.setattr('services.handlers.chat.execution_engine._execute_tools_impl', fail)
    from services.handlers.chat.execution_engine import _execute_tools
    with pytest.raises(RuntimeError, match='checkpoint rejected'):
        await _execute_tools(runtime=r, prepared=p, request=q, blocks=[])
    assert r.checkpoint_base == before
    assert window.capture_projection(r, p, q) == projection


@pytest.mark.parametrize('all_items', [False, True])
async def test_future_waiting_conditions_have_one_authority_and_no_approval_fields(monkeypatch, all_items):
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    from services.task_execution.item_confirmation import WaitingConditions
    from jsonschema import Draft202012Validator
    r, p, h, q, row = await customer_case()
    condition = {'tool': 'generate_image', **({'wait_all': True} if all_items else {'wait_for': [3]})}
    frame = content_tools.semantic_context(q, r)
    frame.confirmation = WaitingConditions.model_validate(condition)
    rr.freeze_frame(r, q, frame, 'write')
    schema = ImageRequestContext.model_json_schema()
    Draft202012Validator(schema).validate(frame.model_dump(mode='json'))
    waiting_schema = schema['$defs']['WaitingConditions']
    for example in waiting_schema['examples']:
        Draft202012Validator(waiting_schema).validate(example)
        WaitingConditions.model_validate(example)
        assert 'approved' not in example
    assert not Draft202012Validator(WaitingConditions.model_json_schema()).is_valid({**condition, 'approved': [3]})
    assert not Draft202012Validator(WaitingConditions.model_json_schema()).is_valid({'wait_for': [3]})
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    await read(r, p, h, q, row, 3)
    old = co.load(r.task_execution_state, ref(row, 3))[2]
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row, 3), 'updates': [{'item_index': 3,
        'item': {'body': old.body + '\n保留新稿等待要求。'}}], 'continue_execution': False}, 'waiting-revision')])
    assert json.loads(p.messages[-1]['content'])['ok']
    newest, = co.catalog(r.task_execution_state)
    record = co._load_record(r.task_execution_state, ref(newest, 3))[1]
    assert record.confirmation_items == ((1, 2, 3) if all_items else (3,))
    assert evaluate(r, q).completion is None


async def test_explicit_handoff_works_when_selected_source_is_the_default_catalog_group(monkeypatch):
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    from tests.test_content_source_scope import cross_case, patch
    from services.task_execution.content_source import prepare_calls
    from services.task_execution.direct_loop import resolve_content_handoff, can_resume_content_calls
    from services.task_execution.reference_boundary import for_runtime, NativeToolCall
    r, p, h, q, row, original, _, _ = await cross_case('none')
    r.task_followup_candidate = {'state': original.model_dump(mode='json')}
    r.task_content_groups = [g for g in r.task_content_groups if g['artifact_id'] != row['artifact_id']]
    await read(r, p, h, q, row, 3)
    selected = patch(r, row, original)
    c = NativeToolCall(name=co.SAVE, call_id=selected['id'],
        arguments=for_runtime(r).arguments(json.loads(selected['arguments']), tool=co.SAVE))
    owner = r.task_followup_candidate
    assert not await prepare_calls(r, q, [c], p.execution_context)
    handoff = await resolve_content_handoff(r, [c])
    assert handoff['state']['root_task_id'] == str(original.root_task_id)
    assert can_resume_content_calls(r, [c], q, source=handoff)
    assert r.task_followup_candidate is owner


@pytest.mark.parametrize('reverse', [False, True])
def test_known_new_revision_retires_only_its_own_stale_model_selector(reverse):
    from services.task_execution.content_reference_catalog import ContentReferences, ContentReferenceError
    from services.task_execution.reference_boundary import NativeContext
    refs = ContentReferences('input-snapshot')
    versions = [{'artifact_id': 'old', 'object_key': 'document-a', 'revision': 1, 'items': []},
                {'artifact_id': 'new', 'object_key': 'document-a', 'revision': 2, 'items': []}]
    for row in reversed(versions) if reverse else versions:
        refs.add(NativeContext(row))
    refs.add(NativeContext({'artifact_id': 'other', 'object_key': 'document-b', 'revision': 1, 'items': []}))
    assert set(refs.available()) == {refs.reverse['new'], refs.reverse['other']}
    with pytest.raises(ContentReferenceError, match='CONTENT_REFERENCE_VERSION_CHANGED'):
        refs.decode({'content_ref': refs.reverse['old'], 'item_index': 1}, '$.base')
    assert refs.decode({'content_ref': refs.reverse['new'], 'item_index': 1}, '$.base')['artifact_id'] == 'new'
