"""Opt-in Max semantic probes through existing Skill/host loop/Gateway/adapter.

Only original immutable methods/materials; no business executor is available.
HTTP admission/DB checkpoints/page remain separately verified local boundaries.
"""
import base64
import copy
import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.test_skill_runtime_actor import actor,prepared,handler
from tests.test_chat_execution_engine import _request
from tests.test_task_execution_bootstrap import bootstrap
from tests.test_task_execution_original_skills import OriginalSource

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('ORIGINAL_SKILL_PAID_APPROVED') not in ('60','100') and not os.getenv('ORIGINAL_SKILL_PAID_REPLAY_LEDGER') and not os.getenv('ORIGINAL_SKILL_PAID_EXACT_REPLAY_LEDGER'),reason='requires explicit new paid batch or offline responses')]


@pytest.mark.parametrize('key',['reference-image-prompts','ecommerce-image-prompts'])
async def test_original_skill_real_planning_without_image_dispatch(key,monkeypatch,tmp_path):
    from core.config import get_settings
    from dotenv import dotenv_values
    from services.agent.execution_budget import ExecutionBudget
    from services.handlers.chat.execution_engine import execute_chat
    from services.model_gateway import ModelGateway,ModelCallRequest
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from services.skills.selection import SkillSelection
    from schemas.message import TextPart,ImagePart
    from tests.fixtures.skill_paid_probe import SkillProbeLedger,ApprovedSkillTransport
    from services.handlers.chat import execution_engine

    settings=get_settings().model_copy(update={'skill_catalog_enabled':True,'skill_runtime_enabled':True,
        'skill_recommendations_enabled':False,'ecom_image_planning_enabled':True})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    source=OriginalSource()
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource',lambda *_:source)
    legacy=AsyncMock(side_effect=AssertionError('Old keyword override attempted'))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.EcommerceWorkflow.initialize',legacy)
    replay=os.getenv('ORIGINAL_SKILL_PAID_REPLAY_LEDGER')
    exact_replay=os.getenv('ORIGINAL_SKILL_PAID_EXACT_REPLAY_LEDGER')
    request_views=[]
    if replay or exact_replay:
        saved=[c for c in json.loads(Path(replay or exact_replay).read_text())['calls'] if c['scenario']==key and c['status']=='received']
        if exact_replay and os.getenv('ORIGINAL_SKILL_PAID_PRIOR_LEDGER'):
            saved.extend(c for c in json.loads(Path(os.environ['ORIGINAL_SKILL_PAID_PRIOR_LEDGER']).read_text())['calls'] if c['scenario']==key and c['status']=='received')
        by_hash={c['body_sha256']:c for c in saved}
        sequence=iter(saved)
        def replay_response(http_request):
            sent=json.loads(http_request.content)
            from tests.fixtures.skill_context_cost import request_context_cost
            request_views.append({'context_cost':request_context_cost(sent,{k:v.body for k,v in source.values.items()}),'body_sha256':__import__('hashlib').sha256(json.dumps(sent,sort_keys=True).encode()).hexdigest(),'roles':[m['role'] for m in sent['messages']],
                'tools':[m for m in sent['messages'] if m['role']=='tool']})
            try:item=by_hash[request_views[-1]['body_sha256']] if exact_replay else next(sequence)
            except (StopIteration,KeyError):raise RuntimeError('SAVED_RESPONSE_EXHAUSTED') from None
            data=''.join('data: '+json.dumps(packet)+'\n\n' for packet in item['packets'])+'data: [DONE]\n\n'
            return httpx.Response(200,content=data,headers={'content-type':'text/event-stream'})
        transport=httpx.MockTransport(replay_response);credential='offline-replay-no-network'
    else:
        ledger=SkillProbeLedger(os.environ['ORIGINAL_SKILL_PAID_LEDGER'])
        credential=dotenv_values('/Users/wucong/EVERYDAYAIONE/backend/.env').get('DASHSCOPE_API_KEY')
        assert credential,'existing configured credential required'
        prior_path=os.getenv('ORIGINAL_SKILL_PAID_PRIOR_LEDGER')
        prior=SkillProbeLedger(prior_path) if prior_path else None
        transport=ApprovedSkillTransport(ledger,key,reuse_received=True,prior_received=prior)
    async def capture_request(http_request):
        if replay or exact_replay:return
        from tests.fixtures.skill_context_cost import request_context_cost
        sent=json.loads(http_request.content)
        request_views.append({'context_cost':request_context_cost(sent,{k:v.body for k,v in source.values.items()})})
    adapter=DashScopeChatAdapter(credential,'qwen3.8-max',builtin_tools_enabled=False,session_cache_enabled=False)
    adapter._client=httpx.AsyncClient(base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        headers={'Authorization':'Bearer '+credential},transport=transport,timeout=120,trust_env=False,
        event_hooks={'request':[capture_request]})
    class Events:
        def publish(self,*args,**kwargs):pass
    gateway=ModelGateway(adapter_factory=lambda *_args,**_kw:adapter,event_publisher=Events())
    boot,state,anchor,_=bootstrap()
    original=Path('/Users/wucong/.codex/task-evidence/skill-coordination-design/374/original-inputs')
    old_request=(original/'original-request.txt').read_text()
    goal=old_request+'\n本次仅完成文字策划和完整提示词，不生成新图片；第一张作为主参考，第二张作为补充款式，保留已有成果。'
    boot=replace(boot,original_goal=goal,require_coverage_review=True)
    from tests.fixtures.skill_paid_probe import pinned_probe_scope
    evidence_dir=Path(os.environ.get('ORIGINAL_SKILL_PAID_EVIDENCE_DIR',tmp_path))
    prior_record=evidence_dir/(key+'.json')
    if prior_record.exists() and not (evidence_dir/(key+'.scope.json')).exists():
        prior=json.loads(prior_record.read_text())
        saved=prior.get('task_state')
        if saved and saved['original_goal']==goal and prior['revision']==source.values[key].revision:
            from services.task_execution.contracts import TaskState
            saved=TaskState.model_validate(saved)
            boot=replace(boot,task_id=saved.root_task_id,conversation_id=saved.conversation_id,
                actor_user_id=saved.actor_user_id,input_message_id=saved.source_message_ids[0],
                execution_identity=saved.execution_identity,org_id=saved.org_id,branch_id=saved.branch_id)
            anchor=replace(anchor,task_id=str(boot.task_id),conversation_id=str(boot.conversation_id),
                input_message_id=str(boot.input_message_id),org_id=str(boot.org_id) if boot.org_id else None)
    boot,anchor=pinned_probe_scope(evidence_dir/(key+'.scope.json'),boot,anchor,
        {'skill':key,'revision':source.values[key].revision,'body_sha256':source.values[key].body_sha256,
         'original_images':['c73d3ea0a8c7cf4e4b8d4c65918eec794ed5aba12765904fe0470cd779765d81',
                            '52f7d869de0d2c5cf05d706bc9c9f1f83c0e0c72ee52fa402895d884b626bda3']})
    runtime=actor();runtime.task_bootstrap=boot
    runtime.task_id=str(boot.task_id);runtime.conversation_id=str(boot.conversation_id);runtime.turn_id=anchor.turn_id
    image_parts=[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode((original/f'original-product-{i}.png').read_bytes()).decode()}} for i in (1,2)]
    async def original_sources(*,snapshot=None,references=None,images=False):
        import hashlib
        from services.task_execution.reducer import digest
        from services.task_execution.contracts import TaskError
        refs=[{'message_id':str(anchor.input_message_id),'content_index':i,'role':'reference'} for i in (1,2)]
        if references is not None and list(references)!=refs:raise TaskError('TASK_PLAN_FACT_SOURCE_UNAVAILABLE')
        hashes=['c73d3ea0a8c7cf4e4b8d4c65918eec794ed5aba12765904fe0470cd779765d81',
            '52f7d869de0d2c5cf05d706bc9c9f1f83c0e0c72ee52fa402895d884b626bda3']
        resolved=[]
        for i,wanted in enumerate(hashes,1):
            file=original/f'original-product-{i}.png'
            if hashlib.sha256(file.read_bytes()).hexdigest()!=wanted:raise TaskError('TASK_PLAN_FACT_SOURCE_CHANGED')
            resolved.append({**refs[i-1],'workspace_path':str(file),'content_sha256':wanted,'file_version':'1','width':1024,'height':1024,'aspect_ratio':'1:1'})
        value={'references':refs,'resolved':resolved,'origins':['user_reference','user_reference'],
            'texts':[{'id':'user:'+str(anchor.input_message_id),'text':goal}]}
        value['sha256']=digest(value)
        if snapshot is not None and snapshot!=value:raise TaskError('TASK_PLAN_FACT_SOURCE_CHANGED')
        pictures=[{'id':'image-'+str(i),'source':'user_reference','image_url':part['image_url']['url']} for i,part in enumerate(image_parts,1)] if images else []
        return value,pictures
    runtime.task_plan_source_provider=original_sources
    # execute_chat installs this seam afresh; setting the runtime attribute
    # alone is overwritten before fact review. Verify the actual factory path.
    monkeypatch.setattr('services.task_execution.plan_fact_sources.create_source_provider',lambda *_args,**_kwargs:original_sources)
    history=[{'role':'system','content':'现有会话摘要和历史成果必须保留。用户当前只要文字规划；不得生成新图片，不得执行写操作。'},
        {'role':'assistant','content':'已有五张成图保留，本次不重复生成。'},
        {'role':'user','content':[{'type':'text','text':goal},*image_parts]}]
    p=prepared();p.messages=copy.deepcopy(history);p.text_content=goal
    p.budget=ExecutionBudget(max_turns=10,max_wall_time=240)
    p.core_tools=[]
    p.stream_kwargs={'max_tokens':6000,'thinking_mode':'disabled','temperature':0}
    p.adapter=gateway.open_chat(ModelCallRequest(model_id='qwen3.8-max',task_id=str(boot.task_id),timeout=120))
    p.execution_context=replace(p.execution_context,org_id=None,actor_user_id=str(boot.actor_user_id),workspace_owner_id=str(boot.actor_user_id),
        feature_flags={**p.execution_context.feature_flags,'ecom_image_planning_enabled':True,'chat_image_async_enabled':True})
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream',AsyncMock(return_value=p))
    h=handler();h.org_id=None;h.db=object()
    h._execute_tool_calls.side_effect=AssertionError('Business dispatch forbidden by this paid approval')
    selection=SkillSelection(skill_id=key,revision=source.values[key].revision)
    request=replace(_request(),content=[TextPart(text=goal),*[ImagePart(url=x['image_url']['url']) for x in image_parts]],selected_skill=selection,
        model_id='qwen3.8-max',task_id=str(boot.task_id),conversation_id=str(boot.conversation_id),user_id=str(boot.actor_user_id),context_anchor=anchor)
    native=execution_engine._read_turn;observations=[]
    async def observe(*args,**kwargs):
        value=await native(*args,**kwargs)
        observations.append({'review':kwargs.get('task_review',False),'text':value[0],
            'calls':value[2],'visible_tools':[t['function']['name'] for t in args[1]]})
        return value
    monkeypatch.setattr(execution_engine,'_read_turn',observe)
    status='not_completed';error=None;result=None
    try:
        result=await execute_chat(handler=h,request=request,runtime=runtime)
        status='returned'
        h._execute_tool_calls.assert_not_awaited();legacy.assert_not_awaited()
        assert all(m in p.messages for m in history),'Original context lost'
        assert not (runtime.task_execution_state and runtime.task_execution_state.operations),'No image/write side effect may be submitted'
        assert runtime.task_execution_state is not None and runtime.task_execution_state.status=='completed','Safety-only return is not final planning acceptance'
    except BaseException as exc:
        status='stopped';error={'type':type(exc).__name__,'message':str(exc)[:300]}
        raise
    finally:
        destination=Path(os.environ.get('ORIGINAL_SKILL_PAID_EVIDENCE_DIR',tmp_path));destination.mkdir(parents=True,exist_ok=True)
        value={'skill':key,'revision':source.values[key].revision,'status':status,'error':error,'observations':observations,'request_views':request_views,
            'tool_transcript':[m for m in p.messages if m.get('role')=='tool'],
            'task_state':runtime.task_execution_state.model_dump(mode='json') if runtime.task_execution_state else None,
            'requirement_review':runtime.task_requirement_review.model_dump(mode='json') if runtime.task_requirement_review else None,
            'plan_reviews':runtime.task_plan_reviews.model_dump(mode='json') if getattr(runtime,'task_plan_reviews',None) else None,
            'skill_checkpoint':runtime.skill_runtime.checkpoint() if runtime.skill_runtime else None,
            'prepared_messages':p.messages,
            'coverage_feedback':[m for m in p.messages if m.get('role')=='system' and str(m.get('content','')).startswith('[Task requirement coverage]')],
            'result':result,'business_dispatch_count':h._execute_tool_calls.await_count,
            'scope':'real original Skill/host loop/ModelGateway/DashScope adapter; prepared context/checkpoint DB and source binding are isolated fixture seams; source bytes are hash-verified originals'}
        path=destination/(key+'.json');path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str));os.chmod(path,0o600)
        await gateway.close()
