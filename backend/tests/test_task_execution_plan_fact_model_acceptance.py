"""Opt-in original Skill/provider factual-review + correction acceptance.

Real original bodies/images and model output, actual host controls/native source
resolver; checkpoint persistence is a test double here. No image-provider IO.
"""
import asyncio
import base64
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.file_executor import FileExecutor
from services.handlers.chat_image_request import ChatImageInputResolver
from services.skills.selection import SkillSelection
from services.task_execution.methods import attach_methods,ImageMethodDocument
from services.task_execution.contracts import PlanStep,WorkPlan
from services.task_execution.reducer import revise_plan
from services.task_execution.plan_fact_sources import capture_sources,check_sources,preview
from services.task_execution.plan_facts import review_messages,review_proof
from services.task_execution.model_bridge import controller_for
from tests.conftest import MockSupabaseClient
from tests.test_task_execution_original_skills import OriginalSource,original_runtime
from tests.test_task_execution_bootstrap import bootstrap,proposal
from tests.test_task_execution_model_bridge import setup_case,execute,call
from tests.test_task_execution_plan_facts import review

pytestmark=pytest.mark.skipif(os.getenv('TASK_PLAN_FACT_LIVE')!='1',reason='opt-in original-provider factual review')


async def provider(payload,log_path):
    process=await asyncio.create_subprocess_exec(os.environ['TASK_ORIGINAL_MODEL_PROXY'],stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(180):
            output,errors=await process.communicate(json.dumps(payload,ensure_ascii=False).encode())
    finally:
        if process.returncode is None:process.terminate();await process.wait()
    if process.returncode:
        error_path=log_path.with_suffix('.stderr.log');error_path.write_bytes(errors);error_path.chmod(0o600)
    assert process.returncode==0,'original provider failed; inspect private diagnostics, factual acceptance not passed'
    reply=json.loads(output)
    log_path.write_text(json.dumps(reply,ensure_ascii=False));log_path.chmod(0o600)
    return reply


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['reference-image-prompts','ecommerce-image-prompts'])
async def test_original_bad_planning_is_rejected_then_corrected_before_original_method_handoff(key,tmp_path):
    root=Path(os.environ['TASK_ORIGINAL_INPUTS_ROOT']);source=OriginalSource();skill=source.values[key]
    selection=SkillSelection(skill_id=key,revision=skill.revision)
    skills=original_runtime(source);await skills.initialize(selection=selection)
    assert (await skills.activate_manual(selection))['ok']
    boot,*_=bootstrap();boot=replace(boot,original_goal=(root/'original-request.txt').read_text())
    state=attach_methods(boot.initialize({**proposal(),'action':'plan','applicable_method_ids':[key]},model_rounds_used=1),skills)
    state=state.model_copy(update={'image_binding_version':3})
    state=revise_plan(state,WorkPlan(expected_revision=state.revision,steps=(
        PlanStep(id='planning',title='原完整规划',requirement_ids=('user.prompts',*state.method_bindings[0].requirement_ids)),
        PlanStep(id='images',title='五张图',depends_on=('planning',),requirement_ids=('user.images',),capability='platform.generate_image'))))
    runtime,prepared,handler,sink=setup_case(initial=state);runtime.skill_runtime=skills
    original_goal=boot.original_goal
    files=FileExecutor(str(tmp_path),str(state.actor_user_id),None)
    parts=[{'type':'text','text':original_goal}]
    for i in (1,2):
        name=f'original-product-{i}.png';shutil.copyfile(root/name,files.resolve_safe_path(name))
        parts.append({'type':'image','workspace_path':name,'name':name})
    db=MockSupabaseClient()
    db.set_table_data('messages',[{'id':str(boot.input_message_id),'role':'user','org_id':None,
        'conversation_id':str(state.conversation_id),'status':'pending','context_revision':None,'content':parts}])
    owner=SimpleNamespace(db=db,user_id=str(state.actor_user_id),workspace_user_id=str(state.actor_user_id),
        org_id=None,context_scope='user',execution_mode='interactive',conversation_id=str(state.conversation_id))
    def native():return ChatImageInputResolver(owner,base_revision=0,input_message_id=str(boot.input_message_id),files=files,require_known_sources=True)
    async def sources(*,snapshot=None,references=None,images=False):
        reader=native();value=check_sources(reader,snapshot) if snapshot is not None else capture_sources(reader,references)
        pictures=[{'id':'image-'+str(i),'source':value['origins'][i-1],'image_url':preview(reader,ref)}
            for i,ref in enumerate(value['resolved'],1)] if images else []
        return value,pictures
    runtime.task_plan_source_provider=sources
    blocks=[]
    original_doc=(root/f'current-original-plan-{key}.json').read_text()
    ImageMethodDocument.model_validate_json(original_doc)
    prepared.messages=[*skills.messages(),{'role':'user','content':[{'type':'text','text':original_goal},
        *[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode((root/f'original-product-{i}.png').read_bytes()).decode()}} for i in (1,2)]]}]
    prepared.last_provider_output=None;prepared.last_reasoning_content=''
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{'expected_revision':state.revision,
        'step_id':'planning','text':original_doc},'original-draft')],blocks)
    artifact=runtime.task_execution_state.artifacts[-1]
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{'expected_revision':runtime.task_execution_state.revision,
        'step_id':'planning','artifact_ids':[artifact.id]},'check-original')],blocks)
    evidence_dir=root/'fact-review-evidence';evidence_dir.mkdir(mode=0o700,exist_ok=True)
    for attempt in range(3):
        pending=runtime.task_plan_reviews.pending
        assert pending is not None
        _,pictures=await sources(snapshot=pending.sources,images=True)
        reply=await provider({'messages':review_messages(runtime.task_execution_state,pending,pictures),'tools':[]},
            evidence_dir/f'{key}-review-{attempt}.json')
        assert reply['calls']==[]
        await review(runtime,prepared,sink,blocks,reply['content'],round=attempt*2+1)
        result=runtime.task_plan_reviews.attempts[-1]
        if attempt==0:assert result.status=='rejected',result
        if result.status=='approved':break
        assert result.status=='rejected',result
        messages=[*prepared.messages,controller_for(runtime).context_message(),{'role':'system','content':
            '核验已指出具体未证实/矛盾事实。保留用户的五张详情图、原产品和全部正确设计，只修正文案事实与单位；'
            '未知材质、尺寸、防水性能不作断言，不能把8入说成8寸。直接调用save_task_text提交同一planning步骤的完整修订JSON，不再另建规划或执行生图。'}]
        schema=next(t for t in controller_for(runtime).schemas() if t['function']['name']=='save_task_text')
        repaired=await provider({'messages':messages,'tools':[schema]},evidence_dir/f'{key}-repair-{attempt}.json')
        assert len(repaired['calls'])==1 and repaired['calls'][0]['name']=='save_task_text'
        await execute(runtime,prepared,handler,sink,repaired['calls'],blocks)
        artifact=runtime.task_execution_state.artifacts[-1]
        await execute(runtime,prepared,handler,sink,[call('complete_task_step',{'expected_revision':runtime.task_execution_state.revision,
            'step_id':'planning','artifact_ids':[artifact.id]},'check-repair-'+str(attempt))],blocks)
    else:pytest.fail('original provider did not produce an accepted factual repair within budget')
    assert review_proof(runtime.task_execution_state,artifact) is not None
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{'expected_revision':runtime.task_execution_state.revision,
        'step_id':'planning','artifact_ids':[artifact.id]},'complete-verified')],blocks)
    assert runtime.task_execution_state.results[0].artifact_ids==(artifact.id,)
    assert runtime.task_execution_state.original_goal==original_goal
    assert source.values[key].body_sha256==skill.body_sha256
    handler._execute_tool_calls.assert_not_called()
