"""Registered objects use the existing fenced save and followup transfer."""
import copy
from dataclasses import replace
from uuid import UUID
import psycopg
import pytest
from services.task_execution import content_objects as co
from services.task_execution.direct_loop import end_candidate
from services.task_execution.contracts import TaskState
from tests.test_content_objects import document, ref
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call
from tests.test_task_execution_postgres import save
from tests.test_model_directed_dispatch_postgres import dispatch_db, controls_db
from tests.test_skill_composition_postgres import seed, connect
from tests.test_task_execution_followup_reuse_postgres import (followup_db,new_turn,adopt,continuation_db,native_result_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db)

@pytest.fixture(scope='module')
def content_db(dispatch_db):
    root=__import__('pathlib').Path(__file__).parents[1]
    with psycopg.connect(dispatch_db) as c:
        for migration in ('376_agent_task_image_method_handoff','377_agent_task_content_objects'):
            c.execute((root/f'migrations/{migration}.sql').read_text())
    yield dispatch_db
    with psycopg.connect(dispatch_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_events');c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for migration in ('377_agent_task_content_objects','376_agent_task_image_method_handoff'):
            c.execute((root/f'migrations/rollback/{migration}_rollback.sql').read_text())

@pytest.mark.asyncio
async def test_registered_object_survives_real_new_turn_and_patch_without_new_review(content_db):
    followup_db=content_db
    f=seed(followup_db)
    async def checkpoint(point,payload):
        with connect(f) as c:return save(c,f,payload,expected=payload.get('_task_execution_expected_revision',0))
    r,p,h,q=case(checkpoint)
    r.task_bootstrap=replace(r.task_bootstrap,task_id=UUID(f['task']),conversation_id=UUID(f['conv']),actor_user_id=UUID(f['user']),
        input_message_id=UUID(f['message']),execution_identity=r.task_bootstrap.execution_identity.model_copy(update={'workspace_owner_id':f['user']}))
    r.task_id=f['task'];r.conversation_id=f['conv'];r.turn_id=f['turn']
    p.execution_context=replace(p.execution_context,actor_user_id=f['user'],workspace_owner_id=f['user'],conversation_id=f['conv'],task_id=f['task'])
    q=replace(q,user_id=f['user'],conversation_id=f['conv'],task_id=f['task']);p.content_controls_available=True
    await execute(r,p,h,q,[call(co.SAVE,{'document':document()},'first-document')])
    original=co.catalog(r.task_execution_state)[0]
    # The actual end-of-turn reducer closes saved work before the next user turn.
    before=r.task_execution_state
    closed=end_candidate(before)
    cp=copy.deepcopy(r.checkpoint_base);cp['_task_execution']=closed.model_dump(mode='json')
    cp['_task_execution_expected_revision']=before.revision
    with connect(f) as c:assert save(c,f,cp,expected=before.revision)['outcome']=='saved'
    old,new,source,obs,merged=new_turn(followup_db,old=f,text='继续原任务，修改第二条')
    assert co.catalog(TaskState.model_validate(source['state']))==[original]
    with connect(new) as c:
        reply=adopt(c,new,source,obs,merged)
        assert reply['outcome']=='saved'
        state=TaskState.model_validate(reply['payload']['_task_execution'])
        assert co.catalog(state)==[original]
        assert co.load(state,ref(original,2))[2].body==document()['items'][1]['body']
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(new['task'],)).fetchone()[0]==0
        assert str(c.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()[0])==new['task']
    async def next_checkpoint(point,payload):
        with connect(new) as c:return save(c,new,payload,expected=payload.get('_task_execution_expected_revision',0))
    r2,p2,h2,q2=case(next_checkpoint)
    r2.task_bootstrap=replace(r.task_bootstrap,task_id=UUID(new['task']),input_message_id=UUID(new['message']))
    r2.task_id=new['task'];r2.conversation_id=new['conv'];r2.turn_id=new['turn']
    r2.task_execution_state=state;r2.committed_task_execution_revision=state.revision
    r2.checkpoint_base=copy.deepcopy(reply['payload']);p2.messages=copy.deepcopy(reply['payload']['messages'])
    p2.execution_context=replace(p.execution_context,task_id=new['task']);p2.content_controls_available=True
    q2=replace(q,task_id=new['task'])
    await execute(r2,p2,h2,q2,[call(co.SAVE,{'base':ref(original,2),
        'updates':[{'item_index':2,'item':{'body':'修订正文\n修订后的完整限制'}}]},'second-revision')])
    updated=co.catalog(r2.task_execution_state)[0]
    assert updated['revision']==2
    assert co.load(r2.task_execution_state,ref(updated,2))[2].materials==co.load(state,ref(original,2))[2].materials
    from psycopg.types.json import Jsonb
    with connect(new) as c:
        raw=r2.task_execution_state.model_dump(mode='json')
        assert c.execute('SELECT agent_task_direct_records_valid(%s)',(Jsonb(raw),)).fetchone()[0]
        forged=copy.deepcopy(raw);forged['steps'][0]['capability']='platform.erp_execute'
        assert not c.execute('SELECT agent_task_direct_records_valid(%s)',(Jsonb(forged),)).fetchone()[0]
        forged=copy.deepcopy(raw);forged['artifacts'][0]['validations']=['artifact.exists']
        assert not c.execute('SELECT agent_task_direct_records_valid(%s)',(Jsonb(forged),)).fetchone()[0]
    h._execute_tool_calls.assert_not_awaited();h2._execute_tool_calls.assert_not_awaited()
