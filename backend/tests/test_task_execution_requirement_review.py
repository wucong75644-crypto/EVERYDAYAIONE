"""Independent coverage phase uses existing model rounds and atomic checkpoints."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.task_execution.bootstrap import StartTaskInput
from services.task_execution.contracts import TaskError
from services.task_execution.requirement_review import (
    review_messages,review_decision,restore_review,apply_review,proposal_digest)
from tests.test_task_execution_bootstrap import bootstrap
from tests.test_task_execution_model_bridge import setup_case,execute,call
from services.handlers.chat.execution_engine import _build_replay_context


def observation(*,images=True):
    return json.dumps({'action':'plan','deliveries':[{'artifact_kind':'image' if images else 'text',
        'minimum':5 if images else 1,'item_count':None if images else 5,
        'evidence':['五张详情图' if images else '五条提示词']}],'uncertainty':[]},ensure_ascii=False)


def proposal(*,images=True):
    return {'action':'plan','applicable_method_ids':[], 'requirements':[{
        'id':'delivery','description':'五张详情图' if images else '五条提示词',
        'artifact_kind':'image' if images else 'text','minimum':5 if images else 1,
        'item_count':None if images else 5,'evidence':['五张详情图'] if images else ['五条提示词']}]}


async def staged(*,images=True,checkpoint=None,goal=None):
    source,state,*_=bootstrap()
    goal=goal or ('请生成五张详情图' if images else '只要五条提示词，不要生成图片')
    source=replace(source,original_goal=goal,require_coverage_review=True)
    runtime,prepared,handler,sink=setup_case(initial=state,checkpoint=checkpoint)
    runtime.task_execution_state=None;runtime.task_bootstrap=source
    prepared.messages=[{'role':'system','content':'SKILL BODY must not enter reviewer'},
        {'role':'assistant','content':'固定历史方案'}, {'role':'user','content':goal}]
    prepared.last_provider_output=None
    value=proposal(images=images)
    if goal not in ('请生成五张详情图','只要五条提示词，不要生成图片'):
        value['requirements'][0]['evidence']=[goal]
    await execute(runtime,prepared,handler,sink,[call('start_task_execution',value,'start')],[])
    return runtime,prepared,handler,sink


@pytest.mark.asyncio
async def test_proposal_is_not_frozen_before_independent_review_and_mixed_batch_cannot_dispatch():
    runtime,prepared,handler,sink=await staged()
    assert runtime.task_execution_state is None
    record=runtime.task_requirement_review
    assert record.pending.requirements[0].artifact_kind=='image'
    assert json.loads(prepared.messages[-1]['content'])['status']=='coverage_review_pending'
    private=review_messages(record)
    assert len(private)==2 and 'SKILL BODY' not in json.dumps(private)
    assert record.version==2 and 'proposal' not in json.loads(private[1]['content'])
    assert 'selected_method_facts' not in json.loads(private[1]['content'])
    assert '固定历史方案' in private[1]['content']
    payload=runtime._replay_checkpoint_callback.call_args.args[1]
    assert payload['_task_requirement_review']==record.model_dump(mode='json')
    assert '_task_execution' not in payload
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_missing_image_verdict_rejects_subset_and_allows_bounded_repair():
    runtime,prepared,handler,sink=await staged()
    # Simulate the actual bad semantic extraction, retaining genuine source evidence.
    bad=runtime.task_requirement_review.pending.model_copy(update={'requirements':(
        runtime.task_requirement_review.pending.requirements[0].model_copy(update={'artifact_kind':'text','minimum':1}),)})
    runtime.task_requirement_review=runtime.task_requirement_review.model_copy(update={'pending':bad})
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(),
        has_tool_calls=False,model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    assert runtime.task_execution_state is None and runtime.task_requirement_review.pending is None
    assert runtime.task_requirement_review.attempts[-1].verdict.coverage=='missing'
    await execute(runtime,prepared,handler,sink,[call('start_task_execution',proposal(),'repair')],[])
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(),
        has_tool_calls=False,model_round=3,next_model_round=4,build_checkpoint=_build_replay_context,turn=3)
    state=runtime.task_execution_state
    assert state.original_goal=='请生成五张详情图' and state.requirements[0].artifact_kind=='image'
    assert state.requirements[0].minimum==5 and state.model_rounds_used==4
    assert len(runtime.task_requirement_review.attempts)==2
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_only_review_never_adds_paid_image_requirement():
    runtime,prepared,handler,sink=await staged(images=False)
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(images=False),
        has_tool_calls=False,model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    assert [(r.artifact_kind,r.minimum,r.item_count) for r in runtime.task_execution_state.requirements]==[('text',1,5)]
    assert runtime.task_execution_state.capability_gates==()
    assert not runtime.task_execution_state.operations


@pytest.mark.asyncio
async def test_blind_extraction_cannot_self_certify_covered_or_omit_actual_images():
    runtime,*_=await staged()
    record=runtime.task_requirement_review
    rejected,verdict=review_decision(record,'{"coverage":"covered","issues":[]}',has_tool_calls=False,model_round=1)
    assert verdict.coverage=='uncertain' and not rejected.approved_proposal_sha256
    bad=record.pending.model_copy(update={'requirements':(record.pending.requirements[0].model_copy(
        update={'artifact_kind':'text','minimum':1,'item_count':5}),)})
    rejected,verdict=review_decision(record.model_copy(update={'pending':bad}),observation(),has_tool_calls=False,model_round=1)
    assert verdict.coverage=='missing' and any('image' in issue for issue in verdict.issues)
    assert not rejected.approved_proposal_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['evidence','action','quantity','extra_image','item_count'])
async def test_blind_extraction_scope_source_quantity_and_prompt_metrics_are_checked(damage):
    runtime,*_=await staged(images=damage!='item_count')
    value=json.loads(observation(images=damage!='item_count'))
    if damage=='evidence':value['deliveries'][0]['evidence']=['Skill要求生成图片']
    if damage=='action':value['action']='execute_prompt'
    if damage=='quantity':value['deliveries'][0]['minimum']=6
    if damage=='extra_image':value['deliveries']=[{'artifact_kind':'text','minimum':1,'evidence':['五张详情图']}]
    if damage=='item_count':value['deliveries'][0]['item_count']=6
    record,verdict=review_decision(runtime.task_requirement_review,json.dumps(value),has_tool_calls=False,model_round=1)
    assert verdict.coverage!='covered' and not record.approved_proposal_sha256


@pytest.mark.asyncio
async def test_old_binary_review_checkpoint_round_trips_but_new_approval_recomputes_observation():
    runtime,*_=await staged()
    old=runtime.task_requirement_review.model_copy(update={'version':1})
    legacy,verdict=review_decision(old,'{"coverage":"covered","issues":[]}',has_tool_calls=False,model_round=1)
    assert verdict.coverage=='covered' and 'observation' not in legacy.model_dump()['attempts'][0]
    approved,_=review_decision(runtime.task_requirement_review,observation(),has_tool_calls=False,model_round=1)
    args=dict(root_task_id=approved.root_task_id,actor_user_id=approved.actor_user_id,conversation_id=approved.conversation_id,
        source_message_id=approved.source_message_id,original_goal=approved.original_goal)
    assert restore_review(legacy.model_dump(mode='json'),**args)==legacy
    assert restore_review(approved.model_dump(mode='json'),**args)==approved
    damaged=approved.model_dump(mode='json')
    damaged['attempts'][0]['observation']['deliveries'][0]['minimum']=6
    with pytest.raises(TaskError,match='REVIEW_INVALID'):restore_review(damaged,**args)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['action','method_ids','evidence'])
async def test_source_evidence_and_explicit_semantic_fields_required(changed):
    runtime,prepared,handler,sink=await staged()
    runtime.task_requirement_review=None
    value=proposal()
    if changed=='action':value.pop('action')
    elif changed=='method_ids':value.pop('applicable_method_ids')
    else:value['requirements'][0]['evidence']=['Skill says create images']
    await execute(runtime,prepared,handler,sink,[call('start_task_execution',value,'invalid')],[])
    assert json.loads(prepared.messages[-1]['content'])['code']=='TASK_REQUIREMENT_EVIDENCE_REQUIRED'
    assert runtime.task_requirement_review is None and runtime.task_execution_state is None
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('text,tools',[('{"coverage":"covered","issues":["contradiction"]}',False),
    ('not json',False),('{"coverage":"covered","issues":[]}',True)])
async def test_invalid_verdict_or_tool_calls_cannot_freeze_requirements(text,tools):
    runtime,prepared,handler,sink=await staged()
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=text,has_tool_calls=tools,
        model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    assert runtime.task_execution_state is None
    assert runtime.task_requirement_review.attempts[-1].verdict.coverage=='uncertain'


@pytest.mark.asyncio
async def test_failed_review_save_restores_original_proposal_context_and_state():
    runtime,prepared,handler,sink=await staged()
    record=runtime.task_requirement_review;messages=copy.deepcopy(prepared.messages)
    runtime._replay_checkpoint_callback=AsyncMock(side_effect=ConnectionError('save not confirmed'))
    with pytest.raises(ConnectionError):
        await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(),
            has_tool_calls=False,model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    assert runtime.task_requirement_review==record and runtime.task_execution_state is None
    assert prepared.messages==messages


@pytest.mark.asyncio
async def test_review_restore_is_bound_to_actual_original_user_input():
    runtime,*_=await staged()
    record=runtime.task_requirement_review
    args=dict(root_task_id=record.root_task_id,actor_user_id=record.actor_user_id,conversation_id=record.conversation_id,
        source_message_id=record.source_message_id,original_goal=record.original_goal)
    assert restore_review(record.model_dump(mode='json'),**args)==record
    with pytest.raises(TaskError,match='SCOPE_CHANGED'):
        restore_review(record.model_dump(mode='json'),**{**args,'original_goal':'只要提示词'})


class MainModelBoundary(Exception):pass


def prepare_loop(runtime,prepared,handler,sink,*,max_turns=5):
    from services.agent.execution_budget import ExecutionBudget
    from services.tools.runtime_context import catalog_context
    prepared.budget=ExecutionBudget(max_turns=max_turns,wrap_up_turns_reserved=0)
    prepared.core_tools=[];prepared.execution_context=catalog_context(None)
    prepared.permission=SimpleNamespace(mode=SimpleNamespace(value='auto'),need_exit_attachment=False,get_reminder=lambda _: '')
    prepared.tool_context=SimpleNamespace(discovered_tools=set(),update_from_batch=lambda _:None,build_context_prompt=lambda:None)
    prepared.stream_kwargs={}
    handler.org_id=None;handler._calculate_credits=lambda _:0;handler._get_conv_source=lambda _:'web'
    handler._pending_emit_payloads=[]
    sink.on_text=AsyncMock();sink.on_thinking=AsyncMock()


@pytest.mark.asyncio
async def test_real_loop_isolates_review_and_consumes_existing_round_budget_and_usage(monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    runtime,prepared,handler,sink=await staged()
    prepare_loop(runtime,prepared,handler,sink)
    original=copy.deepcopy(prepared.messages)
    rounds=[]
    async def model(private,tools,event,review_sink,totals,*args,model_round,task_review=False,**kwargs):
        rounds.append(model_round)
        if task_review:
            assert tools==[] and private.messages==review_messages(runtime.task_requirement_review)
            assert private is not prepared and review_sink is not sink
            totals.usage['prompt_tokens']+=7;totals.usage['completion_tokens']+=3
            return observation(),'',[],set()
        assert runtime.task_execution_state.requirements[0].artifact_kind=='image'
        assert prepared.messages[:len(original)]==original
        raise MainModelBoundary()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    request=ChatExecutionRequest(content=[],user_id=str(runtime.task_bootstrap.actor_user_id),
        conversation_id=runtime.conversation_id,task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    totals=StreamTotals()
    with pytest.raises(MainModelBoundary):
        await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
            sink=sink,totals=totals,blocks=[],runtime=runtime,model_round=1)
    assert rounds==[1,2] and prepared.budget.turns_used==2
    assert totals.usage['prompt_tokens']==7 and totals.usage['completion_tokens']==3
    assert runtime.task_requirement_review.approved_proposal_sha256
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_review_budget_exhaustion_does_not_launch_uncounted_wrap_up_or_business(monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest,_finalize_output_parts
    from services.handlers.chat.stream_session import StreamTotals
    runtime,prepared,handler,sink=await staged()
    prepare_loop(runtime,prepared,handler,sink,max_turns=1)
    model=AsyncMock(return_value=('{"coverage":"uncertain","issues":["上下文不足"]}','',[],set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    synthesis=AsyncMock(side_effect=AssertionError('uncounted model request'))
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',synthesis)
    request=ChatExecutionRequest(content=[],user_id=str(runtime.task_bootstrap.actor_user_id),
        conversation_id=runtime.conversation_id,task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    blocks=[];totals=StreamTotals()
    await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
        sink=sink,totals=totals,blocks=blocks,runtime=runtime,model_round=1)
    assert runtime.task_execution_state is None and blocks[-1]['task_status']=='partial'
    model.assert_awaited_once()
    assert prepared.budget.turns_used==1
    parts=await _finalize_output_parts(handler=handler,prepared=prepared,totals=totals,blocks=blocks,sink=sink,
        cancellation_event=runtime.cancellation_event,runtime=runtime)
    assert parts and parts[-1].text==blocks[-1]['text']
    synthesis.assert_not_awaited()
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('name,arguments',[('generate_image',{'prompt':'bypass'}),
    ('generate_video',{'prompt':'bypass'}),('file_delete',{'path':'original.txt'}),
    ('activate_skill',{'skill_id':'another'})])
async def test_missing_coverage_blocks_actual_dispatch_until_revised(name,arguments):
    runtime,prepared,handler,sink=await staged()
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text='{"coverage":"missing","issues":["遗漏图片"]}',
        has_tool_calls=False,model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    await execute(runtime,prepared,handler,sink,[call(name,arguments,'bypass')],[])
    assert json.loads(prepared.messages[-1]['content'])['code']=='TASK_REQUIREMENT_REVIEW_REQUIRED'
    assert runtime.task_execution_state is None and runtime.task_requirement_review.pending is None
    handler._execute_tool_calls.assert_not_called()
    await execute(runtime,prepared,handler,sink,[call('start_task_execution',proposal(),'repair')],[])
    assert runtime.task_requirement_review.pending


@pytest.mark.asyncio
async def test_pause_after_review_response_preserves_verdict_round_and_original_context(monkeypatch):
    from services.conversation_commands import ConversationCommand,CommandType
    from services.conversation_state import ConversationPauseRequested
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    runtime,prepared,handler,sink=await staged()
    prepare_loop(runtime,prepared,handler,sink)
    original=copy.deepcopy(prepared.messages)
    async def model(*args,**kwargs):
        assert kwargs['task_review'] is True
        args[4].usage['prompt_tokens']+=7
        args[4].usage['completion_tokens']+=3
        runtime.push(ConversationCommand(command_id='pause-after-review',command_type=CommandType.PAUSE,
            conversation_id=runtime.conversation_id,task_id=runtime.task_id))
        return observation(),'',[],set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    request=ChatExecutionRequest(content=[],user_id=str(runtime.task_bootstrap.actor_user_id),
        conversation_id=runtime.conversation_id,task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    with pytest.raises(ConversationPauseRequested):
        await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
            sink=sink,totals=StreamTotals(),blocks=[],runtime=runtime,model_round=1)
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert saved['_task_requirement_review']['approved_proposal_sha256']
    assert saved['_task_requirement_review']['pending'] is None and saved['next_model_round']==2
    assert saved['_task_execution']==runtime.task_execution_state.model_dump(mode='json')
    assert saved['_task_usage']['owner_task_id']==runtime.task_id
    assert saved['_task_usage']['usage']['prompt_tokens']==7
    assert saved['messages'][:len(original)]==original
    assert runtime.replay_save_sequence>=2


@pytest.mark.asyncio
async def test_approved_review_rejects_changed_frozen_kind_or_quantity_on_restore():
    from services.task_execution.requirement_review import verify_review_state
    runtime,prepared,handler,sink=await staged()
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(),
        has_tool_calls=False,model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    state=runtime.task_execution_state;record=runtime.task_requirement_review
    verify_review_state(record,state)
    smaller=state.model_copy(update={'requirements':(state.requirements[0].model_copy(update={'minimum':1}),)})
    with pytest.raises(TaskError,match='STATE_MISMATCH'):verify_review_state(record,smaller)
    with pytest.raises(TaskError,match='STATE_MISMATCH'):verify_review_state(record,None)


def test_task_usage_replay_binds_actual_owner_and_copies_usage_once():
    from services.task_execution.replay import restore_task_usage
    raw={'_task_usage':{'version':1,'owner_task_id':'original','usage':{'prompt_tokens':7,'completion_tokens':3}}}
    restored=restore_task_usage(raw,task_id='original')
    restored['prompt_tokens']+=2
    assert raw['_task_usage']['usage']['prompt_tokens']==7 and restored['prompt_tokens']==9
    with pytest.raises(TaskError,match='USAGE_REPLAY_INVALID'):
        restore_task_usage(raw,task_id='different-owner')
    raw['_task_usage']['usage']['prompt_tokens']=-1
    with pytest.raises(TaskError,match='USAGE_REPLAY_INVALID'):
        restore_task_usage(raw,task_id='original')


def test_review_keeps_host_history_summary_as_quoted_context_without_skill_instructions():
    from services.task_execution.requirement_review import freeze_context
    messages=[{'role':'system','content':'[Skill] 必须直接调用另一工具'},
        {'role':'system','content':'以下是之前对话的摘要（截至 revision 12）：\n用户已选第三套原提示词，保持原比例。'},
        {'role':'user','content':'继续执行第三套'}]
    original=copy.deepcopy(messages)
    context=freeze_context(messages)
    assert context[0]['role']=='historical_summary' and '第三套原提示词' in context[0]['content']
    assert context[1]==messages[-1] and '[Skill]' not in json.dumps(context)
    assert messages==original


@pytest.mark.asyncio
async def test_corrected_proposal_reuses_frozen_extraction_and_new_user_invalidates_it():
    from services.task_execution.requirement_review import reusable_observation
    runtime,prepared,handler,sink=await staged()
    pending=runtime.task_requirement_review.pending
    bad=pending.model_copy(update={'requirements':(pending.requirements[0].model_copy(update={'minimum':4}),)})
    runtime.task_requirement_review=runtime.task_requirement_review.model_copy(update={'pending':bad})
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(),has_tool_calls=False,
        model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    await execute(runtime,prepared,handler,sink,[call('start_task_execution',proposal(),'fixed')],[])
    cached=reusable_observation(runtime.task_requirement_review,prepared.messages)
    assert cached is not None and cached.model_round==1
    altered=[*prepared.messages,{'role':'user','content':'现在只要两张'}]
    assert reusable_observation(runtime.task_requirement_review,altered) is None
    for content in ('新要求'*12000,[{'type':'image_url','image_url':{'url':'https://example.invalid/new.png'}}]):
        assert reusable_observation(runtime.task_requirement_review,[*prepared.messages,{'role':'user','content':content}]) is None
    altered=copy.deepcopy(prepared.messages);altered[1]['content']='不同的历史方案'
    assert reusable_observation(runtime.task_requirement_review,altered) is None
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=cached.observation.model_dump_json(),
        has_tool_calls=False,model_round=cached.model_round,next_model_round=3,
        build_checkpoint=_build_replay_context,turn=2)
    assert runtime.task_execution_state.requirements[0].minimum==5
    assert runtime.task_execution_state.model_rounds_used==3
    assert len(runtime.task_requirement_review.attempts)==2
    restored=restore_review(runtime.task_requirement_review.model_dump(mode='json'),root_task_id=runtime.task_bootstrap.task_id,
        actor_user_id=runtime.task_bootstrap.actor_user_id,conversation_id=runtime.task_bootstrap.conversation_id,
        source_message_id=runtime.task_bootstrap.input_message_id,original_goal=runtime.task_bootstrap.original_goal)
    assert restored==runtime.task_requirement_review
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_cache_requires_current_prompt_fingerprint_and_old_checkpoint_still_restores(monkeypatch):
    from services.task_execution import requirement_review as reviews
    runtime,prepared,handler,sink=await staged()
    record,_=review_decision(runtime.task_requirement_review,observation(),has_tool_calls=False,model_round=1,messages=prepared.messages)
    assert reviews.reusable_observation(record,prepared.messages) is not None
    original=reviews.review_messages
    monkeypatch.setattr(reviews,'review_messages',lambda value:[*original(value),{'role':'system','content':'Changed extraction contract'}])
    assert reviews.reusable_observation(record,prepared.messages) is None
    old=record.model_dump(mode='json')
    old['attempts'][0].pop('observation_input_sha256')
    restored=restore_review(old,root_task_id=runtime.task_bootstrap.task_id,actor_user_id=runtime.task_bootstrap.actor_user_id,
        conversation_id=runtime.task_bootstrap.conversation_id,source_message_id=runtime.task_bootstrap.input_message_id,
        original_goal=runtime.task_bootstrap.original_goal)
    assert reviews.reusable_observation(restored,prepared.messages) is None
    assert 'observation_input_sha256' not in restored.model_dump(mode='json')['attempts'][0]


@pytest.mark.asyncio
async def test_rejected_proposal_feedback_exposes_source_backed_expected_shape_without_a_grant():
    runtime,prepared,handler,sink=await staged()
    bad=runtime.task_requirement_review.pending.model_copy(update={'requirements':(
        runtime.task_requirement_review.pending.requirements[0].model_copy(update={'minimum':4}),)})
    runtime.task_requirement_review=runtime.task_requirement_review.model_copy(update={'pending':bad})
    await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=observation(),has_tool_calls=False,
        model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    output=json.loads(prepared.messages[-1]['content'].split('\n')[-1])
    assert output['observed_requirements']['deliveries'][0]['minimum']==5
    assert output['observed_requirements']['deliveries'][0]['evidence']==['五张详情图']
    assert output['task_initialized'] is False and runtime.task_execution_state is None
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_valid_source_ambiguity_asks_once_without_proposal_retry_or_business(monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest,_finalize_output_parts
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.requirement_review import clarification_question,extraction_digest
    runtime,prepared,handler,sink=await staged(goal='生成一些图片')
    prepare_loop(runtime,prepared,handler,sink)
    original=copy.deepcopy(prepared.messages)
    value=json.loads(observation());value['deliveries'][0]['minimum']=1
    value['deliveries'][0]['evidence']=['生成一些图片']
    value['uncertainty']=['DELIVERY_COUNT_UNCLEAR']
    model=AsyncMock(return_value=(json.dumps(value),'',[],set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    request=ChatExecutionRequest(content=[],user_id=str(runtime.task_bootstrap.actor_user_id),
        conversation_id=runtime.conversation_id,task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    blocks=[];totals=StreamTotals()
    await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
        sink=sink,totals=totals,blocks=blocks,runtime=runtime,model_round=1)
    model.assert_awaited_once()
    handler._execute_tool_calls.assert_not_called()
    assert runtime.task_execution_state is None
    assert len(runtime.task_requirement_review.attempts)==1
    assert prepared.messages[:len(original)]==original
    assert blocks[-1]['task_status']=='waiting_input' and '确认' in blocks[-1]['text']
    parts=await _finalize_output_parts(handler=handler,prepared=prepared,totals=totals,blocks=blocks,sink=sink,
        cancellation_event=runtime.cancellation_event,runtime=runtime)
    assert parts[-1].text==blocks[-1]['text']
    from services.conversation_commands import SafePoint
    from services.handlers.chat.executor import _generation_outcome_from_replay
    from schemas.message import serialize_content_parts
    await runtime.safe_point(SafePoint.BEFORE_COMMIT,replay_payload={
        **_build_replay_context(prepared.messages,blocks,prepared.budget.turns_used,next_model_round=2),
        'checkpoint_kind':'commit_ready','result_content':serialize_content_parts(parts),
        'usage':dict(totals.usage),'credits_cost':0,'tool_digest':None})
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert saved['content_blocks'][-1]['text']==blocks[-1]['text']
    assert '_task_execution' not in saved and saved['_task_bootstrap']['input']['original_goal']=='生成一些图片'
    record=runtime.task_requirement_review
    restored=restore_review(saved['_task_requirement_review'],root_task_id=record.root_task_id,
        actor_user_id=record.actor_user_id,conversation_id=record.conversation_id,
        source_message_id=record.source_message_id,original_goal=record.original_goal)
    assert restored==record
    outcome=_generation_outcome_from_replay(saved)
    assert outcome.result_content[-1]['text']==blocks[-1]['text'] and outcome.credits_cost==0
    model.assert_awaited_once()
    from services.task_execution.requirement_review import freeze_context,review_messages
    next_messages=prepared.messages+[{'role':'assistant','content':blocks[-1]['text']},
        {'role':'user','content':'三张'}]
    next_record=record.model_copy(update={'original_goal':'三张','context':freeze_context(next_messages),
        'pending':None,'attempts':()})
    history=json.loads(review_messages(next_record)[1]['content'])['fixed_history']
    assert any(m['content']=='生成一些图片' for m in history)
    assert history[-2:]==next_messages[-2:]

    assert record.attempts[-1].observation_input_sha256==extraction_digest(record,prepared.messages)
    assert clarification_question(record,prepared.messages)==blocks[-1]['text']
    assert clarification_question(record,prepared.messages+[{'role':'user','content':'换个要求'}]) is None
    attempt=record.attempts[-1]
    bad=attempt.observation.model_copy(update={'deliveries':(attempt.observation.deliveries[0].model_copy(
        update={'evidence':('非用户原文',)}),)})
    damaged=record.model_copy(update={'attempts':(attempt.model_copy(update={'observation':bad}),)})
    assert clarification_question(damaged,prepared.messages) is None


@pytest.mark.asyncio
async def test_explicit_selection_changes_invalidate_blind_observation_cache():
    from services.task_execution.requirement_review import extraction_digest
    runtime,prepared,*_=await staged()
    record=runtime.task_requirement_review
    changed=record.model_copy(update={'selected_methods':({'skill_id':'reference-image-prompts',
        'name':'参考图多方案提示词','revision':'original','sources':['user']},)})
    assert extraction_digest(record,prepared.messages)!=extraction_digest(changed,prepared.messages)
