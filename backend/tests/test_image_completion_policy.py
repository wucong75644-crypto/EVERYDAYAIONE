"""Current-input scope plus native effects, never status prose, can end a turn."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from services.task_execution.image_completion_policy import ImageCompletionScope, verified_scope, completion_decision
from services.task_execution.image_continuation import advance_images
from services.task_execution.request_window import input_revision
from tests.test_model_directed_records import operation_state


def completed_case():
    bootstrap, source = operation_state('accepted')
    owner = str(bootstrap.task_id)
    part = {'type': 'image', 'url': 'https://example.invalid/image.png'}
    child = dict(operation_id=source.operations[0].id, child_task_id=source.operations[0].receipt_ref[5:],
        user_id=str(source.actor_user_id), org_id=None, conversation_id=str(source.conversation_id),
        origin={'parent_task_id': owner, 'tool_call_id': 'first', 'turn_id': 'None'}, status='completed',
        phase='published', message_status='completed', part=part, message_content=[part],
        message_id=str(uuid4()), message_revision=1, generation_task_id=source.operations[0].receipt_ref[5:], part_sha256='a'*64)
    cp = {'messages': [{'role': 'user', 'content': '第三个做成图，正方形，一千就行。'}],
          '_request_context_v1': {'context_revision': 8}}
    rr = dict(status='resolved', input_message_id=str(bootstrap.input_message_id), execution_task_id=owner,
        target={'kind': 'content', 'item_indices': [3]}, action={'intent': 'execute_saved', 'image_count': 1,
          'request_context': {'image_completion_scope': {'after_images': 'terminal', 'evidence': ['第三个做成图']}}})
    cp['_request_resolution_v1'] = rr
    rr['input_revision'] = input_revision(SimpleNamespace(task_id=owner, checkpoint_base=cp),
        SimpleNamespace(context_anchor=SimpleNamespace(input_message_id=rr['input_message_id'], base_revision=8)))
    task = dict(input_message_id=rr['input_message_id'], delivery_context={'channel': 'web', 'source_owner_task_id': owner})
    candidate, _, failures = advance_images(source, [child], owner_task_id=uuid4(), base_revision=2)
    return source, candidate, cp, task, [child], failures


def test_native_image_only_completion_and_unknown_scope():
    values = completed_case()
    assert completion_decision(*values) == 'finish'
    cp = values[2]
    del cp['_request_resolution_v1']['action']['request_context']['image_completion_scope']
    assert completion_decision(*values) == 'resume_model'


@pytest.mark.parametrize('scenario', ['continue', 'unknown', 'no_evidence', 'new_owner', 'new_input', 'stale_revision',
    'deliveries', 'continue_delivery', 'confirmation', 'waiting', 'missing_image', 'count', 'failure', 'other_channel',
    'working_plan', 'pending_batch', 'undelivered_change', 'accepted_operation', 'uncertain_operation'])
def test_no_early_completion_for_unmet_obligations(scenario):
    from services.task_execution.model_directed import update_working_plan
    from services.task_execution.user_changes import UserChange, UserChanges
    import hashlib
    source, candidate, cp, task, children, failures = completed_case()
    rr = cp['_request_resolution_v1']; frame = rr['action']['request_context']
    if scenario in {'continue', 'unknown'}: frame['image_completion_scope']['after_images'] = scenario
    elif scenario == 'no_evidence': frame['image_completion_scope']['evidence'] = []
    elif scenario == 'new_owner': task['delivery_context']['source_owner_task_id'] = str(uuid4())
    elif scenario == 'new_input': task['input_message_id'] = str(uuid4())
    elif scenario == 'stale_revision': cp['_request_context_v1']['context_revision'] += 1
    elif scenario == 'deliveries': frame['deliveries'] = ['code_execute']
    elif scenario == 'continue_delivery': frame['continue_delivery'] = True
    elif scenario == 'confirmation': frame['confirmation'] = {'tool': 'generate_video', 'wait_all': True}
    elif scenario == 'waiting': frame['media'] = {'wait_for': [3]}
    elif scenario == 'missing_image': children[0]['phase'] = 'stored'
    elif scenario == 'count': rr['target']['item_indices'] = [1, 2, 3]
    elif scenario == 'failure': failures = [{'task_id': children[0]['child_task_id'], 'status': 'failed'}]
    elif scenario == 'other_channel': task['delivery_context']['channel'] = 'wechat'
    elif scenario == 'working_plan': candidate = update_working_plan(candidate, {'steps': [{'step': '出完图比较一下', 'status': 'pending'}]})
    elif scenario == 'pending_batch': cp['messages'].append({'role':'assistant','tool_calls':[{'id':'later','function':{'name':'code_execute','arguments':'{}'}}]})
    elif scenario == 'undelivered_change':
        change = UserChange(event_id=uuid4(), owner_task_id=source.operations[0].owner_task_id,
            message='再做一个文件', message_sha256=hashlib.sha256('再做一个文件'.encode()).hexdigest())
        cp['_task_user_changes'] = UserChanges(root_task_id=source.root_task_id, items=(change,)).model_dump(mode='json')
    elif scenario in {'accepted_operation', 'uncertain_operation'}:
        candidate = candidate.model_copy(update={'operations': source.operations if scenario == 'accepted_operation'
            else (source.operations[0].model_copy(update={'status': 'uncertain'}),)})
    assert completion_decision(source, candidate, cp, task, children, failures) != 'finish'


@pytest.mark.parametrize('text,scope,continued,expected', [
    ('第三个做成图', {'after_images':'terminal','evidence':['第三个做成图']}, False, 'terminal'),
    ('出好再比较一下', {'after_images':'continue','evidence':['出好再比较一下']}, False, 'continue'),
    ('出好再做一个文件', {'after_images':'terminal','evidence':['出好']}, True, 'continue'),
    ('出图', {'after_images':'terminal','evidence':['上次说的']}, False, 'unknown'),
    ('出图', {'after_images':'terminal','evidence':[]}, False, 'unknown'),
])
def test_semantic_evidence_is_a_quote_not_a_keyword_rule(text, scope, continued, expected):
    assert verified_scope(scope, text, continued=continued).after_images == expected


def test_schema_forbids_status_authority_and_unknown_fields():
    with pytest.raises(ValidationError):
        ImageCompletionScope.model_validate({'after_images':'completed','evidence':[]})
    with pytest.raises(ValidationError):
        ImageCompletionScope.model_validate({'after_images':'terminal','evidence':['出图'],'approved':True})


def test_current_edit_has_one_real_image_without_requiring_router_image_count():
    values = completed_case()
    values[2]['_request_resolution_v1']['target'] = {'kind':'image'}
    values[2]['_request_resolution_v1']['action']['image_count'] = None
    assert completion_decision(*values) == 'finish'


@pytest.mark.parametrize('draft', [{'phase':'staged'}, {'phase':'committed','delivery_complete':False}])
def test_unfinished_saved_document_is_not_discarded_after_image_completion(draft):
    values = completed_case(); values[2]['_content_response_v1'] = draft
    assert completion_decision(*values) == 'resume_model'


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', ['generate', 'execute_saved', 'repeat', 'edit'])
async def test_actual_model_reply_is_kept_after_receipt_in_each_image_action(intent):
    from dataclasses import replace
    from schemas.message import TextPart
    from services.adapters.types import StreamChunk
    from services.handlers.chat.execution_engine import _read_turn
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from tests.test_request_resolution import setup_case
    from tests.semantic_fixtures import semantic_request
    from services.task_execution import request_resolution as rr
    runtime, prepared, _, request, _ = setup_case()
    text = '第三个做成一张图，正方形，一千就行。'
    request = semantic_request(replace(request, content=[TextPart(text=text)]), intent)
    rr.freeze(runtime, request, {'kind':'current','basis':'current_input'})
    prepared.image_receipt_seen = True
    prepared.stream_kwargs = {}
    # This is a provider double: assertions below inspect the exact outgoing
    # model messages and prove the host preserves returned prose byte-for-byte.
    natural = '第三个方案已经开始做了，按你说的正方形、一千。图片出来会显示在这里。'
    seen=[]
    async def stream(**kwargs):
        seen.append(kwargs)
        yield StreamChunk(content=natural, finish_reason='stop')
    prepared.adapter.stream_chat = stream
    from unittest.mock import AsyncMock
    sink=CollectingExecutionSink(); sink.start=AsyncMock(); totals=StreamTotals()
    prepared.delivery_start_pending=True
    result=await _read_turn(prepared,[],runtime.cancellation_event,sink,totals,[],runtime,request=request)
    assert result[0] == sink.text == totals.text == natural
    sink.start.assert_awaited_once()
    assert prepared.delivery_start_pending is False
    system='\n'.join(m['content'] for m in seen[0]['messages'] if m['role']=='system')
    # Guidance is independent of which short-reference catalog is installed.
    assert 'accepted只是已受理' in system
    assert '不照抄固定状态模板' in system


@pytest.mark.asyncio
async def test_final_router_schema_prompt_examples_and_frozen_action_agree(monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from jsonschema import Draft202012Validator
    from dataclasses import replace
    from schemas.message import TextPart
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext, classify_workflow
    from services.task_execution import request_resolution as rr
    from tests.test_request_resolution import setup_case
    r, _, _, q, _ = setup_case()
    q = replace(q, content=[TextPart(text='第三个做成图')])
    frame = ImageRequestContext(target=rr.Target(kind='current',basis='current_input'),focus='current_request',
        image_completion_scope=ImageCompletionScope(after_images='terminal',evidence=['第三个做成图']))
    answer = dict(kind='ordinary_image',mode='new',reuse_through_stage=0,intent='generate',image_count=1,
                  request_context=frame.model_dump(mode='json',exclude_none=True))
    router=SimpleNamespace(close=AsyncMock(),call_tool_model=AsyncMock(return_value={'choices':[{'message':{'tool_calls':[
        {'function':{'name':'select_image_workflow','arguments':json.dumps(answer)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings',lambda:SimpleNamespace(intent_router_enabled=True,dashscope_api_key='offline',intent_router_model='offline',intent_router_timeout=5))
    monkeypatch.setattr('services.intent_router.IntentRouter',lambda:router)
    result=await classify_workflow(q.content[0].text)
    sent=router.call_tool_model.await_args.kwargs
    tool=sent['tool']['function'] if 'tool' in sent else sent['tools'][0]['function']
    schema=tool['parameters'];scope=schema['$defs']['ImageCompletionScope']
    assert 'image_completion_scope' in schema['properties']['request_context']['required']
    for example in scope['examples']:
        Draft202012Validator(scope).validate(example)
        assert ImageCompletionScope.model_validate(example).model_dump()==example
    assert '做好再比较一下' in sent['system_prompt']
    frozen=rr.freeze_frame(r,q,ImageRequestContext.model_validate(result['request_context']),result['intent'],image_count=1)
    assert frozen['action']['request_context']['image_completion_scope']['after_images']=='terminal'
    frame.image_completion_scope=ImageCompletionScope(after_images='terminal',evidence=['另一个用户的要求'])
    assert rr.freeze_frame(r,q,frame,'generate')['action']['request_context']['image_completion_scope']['after_images']=='unknown'


def test_scope_quote_may_use_only_current_owners_authenticated_delivered_changes():
    from services.task_execution.image_completion_policy import user_text
    request=SimpleNamespace(content=[{'type':'text','text':'第三个做成图'}])
    runtime=SimpleNamespace(task_id='current',task_user_changes=SimpleNamespace(items=[
        SimpleNamespace(owner_task_id='current',delivered=True,message='再比较一下'),
        SimpleNamespace(owner_task_id='prior',delivered=True,message='别的任务'),
        SimpleNamespace(owner_task_id='current',delivered=False,message='尚未送达'),
    ]))
    assert '再比较一下' in user_text(request,runtime)
    assert '别的任务' not in user_text(request,runtime)
    assert '尚未送达' not in user_text(request,runtime)


@pytest.mark.parametrize('raw', [
    {'after_images':'terminal','evidence':['出图'],'uncertainty_reason':None},
    '{"after_images":"terminal","evidence":["出图"]}',
    {'after_images':'done'}, {'evidence':['出图']}, {'after_images':'terminal','evidence':['']},
])
def test_invalid_advisory_metadata_cannot_poison_valid_business_approval(raw):
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    native={'image_completion_scope':raw,'size':{'aspect_ratio':'1:1','resolution':'1K'},
            'media':{'tool':'generate_image','approved':[3],'artifact_id':'original','revision':1}}
    frame=ImageRequestContext.model_validate(native)
    assert frame.image_completion_scope.after_images=='unknown'
    assert frame.media.approved==[3] and frame.media.artifact_id=='original' and frame.media.revision==1
    assert frame.size.resolution=='1K' and frame.size.aspect_ratio=='1:1'
    with pytest.raises(ValidationError):
        ImageRequestContext.model_validate({**native,'size':{'aspect_ratio':'invented'}})


def test_already_approved_selected_wait_is_not_an_unmet_obligation():
    values=completed_case()
    values[2]['_request_resolution_v1']['action']['request_context']['media'] = {
        'wait_for':[3],'approved':[3],'wait_all':False,'tool':'generate_image'}
    assert completion_decision(*values)=='finish'
