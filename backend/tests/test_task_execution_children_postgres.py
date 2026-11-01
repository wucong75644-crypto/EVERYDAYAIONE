"""Native image acceptance atomically keeps logical child dependency durable."""
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.task_execution.contracts import TaskState
from services.task_execution.image_binding import image_prompt
from services.task_execution.methods import ImageMethodDocument
from services.tools import ToolCall
from services.handlers.chat_image_request import freeze_image_request
from tests.test_task_execution_operations_postgres import (
    operation_db,task_db,composition_db,ecom_db,isolated_db,seed,connect,save,typed_store)
from tests.test_task_execution_image_binding import ready
from tests.test_task_execution_methods import document
from tests.test_task_execution_postgres import bind_seed

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module')
def children_db(operation_db):
    with psycopg.connect(operation_db) as c:
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/342_agent_task_image_dependencies.sql').read_text())
    yield operation_db
    with psycopg.connect(operation_db) as c:
        c.execute('DELETE FROM agent_task_events WHERE child_task_id IS NOT NULL')
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/rollback/342_agent_task_image_dependencies_rollback.sql').read_text())


async def child_case(dsn, monkeypatch, *, image_count=None, downstream=False, existing_source=False,
                     effect_key='delivery-slot-1',binding_version=1):
    f = seed(dsn)
    state = bind_seed(await ready(monkeypatch),f)
    raw = state.model_dump(mode='json')
    if image_count is not None:
        import hashlib
        text=document(image_count)
        raw['original_goal']=f'先完整规划，再生成{image_count}张详情图'
        for requirement in raw['requirements']:
            if requirement['artifact_kind']=='image':
                requirement.update(minimum=image_count,description=f'交付{image_count}张详情图')
        for binding in raw['method_bindings']:
            binding['image_count']=image_count
        for artifact in raw['artifacts']:
            if artifact['kind']=='text':
                artifact.update(inline_text=text,sha256=hashlib.sha256(text.encode()).hexdigest())
    for artifact in raw['artifacts']:
        artifact.update(source_task_id=f['task'],delivery_task_id=f['task'])
    if downstream:
        raw['original_goal']+='，图片完成后整理结果说明'
        raw['requirements'].append({'id':'user.summary','description':'整理结果说明','source_message_id':f['message'],
            'artifact_kind':'text','minimum':1,'validator':'artifact.exists'})
        raw['steps'].append({'id':'summary','title':'整理结果说明','depends_on':['images'],'requirement_ids':['user.summary']})
    for result in raw['results']:
        result['completed_task_id'] = f['task']
    state = TaskState.model_validate(raw)
    if binding_version>=2:
        from services.task_execution.image_resources import BindImageResources,bind_resources
        from services.task_execution.reducer import register_artifacts
        state=state.model_copy(update={'image_binding_version':binding_version})
        if binding_version>=3:
            import json
            from types import SimpleNamespace
            from unittest.mock import AsyncMock
            from services.task_execution.plan_facts import before_complete,sections,apply_review
            from services.task_execution.reducer import digest
            from services.handlers.chat.execution_engine import _build_replay_context
            from tests.test_task_execution_model_bridge import setup_case
            with psycopg.connect(f['dsn']) as c:
                c.execute('UPDATE messages SET content=%s WHERE id=%s',
                    (Jsonb([{'type':'text','text':state.original_goal}]),f['message']))
            # Component evidence only: the model verdict is a double, while
            # the receipt producer and final native source revalidation are real.
            sources={'references':[],'resolved':[],'origins':[],
                'texts':[{'id':'user:'+f['message'],'text':state.original_goal}]}
            sources['sha256']=digest(sources)
            runtime,prepared,_,sink=setup_case(initial=state)
            runtime.task_id=f['task'];runtime.task_plan_source_provider=AsyncMock(return_value=(sources,[]))
            await before_complete(runtime,state,'planning',[state.artifacts[0].id],force=True)
            fields=sections(state.artifacts[0])
            reply={'reviewed_sections':list(fields),'claims':[{'section':key,'claim':text,'status':'supported',
                'evidence':[{'source_id':'user:'+f['message'],'kind':'user_text','quote':state.original_goal}]} for key,text in fields.items()]}
            await apply_review(runtime=runtime,prepared=prepared,blocks=[],text=json.dumps(reply),has_tool_calls=False,
                model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1,sink=sink)
            state=runtime.task_execution_state
        async def no_images(refs,**_):
            assert refs==[]
            return []
        artifact,_=await bind_resources(state,BindImageResources(expected_revision=state.revision,
            planning_artifact_id=state.artifacts[0].id,image_item_index=1,mode='text_to_image',references=()),
            resolver=no_images,owner_task_id=f['task'],call_id='bind-empty-originals')
        state=register_artifacts(state,[artifact])
    if existing_source:
        from tests.task_execution_source_fixtures import existing_source_state
        state=existing_source_state(state)
    with connect(f) as c:
        save(c,f,{'messages':[],'_task_execution':state.model_dump(mode='json')})
    brief = ImageMethodDocument.model_validate_json(document()).image_plans[0]
    args = {'mode':'text_to_image','prompt':image_prompt(brief),'aspect_ratio':'3:4'}
    if binding_version>=2:args['size_requirement']={'mode':'explicit'}
    call = ToolCall('native-1','generate_image',args)
    first = await typed_store(f).begin(state,step_id='images',effect_key=effect_key,effect_version=1,call=call)
    origin = {'parent_task_id':f['task'],'actor_user_id':f['user'],'workspace_owner_id':f['user'],
        'org_id':None,'context_scope':'user','conversation_id':f['conv'],'turn_id':f['turn'],
        'input_message_id':f['message'],'base_context_revision':0,'tool_call_id':call.call_id}
    snapshot = freeze_image_request({k:v for k,v in args.items() if k!='size_requirement'},[],origin=origin,max_requests=6,max_credits=100)
    return f,first,snapshot


def accept_child(f,snapshot):
    with connect(f) as c:
        return c.execute('SELECT accept_chat_image_request(%s,%s,%s,%s)',
            (f['task'],f['token'],Jsonb(snapshot),None)).fetchone()[0]


@pytest.mark.asyncio
async def test_native_acceptance_and_logical_dependency_commit_together_and_replay_does_not_duplicate(children_db,monkeypatch):
    f,first,snapshot = await child_case(children_db,monkeypatch)
    child = accept_child(f,snapshot)
    again = accept_child(f,snapshot)
    assert again['task_id']==child['task_id'] and again['outcome']=='replay'
    with connect(f) as c:
        rows = c.execute('SELECT root_task_id,operation_id,child_task_id,status,payload FROM agent_task_events WHERE root_task_id=%s',
            (f['task'],)).fetchall()
    assert len(rows)==1 and str(rows[0][0])==f['task'] and rows[0][1]==first.operation.id
    assert str(rows[0][2])==child['task_id'] and rows[0][3]=='pending'
    assert rows[0][4]['kind']=='image_child'


@pytest.mark.asyncio
async def test_child_binding_failure_rolls_back_native_task_message_and_budget(children_db,monkeypatch):
    f,first,snapshot = await child_case(children_db,monkeypatch)
    snapshot['origin']['tool_call_id']='unbound-call'
    with pytest.raises(psycopg.errors.InsufficientPrivilege,match='TASK_IMAGE_OPERATION_BINDING_REQUIRED'):
        accept_child(f,snapshot)
    with psycopg.connect(f['dsn']) as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM messages WHERE conversation_id=%s',(f['conv'],)).fetchone()[0]==1
        assert c.execute('SELECT credits FROM users WHERE id=%s',(f['user'],)).fetchone()[0]==100
        assert c.execute('SELECT count(*) FROM agent_task_events WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==0


@pytest.mark.asyncio
async def test_terminal_child_event_survives_closed_parent_and_worker_only_sees_ready_child(children_db,monkeypatch):
    f,first,snapshot = await child_case(children_db,monkeypatch)
    child = accept_child(f,snapshot)
    with psycopg.connect(f['dsn']) as c:
        c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(f['task'],))
    with connect(f) as c:
        c.execute("SELECT set_config('app.access_kind','worker',true),set_config('app.actor_user_id','',true)")
        assert c.execute('SELECT count(*) FROM agent_task_events WHERE child_task_id=%s',(child['task_id'],)).fetchone()[0]==0
    with psycopg.connect(f['dsn']) as c:
        c.execute("UPDATE tasks SET status='completed' WHERE id=%s",(child['task_id'],))
    with connect(f) as c:
        c.execute("SELECT set_config('app.access_kind','worker',true),set_config('app.actor_user_id','',true)")
        row = c.execute('SELECT status,payload FROM agent_task_events WHERE child_task_id=%s',(child['task_id'],)).fetchone()
    assert row[0]=='pending' and row[1]['child_task_id']==child['task_id']


@pytest.mark.asyncio
async def test_child_identity_cannot_be_retargeted_to_another_effect(children_db,monkeypatch):
    f,first,snapshot = await child_case(children_db,monkeypatch)
    child=accept_child(f,snapshot)
    with pytest.raises(psycopg.errors.InsufficientPrivilege,match='TASK_CHILD_IDENTITY_IMMUTABLE'):
        with connect(f) as c:
            c.execute('UPDATE agent_task_events SET operation_id=%s WHERE child_task_id=%s',('0'*64,child['task_id']))
