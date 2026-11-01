"""Real completed planning artifacts bind native prompts and output slots."""
from dataclasses import replace

import pytest

from services.task_execution.contracts import PlanStep, TaskError, WorkPlan
from services.task_execution.image_binding import bind_image_call, image_prompt, validate_image_steps
from services.task_execution.methods import ImageMethodDocument
from services.task_execution.reducer import complete_step, revise_plan, digest
from services.tools import ToolCall
from services.tools.catalog import build_capability_catalog
from tests.test_task_execution_methods import saved_plan, document


async def ready(monkeypatch):
    runtime, *_ = await saved_plan(monkeypatch)
    state = runtime.task_execution_state
    return complete_step(state, 'planning', [state.artifacts[0].id])


def native(index=1, **changes):
    brief = ImageMethodDocument.model_validate_json(document()).image_plans[index-1]
    return ToolCall('native', 'generate_image', {'mode': 'image_to_image', 'prompt': image_prompt(brief),
        'aspect_ratio': brief.aspect_ratio, 'references': [{'message_id': 'original', 'content_index': 0}], **changes})


@pytest.mark.asyncio
async def test_exact_original_prompt_negative_ratio_and_index_bind_to_real_completed_artifact(monkeypatch):
    state = await ready(monkeypatch)
    call = native(3)
    binding = bind_image_call(state, call, build_capability_catalog())
    assert binding.step_id == 'images' and binding.item_index == 3
    assert binding.requirement_id == 'user.images'
    assert binding.planning_artifact_id == state.artifacts[0].id
    assert binding.arguments_sha256 == digest({'mode': 'image_to_image',
        'prompt': call.arguments['prompt'], 'aspect_ratio': '3:4',
        'references': [{'message_id': 'original', 'content_index': 0}]})


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'prompt': '通用漂亮商品图'}, {'aspect_ratio': '1:1'},
    {'prompt': ImageMethodDocument.model_validate_json(document()).image_plans[0].prompt}])
async def test_shortcut_prompt_omitted_negative_or_changed_ratio_cannot_bind(monkeypatch, change):
    with pytest.raises(TaskError, match='TASK_IMAGE_ARGUMENTS_NOT_PLANNED'):
        bind_image_call(await ready(monkeypatch), native(**change), build_capability_catalog())


@pytest.mark.asyncio
async def test_saved_text_without_completed_method_step_is_not_execution_evidence(monkeypatch):
    runtime, *_ = await saved_plan(monkeypatch)
    with pytest.raises(TaskError, match='TASK_IMAGE_PLAN_EVIDENCE_MISSING'):
        bind_image_call(runtime.task_execution_state, native(), build_capability_catalog())


@pytest.mark.asyncio
async def test_different_call_id_or_step_name_does_not_create_new_effect_slot(monkeypatch):
    state = await ready(monkeypatch)
    first = bind_image_call(state, native(), build_capability_catalog())
    image = state.steps[-1].model_copy(update={'id': 'renamed'})
    updated = revise_plan(state, WorkPlan(expected_revision=state.revision, steps=(state.steps[0], image)))
    second = bind_image_call(updated, replace(native(), call_id='new-call'), build_capability_catalog())
    assert second.effect_key == first.effect_key and second.effect_version == first.effect_version
    assert bind_image_call(state, native(2), build_capability_catalog()).effect_key != first.effect_key


@pytest.mark.asyncio
async def test_split_image_contributions_have_explicit_nonoverlapping_slots(monkeypatch):
    state = await ready(monkeypatch)
    images = tuple(PlanStep(id=f'image-{index}', title=f'第{index}张', depends_on=('planning',),
        contributes_to=('user.images',), capability='platform.generate_image', image_item_index=index)
        for index in range(1,6))
    validate_image_steps(state, (state.steps[0], *images))
    updated = revise_plan(state, WorkPlan(expected_revision=state.revision, steps=(state.steps[0], *images)))
    assert bind_image_call(updated, native(4), build_capability_catalog()).step_id == 'image-4'
    with pytest.raises(TaskError, match='TASK_IMAGE_SLOT_OVERLAP'):
        validate_image_steps(state, (state.steps[0], images[0], images[0].model_copy(update={'id': 'alias'})))


@pytest.mark.asyncio
@pytest.mark.parametrize('index,full', [(6,False), (1,True)])
async def test_slot_out_of_user_quantity_or_single_slot_claiming_whole_set_rejected(monkeypatch,index,full):
    state = await ready(monkeypatch)
    step = PlanStep(id='one', title='图片', capability='platform.generate_image', image_item_index=index,
        requirement_ids=('user.images',) if full else (), contributes_to=() if full else ('user.images',))
    with pytest.raises(TaskError, match='TASK_IMAGE_SLOT_INVALID'):
        validate_image_steps(state, (state.steps[0], step))


@pytest.mark.asyncio
async def test_registry_capability_and_saved_business_plan_are_not_silently_reinterpreted(monkeypatch):
    from services.tools.registry import ToolRegistry
    catalog = build_capability_catalog()
    wrong = replace(catalog.get('generate_image'), capability='platform.generate_video')
    with pytest.raises(TaskError, match='TASK_IMAGE_NATIVE_TOOL_MISMATCH'):
        bind_image_call(await ready(monkeypatch), native(), ToolRegistry([wrong]))
    with pytest.raises(TaskError, match='TASK_IMAGE_ARGUMENTS_NOT_PLANNED'):
        bind_image_call(await ready(monkeypatch), ToolCall('ready', 'generate_image', {'plan_source': {}}), catalog)


def grouped_ready_state(state):
    from services.task_execution.contracts import TaskState,PlanStep
    image=next(r for r in state.requirements if r.artifact_kind=='image')
    groups=(image.model_copy(update={'id':'user.main','minimum':2}),
        image.model_copy(update={'id':'user.detail','minimum':3}))
    steps=tuple(PlanStep(id=f'image-{index}',title=f'图{index}',capability='platform.generate_image',
        depends_on=('planning',),contributes_to=(groups[0 if index<=2 else 1].id,),image_item_index=index)
        for index in range(1,6))
    return TaskState.model_validate({**state.model_dump(),'requirements':tuple(r for r in state.requirements
        if r.artifact_kind!='image')+groups,'steps':(state.steps[0],*steps)})


@pytest.mark.asyncio
async def test_multiple_delivery_groups_bind_global_plan_indices_to_stable_slots(monkeypatch):
    from services.task_execution.image_binding import image_slot_key,image_slots,validate_image_steps
    state=grouped_ready_state(await ready(monkeypatch))
    steps=state.steps[1:]
    validate_image_steps(state,state.steps)
    keys=[]
    for index in range(1,6):
        bound=bind_image_call(state,native(index),build_capability_catalog())
        group='user.main' if index<=2 else 'user.detail'
        assert bound.item_index==index and bound.requirement_id==group
        assert bound.effect_key==image_slot_key(group,index)
        keys.append(bound.effect_key)
    assert len(set(keys))==5 and len(image_slots(state))==5
    duplicate=steps[-1].model_copy(update={'image_item_index':1})
    with pytest.raises(TaskError,match='SLOT_OVERLAP'):
        validate_image_steps(state,(state.steps[0],*steps[:-1],duplicate))


@pytest.mark.asyncio
async def test_method_plan_count_preserves_sum_of_independent_image_groups(monkeypatch):
    from tests.test_task_execution_methods import bound
    from tests.test_task_execution_bootstrap import bootstrap
    from services.task_execution.methods import attach_methods
    _,skills=await bound(monkeypatch)
    boot,*_=bootstrap()
    initial=boot.initialize({'action':'plan','applicable_method_ids':['report'],'requirements':[
        {'id':'main','description':'两张主图','artifact_kind':'image','minimum':2},
        {'id':'detail','description':'三张详情图','artifact_kind':'image','minimum':3}]},model_rounds_used=1)
    state=attach_methods(initial,skills)
    assert state.method_bindings[0].image_count==5


@pytest.mark.asyncio
async def test_duplicate_binding_and_generation_steps_receive_actionable_repair_without_dispatch(monkeypatch):
    import json
    from tests.test_task_execution_model_bridge import setup_case,execute,call
    state=await ready(monkeypatch)
    images=[{'id':f'generate-{i}','title':f'生成第{i}张','capability':'platform.generate_image',
        'depends_on':['planning'],'contributes_to':['user.images'],'image_item_index':i} for i in range(1,6)]
    duplicate={**images[0],'id':'bind-original-1','title':'绑定第1张原图'}
    runtime,prepared,handler,sink=setup_case(initial=state)
    await execute(runtime,prepared,handler,sink,[call('update_task_plan',{
        'expected_revision':state.revision,'steps':[state.steps[0].model_dump(mode='json'),duplicate,*images]},'bad-plan')],[])
    result=json.loads(prepared.messages[-1]['content'])
    assert result['code']=='TASK_IMAGE_SLOT_OVERLAP' and result['state_unchanged'] is True
    assert 'bind_task_image_sources' in result['instruction'] and '控制动作' in result['instruction']
    assert runtime.task_execution_state.steps==state.steps
    handler._execute_tool_calls.assert_not_called()
