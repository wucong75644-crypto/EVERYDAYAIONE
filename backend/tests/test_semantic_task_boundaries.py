"""Natural-language decisions have one source; native facts retain their gates."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from schemas.message import TextPart, infer_generation_type
from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
from services.agent.order_scope import normalize_order_scope
from services.handlers.chat import content_tools as tools, native_plan_delivery
from services.handlers.chat_image_request import ChatImageInputResolver
from services.handlers.image_size_requirements import resolve_size_requirement, ImageSizeError
from services.task_execution import content_objects as co, item_confirmation as confirmation
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,intent,accepted',[('unrelated','write',True),('ordinary_image','write',True),('unrelated','explain',False),('unavailable',None,False)])
async def test_missing_authoring_metadata_is_bound_once_without_rewriting_the_reply(monkeypatch,kind,intent,accepted):
    from unittest.mock import AsyncMock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    w=object.__new__(EcommerceWorkflow);w.value=None;w.input=[{'type':'text','text':'三条脚本等我确认'}]
    w.request=SimpleNamespace(replay_context=None,task_id='current');w.handler=SimpleNamespace(org_id=None)
    w.runtime=SimpleNamespace(skill_coordination=True,task_user_changes=None)
    classify=AsyncMock(return_value={'kind':kind,'intent':intent,'image_count':None})
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',classify)
    async def bind():w.value=SimpleNamespace(intent='write')
    w.identify=AsyncMock(side_effect=bind)
    assert await w.bind_authored_response(3) is accepted
    classify.assert_awaited_once()
    assert w.identify.await_count==int(accepted)
    if accepted:
        assert await w.bind_authored_response(3) is False
        classify.assert_awaited_once()


def test_optional_canvas_reference_does_not_change_old_checkpoint_projection():
    assert 'canvas_resource' not in ImageRequestContext().model_dump(mode='json')


def test_saved_source_roster_includes_delivered_appends_and_preserves_old_callers():
    r,_,_,q=case()
    q=replace(q,content=[TextPart(text='等我补图')],context_anchor=SimpleNamespace(input_message_id=str(uuid4())))
    def change(owner,delivered,kind='image'):
        return SimpleNamespace(owner_task_id=owner,delivered=delivered,attachments=[
            SimpleNamespace(kind=kind,workspace_path='资料/追加.png',resource_ref='fref1_TEST')])
    r.task_user_changes=SimpleNamespace(items=[change(q.task_id,True),change(q.task_id,False),change(uuid4(),True),change(q.task_id,True,'file')])
    assert co.current_source_images(q)==()
    sources=co.current_source_images(q,r)
    assert len(sources)==1 and sources[0].reference.resource_ref=='fref1_TEST'


@pytest.mark.asyncio
async def test_appended_position_labels_use_resolved_identity_and_keep_model_order(monkeypatch):
    from services.task_execution.inline_image_action import label_current_inputs
    r,_,_,q=case();q=replace(q,content=[TextPart(text='调整追加图片')],context_anchor=SimpleNamespace(input_message_id=str(uuid4())))
    refs=['fref1_ONE','fref1_TWO']
    r.task_user_changes=SimpleNamespace(items=[SimpleNamespace(owner_task_id=q.task_id,delivered=True,
        attachments=[SimpleNamespace(kind='image',workspace_path=f'{i}.png',resource_ref=ref) for i,ref in enumerate(refs)])])
    original='模型的完整编辑原文。'
    item=co.ContentItem(title='编辑',body=original,materials=tuple(co.Material(
        reference={'resource_ref':ref},role='参考',usage='input')for ref in reversed(refs)))
    class Resolver:
        def resolve(self,selectors):
            return [{'workspace_path':row['resource_ref']+'.png','content_sha256':str(refs.index(row['resource_ref']))*64}for row in selectors]
    monkeypatch.setattr('services.task_execution.image_resources.native_resolver',lambda *a:Resolver())
    result=await label_current_inputs(item,None,q,None,runtime=r)
    assert result.body=='输入图片1对应追加图2（用户图2）。\n输入图片2对应追加图1（用户图1）。\n\n'+original
    assert result.materials==item.materials


def test_generated_canvas_remains_authoritative_until_current_user_specs_change_it():
    args={'mode':'image_to_image','prompt':'只换背景','aspect_ratio':'1:1','size_requirement':{'mode':'explicit'}}
    refs=[{'aspect_ratio':'16:9','width':1280,'height':720}]
    previous={'mode':'explicit','aspect_ratio':'16:9','resolution':'2K'}
    out,_=resolve_size_requirement(args,refs,intent={},previous=previous)
    assert (out['aspect_ratio'],out['resolution'])==('16:9','2K')
    out,_=resolve_size_requirement(args,refs,intent={'aspect_ratio':'3:4'},previous=previous)
    assert out['aspect_ratio']=='3:4'


def test_historical_edit_is_not_addressed_as_a_current_attachment():
    resolver=object.__new__(ChatImageInputResolver);resolver.input_message_id='current'
    resolver._message=lambda _: {'id':'current','role':'user','content':[{'type':'text','text':'再换成浅灰色。'}]}
    assert resolver.size_context(use_previous=False,semantic_context=ImageRequestContext(canvas_image=1))==({}, {})
    resolver._message=lambda _: {'id':'current','role':'user','content':[{'type':'image','name':'实际当前图'}]}
    with pytest.raises(ImageSizeError,match='IMAGE_CANVAS_BINDING_INVALID'):
        resolver.size_context(use_previous=False,semantic_context=ImageRequestContext(canvas_image=2))


def test_appended_resource_projection_preserves_owner_delivery_and_identity():
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow,routing_input
    w=object.__new__(EcommerceWorkflow);w.input=[{'type':'text','text':'原任务'}];w.request=SimpleNamespace(task_id='current')
    def change(owner,delivered):
        return SimpleNamespace(owner_task_id=owner,delivered=delivered,event_id='event',message='改追加这张',
            attachments=[SimpleNamespace(kind='image',workspace_path='照片/产品.png',resource_ref='fref1_TEST')])
    w.runtime=SimpleNamespace(task_user_changes=SimpleNamespace(items=[change('current',True),change('other',True),change('current',False)]))
    projected=routing_input(w.action_input())
    assert len(projected)==3
    assert projected[-1]=={'type':'image','name':'产品.png','reference':{'resource_ref':'fref1_TEST'},'event_id':'event','origin':'append'}


def test_appended_canvas_is_bound_to_authenticated_bytes_not_a_current_ordinal():
    from unittest.mock import Mock
    attachment=SimpleNamespace(kind='image',resource_ref='fref1_TEST')
    actor=SimpleNamespace(task_id='current',checkpoint_base={},task_user_changes=SimpleNamespace(items=[
        SimpleNamespace(owner_task_id='current',delivered=True,attachments=[attachment])]))
    r=object.__new__(ChatImageInputResolver);r.input_message_id='message';r.owner=SimpleNamespace(_actor_runtime=actor)
    r._message=lambda _: {'id':'message','role':'user','content':[{'type':'text','text':'用追加的图'}]}
    r.resolve=Mock(return_value=[{'workspace_path':'actual.png','content_sha256':'a'*64}])
    context=ImageRequestContext(canvas_resource='fref1_TEST')
    intent,_=r.size_context(use_previous=False,semantic_context=context)
    refs=[{'workspace_path':'actual.png','content_sha256':'a'*64,'aspect_ratio':'3:4','width':600,'height':800}]
    args,_=resolve_size_requirement({'mode':'image_to_image','prompt':'只换背景'},refs,intent=intent)
    assert args['aspect_ratio']=='3:4'
    actor.task_user_changes.items[0].delivered=False
    with pytest.raises(ImageSizeError,match='IMAGE_CANVAS_BINDING_INVALID'):
        r.size_context(use_previous=False,semantic_context=context)


def test_host_canvas_binding_error_stops_without_asking_the_model_to_rewrite_parameters():
    from services.handlers.chat import tool_argument_repairs as repairs
    r,p,_,q=case()
    calls=[call('generate_image',{'previous_result_edit':'再换成浅灰色'},'gray')]
    batch=repairs.Batch.with_sources(r,q,calls,{'gray':('IMAGE_CANVAS_BINDING_INVALID','宿主投影不一致')})
    assert batch.state.stopped and list(batch.state.counts.values())==[1]
    batch.record(r,p.messages,[])
    assert repairs.stop_message(r)==repairs.CANVAS_STOP
    assert 'repair_arguments' not in batch.rejections['gray']


@pytest.mark.parametrize('explicit',[False,True])
def test_unknown_semantic_size_does_not_erase_model_supplied_canvas(explicit):
    from copy import deepcopy
    from services.handlers.chat_image_preparation import prepare_input
    from services.handlers.chat_input_handoff import digest
    args={'mode':'image_to_image','prompt':'编辑图像','aspect_ratio':'16:9','resolution':'2K',
        'references':[{'message_id':'original','content_index':0,'role':'底图'}]}
    if explicit:
        args['size_requirement']={'mode':'explicit'}
    source={'content_object':{'arguments_sha256':digest(args)}}
    ref={'source':'user','message_id':'original','content_index':0,'workspace_path':'original.png','aspect_ratio':'3:4','width':600,'height':800}
    owner=SimpleNamespace(task_id='current',_actor_runtime=SimpleNamespace(checkpoint_base={'_media_decisions_v1':{}}))
    resolver=SimpleNamespace(owner=owner,normalize_legacy=lambda a:deepcopy(a),
        locate_source_prompt=lambda _:source,complete_source_prompt=lambda s,p:(p,s),
        content_image_item=lambda _:(SimpleNamespace(source_call_id='inline-save-edit',source_task_id='current'),SimpleNamespace(options={})),
        resolve=lambda _:[ref],verify=lambda *a,**k:None,size_context=lambda **k:({},{}))
    result,_,_,_=prepare_input(resolver,args,scoped=None,workflow=SimpleNamespace(request_context=ImageRequestContext()),
        settings=SimpleNamespace(chat_image_transparent_enabled=False),entry_checked=True)
    assert (result['aspect_ratio'],result['resolution'])==('16:9' if explicit else '3:4','2K')
    assert result['prompt']==args['prompt']


@pytest.mark.asyncio
@pytest.mark.parametrize('selection',['image','video','chat','explicit_chat','unknown'])
async def test_entry_keeps_explicit_media_models_without_parsing_free_text(monkeypatch,selection):
    from unittest.mock import AsyncMock
    from schemas.message import GenerationType
    from api.routes.message_request_preparation import resolve_generation_context
    from services.adapters.factory import IMAGE_MODEL_REGISTRY,VIDEO_MODEL_REGISTRY
    monkeypatch.setattr('services.ip_location_service.extract_client_ip',lambda _:None)
    monkeypatch.setattr('services.ip_location_service.get_location_by_ip',AsyncMock(return_value=None))
    model={'image':next(iter(IMAGE_MODEL_REGISTRY)),'video':next(iter(VIDEO_MODEL_REGISTRY)),
        'chat':'qwen3.8-max','explicit_chat':next(iter(IMAGE_MODEL_REGISTRY)),'unknown':'unknown-model'}[selection]
    request=SimpleNamespace(model=model,generation_type=GenerationType.CHAT if selection=='explicit_chat' else None,
        params=None,content=[TextPart(text='解释生成图片和视频是什么意思，先只写文字')])
    result=await resolve_generation_context(SimpleNamespace(),request)
    assert result==({'image':GenerationType.IMAGE,'video':GenerationType.VIDEO}.get(selection,GenerationType.CHAT))


@pytest.mark.asyncio
async def test_semantic_authoring_publishes_commentary_and_exact_document_without_reauthoring():
    import json
    from services.adapters.types import StreamChunk,ToolCallDelta
    from tests.test_content_objects import run_content_loop
    r,p,h,q=case();r.skill_coordination=True
    intro='我先看一下参考资料，下面整理三条方案。\n\n'
    document={'title':'三条方案','display_prefix':'## 正式方案',
        'items':[{'title':str(i),'body':f'方案{i}的完整最终原文，保留全部原始限制。'}for i in (1,2,3)]}
    rounds=0
    async def stream(**kwargs):
        nonlocal rounds
        rounds+=1
        yield StreamChunk(content=intro)
        yield StreamChunk(tool_calls=[ToolCallDelta(index=0,id='publish',name=co.SAVE,
            arguments_delta=json.dumps({'document':document,'confirmation':{'tool':'generate_image','wait_all':True}}))])
        yield StreamChunk(finish_reason='tool_calls')
    sink,blocks,_=await run_content_loop(r,p,h,q,stream)
    texts=[b['text']for b in blocks if b['type']=='text']
    assert rounds==1 and texts==[intro,co.render(co.ContentDocument.model_validate(document))]
    row,=co.catalog(r.task_execution_state)
    _,saved,_=co._load_record(r.task_execution_state,{'artifact_id':row['artifact_id'],'item_index':1})
    assert saved.confirmation_items==(1,2,3)
    assert saved.document==co.ContentDocument.model_validate(document)
    assert all(item.body in texts[-1] for item in saved.document.items)
    assert intro not in saved.document.items[0].body
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('variant',['nullable','ready_selection','known_pending','unrelated_pending'])
async def test_workflow_wire_normalization_keeps_backend_owned_identity(monkeypatch,variant):
    import json
    from services.agent.image.ecommerce_planner.workflow import classify_workflow
    from services.intent_router import IntentRouter
    media=None if variant=='nullable' else {'tool':('generate_video' if variant=='known_pending' else 'generate_image' if variant=='unrelated_pending' else None),
        'approved':[2], 'confirmation_only':True}
    frame={'size':None,'platform':'JD','media':media,'deliveries':[]}
    result={'kind':'ordinary_image','mode':'new','reuse_through_stage':0,'intent':'write',
        'image_count':3,'request_context':frame}
    async def transport(self,**kwargs):
        assert kwargs['max_tokens']==2048
        return {'choices':[{'message':{'tool_calls':[{'function':{'name':'select_image_workflow',
            'arguments':json.dumps(result)}}]}}]}
    monkeypatch.setattr(IntentRouter,'call_tool_model',transport)
    context={'saved_groups':[{'artifact_id':'saved-original','revision':7,'confirmation_tool':'generate_video',
        'items':[{'item_index':2,'awaiting_confirmation':True}]}]} if variant in {'known_pending','unrelated_pending'} else None
    value=await classify_workflow([{'type':'text','text':'第二条可以了'}],None,context=context)
    assert value['request_context']['size']=={} and value['request_context']['platform']=='京东'
    directive=value['request_context']['media']
    if variant=='known_pending':
        assert directive['artifact_id']=='saved-original' and directive['revision']==7
        assert directive['approved']==[2] and directive['confirmation_only'] is True
    else:
        assert directive['approved']==[] and directive['confirmation_only'] is False


def test_first_delivery_projection_preserves_confirmation_contract():
    projected,=tools.model_tools([co.SCHEMA],first_delivery=True)
    assert 'confirmation' in projected['function']['parameters']['properties']
    assert 'wait_all' in projected['function']['description']
    assert 'wait_for' in projected['function']['description']


def test_order_scope_examples_and_model_contract_use_one_parameter_source():
    from services.agent.plan_builder import build_multi_extract_prompt,build_extract_prompt
    for prompt in (build_multi_extract_prompt('query'),build_extract_prompt('query')):
        assert '"order_scope":"only_scalping"' in prompt
        assert '"is_scalping":true' not in prompt
        assert '"include_invalid":true' not in prompt


def test_image_material_contract_includes_visual_references_as_execution_input():
    description=co.Material.model_json_schema()['properties']['usage']['description']
    assert '风格、颜色、纹理参考' in description
    assert co.Material(reference={'resource_ref':'image-one'},role='颜色参考',usage='input').usage=='input'
    assert co.Material(reference={'resource_ref':'background-note'},role='仅阅读资料',usage='analysis').usage=='analysis'


@pytest.mark.parametrize('legacy,expected',[(True,'all'),(False,'valid')])
def test_legacy_explicit_scope_boolean_is_normalized_without_query_words(legacy,expected):
    value={'doc_type':'order','include_invalid':legacy}
    normalize_order_scope(value,'not interpreted by the compiler')
    assert value['order_scope']==expected and value['include_invalid'] is legacy


@pytest.mark.parametrize('text', ['生成1920×1080，差一点没关系', '生成约1920×1080', '图片接近1920×1080就可以'])
def test_semantic_approximation_reaches_native_size_without_reparsing_words(text):
    context=ImageRequestContext(size={'width':1920,'height':1080,'approximate_pixels':True})
    resolver=object.__new__(ChatImageInputResolver)
    resolver.input_message_id='current'
    resolver._message=lambda _: {'id':'current','role':'user','content':[{'type':'text','text':text}]}
    intent,previous=resolver.size_context(use_previous=False,semantic_context=context)
    args,target=resolve_size_requirement({'mode':'text_to_image','model':'gpt-image-2-text-to-image','prompt':'产品图'},[],intent=intent,previous=previous)
    assert (args['aspect_ratio'],args['resolution'])==('16:9','2K')
    assert target['requested_width']==1920 and target['requested_height']==1080


@pytest.mark.parametrize('text', ['不做方图，输出竖版','不要1:1，输出3:4'])
def test_negated_dimensions_cannot_override_the_current_semantic_canvas(text):
    context=ImageRequestContext(size={'aspect_ratio':'3:4'})
    resolver=object.__new__(ChatImageInputResolver);resolver.input_message_id='current'
    resolver._message=lambda _: {'id':'current','role':'user','content':[{'type':'text','text':text}]}
    intent,_=resolver.size_context(use_previous=False,semantic_context=context)
    args,_=resolve_size_requirement({'mode':'text_to_image','prompt':'产品图'},[],intent=intent)
    assert args['aspect_ratio']=='3:4'
    with pytest.raises(ImageSizeError,match='IMAGE_EXACT_SIZE_UNSUPPORTED'):
        resolve_size_requirement({'mode':'text_to_image','prompt':'产品图'},[],intent={'width':1920,'height':1080})


@pytest.mark.parametrize('text', ['先写三套主图方案给我看看','先写三套主图方案给我看看，不要上传'])
def test_ready_plan_delivery_is_independent_of_negated_delivery_words(text):
    runtime,_,_,request=case();runtime.skill_coordination=True
    runtime.workflow_projection={'intent':'write','request_context':{'size':{},'platform':'京东'}}
    request=replace(request,content=[TextPart(text=text)])
    result=SimpleNamespace(metadata={'status':'ready','plan_id':str(uuid4()),
        'server_plan_owner_task_id':str(request.task_id),'server_plan_text':'三套完整方案正文'})
    native_plan_delivery.stage(runtime,request,[({'name':'plan_ecommerce_images'},result,False,None)])
    assert runtime.checkpoint_base[native_plan_delivery.KEY]['text']=='三套完整方案正文'


@pytest.mark.parametrize('text', ['等我说可以了再做成视频','等我点头再生成视频','等我确认后再做成视频'])
def test_future_delivery_uses_waiting_state_instead_of_surface_verbs(text):
    request=SimpleNamespace(content=[TextPart(text=text)],params={})
    runtime=SimpleNamespace(skill_coordination=True,workflow_projection={'request_context':{
        'deliveries':[], 'media':{'tool':'generate_video','wait_all':True}}})
    assert tools.requested_delivery_tools(request,runtime)=={}
    assert tools.waits_for_video_confirmation(request,runtime)


@pytest.mark.parametrize('text', ['请问要用哪张图片？','请问要用哪张图片。','必须先给我两张参考图，我才能继续。'])
def test_unaccepted_status_does_not_delete_clarification_or_authored_body(text):
    result=tools.unconfirmed_image_reply(text,False)
    assert result.startswith(text) and tools.IMAGE_NOT_SUBMITTED in result


@pytest.mark.parametrize('text', ['解释生成图片是什么意思，不要生成图片','不要生成视频，先写方案','帮我画一张图的提示词，只写文字'])
def test_free_text_without_an_explicit_request_type_keeps_the_chat_entry(text):
    assert infer_generation_type([TextPart(text=text)]).value=='chat'


@pytest.mark.parametrize('text', ['统计全部订单，不要排除刷单','统计有效订单，不要只查刷单'])
def test_order_scope_is_compiled_without_overwriting_the_extracted_parameter(text):
    value={'doc_type':'order','order_scope':'all','order_status':'CLOSED'}
    normalize_order_scope(value,text)
    assert value=={'doc_type':'order','order_scope':'all','order_status':'CLOSED','include_invalid':True}


@pytest.mark.asyncio
async def test_semantic_confirmation_keeps_original_saved_identity_and_rejects_a_changed_version():
    runtime,prepared,handler,request=case();prepared.content_controls_available=True
    runtime.skill_coordination=True
    request=replace(request,content=[TextPart(text='第2条和第3条先等我点头。')])
    draft={'title':'三条短片','items':[{'title':str(i),'body':f'视频脚本{i}'}for i in range(1,4)]}
    await execute(runtime,prepared,handler,request,[call(co.SAVE,{'document':draft,
        'confirmation':{'tool':'generate_video','wait_for':[2,3]}},'save')])
    row,=co.catalog(runtime.task_execution_state,for_model=True)
    assert [i.get('awaiting_confirmation',False) for i in row['items']]==[False,True,True]
    submit=call('generate_video',{'content_source':{'artifact_id':row['artifact_id'],'item_index':2}},'second')
    value=confirmation.MediaDirective(tool='generate_video',approved=[2],confirmation_only=True,
        artifact_id=row['artifact_id'],revision=row['revision'])
    for text in ['确认第2条','第2条我确认了，可以开始做了','第二条可以做了']:
        assert confirmation.check(submit,runtime.task_execution_state,text,semantic=value) is None
    assert confirmation.check(submit,runtime.task_execution_state,'确认第2条',
        semantic=value.model_copy(update={'revision':row['revision']+1}))[0]=='CONTENT_CONFIRMATION_AMBIGUOUS'
    assert confirmation.check(submit,runtime.task_execution_state,'先不要确认第2条',
        semantic=confirmation.MediaDirective())[0]=='CHAT_MEDIA_AWAITING_USER'
    third=call('generate_video',{'content_source':{'artifact_id':row['artifact_id'],'item_index':3}},'third')
    assert confirmation.check(third,runtime.task_execution_state,'确认第2条',semantic=value)[0]=='CONTENT_CONFIRMATION_DIFFERENT_ITEM'
