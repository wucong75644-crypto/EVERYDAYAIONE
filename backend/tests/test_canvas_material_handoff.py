"""Canvas defaults and material identity, from authored items to frozen requests."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.agent.image.ecommerce_planner.workflow import ImageRequestContext, EcommerceWorkflow
from services.handlers.chat_image_request import freeze_image_request, verify_frozen_request
from services.handlers.image_dimensions import output_size_check
from services.handlers.image_size_requirements import resolve_size_requirement
from services.task_execution import content_objects as co
from services.task_execution.contracts import TaskState, TaskError
from services.tools.catalog import build_capability_catalog
from tests.test_content_objects import case


def refs():
    return [dict(workspace_path='style.png', content_sha256='a'*64, role='波点参考',
                 width=1280, height=1280, aspect_ratio='1:1'),
            dict(workspace_path='product.jpg', content_sha256='b'*64, role='主体',
                 width=1280, height=1707, aspect_ratio='1280:1707')]


@pytest.mark.parametrize('count', [0, 1, 2])
def test_first_creative_request_uses_auto_without_resizing_or_dropping_sources(count):
    images=refs()[:count]
    original=deepcopy(images)
    args,target=resolve_size_requirement({'mode':'image_to_image' if count else 'text_to_image',
        'prompt':'参考图片设计新品。'},images,action='generate')
    assert (args['aspect_ratio'], args['resolution']) == ('auto','1K')
    assert images==original and len(images)==count
    snapshot=freeze_image_request(args, images, origin={}, max_requests=15,max_credits=300,size_requirement=target)
    images.clear()
    verify_frozen_request(snapshot)
    assert len(snapshot['references'])==count and snapshot['size_requirement']['aspect_ratio']=='auto'
    assert output_size_check({'width':1086,'height':1448,'aspect_ratio':'3:4'},target)['size_matches']


def test_edit_uses_target_canvas_without_removing_the_style_image():
    images=refs()
    args,target=resolve_size_requirement({'mode':'image_to_image','prompt':'借用波点，修改产品图。',
        'aspect_ratio':'auto','size_requirement':{'mode':'inherit_reference','reference_index':1}},
        images,action='edit')
    assert args['aspect_ratio']=='3:4' and target['reference_sha256']=='b'*64
    assert [image['role'] for image in images]==['波点参考','主体']


def test_only_a_genuinely_unselected_edit_target_needs_clarification():
    with pytest.raises(ValueError,match='IMAGE_CANVAS_REFERENCE_REQUIRED'):
        resolve_size_requirement({'mode':'image_to_image','prompt':'修改图片。'},refs(),action='edit')
    args,_=resolve_size_requirement({'mode':'image_to_image','prompt':'设计新品。'},refs(),action='generate')
    assert args['aspect_ratio']=='auto'


def test_user_size_and_actual_previous_canvas_outrank_model_auto():
    args,_=resolve_size_requirement({'mode':'image_to_image','prompt':'改背景。','aspect_ratio':'auto'},
        refs()[1:],intent={'aspect_ratio':'1:1'},previous={'aspect_ratio':'3:4','resolution':'2K'},action='edit')
    assert (args['aspect_ratio'],args['resolution'])==('1:1','2K')
    args,_=resolve_size_requirement({'mode':'image_to_image','prompt':'改背景。','aspect_ratio':'auto'},
        refs()[1:],previous={'mode':'explicit','aspect_ratio':'3:4','resolution':'2K'},action='edit')
    assert (args['aspect_ratio'],args['resolution'])==('3:4','2K')


def registered(canvas=False):
    runtime,_,_,_=case()
    sources=(co.SourceImage(name='style.png',reference=co.MaterialReference(message_id=str(uuid4()),content_index=1)),
             co.SourceImage(name='product.jpg',reference=co.MaterialReference(resource_ref='fref1_APPENDED_IMAGE')))
    materials=[co.Material(reference=sources[0].reference,role='波点风格参考',usage='input'),
               co.Material(reference=sources[1].reference,role='用户指定主体',usage='input')]
    frame=ImageRequestContext(materials=materials,reference_usage='input',
        **({'canvas_resource':'fref1_APPENDED_IMAGE'} if canvas else {}))
    doc=co.ContentDocument(title='新品三套',items=tuple(co.ContentItem(title=f'方案{i}',body=f'原始方案{i}正文。')for i in range(1,4)))
    call={'id':'save-original','name':co.SAVE,'arguments':json.dumps({'document':doc.model_dump(mode='json')})}
    state,artifact,saved,_=co.save(runtime,call,build_capability_catalog(),has_image_attachments=True,
        source_images=sources,author_request='三套都用后补活页本做主体，上一张参考波点。',request_context=frame)
    return state,artifact,saved,doc,materials


@pytest.mark.parametrize('canvas', [False,True])
def test_online_first_and_third_execute_same_original_after_checkpoint_reload(canvas):
    state,artifact,saved,original,materials=registered(canvas)
    restored=TaskState.model_validate_json(state.model_dump_json())
    for position in (1,3):
        args,binding=co.compile_input(restored,'generate_image',{},
            {'artifact_id':artifact.id,'item_index':position,'sha256':artifact.sha256})
        assert args['prompt']==original.items[position-1].body
        assert hashlib.sha256(args['prompt'].encode()).digest()==hashlib.sha256(saved.items[position-1].body.encode()).digest()
        assert [x['role']for x in args['references']]==[m.role for m in materials]
        assert args['references'][1]['resource_ref']=='fref1_APPENDED_IMAGE'
        assert binding['proof']['source']['artifact_id']==artifact.id
        if canvas:
            assert args['size_requirement']=={'mode':'inherit_reference','reference_index':1}
        else:
            assert args['aspect_ratio']=='auto'
        normalized,target=resolve_size_requirement(args,refs(),action='execute_saved')
        assert normalized['aspect_ratio']==('3:4' if canvas else 'auto')
        frozen=freeze_image_request(normalized,refs(),origin={},max_requests=15,max_credits=300,size_requirement=target)
        verify_frozen_request(frozen)
        assert len(frozen['references'])==2
    with pytest.raises(TaskError,match='CONTENT_ARGUMENT_CONFLICT'):
        co.compile_input(restored,'generate_image',{},
            {'artifact_id':artifact.id,'item_index':1,'source_image_indices':[2]})


def test_router_receives_real_current_and_appended_locators_in_one_order():
    task=str(uuid4());message=str(uuid4())
    flow=EcommerceWorkflow.__new__(EcommerceWorkflow)
    flow.request=SimpleNamespace(task_id=task,context_anchor=SimpleNamespace(input_message_id=message))
    flow.input=[{'type':'text','text':'先写方案'},{'type':'image','name':'style.png'}]
    flow.runtime=SimpleNamespace(task_user_changes=SimpleNamespace(items=(SimpleNamespace(owner_task_id=task,
        delivered=True,event_id=uuid4(),message='再补主体图',attachments=(SimpleNamespace(kind='image',
            workspace_path='uploads/product.jpg',resource_ref='fref1_APPENDED_IMAGE'),)),)))
    inputs=flow.action_input()
    assert inputs[1]['reference']=={'message_id':message,'content_index':1}
    assert inputs[-1]['reference']=={'resource_ref':'fref1_APPENDED_IMAGE'}
    assert flow.input[1]=={'type':'image','name':'style.png'}


def test_old_context_keeps_serialized_shape_and_frozen_request_is_not_reselected():
    assert 'materials' not in ImageRequestContext().model_dump(mode='json')
    args,target=resolve_size_requirement({'mode':'image_to_image','prompt':'原冻结请求。',
        'aspect_ratio':'3:4','size_requirement':{'mode':'explicit'}},refs())
    frozen=freeze_image_request(args,refs(),origin={},max_requests=15,max_credits=300,size_requirement=target)
    original=deepcopy(frozen)
    resolve_size_requirement({'mode':'image_to_image','prompt':'新任务。'},refs(),action='generate')
    verify_frozen_request(frozen)
    assert frozen==original


@pytest.mark.asyncio
async def test_router_selects_short_indices_and_host_restores_original_signed_refs(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import classify_workflow
    from services.intent_router import IntentRouter
    from core.config import get_settings
    settings=get_settings()
    monkeypatch.setattr(settings,'intent_router_enabled',True)
    monkeypatch.setattr(settings,'dashscope_api_key','offline')
    signed='fref1_'+('x'*1000)
    original={'message_id':str(uuid4()),'content_index':1}
    response={'kind':'ordinary_image','intent':'write','mode':'new','reuse_through_stage':0,'image_count':3,
        'request_context':{'materials':[{'material_index':1,'role':'波点参考','usage':'input'},
        {'material_index':2,'role':'主体','usage':'input'}],'canvas_resource':'image:2'}}
    async def call(self,**kwargs):
        raw=json.loads(kwargs['text'])['raw_user_input']
        assert [p['material_index'] for p in raw]==[1,2]
        assert signed not in kwargs['text'] and str(original['message_id']) not in kwargs['text']
        return {'choices':[{'message':{'tool_calls':[{'function':{'name':'select_image_workflow',
            'arguments':json.dumps(response)}}]}}]}
    monkeypatch.setattr(IntentRouter,'call_tool_model',call)
    result=await classify_workflow([{'type':'image','reference':original},
        {'type':'image','origin':'append','reference':{'resource_ref':signed}}])
    context=ImageRequestContext.model_validate(result['request_context'])
    assert [m.reference.model_dump(exclude_none=True) for m in context.materials]==[original,{'resource_ref':signed}]
    assert context.canvas_resource==signed
    response['request_context']['materials'][1]['material_index']=3
    assert (await classify_workflow([{'type':'image','reference':original},
        {'type':'image','origin':'append','reference':{'resource_ref':signed}}]))['kind']=='unavailable'


@pytest.mark.parametrize('canvas', [False,True])
def test_changing_saved_inputs_does_not_carry_old_auto_or_reference_index(canvas):
    state,artifact,_,_,_=registered(canvas)
    new_ref={'message_id':str(uuid4()),'content_index':0}
    args,_=co.compile_input(state,'generate_image',{},
        {'artifact_id':artifact.id,'item_index':1,'image_override':[new_ref]})
    assert args['references']==[{**new_ref,'role':'原图参考'}]
    assert 'size_requirement' not in args and 'aspect_ratio' not in args
    normalized,_=resolve_size_requirement(args,refs()[1:],action='edit')
    assert normalized['aspect_ratio']=='3:4'


def test_canvas_follows_material_identity_even_when_input_order_is_reversed():
    from services.task_execution.content_groups import bind_group
    state,artifact,_,original,materials=registered()
    _,record,_=co._load_record(state,{'artifact_id':artifact.id,'item_index':1})
    reversed_materials=list(reversed(materials))
    context=ImageRequestContext(materials=reversed_materials,canvas_image=1)
    saved=bind_group(original,record.source_images,'原图作为底图，追加图参考风格。',semantic_context=context)
    assert saved.items[0].options['size_requirement']=={'mode':'inherit_reference','reference_index':1}
    assert saved.items[0].materials==tuple(reversed_materials)
    context.materials=[co.Material(reference={'resource_ref':'fref1_FOREIGN'},role='foreign',usage='input')]
    with pytest.raises(TaskError,match='CONTENT_SOURCE_IMAGE_UNAVAILABLE'):
        bind_group(original,record.source_images,'',semantic_context=context)


def test_explicit_analysis_material_is_not_sent_to_the_image_supplier():
    from services.task_execution.content_groups import bind_group
    state,artifact,_,original,materials=registered()
    _,record,_=co._load_record(state,{'artifact_id':artifact.id,'item_index':1})
    materials[0]=materials[0].model_copy(update={'usage':'analysis'})
    context=ImageRequestContext(materials=materials)
    saved=bind_group(original,record.source_images,'只观察第一张，用第二张做主体。',semantic_context=context)
    assert [m.reference for m in saved.items[0].materials if m.usage=='input']==[materials[1].reference]
    per_item=original.model_copy(update={'items':(original.items[0].model_copy(update={'materials':(materials[1],)}),)})
    assert bind_group(per_item,record.source_images,'',semantic_context=context).items[0].materials==(materials[1],)


def test_ready_server_plan_auto_reaches_dispatch_without_numeric_ratio_parsing():
    from services.handlers.chat_image_preparation import prepare_input
    from services.agent.image.ecommerce_planner.contracts import source_id
    owner=SimpleNamespace(user_id=str(uuid4()),org_id=None,conversation_id=str(uuid4()))
    images=[{**r,'message_id':str(uuid4()),'content_index':1,'file_version':1} for r in refs()]
    body='使用两张参考图完成产品新款设计。'
    item_id=str(uuid4());plan_id=str(uuid4())
    plan={'id':plan_id,'user_id':owner.user_id,'org_id':None,'conversation_id':owner.conversation_id,
        'plan_revision':1,'status':'ready','target_size':{'mode':'auto','aspect_ratio':'auto','resolution':'1K'},
        'items':[{'item_id':item_id,'aspect_ratio':'auto','request_text':body,
            'request_text_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'references':[{'message_id':r['message_id'],'content_index':1,'role':r['role']} for r in images]}],
        'input_snapshot':{'resolved_references':[{**r,'source_id':source_id(r)} for r in images]}}
    class Query:
        def __getattr__(self,_):return lambda *a,**kw:self
        def execute(self):return SimpleNamespace(data=plan)
    resolver=SimpleNamespace(owner=owner,normalize_legacy=lambda args:deepcopy(args),
        locate_source_prompt=lambda _:None,resolve=lambda _:deepcopy(images),size_context=lambda **_:({},{}))
    normalized,resolved,size,proof=prepare_input(resolver,{'plan_source':{'plan_id':plan_id,'revision':1,'item_id':item_id}},
        scoped=SimpleNamespace(table=lambda _:Query()),workflow=None,
        settings=SimpleNamespace(chat_image_transparent_enabled=False),entry_checked=True)
    assert (normalized['aspect_ratio'],normalized['resolution'])==('auto','1K')
    assert normalized['prompt']==body and len(resolved)==2
    assert size==plan['target_size'] and proof['item_id']==item_id


def test_explicit_inheritance_keeps_its_target_even_after_an_auto_request():
    from services.handlers.image_size_requirements import ImageSizeError
    args,target=resolve_size_requirement({'mode':'image_to_image',
        'size_requirement':{'mode':'inherit_reference','reference_index':1}},refs(),
        previous={'aspect_ratio':'auto','resolution':'1K'},action='execute_saved')
    assert args['aspect_ratio']=='3:4' and target['reference_sha256']=='b'*64
    with pytest.raises(ImageSizeError,match='IMAGE_CANVAS_REFERENCE_REQUIRED') as error:
        resolve_size_requirement({'mode':'text_to_image','size_requirement':{'mode':'inherit_reference'}},[],action='generate')
    assert error.value.code=='IMAGE_CANVAS_REFERENCE_REQUIRED'


def test_semantic_defaults_do_not_promote_prose_suggestions_into_native_parameters():
    from services.task_execution.content_groups import bind_group
    context=ImageRequestContext(reference_usage='input')
    body='保留产品，使用竖向构图。 --ar 3:4 --v 6.0'
    doc=co.ContentDocument(title='方案',items=(co.ContentItem(title='正文',body=body),))
    saved=bind_group(doc,(),'创作一款新品。',semantic_context=context)
    assert saved.items[0].body==body and saved.items[0].options=={'aspect_ratio':'auto'}
    explicit=doc.model_copy(update={'items':(doc.items[0].model_copy(update={'options':{'aspect_ratio':'3:4'}}),)})
    assert bind_group(explicit,(),'创作。',semantic_context=context).items[0].options['aspect_ratio']=='3:4'
    context.size.aspect_ratio='1:1'
    assert bind_group(explicit,(),'输出方图。',semantic_context=context).items[0].options['aspect_ratio']=='1:1'


def test_plain_writing_does_not_gain_image_defaults_from_shared_request_context():
    from services.task_execution.content_groups import bind_group
    doc=co.ContentDocument(title='聊天总结',items=(co.ContentItem(title='正文',body='今天的工作总结。'),))
    saved=bind_group(doc,(),'帮我总结一下。',semantic_context=ImageRequestContext())
    assert saved==doc and saved.items[0].options=={}
