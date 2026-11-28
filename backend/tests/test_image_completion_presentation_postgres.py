"""Actual fenced outbox and continuation migration, in an owned local DB only."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4, UUID

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_task_execution_continuation_postgres import (
    continuation_db, children_db, operation_db, task_db, composition_db, ecom_db, isolated_db,
    finished, enqueue, claim_internal, connect)
from tests.test_task_execution_legacy_actor_postgres import install_legacy_actor_schema
from services.task_execution.image_continuation import continuation_payload
from services.task_execution.contracts import TaskState
from services.task_execution.bootstrap import TaskBootstrap
from services.task_execution.model_directed import record_calls
from services.task_execution.request_window import input_revision
from services.tools import ToolCall
from tests.test_model_directed_records import Registry
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def presentation_db(continuation_db):
    install_legacy_actor_schema(continuation_db)
    def definitions(c):
        return c.execute("SELECT proname,prosrc,proowner,proacl,prosecdef,proconfig FROM pg_proc WHERE oid IN ('enqueue_agent_task_continuation(uuid,bigint,uuid,text)'::regprocedure,'append_conversation_delivery_event(uuid,uuid,text,jsonb,uuid)'::regprocedure) ORDER BY proname").fetchall()
    with psycopg.connect(continuation_db) as c:
        c.execute('GRANT EXECUTE ON FUNCTION begin_conversation_delivery_session(uuid,uuid,integer,uuid),append_conversation_delivery_event(uuid,uuid,text,jsonb,uuid) TO everydayai')
        original=definitions(c)
        role=c.execute("SELECT current_setting('role')").fetchone()[0]
        c.execute((ROOT/'migrations/389_internal_continuation_presentation.sql').read_text())
        assert c.execute("SELECT current_setting('role')").fetchone()[0]==role
        assert [r[2:] for r in definitions(c)]==[r[2:] for r in original]
    yield continuation_db
    with psycopg.connect(continuation_db) as c:
        c.execute((ROOT/'migrations/rollback/389_internal_continuation_presentation_rollback.sql').read_text())
        assert definitions(c)==original
        c.execute((ROOT/'migrations/389_internal_continuation_presentation.sql').read_text())
        c.execute((ROOT/'migrations/rollback/389_internal_continuation_presentation_rollback.sql').read_text())


@pytest.mark.asyncio
async def test_visibility_tracks_durable_fenced_start_and_is_retry_idempotent(presentation_db, monkeypatch):
    f, state, _, event = await finished(presentation_db, monkeypatch)
    task, claim = claim_internal(f, enqueue(f, state, event)['task_id'])
    def marker(c):
        return c.execute('SELECT generation_params FROM messages WHERE id=%s', (task['assistant_message_id'],)).fetchone()[0]['delivery_presentation']
    with connect(f) as c:
        assert marker(c) == {'origin':'internal_continuation', 'output_started':False}
        c.execute('SELECT begin_conversation_delivery_session(%s,%s,%s,%s)',
            (claim.task_id, claim.execution_token, task['execution_attempt'], task['assistant_message_id']))
        event_id=uuid4()
        def start(token):
            return c.execute('SELECT append_conversation_delivery_event(%s,%s,%s,%s,%s)',
                (claim.task_id,token,'message_start',Jsonb({'model':task['model_id']}),event_id)).fetchone()[0]
        assert start(uuid4())['outcome']=='ownership_lost'
        assert marker(c)['output_started'] is False
        first=start(claim.execution_token)
        assert marker(c)['output_started'] is True
        assert start(claim.execution_token)==first
        assert c.execute('SELECT count(*) FROM conversation_delivery_events WHERE task_id=%s', (claim.task_id,)).fetchone()[0]==1
        assert c.execute('SELECT status FROM tasks WHERE id=%s', (claim.task_id,)).fetchone()[0]=='running'


@pytest.mark.asyncio
@pytest.mark.parametrize('scope,expected', [('terminal','completed'), ('continue','active'), ('unknown','active'), (None,'active')])
async def test_model_directed_completion_uses_current_scope_in_native_transaction(presentation_db, monkeypatch, scope, expected):
    f, old, child, event = await finished(presentation_db, monkeypatch)
    # Seed the real model-directed protocol using the same accepted native child.
    # This conversion is fixture setup only, not a production compatibility path.
    b=TaskBootstrap(task_id=UUID(f['task']),conversation_id=old.conversation_id,actor_user_id=old.actor_user_id,
        input_message_id=UUID(f['message']),original_goal='第三个做成一张图，正方形，一千就行。',
        execution_identity=old.execution_identity,model_directed=True)
    direct=record_calls(None,b,[ToolCall('native-1','generate_image',{'prompt':'保留蓝白灯塔'})],Registry())
    op=old.operations[-1].model_copy(update={'step_id':direct.steps[0].id})
    direct=TaskState.model_validate({**direct.model_dump(), 'revision':old.revision, 'status':'waiting_dependencies', 'operations':[op]})
    with connect(f) as c:
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        cp['_task_execution']=direct.model_dump(mode='json')
        cp['_request_context_v1']={'context_revision':1}
        rr={'status':'resolved','input_message_id':f['message'],'execution_task_id':f['task'],
            'target':{'kind':'content','item_indices':[3]},'action':{'intent':'execute_saved','image_count':1,'request_context':{}}}
        if scope:rr['action']['request_context']['image_completion_scope']={'after_images':scope,'evidence':['第三个做成一张图']}
        cp['_request_resolution_v1']=rr
        rr['input_revision']=input_revision(SimpleNamespace(task_id=f['task'],checkpoint_base=cp),
            SimpleNamespace(context_anchor=SimpleNamespace(input_message_id=f['message'],base_revision=1)))
        c.execute('UPDATE agent_task_states SET state=%s WHERE root_task_id=%s',(Jsonb(direct.model_dump(mode='json')),f['task']))
        c.execute('UPDATE conversation_turn_checkpoints SET state=%s WHERE task_id=%s',(Jsonb(cp),f['task']))
        c.execute("UPDATE tasks SET delivery_context=delivery_context||'{\"channel\":\"web\"}'::jsonb WHERE id=%s",(f['task'],))
    task,claim=claim_internal(f,enqueue(f,direct,event)['task_id'])
    with connect(f) as c:
        bundle=c.execute('SELECT load_agent_image_continuation(%s,%s)',(claim.task_id,claim.execution_token)).fetchone()[0]
        identity=SimpleNamespace(actor_user_id=f['user'],**direct.execution_identity.model_dump())
        payload,candidate=continuation_payload(bundle,task=task,claim=claim,execution_scope=identity)
        assert candidate.status==expected
        assert payload['messages'][:-1]==bundle['checkpoint']['messages']
        applied=c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',
            (claim.task_id,claim.execution_token,direct.revision,Jsonb(payload))).fetchone()[0]
        assert applied['outcome']=='saved'
        assert c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]['status']==expected
