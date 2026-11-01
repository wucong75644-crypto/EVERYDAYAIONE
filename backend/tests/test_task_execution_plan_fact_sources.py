"""Actual scoped file originals remain identical across review and submission."""
import copy
from types import SimpleNamespace

import pytest
from PIL import Image

from services.task_execution.plan_fact_sources import capture_sources,check_sources,preview,verify_review_sources
from services.task_execution.contracts import TaskError
from tests.test_chat_image_sources import context,resolver
from tests.test_chat_image_reference_continuity import source


def test_originals_and_user_texts_are_host_resolved_and_previews_do_not_enter_snapshot(context):
    native=resolver(context)
    captured=capture_sources(native)
    assert len(captured['resolved'])==2 and len(captured['texts'])==2
    assert 'image_url' not in str(captured) and 'base64' not in str(captured)
    assert all(r['content_sha256'] for r in captured['resolved'])
    assert captured['origins']==['user_reference','user_reference']
    assert preview(native,captured['resolved'][0]).startswith('data:image/jpeg;base64,')
    assert check_sources(resolver(context),captured)==captured


@pytest.mark.parametrize('changed',['file','text','snapshot','future'])
def test_source_mutation_or_future_history_cannot_reuse_approval(context,changed):
    native=resolver(context);captured=capture_sources(native)
    row,owner,files,current=context
    if changed=='file':Image.new('RGB',(4,4),'green').save(files.resolve_safe_path('A.png'))
    if changed=='text':row['content'][0]['text']='replacement user facts'
    if changed=='snapshot':captured['texts'][0]['text']='fabricated source'
    if changed=='future':row['context_revision']=2
    with pytest.raises((TaskError,ValueError,PermissionError)):
        check_sources(resolver(context),captured)


@pytest.mark.parametrize('kind',['uploaded','generated','quoted_generated'])
def test_native_source_provenance_is_retained_separately_from_v2_fingerprint(context,kind):
    row,owner,_,current=context
    if kind!='uploaded':row['role']='assistant'
    if kind=='quoted_generated':
        current['content']=[{'type':'image','workspace_path':'B.png','source_message_id':row['id'],
            'source_content_index':3,'source_task_id':'original-generation'}]
        owner.db.set_table_data('tasks',[{'id':'original-generation','user_id':owner.user_id,'org_id':owner.org_id,
            'conversation_id':row['conversation_id'],'type':'image','status':'completed','assistant_message_id':row['id']}])
        refs=[{'message_id':current['id'],'content_index':0,'role':'reference'}]
    else:refs=[{'message_id':row['id'],'content_index':3,'role':'reference'}]
    captured=capture_sources(resolver(context),refs)
    assert captured['origins']==['user_reference' if kind=='uploaded' else 'generated']
    assert 'source' not in captured['resolved'][0]


@pytest.mark.asyncio
async def test_factual_approval_cannot_be_reused_for_another_original(monkeypatch,context):
    from tests.test_task_execution_plan_facts import drafted,observation,review
    runtime,prepared,_,sink,blocks,artifact=await drafted(monkeypatch)
    native=resolver(context)
    refs=[{'message_id':'source-message','content_index':1,'role':'reference'}]
    captured=capture_sources(native,refs)
    pending=runtime.task_plan_reviews.pending.model_copy(update={'sources':captured})
    runtime.task_plan_reviews=runtime.task_plan_reviews.model_copy(update={'pending':pending})
    # Test double cites real user-text evidence. It is not provider acceptance.
    import json
    value=json.loads(observation(artifact))
    for claim in value['claims']:
        claim['evidence']=[{'source_id':captured['texts'][0]['id'],'kind':'user_text','quote':captured['texts'][0]['text']}]
    await review(runtime,prepared,sink,blocks,json.dumps(value))
    actual=native.resolve(refs)
    verify_review_sources(runtime.task_execution_state,artifact,native,actual)
    other=native.resolve([{'message_id':'source-message','content_index':3,'role':'product'}])
    with pytest.raises(TaskError,match='REFERENCE_NOT_REVIEWED'):
        verify_review_sources(runtime.task_execution_state,artifact,native,other)


@pytest.mark.asyncio
async def test_imported_native_data_can_support_plan_facts_and_changed_receipt_is_rejected(monkeypatch,mock_db,tmp_path):
    from services.task_execution.plan_fact_sources import create_source_provider
    from services.task_execution.image_resources import native_resolver
    from services.task_execution.native_results import import_result
    from services.task_execution.controller import ImportNativeResult
    from services.task_execution.reducer import register_artifacts
    from tests.test_task_execution_native_results import execute_case
    state,context,runtime,_,service,call,_,_,_,record=await execute_case()
    artifact,_=import_result(state,ImportNativeResult(expected_revision=state.revision,step_id='query',tool_call_id=call.call_id),
        record,owner_task_id=state.root_task_id,context=context,registry=service.registry)
    state=register_artifacts(state,[artifact]);runtime.task_execution_state=state
    task={'id':str(state.root_task_id),'user_id':str(state.actor_user_id),'org_id':None,'conversation_id':str(state.conversation_id),
        'input_message_id':str(state.source_message_ids[0]),'base_context_revision':0,'request_params':{}}
    mock_db.set_table_data('tasks',[task])
    mock_db.set_table_data('messages',[{'id':task['input_message_id'],'conversation_id':task['conversation_id'],'org_id':None,
        'role':'user','status':'pending','context_revision':None,'content':[{'type':'text','text':state.original_goal}]}])
    stored={**copy.deepcopy(record),'conversation_id':str(state.conversation_id)}
    mock_db.set_table_data('tool_invocations',[stored])
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path))
    handler=SimpleNamespace(db=mock_db,_actor_runtime=runtime)
    request=SimpleNamespace(task_id=task['id'],conversation_id=task['conversation_id'])
    provider=create_source_provider(handler,request,context)
    snapshot,pictures=await provider(images=True)
    assert pictures==[] and snapshot['tool_facts'][0]['id']=='tool:'+artifact.id
    assert '8入' in snapshot['tool_facts'][0]['text']
    actual,_=await provider(snapshot=snapshot,images=False)
    assert actual==snapshot
    stored['result']['tool_result']['raw']['data'][0]['capacity']='12入'
    with pytest.raises(TaskError,match='RESULT_CHANGED'):
        await provider(snapshot=snapshot,images=False)
