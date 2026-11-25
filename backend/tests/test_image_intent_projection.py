"""Image intent is one frozen semantic decision, not a per-consumer word list."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from schemas.message import TextPart
from services.handlers.chat import content_tools as tools
from services.agent.image.ecommerce_planner.workflow import WorkflowBinding


@pytest.mark.parametrize('text', ['就这样吧。', '第一个和第三个做成图吧。', '解释“马上生图”这句话。', 'please use those two'])
@pytest.mark.parametrize('intent', ['write','execute_saved','edit','explain'])
def test_every_consumer_uses_the_same_intent_independently_of_surface_words(text,intent):
    q=SimpleNamespace(content=[TextPart(text=text)],params={})
    runtime=SimpleNamespace(skill_coordination=True,workflow_projection={'intent':intent})
    assert tools.image_execution_request(q,runtime) is (intent in {'execute_saved','edit'})
    assert tools.image_writing_only_request(q,runtime) is (intent in {'write','explain'})
    assert tools.selects_previous_item(q,runtime) is (intent=='execute_saved')
    assert tools.requests_generated_image_edit(q,runtime=runtime) is (intent=='edit')
    assert tools.authored_text_request(q,runtime) is (intent=='write')


def test_old_workflow_projection_serializes_without_new_defaults():
    raw={'kind':'main_images','root_task_id':str(uuid4()),'window_task_id':str(uuid4()),'generation_run_id':str(uuid4())}
    old=WorkflowBinding.model_validate(raw).model_dump(mode='json')
    assert 'intent' not in old and 'input_revision' not in old
    frozen={**old,'intent':'execute_saved','input_revision':'original-revision'}
    assert WorkflowBinding.model_validate(frozen).model_dump(mode='json')==frozen


def test_unclassified_new_actor_does_not_fall_back_to_a_word_based_permission():
    q=SimpleNamespace(content=[TextPart(text='只写这个生图提示词')],params={})
    runtime=SimpleNamespace(skill_coordination=True)
    assert tools.semantic_intent(q,runtime) is None
    assert not tools.image_execution_request(q,runtime)
    assert not tools.image_writing_only_request(q,runtime)
    assert not tools.selects_previous_item(q,runtime)


@pytest.mark.asyncio
async def test_appended_input_is_reclassified_once_only_after_delivery(monkeypatch):
    from unittest.mock import AsyncMock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    from services.task_execution.content_delivery import input_revision
    task=str(uuid4())
    q=SimpleNamespace(task_id=task,params={},selected_skill=None,context_anchor=None)
    runtime=SimpleNamespace(skill_coordination=True,skill_runtime=None,task_user_changes=SimpleNamespace(items=()))
    w=EcommerceWorkflow.__new__(EcommerceWorkflow)
    w.runtime,w.request,w.handler=runtime,q,SimpleNamespace(org_id=None)
    w.previous,w.route,w.input=None,None,[{'type':'text','text':'先给我看三个方案'}]
    w.value=WorkflowBinding(kind='ordinary_image',root_task_id=task,window_task_id=task,
        generation_run_id=str(uuid4()),intent='write',input_revision=input_revision(runtime,q))
    router=AsyncMock(return_value={'kind':'ordinary_image','mode':'new','reuse_through_stage':0,'intent':'generate'})
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',router)
    change=SimpleNamespace(event_id=uuid4(),message_sha256='b'*64,owner_task_id=task,
        delivered=False,message='现在直接做出来吧',attachments=())
    runtime.task_user_changes.items=(change,)
    await w.refresh_action()
    router.assert_not_awaited()
    change.delivered=True
    await w.refresh_action()
    await w.refresh_action()
    router.assert_awaited_once()
    assert router.await_args.args[0]==[*w.input,{'type':'text','text':change.message}]
    assert w.value.intent=='generate' and tools.semantic_intent(q,runtime)=='generate'
    assert runtime.workflow_projection['input_revision']==input_revision(runtime,q)


@pytest.mark.asyncio
async def test_restoring_ready_execution_reuses_frozen_identity_without_reclassification(monkeypatch):
    from unittest.mock import AsyncMock,MagicMock,Mock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    task,user,conv,plan=[str(uuid4()) for _ in range(4)]
    frozen=WorkflowBinding(kind='main_images',mode='input',root_task_id=task,window_task_id=task,
        generation_run_id=str(uuid4()),plan_id=plan,intent='execute_saved',input_revision='frozen')
    q=SimpleNamespace(task_id=task,user_id=user,conversation_id=conv,params={},replay_context={'safe_point':'before_tool'},
        selected_skill=None,content=[TextPart(text='这个话术不需要再被解析')])
    w=EcommerceWorkflow(SimpleNamespace(db=Mock(),org_id=None),q,SimpleNamespace(checkpoint_base={},skill_coordination=True,skill_runtime=None),None)
    query=MagicMock()
    for name in ('select','eq','single','maybe_single'):getattr(query,name).return_value=query
    ready={'id':plan,'status':'ready','plan_revision':2,'items':[]}
    query.execute.side_effect=[SimpleNamespace(data={'user_id':user,'org_id':None,'conversation_id':conv,
        'status':'running','input_message_id':str(uuid4()),'request_params':{'_ecom_workflow':frozen.model_dump(mode='json')}}),
        SimpleNamespace(data={'role':'user','content':[]}),SimpleNamespace(data=ready)]
    w.scope=SimpleNamespace(table=lambda _:query)
    router=AsyncMock(side_effect=AssertionError('Frozen recovery must not classify again'))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',router)
    await w.initialize()
    assert w.ready_execution==ready and w.value==frozen
    router.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('current_owner', ['current','foreign','current-with-pending-change'])
async def test_adopted_content_keeps_source_identity_and_current_window_intent(monkeypatch,current_owner):
    import hashlib
    from unittest.mock import AsyncMock,MagicMock,Mock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    from services.task_execution.content_delivery import input_revision
    task,user,conv,root,run=[str(uuid4()) for _ in range(5)]
    source=WorkflowBinding(kind='ordinary_image',root_task_id=root,window_task_id=task,
        generation_run_id=run,intent='write',image_count=3,input_revision='old-writing-window')
    current=WorkflowBinding(kind='ordinary_image',root_task_id=task,
        window_task_id=task if current_owner.startswith('current') else str(uuid4()),generation_run_id=str(uuid4()),
        intent='execute_saved',input_revision='new-window-before-transfer')
    text='第一个和第三个做成图吧。'
    q=SimpleNamespace(task_id=task,user_id=user,conversation_id=conv,params={},selected_skill=None,
        replay_context={'safe_point':'before_model'},content=[TextPart(text=text)],context_anchor=None)
    runtime=SimpleNamespace(skill_coordination=True,skill_runtime=None,task_user_changes=SimpleNamespace(items=()),
        checkpoint_base={'_task_followup_input_checkpoint':{'_ecom_workflow':current.model_dump(mode='json')},
            '_task_followup':{'input_sha256':hashlib.sha256(text.encode()).hexdigest()}})
    if current_owner=='current-with-pending-change':
        runtime.task_user_changes.items=(SimpleNamespace(event_id=uuid4(),owner_task_id=task,
            message='先别做了',message_sha256=hashlib.sha256('先别做了'.encode()).hexdigest(),delivered=False),)
    w=EcommerceWorkflow(SimpleNamespace(db=Mock(),org_id=None),q,runtime,None)
    query=MagicMock()
    for name in ('select','eq','single'):getattr(query,name).return_value=query
    query.execute.side_effect=[SimpleNamespace(data={'user_id':user,'org_id':None,'conversation_id':conv,
        'status':'running','input_message_id':str(uuid4()),'request_params':{'_ecom_workflow':source.model_dump(mode='json')}}),
        SimpleNamespace(data={'role':'user','content':[{'type':'text','text':text}]})]
    w.scope=SimpleNamespace(table=lambda _:query)
    router=AsyncMock(side_effect=AssertionError('Use the frozen incoming window, not a second model decision'))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.workflow.classify_workflow',router)
    await w.initialize()
    if current_owner.startswith('current'):
        assert w.value.intent=='execute_saved'
        if current_owner=='current':
            assert w.value.input_revision==input_revision(runtime,q)
        else:
            assert w.value.input_revision!=input_revision(runtime,q)
        await w.refresh_action()
    else:
        assert w.value.intent=='write'
    assert w.value.root_task_id==root and w.value.generation_run_id==run and w.value.plan_id is None
    router.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('order', [(1,2),(2,1)])
async def test_input_position_labels_follow_resolved_materials_without_rewriting_model_body(monkeypatch,order):
    from services.task_execution import content_objects as content
    from services.task_execution.inline_image_action import label_current_inputs
    message=str(uuid4())
    item=content.ContentItem(title='编辑',body='这是模型润色后的画面要求，不得被替换。',materials=tuple(
        content.Material(reference=content.MaterialReference(message_id=message,content_index=i),role=f'素材{i}',usage='input')
        for i in order))
    from schemas.message import TextPart,ImagePart
    q=SimpleNamespace(content=[TextPart(text='编辑'),ImagePart(name='图一',workspace_path='one.png'),ImagePart(name='图二',workspace_path='two.png')],
        context_anchor=SimpleNamespace(input_message_id=message))
    class Resolver:
        def resolve(self,selectors):
            return [{'message_id':row['message_id'],'content_index':row['content_index']} for row in selectors]
    monkeypatch.setattr('services.task_execution.image_resources.native_resolver',lambda *a:Resolver())
    result=await label_current_inputs(item,None,q,None)
    assert result.body=='\n'.join(f'输入图片{i}对应用户图{number}。' for i,number in enumerate(order,1))+'\n\n'+item.body
    assert result.materials==item.materials
    assert (await label_current_inputs(result,None,q,None))==result
