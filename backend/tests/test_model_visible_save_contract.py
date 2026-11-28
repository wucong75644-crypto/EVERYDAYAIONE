"""Exercise public messages, syntax feedback and actual customer success proofs."""
import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import content_revision as cr, content_objects as co
from services.task_execution.reference_boundary import ArgumentSyntaxError, ModelReferenceBoundary
from services.tools.argument_validation import decode_tool_arguments, argument_syntax_issue
from tests.test_saved_revision_workflow import customer_case, tools_for, workflow
from tests.test_content_revision_contract import configure_reference


async def test_actual_model_stage_and_tool_use_the_same_public_fields():
    from services.handlers.chat.execution_engine import _read_turn
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.adapters.types import StreamChunk
    r, p, h, q, row = await customer_case(size={'resolution': '4K'})
    configure_reference(r, row)
    received = []
    async def stream_chat(**kwargs):
        received.extend(kwargs['messages'])
        yield StreamChunk(finish_reason='stop')
    p.adapter = SimpleNamespace(stream_chat=stream_chat)
    p.stream_kwargs = {}
    offers = tools_for(r, q)
    await _read_turn(p, [{'type': 'function', 'function': t} for t in offers.values()],
        r.cancellation_event, CollectingExecutionSink(), StreamTotals(), [], r, request=q)
    message = next(m['content'] for m in received if isinstance(m.get('content'), str)
        and m['content'].startswith('[Current saved draft action]'))
    stage = json.JSONDecoder().raw_decode(message.split('\n', 1)[1])[0]
    tool_stage = workflow(offers['read_conversation_text'])
    assert stage['items'][0]['required_update_fields'] == tool_stage['items'][0]['required_update_fields'] == ['options', 'prompt', 'presentation']
    assert cr.pending_reads(r, q)  # Model projection is not a read proof.


async def test_explanation_stop_and_discovery_recovery_share_public_field_projection():
    from tests.test_content_completion_workflow import revision_case
    from services.task_execution.text_delivery import check
    r, p, h, q, row = await revision_case(reference=True)
    result = check(r, q, {'purpose': 'explanation'})
    assert result['pending_action']['items'][0]['required_update_fields'] == ['options', 'prompt', 'presentation']
    native = {'recovery': {'items': [{'required_update_fields': ['options', 'body', 'display_prefix']} ]},
        'options': {'body': 'literal user data'}, 'body': 'body and display_prefix are authored prose'}
    public = cr.public_fields(r, native)
    assert public['recovery']['items'][0]['required_update_fields'] == ['options', 'prompt', 'presentation']
    assert native['recovery']['items'][0]['required_update_fields'] == ['options', 'body', 'display_prefix']
    assert public['options'] == native['options'] and public['body'] == native['body']


@pytest.mark.parametrize('raw', [
    '{"updates":[{"item":{"prompt":{"positive":"保留{本子}和[文字]","negative":"水印"}}]}',
    '{"updates":[{"item":{"presentation":{"reference_usage":"布面\\n正方形1K"}}],"continue_execution":true}',
])
def test_missing_update_closure_is_diagnosed_without_repairing_or_repeating_schema(raw):
    from tests.test_save_contract_failure_paths import contract
    refs, _, public = contract(True, revise_saved=True)
    with pytest.raises(ValueError) as caught:
        decode_tool_arguments(raw)
    error = ArgumentSyntaxError(raw, caught.value, co.SAVE)
    result = ModelReferenceBoundary(refs).repair(error, public)
    issue, = result['issues']
    assert issue['path'] == '$.updates[0]'
    assert (issue['kind'], issue['expected_token'], issue['actual_token']) == ('container_not_closed', '}', ']')
    assert result['submission_state'] == 'not_dispatched' and not result['accepted']
    assert 'parameters' not in result and result['tool_name'] == co.SAVE
    assert result['allowed_forms'] == [['base', 'updates']]
    with pytest.raises(ValueError):
        decode_tool_arguments(raw)


@pytest.mark.parametrize('raw', ['{"a":1 "b":2}', '{"a":"未结束', '{"a":1,"a":2}', '{"a":NaN}'])
def test_other_syntax_errors_do_not_claim_a_missing_object_closure(raw):
    with pytest.raises(ValueError) as caught:
        decode_tool_arguments(raw)
    assert 'expected_token' not in argument_syntax_issue(raw, caught.value)


def test_public_examples_have_concrete_sections_and_decode_to_valid_native_input():
    from tests.test_save_contract_failure_paths import contract
    from services.task_execution.reference_prompt_format import workflow_examples
    refs, _, tool = contract(True, revise_saved=True)
    state = {'items': [{'content_source': tool['parameters']['examples'][0]['base'],
        'required_update_fields': ['options', 'prompt', 'presentation'],
        'saved_options': {'aspect_ratio': '1:1', 'resolution': '2K'}, 'requested_size': {'resolution': '1K'}}],
        'continue_execution': True}
    from tests.model_update_fixtures import model_arguments
    sample, = [model_arguments(s) for s in workflow_examples(tool['parameters']['examples'], state)]
    Draft202012Validator(tool['parameters']).validate(sample)
    native = refs.decode_call(co.SAVE, sample)
    parsed = co.SaveContent.model_validate(native)
    assert parsed.updates[0].item.options == {'aspect_ratio': '1:1', 'resolution': '1K'}
    assert '## 完整生图提示词' in parsed.updates[0].item.body
    assert '原稿全部' not in json.dumps(sample, ensure_ascii=False)
    from services.task_execution.methods import presentation_issues, REFERENCE_PROMPTS_PROFILE
    document = co.ContentDocument(title='结构示例', display_prefix='# 参考图判断\n\n产品摄影参考图。',
        items=(co.ContentItem(title='蓝白灯塔', **parsed.updates[0].item.model_dump(exclude_unset=True)),))
    assert not presentation_issues(document, REFERENCE_PROMPTS_PROFILE)


async def test_customer_gate_rejects_failed_save_and_stale_success_receipts():
    from tests.manual_request_context_customer_acceptance import assert_saved_draft
    r, p, h, q, row = await customer_case()
    base = {'_task_execution': r.task_execution_state.model_dump(mode='json'), '_content_response_v1': {
        'phase': 'rejected', 'owner_task_id': r.task_id, 'artifact_id': row['artifact_id']}}
    with pytest.raises(AssertionError, match='No committed save'):
        assert_saved_draft({'checkpoint': base}, r.task_id)
    stale = copy.deepcopy(base)
    stale['_content_response_v1'].update(phase='committed', owner_task_id='another-task')
    with pytest.raises(AssertionError, match='No committed save'):
        assert_saved_draft({'checkpoint': stale}, r.task_id)
