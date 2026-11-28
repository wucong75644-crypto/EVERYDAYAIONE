"""Strict parsing must preserve native barriers, receipts and correction limits."""
import copy
import json

import pytest

from tests import test_skill_runtime_actor as skills
from tests.test_skill_runtime import state
from tests import test_task_execution_model_bridge as controls
from tests.test_model_directed_loop import case, execute
from services.task_execution.model_directed import PLAN_SCHEMA
from services.task_execution.reference_boundary import ModelReferenceBoundary
from tests.tool_context_test_utils import parse_json_tool_content


@pytest.mark.parametrize('raw', [
    'bad json',
    '{"skill_id":"report","skill_id":"report"}',
    '{"skill_id":"report","args":{"value":NaN}}',
])
@pytest.mark.parametrize('reverse', [False, True])
async def test_invalid_skill_control_keeps_complete_batch_barrier(raw, reverse):
    snapshots = []
    async def checkpoint(point, payload):
        snapshots.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}
    runtime = skills.actor(checkpoint)
    runtime.skill_runtime = state()
    await runtime.skill_runtime.initialize()
    prepared = skills.prepared()
    calls = [skills.call(args=raw), skills.call('web_search', '{}', 'business')]
    handler, _ = await skills.batch(runtime, prepared, list(reversed(calls)) if reverse else calls)
    handler._execute_tool_calls.assert_not_awaited()
    runtime.skill_runtime.source.load.assert_not_awaited()
    results = {m['tool_call_id']: parse_json_tool_content(m['content']) for m in prepared.messages if m.get('role') == 'tool'}
    assert results['activate-1']['code'] == 'SKILL_ACTIVATION_INVALID'
    assert results['business']['code'] == 'SKILL_ACTIVATION_BARRIER'
    assert len(results) == 2
    assert {m['tool_call_id'] for m in snapshots[-1]['messages'] if m.get('role') == 'tool'} == set(results)


@pytest.mark.parametrize('raw', ['{broken', '{"expected_revision":0,"expected_revision":0,"steps":[]}'])
@pytest.mark.parametrize('reverse', [False, True])
async def test_invalid_task_control_cannot_release_business_sibling(raw, reverse):
    runtime, prepared, handler, sink = controls.setup_case()
    calls = [{**controls.call(), 'arguments': raw}, controls.call('web_search', {'query': '本子'}, 'business')]
    await controls.execute(runtime, prepared, handler, sink, list(reversed(calls)) if reverse else calls, [])
    handler._execute_tool_calls.assert_not_awaited()
    assert runtime.task_execution_state.revision == 0
    results = {m['tool_call_id']: parse_json_tool_content(m['content']) for m in prepared.messages if m.get('role') == 'tool'}
    assert results['control']['code'] == 'TASK_CONTROL_INPUT_INVALID'
    assert results['control']['state_unchanged']
    assert results['business']['code'] == 'TASK_CONTROL_BATCH_BARRIER'


@pytest.mark.parametrize('raw', [
    '{broken', 'null', '[]',
    '{"steps":[],"steps":[{"step":"核对需求","status":"pending"}]}',
    '{"steps":[{"step":"核对需求","status":"pending"}],"explanation":NaN}',
])
async def test_strict_working_plan_rejection_keeps_native_contract(raw):
    runtime, prepared, handler, request = case()
    blocks = await execute(runtime, prepared, handler, request, [{'id': 'plan', 'name': 'update_task_plan', 'arguments': raw}])
    result = json.loads(blocks[0]['output'])
    assert result['code'] == 'TASK_WORKING_PLAN_INVALID' and result['state_unchanged']
    assert result['parameters'] == PLAN_SCHEMA['function']['parameters']
    assert result['errors'] and runtime.task_direct_plan is None
    handler._execute_tool_calls.assert_not_awaited()


def test_final_tool_contract_strips_host_reference_annotation_in_both_protocols():
    from services.tools import build_legacy_catalog
    from services.adapters.dashscope.responses import stable_tools, response_tools
    native = build_legacy_catalog().require('generate_image').to_schema()
    assert native['function']['parameters']['properties']['references']['items']['x-reference-kind'] == 'image'
    public = ModelReferenceBoundary(None).schema(native)
    for wire in (stable_tools([public]), response_tools([public], ())):
        assert 'x-reference-kind' not in json.dumps(wire)
        function = wire[0].get('function', wire[0])
        assert function['parameters'] == public['function']['parameters']
