"""Draft -> evidence review -> correction -> phase handoff, without business IO."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from services.handlers.chat.execution_engine import _build_replay_context
from services.task_execution.contracts import TaskError
from services.task_execution.plan_facts import (
    apply_review,evaluate,review_proof,review_messages,restore_reviews,before_complete,sections)
from services.task_execution.reducer import digest
from tests.test_task_execution_methods import bound,document
from tests.test_task_execution_model_bridge import setup_case,execute,call


async def drafted(monkeypatch):
    state,skills=await bound(monkeypatch)
    state=state.model_copy(update={'image_binding_version':3})
    runtime,prepared,handler,sink=setup_case(initial=state)
    runtime.skill_runtime=skills
    snapshot={'references':[],'resolved':[],'texts':[{'id':'user:original','text':'8入大容量风琴包，保留产品外观，生成五张详情图。'}]}
    snapshot['sha256']=digest(snapshot)
    runtime.task_plan_source_provider=AsyncMock(return_value=(snapshot,[]))
    text=json.loads(document());text['product_facts']='8寸防水PP材质风琴包'
    blocks=[]
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{
        'expected_revision':state.revision,'step_id':'planning','text':json.dumps(text,ensure_ascii=False)},'draft')],blocks)
    artifact=runtime.task_execution_state.artifacts[0]
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[artifact.id]},'check')],blocks)
    return runtime,prepared,handler,sink,blocks,artifact


def observation(artifact,*,rejected=False):
    result=[]
    for name,text in sections(artifact).items():
        result.append({'claim':text,'section':name,'status':'contradicted' if rejected and name=='product_facts' else 'supported',
            'evidence':[{'source_id':'user:original','kind':'user_text','quote':'8入大容量风琴包'}],
            'correction':'数量8入不是尺寸8寸；删除未核实的防水和PP材质。' if rejected and name=='product_facts' else ''})
    return json.dumps({'reviewed_sections':list(sections(artifact)),'claims':result},ensure_ascii=False)


async def review(runtime,prepared,sink,blocks,text,*,round=1):
    await apply_review(runtime=runtime,prepared=prepared,blocks=blocks,text=text,has_tool_calls=False,model_round=round,
        next_model_round=round+1,build_checkpoint=_build_replay_context,turn=round,sink=sink)


@pytest.mark.asyncio
async def test_unreviewed_draft_cannot_complete_or_retire_method_and_business_batch_is_barred(monkeypatch):
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    assert 'report' in runtime.skill_runtime.active and not runtime.task_execution_state.results
    assert runtime.task_plan_reviews.pending.artifact_id==artifact.id
    assert json.loads(prepared.messages[-1]['content'])['status']=='fact_review_pending'
    assert any(b.get('text','').startswith('规划草稿（待事实核验）') for b in blocks)
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert saved['_task_plan_reviews']==runtime.task_plan_reviews.model_dump(mode='json')
    old=copy.deepcopy(runtime.task_execution_state)
    await execute(runtime,prepared,handler,sink,[call('generate_image',{'prompt':'bypass'},'business')],blocks)
    assert json.loads(prepared.messages[-1]['content'])['code']=='TASK_PLAN_FACT_REVIEW_PENDING'
    assert runtime.task_execution_state==old
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_eight_compartments_not_eight_inches_is_corrected_before_method_exit(monkeypatch):
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    original=copy.deepcopy(prepared.messages)
    await review(runtime,prepared,sink,blocks,observation(artifact,rejected=True))
    assert runtime.task_plan_reviews.attempts[-1].status=='rejected'
    assert runtime.task_plan_reviews.pending is None and review_proof(runtime.task_execution_state,artifact) is None
    assert runtime.task_execution_state.results==() and 'report' in runtime.skill_runtime.active
    assert prepared.messages[:len(original)]==original
    text=json.loads(artifact.inline_text);text['product_facts']='8入大容量风琴包；材质、防水性能和尺寸未提供，不作断言。'
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','text':json.dumps(text,ensure_ascii=False)},'repair')],blocks)
    corrected=runtime.task_execution_state.artifacts[-1]
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[corrected.id]},'recheck')],blocks)
    await review(runtime,prepared,sink,blocks,observation(corrected),round=3)
    assert review_proof(runtime.task_execution_state,corrected) is not None
    assert review_proof(runtime.task_execution_state,artifact) is None
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[corrected.id]},'complete')],blocks)
    assert runtime.task_execution_state.results[0].artifact_ids==(corrected.id,)
    assert 'report' in runtime.skill_runtime.resident and 'report' not in runtime.skill_runtime.active
    assert runtime.task_execution_state.artifacts[0].inline_text==artifact.inline_text
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['missing_section','wrong_quote','forged_source','wrong_claim','tools'])
async def test_model_cannot_publish_review_receipt_with_missing_or_forged_evidence(monkeypatch,damage):
    runtime,_,_,_,_,artifact=await drafted(monkeypatch)
    value=json.loads(observation(artifact))
    if damage=='missing_section':value['reviewed_sections'].pop()
    if damage=='wrong_quote':value['claims'][0]['evidence'][0]['quote']='规格8寸'
    if damage=='forged_source':value['claims'][0]['evidence'][0]['source_id']='user:other'
    if damage=='wrong_claim':value['claims'][0]['claim']='已验证全部事实'
    _observation,status,_=evaluate(runtime.task_execution_state,runtime.task_plan_reviews.pending,
        json.dumps(value),damage=='tools')
    assert status=='uncertain'


@pytest.mark.asyncio
async def test_failed_review_checkpoint_does_not_lose_pending_request_or_original_context(monkeypatch):
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    state=runtime.task_execution_state;book=runtime.task_plan_reviews
    messages=copy.deepcopy(prepared.messages);before_blocks=copy.deepcopy(blocks)
    runtime._replay_checkpoint_callback=AsyncMock(side_effect=ConnectionError('unknown save'))
    with pytest.raises(ConnectionError):await review(runtime,prepared,sink,blocks,observation(artifact))
    assert runtime.task_execution_state==state and runtime.task_plan_reviews==book
    assert prepared.messages==messages and blocks==before_blocks
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_review_book_restore_rechecks_scope_artifact_and_decision(monkeypatch):
    runtime,prepared,_,sink,blocks,artifact=await drafted(monkeypatch)
    await review(runtime,prepared,sink,blocks,observation(artifact))
    raw=runtime.task_plan_reviews.model_dump(mode='json');state=runtime.task_execution_state
    assert restore_reviews(raw,state)==runtime.task_plan_reviews
    raw['attempts'][0]['observation']['claims'][0]['evidence'][0]['quote']='invented'
    with pytest.raises(TaskError,match='REVIEW_INVALID'):restore_reviews(raw,state)
    raw=runtime.task_plan_reviews.model_dump(mode='json');raw['identity_sha256']='0'*64
    with pytest.raises(TaskError,match='SCOPE_CHANGED'):restore_reviews(raw,state)


@pytest.mark.asyncio
async def test_retry_budget_is_per_step_and_cannot_reset_by_saving_new_artifacts(monkeypatch):
    runtime,prepared,_,sink,blocks,artifact=await drafted(monkeypatch)
    for i in range(3):
        await review(runtime,prepared,sink,blocks,observation(artifact,rejected=True),round=i)
        if i<2:assert not await before_complete(runtime,runtime.task_execution_state,'planning',[artifact.id])
    with pytest.raises(TaskError,match='REVIEW_LIMIT'):
        await before_complete(runtime,runtime.task_execution_state,'planning',[artifact.id])


@pytest.mark.asyncio
async def test_review_prompt_uses_actual_sources_without_skill_body_or_execution_tools(monkeypatch):
    runtime,_,_,_,_,artifact=await drafted(monkeypatch)
    messages=review_messages(runtime.task_execution_state,runtime.task_plan_reviews.pending,[])
    assert len(messages)==2 and messages[1]['role']=='user'
    value=json.loads(messages[1]['content'][0]['text'])
    assert value['plan_sections']['product_facts']==sections(artifact)['product_facts']
    assert value['user_source_texts'][0]['text'].startswith('8入')
    assert 'skill' not in value and 'tools' not in value


@pytest.mark.asyncio
async def test_real_loop_review_uses_existing_budget_usage_and_full_checkpoint(monkeypatch):
    from tests.test_task_execution_requirement_review import prepare_loop,MainModelBoundary
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    prepare_loop(runtime,prepared,handler,sink)
    original=copy.deepcopy(prepared.messages)
    async def model(isolated,tools,event,review_sink,totals,*args,model_round,task_review=False,**kwargs):
        if task_review:
            assert task_review=='plan_facts' and tools==[] and isolated is not prepared
            totals.usage['prompt_tokens']+=7;totals.usage['completion_tokens']+=3
            return observation(artifact),'',[],set()
        assert runtime.task_plan_reviews.pending is None
        assert review_proof(runtime.task_execution_state,artifact) is not None
        raise MainModelBoundary()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    request=ChatExecutionRequest(content=[],user_id=str(runtime.task_execution_state.actor_user_id),
        conversation_id=runtime.conversation_id,task_id=runtime.task_id,message_id='output',model_id='offline',context_anchor=None)
    totals=StreamTotals()
    with pytest.raises(MainModelBoundary):
        await _run_loop(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
            sink=sink,totals=totals,blocks=blocks,runtime=runtime,model_round=1)
    assert prepared.messages[:len(original)]==original
    assert totals.usage['prompt_tokens']==7 and prepared.budget.turns_used==2
    assert runtime.task_execution_state.model_rounds_used>=2
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert saved['_task_usage']['usage']['completion_tokens']==3
    assert saved['_task_plan_reviews']['pending'] is None


@pytest.mark.asyncio
async def test_pause_after_review_response_saves_verdict_proof_and_usage_before_stopping(monkeypatch):
    from services.conversation_commands import ConversationCommand,CommandType
    from services.conversation_state import ConversationPauseRequested
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    runtime.task_usage={'prompt_tokens':7,'completion_tokens':3}
    runtime.push(ConversationCommand(command_id='pause-after-facts',command_type=CommandType.PAUSE,
        conversation_id=runtime.conversation_id,task_id=runtime.task_id))
    with pytest.raises(ConversationPauseRequested):
        await review(runtime,prepared,sink,blocks,observation(artifact),round=4)
    saved=runtime._replay_checkpoint_callback.call_args.args[1]
    assert saved['_task_plan_reviews']['pending'] is None and saved['next_model_round']==5
    assert saved['_task_usage']['usage']['prompt_tokens']==7
    assert review_proof(runtime.task_execution_state,artifact) is not None
    assert restore_reviews(saved['_task_plan_reviews'],runtime.task_execution_state).attempts[-1].status=='approved'
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[ValueError('IMAGE_ORIGINAL_UNAVAILABLE'),PermissionError('RESOURCE_ACTION_DENIED'),OSError('sensitive /path')])
async def test_first_source_capture_errors_are_repairable_and_do_not_terminate_control_batch(monkeypatch,error):
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    runtime.task_plan_reviews=None
    runtime.task_plan_source_provider=AsyncMock(side_effect=error)
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[artifact.id]},'missing-original')],blocks)
    result=json.loads(prepared.messages[-1]['content'])
    assert result['ok'] is False and result['state_unchanged'] is True
    assert 'fact_references' in result['instruction'] and 'sensitive' not in str(result)
    assert runtime.task_plan_reviews is None and runtime.task_execution_state.results==()
    assert 'report' in runtime.skill_runtime.active
    handler._execute_tool_calls.assert_not_called()


async def test_paraphrased_review_quote_is_protocol_error_not_reason_to_replace_draft(monkeypatch):
    runtime,prepared,handler,sink,blocks,artifact=await drafted(monkeypatch)
    pending=runtime.task_plan_reviews.pending
    raw=json.loads(observation(artifact));raw['claims'][0]['claim']='这是核验器自行概括而非原文'
    await review(runtime,prepared,sink,blocks,json.dumps(raw,ensure_ascii=False))
    attempt=runtime.task_plan_reviews.attempts[-1]
    assert attempt.status=='uncertain' and attempt.observation is None
    assert '逐字原文' in attempt.issues[0]
    assert '保留原artifact_id' in prepared.messages[-1]['content']
    assert '重新生成规划' in prepared.messages[-1]['content']
    assert review_proof(runtime.task_execution_state,artifact) is None
    assert runtime.task_execution_state.artifacts[0]==artifact
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[artifact.id]},'retry-review')],blocks)
    assert runtime.task_plan_reviews.pending.artifact_sha256==pending.artifact_sha256
    assert runtime.task_plan_reviews.pending.sources==pending.sources
    messages=review_messages(runtime.task_execution_state,runtime.task_plan_reviews.pending,[],repair_issues=attempt.issues)
    assert 'Host review-protocol feedback' in messages[-1]['content'][-1]['text']
    assert runtime.task_execution_state.results==()
    handler._execute_tool_calls.assert_not_called()
