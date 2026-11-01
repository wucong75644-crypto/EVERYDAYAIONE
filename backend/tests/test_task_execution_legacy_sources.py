"""Saved planning is read, never regenerated or treated as new instructions."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from services.task_execution.methods import ImageMethodDocument
from services.task_execution.legacy_sources import native_plan_document
from services.task_execution.image_binding import bind_image_call
from services.task_execution.contracts import TaskError
from services.tools import ToolCall
from services.tools.catalog import build_capability_catalog
from tests.test_task_execution_methods import bound,document
from tests.test_task_execution_model_bridge import setup_case,execute,call


def plan():
    doc=ImageMethodDocument.model_validate_json(document())
    items=[]
    for position,brief in enumerate(doc.image_plans,1):
        request=brief.prompt+'\n\n负面提示词：'+brief.negative_prompt
        items.append({'position':position,'item_id':str(uuid4()),'name':brief.title,'scheme_markdown':brief.layout,
            'positive_prompt':brief.prompt,'negative_prompt':brief.negative_prompt,'aspect_ratio':brief.aspect_ratio,
            'request_text':request,'request_text_sha256':hashlib.sha256(request.encode()).hexdigest()})
    return {'id':str(uuid4()),'plan_revision':4,'status':'ready','target_size':{'aspect_ratio':'3:4'},
        'stage_outputs':{'1':{'product':'原商品和参考图判断'},'2':'原整套视觉和页面衔接'},'items':items}


@pytest.mark.asyncio
async def test_imported_native_plan_completes_method_and_binds_original_plan_source(monkeypatch):
    state,skills=await bound(monkeypatch)
    runtime,prepared,handler,sink=setup_case(initial=state)
    runtime.skill_runtime=skills
    original=plan();source_task=str(uuid4())
    db=SimpleNamespace(rpc=lambda name,params:SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(data={
        'outcome':'found','kind':'ecom_plan','source_id':original['id'],'source_revision':4,
        'source_task_id':source_task,'plan':original}))))
    runtime.task_artifact_reader=SimpleNamespace(db=db,task_id=str(state.root_task_id),execution_token=str(uuid4()))
    blocks=[]
    await execute(runtime,prepared,handler,sink,[call('import_task_source',{'expected_revision':state.revision,'step_id':'planning',
        'kind':'ecom_plan','source_id':original['id'],'source_revision':4},'import')],blocks)
    saved=runtime.task_execution_state;artifact=saved.artifacts[0]
    doc=ImageMethodDocument.model_validate_json(artifact.inline_text)
    assert [brief.prompt for brief in doc.image_plans]==[i['positive_prompt'] for i in original['items']]
    assert 'reference-prompts-v1' in artifact.validations and str(artifact.source_task_id)==source_task
    assert not [b for b in blocks if b['type']=='text']
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{'expected_revision':saved.revision,
        'step_id':'planning','artifact_ids':[artifact.id]},'complete')],blocks)
    item=original['items'][2]
    args={'plan_source':{'plan_id':original['id'],'revision':4,'item_id':item['item_id']}}
    binding=bind_image_call(runtime.task_execution_state,ToolCall('old-plan','generate_image',args),build_capability_catalog())
    assert binding.item_index==3
    args['plan_source']['revision']=5
    with pytest.raises(TaskError,match='NOT_PLANNED'):
        bind_image_call(runtime.task_execution_state,ToolCall('tampered','generate_image',args),build_capability_catalog())
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.parametrize('field',['request_text','request_text_sha256','aspect_ratio','position'])
def test_changed_saved_plan_does_not_get_execution_evidence(field):
    value=plan();value['items'][0][field]=99 if field=='position' else 'tampered'
    with pytest.raises((TaskError,ValueError)):
        native_plan_document(value)


@pytest.mark.asyncio
async def test_plain_final_prompt_handoff_preserves_exact_source_without_claiming_new_analysis(monkeypatch):
    import json
    from services.task_execution.bootstrap import TaskBootstrap
    from services.task_execution.contracts import PlanStep,WorkPlan
    from services.task_execution.reducer import revise_plan
    from services.task_execution.image_sources import SOURCE_REQUIREMENT,SOURCE_VALIDATOR,SavedPromptDocument
    from services.task_execution import methods
    from tests.test_task_execution_bootstrap import bootstrap
    from tests.test_skill_runtime import state as skill_state
    _,skills=await bound(monkeypatch)
    original, *_=bootstrap()
    initial=original.initialize({'action':'execute_prompt','requirements':[{'id':'images','description':'五张图',
        'artifact_kind':'image','minimum':5}]},model_rounds_used=1)
    initial=methods.attach_methods(initial,skills)
    assert not initial.method_bindings
    initial=revise_plan(initial,WorkPlan(expected_revision=initial.revision,steps=(
        PlanStep(id='read-original',title='读取原提示词',requirement_ids=(SOURCE_REQUIREMENT,)),
        PlanStep(id='images',title='执行原稿',requirement_ids=('user.images',),depends_on=('read-original',),capability='platform.generate_image'))))
    runtime,prepared,handler,sink=setup_case(initial=initial);runtime.skill_runtime=skills
    message,source_task=str(uuid4()),str(uuid4())
    prompt='保留蓝色荔枝风琴包原外观，放在浅色桌面，柔和侧光。\n负面：不得更改花纹。'
    body='已确认的最终提示词：\n```text\n'+prompt+'\n```'
    db=SimpleNamespace(rpc=lambda *args:SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(data={
        'outcome':'found','kind':'legacy_message','source_id':message,'source_revision':None,
        'source_task_id':source_task,'text':body,'parts':[{'content_index':0,'text':body}]}))))
    runtime.task_artifact_reader=SimpleNamespace(db=db,task_id=str(initial.root_task_id),execution_token=str(uuid4()))
    blocks=[]
    await execute(runtime,prepared,handler,sink,[call('import_task_source',{'expected_revision':initial.revision,
        'step_id':'read-original','kind':'legacy_message','source_id':message},'preview')],blocks)
    result=json.loads(next(m['content'] for m in reversed(prepared.messages) if m['role']=='tool'))
    choice=next(c for c in result['prompt_candidates'] if c['preview']==prompt)
    assert SOURCE_VALIDATOR not in runtime.task_execution_state.artifacts[0].validations
    await execute(runtime,prepared,handler,sink,[call('import_task_source',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'read-original','kind':'legacy_message',
        'source_id':message,'prompt_ids':[choice['id']],'aspect_ratio':'3:4'},'select')],blocks)
    artifact=runtime.task_execution_state.artifacts[-1]
    assert SOURCE_VALIDATOR in artifact.validations and 'reference-prompts-v1' not in artifact.validations
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'read-original','artifact_ids':[artifact.id]},'done')],blocks)
    assert 'report' in runtime.skill_runtime.resident and not runtime.skill_runtime.active
    source=SavedPromptDocument.model_validate_json(artifact.inline_text)
    for index,brief in enumerate(source.image_plans,1):
        assert brief.prompt==prompt
        args={'mode':'text_to_image','prompt':prompt,'aspect_ratio':'3:4','plan_item_id':brief.plan_item_id,
            'source_prompt':brief.source_prompt.model_dump(mode='json')}
        binding=bind_image_call(runtime.task_execution_state,ToolCall('image-'+str(index),'generate_image',args),build_capability_catalog())
        assert binding.item_index==index
    restored=skill_state(skills.source,platform_tool_names={'generate_image','file_search'},
        task_execution_state=runtime.task_execution_state)
    await restored.initialize(runtime.skill_runtime.checkpoint())
    assert set(restored.resident)=={'report'} and restored.effective_allowed_tool_names=={'generate_image','file_search'}
    # Compression can remove prior tool text; read the persisted exact artifact.
    prepared.messages[:]=[{'role':'user','content':'继续执行原稿，摘要已经压缩'}]
    await execute(runtime,prepared,handler,sink,[call('read_task_text',{'expected_revision':runtime.task_execution_state.revision,
        'artifact_id':artifact.id},'read')],blocks)
    assert json.loads(prepared.messages[-1]['content'])['text']==artifact.inline_text
    handler._execute_tool_calls.assert_not_called()
