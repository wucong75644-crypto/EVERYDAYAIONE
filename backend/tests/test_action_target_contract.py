"""Saved recipes and actual image edits keep different executable contracts."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.agent.image.ecommerce_planner.workflow import ImageRequestContext, WorkflowBinding
from services.task_execution import request_resolution as rr, action_target_contract as contract
from services.task_execution.contracts import TaskError
from services.task_execution.image_reference_catalog import for_runtime
from tests.test_request_catalog import catalog_case
from tests.test_task_execution_model_bridge import call


@pytest.mark.parametrize('short', [False, True])
def test_complete_saved_and_image_examples_obey_final_selection_schema(monkeypatch, short):
    r, _, _, _, _, _ = catalog_case(monkeypatch)
    schema = rr.selection_schema(r)
    if short:
        rr.context(r)['image_reference_version'] = 1
        schema = for_runtime(r).schema(schema)
    params = schema['function']['parameters']
    Draft202012Validator.check_schema(params)
    from services.tools.argument_validation import _issues
    validator = Draft202012Validator(params)
    for example in params['examples']:
        validator.validate(example)
        if example['request_context']['target']['kind'] == 'content':
            for wrong in ('edit', 'generate'):
                invalid = {**example, 'intent': wrong}
                assert not validator.is_valid(invalid)
                assert _issues(invalid, params)
    if short:
        ref = next(iter(for_runtime(r).references))
        for intent in ('edit', 'write', 'write_then_submit_images', 'explain'):
            validator.validate({'intent': intent, 'request_context': {'focus': 'current_request',
                'target': {'kind': 'image', 'basis': 'active_request', 'image_ref': ref}}})
        assert not [e for e in params['examples'] if e['request_context']['target']['kind'] == 'image']
        # An unbound directory may allow selection without implying a default candidate.


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong', ['edit', 'generate'])
async def test_bad_correction_does_not_mutate_selection_approval_or_workflow(monkeypatch, wrong):
    r, p, _, q, rows, _ = catalog_case(monkeypatch)
    target = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'old-a',
        'revision': 1, 'item_indices': [1], 'source_message_id': rows[2]['message_id']}
    frame = ImageRequestContext(target=target, focus='selected_target',
        media={'tool': 'generate_image', 'approved': [1], 'artifact_id': 'old-a', 'revision': 1})
    rr.freeze_frame(r, q, frame, 'execute_saved')
    original = copy.deepcopy(rr.binding(r))
    workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='execute_saved', request_context=frame), save=AsyncMock())
    p.ecom_workflow = workflow
    old_value = workflow.value.model_dump(mode='json')
    with pytest.raises(TaskError, match=contract.CODE):
        await rr.select(r, q, p, {'intent': wrong, 'request_context': {'target': target, 'focus': 'selected_target'}})
    assert rr.binding(r) == original
    assert workflow.value.model_dump(mode='json') == old_value
    workflow.save.assert_not_awaited()
    r.workflow_projection = {'intent': wrong}
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': 'old-a', 'item_index': 1}}))[0] == contract.CODE


@pytest.mark.parametrize('intent', contract.CONTENT_INTENTS)
def test_legal_saved_actions_keep_their_existing_meaning(intent):
    contract.validate(intent, 'content')


def test_generic_text_or_video_consumers_have_no_image_action_to_reclassify():
    contract.validate(None, 'content')
