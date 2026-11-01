"""Real atomic operation/Actor-ledger bridge, before business adapter rollout."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.task_execution.contracts import TaskState
from services.task_execution.reducer import digest
from services.tool_invocation_store import hash_tool_arguments
from tests.test_task_execution_postgres import task_db, composition_db, ecom_db, isolated_db, seed, connect, save, task_payload

ROOT = Path(__file__).parents[1]
ARGUMENTS = {'prompt': '执行已规划画面', 'aspect_ratio': '3:4'}
ARGS_HASH = hash_tool_arguments(ARGUMENTS)


@pytest.fixture(scope='module')
def operation_db(task_db):
    with psycopg.connect(task_db) as c:
        c.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        c.execute('SET LOCAL ROLE everydayai')
        c.execute((ROOT/'migrations/139_tool_invocations.sql').read_text())
        c.execute('ALTER TABLE tool_invocations ENABLE ROW LEVEL SECURITY')
        c.execute('ALTER TABLE tool_invocations FORCE ROW LEVEL SECURITY')
        c.execute('''CREATE POLICY test_operation_ledger_scope ON tool_invocations USING (
            EXISTS (SELECT 1 FROM tasks t WHERE t.id=tool_invocations.task_id
              AND t.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
              AND t.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid))''')
        c.execute((ROOT/'migrations/341_agent_task_operations.sql').read_text())
    yield task_db
    with psycopg.connect(task_db) as c:
        c.execute('DELETE FROM conversation_turn_checkpoints WHERE state ? \'_task_execution\'')
        c.execute('DELETE FROM agent_task_states')
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute('SET LOCAL ROLE everydayai')
        c.execute((ROOT/'migrations/rollback/341_agent_task_operations_rollback.sql').read_text())
        c.execute('DROP TABLE tool_invocations CASCADE')


def initial(dsn, *, dependent=False, gate=False):
    f = seed(dsn)
    payload = task_payload(f)
    value = payload['_task_execution']
    value['requirements'][0]['artifact_kind'] = 'image'
    value['steps'] = [{'id': 'image', 'title': '生成图片', 'requirement_ids': ['report'],
        'depends_on': ['planning'] if dependent else [], 'capability': 'platform.generate_image'}]
    if dependent or gate:
        value['requirements'].append({**value['requirements'][0], 'id': 'planning',
            'artifact_kind': 'text', 'description': '完整提示词', 'validator': 'method.valid'})
        value['steps'].insert(0, {'id': 'planning', 'title': '规划', 'requirement_ids': ['planning']})
    if gate:
        value['capability_gates'] = [{'capability': 'platform.generate_image', 'requirement_ids': ['planning']}]
    payload['_task_execution'] = TaskState.model_validate(value).model_dump(mode='json')
    payload['opaque_existing_context'] = {'keep': True}
    with connect(f) as c:
        save(c, f, payload)
    return f, payload['_task_execution']


def begin(c, f, revision, *, call='native-1', token=None, effect='image-slot-1', version=1,
          args_hash=ARGS_HASH, step='image', capability='platform.generate_image', tool='generate_image'):
    return c.execute('SELECT begin_agent_task_operation(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (f['task'], token or f['token'], f['task'], revision, step, capability,
         effect, version, call, tool, args_hash)).fetchone()[0]


def complete(c, f, state, *, status='accepted', receipt=None, result=None, token=None):
    return c.execute('SELECT complete_agent_task_operation(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (f['task'], token or f['token'], f['task'], state['revision'], state['operations'][0]['id'],
         status, receipt, Jsonb(result or {'kind': 'scalar', 'value': 'accepted'}), '')).fetchone()[0]


def facts(f):
    with connect(f) as c:
        logical = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s', (f['task'],)).fetchone()[0]
        cp = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s', (f['task'],)).fetchone()[0]
        ledger = c.execute('SELECT tool_call_id,status,result FROM tool_invocations WHERE task_id=%s', (f['task'],)).fetchall()
    assert cp['_task_execution'] == logical
    assert cp['opaque_existing_context'] == {'keep': True}
    return logical, ledger


def test_reservation_and_original_invocation_share_one_transaction(operation_db):
    f, old = initial(operation_db)
    with connect(f) as c:
        result = begin(c, f, old['revision'])
    assert result['outcome'] == 'execute'
    current, ledger = facts(f)
    assert current == result['state'] and current['revision'] == old['revision'] + 1
    assert current['operations'][0]['id'] == digest([f['task'], 'image-slot-1', 1])
    assert current['operations'][0]['owner_turn_id'] == f['turn']
    assert ledger == [('native-1', 'running', None)]
    TaskState.model_validate(current)


def test_new_call_id_and_lost_ack_only_reconcile_original_running_call(operation_db):
    f, old = initial(operation_db)
    with connect(f) as c:
        first = begin(c, f, old['revision'])
    with connect(f) as c:
        retry = begin(c, f, first['state']['revision'], call='new-model-call')
    assert retry['outcome'] == 'reconcile' and retry['state'] == first['state']
    assert len(facts(f)[1]) == 1


def test_accepted_is_not_image_completion_and_replays_lossless_native_payload(operation_db):
    f, old = initial(operation_db)
    payload = {'kind': 'agent_result', 'status': 'success', 'metadata': {'accepted': True},
        'emit_payloads': [{'kind': 'file', 'file': {'name': '原回执'}}]}
    receipt = 'task:' + str(uuid4())
    with connect(f) as c:
        first = begin(c, f, old['revision'])
    with connect(f) as c:
        done = complete(c, f, first['state'], receipt=receipt, result=payload)
    with connect(f) as c:
        retry = begin(c, f, done['state']['revision'], call='another-call')
        same = complete(c, f, done['state'], receipt=receipt, result=payload)
    assert done['outcome'] == same['outcome'] == 'saved'
    assert retry['outcome'] == 'replay' and retry['result'] == payload
    current, ledger = facts(f)
    assert len(ledger) == 1 and ledger[0][1:] == ('succeeded', payload)
    assert current['status'] == 'active' and current['artifacts'] == [] and current['results'] == []
    assert current['operations'][0]['status'] == 'accepted'


@pytest.mark.parametrize('changed', [dict(args_hash='0'*64), dict(step='other'),
    dict(capability='platform.generate_video'), dict(tool='generate_video')])
def test_same_effect_cannot_change_arguments_or_native_binding(operation_db, changed):
    f, old = initial(operation_db)
    with connect(f) as c:
        first = begin(c, f, old['revision'])
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='TASK_EFFECT_ARGUMENTS_CHANGED'):
        with connect(f) as c:
            begin(c, f, first['state']['revision'], call='new', **changed)
    assert facts(f)[0] == first['state']


@pytest.mark.parametrize('mode,outcome', [('token', 'ownership_lost'), ('lease', 'lease_expired'),
    ('terminal', 'terminal'), ('stale', 'stale')])
def test_bad_fence_or_revision_never_reserves_native_io(operation_db, mode, outcome):
    f, old = initial(operation_db)
    if mode in {'lease', 'terminal'}:
        with psycopg.connect(f['dsn']) as c:
            c.execute('UPDATE tasks SET '+("lease_expires_at=now()-interval '1 second'" if mode=='lease'
                else "status='completed'")+' WHERE id=%s', (f['task'],))
    with connect(f) as c:
        result = begin(c, f, old['revision'] + (1 if mode=='stale' else 0),
            token=str(uuid4()) if mode=='token' else None)
    assert result['outcome'] == outcome
    assert facts(f) == (old, [])


@pytest.mark.parametrize('dependent,gate,code', [(True,False,'TASK_STEP_NOT_READY'),
    (False,True,'TASK_PREREQUISITE_INCOMPLETE')])
def test_host_dependency_and_method_gate_checked_before_native_reservation(operation_db, dependent, gate, code):
    f, old = initial(operation_db, dependent=dependent, gate=gate)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match=code):
        with connect(f) as c:
            begin(c, f, old['revision'])
    assert facts(f) == (old, [])


def test_existing_native_invocation_conflict_rolls_back_logical_reservation(operation_db):
    f, old = initial(operation_db)
    with connect(f) as c:
        c.execute('SELECT begin_tool_invocation(%s,%s,%s,%s,%s,%s,%s)',
            (f['task'], f['conv'], f['turn'], f['token'], 'native-1', 'generate_image', ARGS_HASH))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='TASK_OPERATION_INVOCATION_CONFLICT'):
        with connect(f) as c:
            begin(c, f, old['revision'])
    assert facts(f)[0] == old


def test_concurrent_reservations_grant_only_one_execute(operation_db):
    f, old = initial(operation_db)
    def reserve(call):
        with connect(f) as c:
            return begin(c, f, old['revision'], call=call)['outcome']
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, ['one', 'two']))
    assert sorted(outcomes) == ['execute', 'stale']
    assert len(facts(f)[1]) == 1


def test_uncertain_result_cannot_be_retried_or_rewritten_as_success(operation_db):
    f, old = initial(operation_db)
    with connect(f) as c:
        first = begin(c, f, old['revision'])
    with connect(f) as c:
        uncertain = complete(c, f, first['state'], status='uncertain')
    with connect(f) as c:
        retry = begin(c, f, uncertain['state']['revision'], call='new')
    assert retry['outcome'] == 'reconcile'
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='TASK_EFFECT_TRANSITION_DENIED'):
        with connect(f) as c:
            complete(c, f, uncertain['state'], status='completed', receipt='pretend:success')
    assert facts(f)[0] == uncertain['state']


def test_completion_ledger_failure_rolls_back_task_and_checkpoint(operation_db):
    f, old = initial(operation_db)
    with connect(f) as c:
        first = begin(c, f, old['revision'])
    with psycopg.connect(f['dsn']) as c:
        c.execute('''CREATE FUNCTION test_fail_native_completion() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected ledger failure' USING ERRCODE='23514'; END $$''')
        c.execute('''CREATE TRIGGER test_fail_native_completion BEFORE UPDATE ON tool_invocations
            FOR EACH ROW EXECUTE FUNCTION test_fail_native_completion()''')
    try:
        with pytest.raises(psycopg.errors.CheckViolation, match='injected ledger failure'):
            with connect(f) as c:
                complete(c, f, first['state'], receipt='task:' + str(uuid4()))
        assert facts(f) == (first['state'], [('native-1', 'running', None)])
    finally:
        with psycopg.connect(f['dsn']) as c:
            c.execute('DROP TRIGGER test_fail_native_completion ON tool_invocations')
            c.execute('DROP FUNCTION test_fail_native_completion()')


def test_other_actor_cannot_read_or_reserve_an_effect(operation_db):
    f, old = initial(operation_db)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='TASK_OPERATION_SCOPE_DENIED'):
        with connect(f) as c:
            c.execute("SELECT set_config('app.actor_user_id',%s,true)", (str(uuid4()),))
            begin(c, f, old['revision'])
    assert facts(f) == (old, [])


def test_missing_actor_marker_fails_closed_at_reservation_and_completion(operation_db):
    f, old = initial(operation_db)
    with psycopg.connect(f['dsn']) as c:
        c.execute('UPDATE tasks SET delivery_context=NULL WHERE id=%s', (f['task'],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='TASK_OPERATION_SCOPE_DENIED'):
        with connect(f) as c:
            begin(c, f, old['revision'])
    assert facts(f) == (old, [])
    with psycopg.connect(f['dsn']) as c:
        c.execute('UPDATE tasks SET delivery_context=\'{"actor":true}\' WHERE id=%s', (f['task'],))
    with connect(f) as c:
        first = begin(c, f, old['revision'])
    with psycopg.connect(f['dsn']) as c:
        c.execute('UPDATE tasks SET delivery_context=NULL WHERE id=%s', (f['task'],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='TASK_OPERATION_SCOPE_DENIED'):
        with connect(f) as c:
            complete(c, f, first['state'], receipt='task:' + str(uuid4()))
    assert facts(f) == (first['state'], [('native-1', 'running', None)])


def test_rollback_checks_other_tenants_and_preserves_force_rls_after_refusal(operation_db):
    f, old = initial(operation_db)
    with connect(f) as c:
        begin(c, f, old['revision'])
    with pytest.raises(psycopg.errors.RaiseException, match='TASK_OPERATION_ROLLBACK_HAS_STATE'):
        with psycopg.connect(f['dsn']) as c:
            c.execute('SET LOCAL ROLE everydayai')
            c.execute((ROOT/'migrations/rollback/341_agent_task_operations_rollback.sql').read_text())
    with psycopg.connect(f['dsn']) as c:
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='agent_task_states'::regclass").fetchone()[0]
        assert c.execute("SELECT to_regprocedure('begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text)') IS NOT NULL").fetchone()[0]


def typed_store(f):
    from psycopg import sql
    from services.task_execution.operation_store import TaskOperationStore
    from services.tools.catalog import build_capability_catalog
    class DB:
        def rpc(self, name, parameters):
            async def execute():
                query = sql.SQL('SELECT {}({})').format(sql.Identifier(name), sql.SQL(',').join(
                    sql.SQL('{} => %s').format(sql.Identifier(key)) for key in parameters))
                with connect(f) as c:
                    result = c.execute(query, tuple(parameters.values())).fetchone()[0]
                return SimpleNamespace(data=result)
            return SimpleNamespace(execute=execute)
    return TaskOperationStore(DB(), task_id=f['task'], execution_token=f['token'], turn_id=f['turn'],
        registry=build_capability_catalog())


@pytest.mark.asyncio
async def test_typed_store_and_full_native_result_round_trip_through_actual_rpc(operation_db):
    from services.tools import ToolCall
    from services.tools.result_payload import encode_result
    from tests.test_task_execution_operation_store import native_result
    f, old = initial(operation_db)
    store = typed_store(f)
    call = ToolCall('native-1', 'generate_image', ARGUMENTS)
    first = await store.begin(TaskState.model_validate(old), step_id='image', effect_key='image-slot-1',
        effect_version=1, call=call)
    assert first.outcome == 'execute'
    result = native_result(first.state, call)
    done = await store.complete_invocation(first.state, operation_id=first.operation.id, result=result,
        status='accepted', receipt_ref='task:' + str(uuid4()))
    replay = await store.begin(done.state, step_id='image', effect_key='image-slot-1', effect_version=1,
        call=ToolCall('new-call', 'generate_image', ARGUMENTS))
    assert replay.outcome == 'replay' and replay.result == encode_result(result)
    assert replay.state == done.state


@pytest.mark.asyncio
async def test_proven_not_accepted_can_correct_arguments_without_new_effect_or_lost_history(operation_db):
    from services.tools import ToolCall
    from services.task_execution.contracts import TaskError
    from tests.test_task_execution_operation_store import native_result
    f, old = initial(operation_db)
    store = typed_store(f)
    call = ToolCall('first', 'generate_image', ARGUMENTS)
    first = await store.begin(TaskState.model_validate(old), step_id='image', effect_key='image-slot-1',
        effect_version=1, call=call)
    rejected = await store.complete_invocation(first.state, operation_id=first.operation.id,
        result=native_result(first.state, call, error=True), status='failed_before_dispatch')
    with pytest.raises(TaskError, match='TASK_OPERATION_NEW_ATTEMPT_ID_REQUIRED'):
        await store.begin(rejected.state, step_id='image', effect_key='image-slot-1', effect_version=1, call=call)
    corrected_call = ToolCall('corrected', 'generate_image', {**ARGUMENTS, 'output_format': 'png'})
    corrected = await store.begin(rejected.state, step_id='image', effect_key='image-slot-1',
        effect_version=1, call=corrected_call)
    assert corrected.outcome == 'execute' and corrected.operation.id == first.operation.id
    assert corrected.operation.previous_attempts[0].invocation_id == 'first'
    assert corrected.operation.previous_attempts[0].arguments_sha256 == first.operation.arguments_sha256
    assert corrected.operation.arguments_sha256 != first.operation.arguments_sha256
    done = await store.complete_invocation(corrected.state, operation_id=corrected.operation.id,
        result=native_result(corrected.state, corrected_call), status='accepted', receipt_ref='task:' + str(uuid4()))
    current, ledger = facts(f)
    assert len(current['operations']) == 1 and len(ledger) == 2
    assert current == done.state.model_dump(mode='json')


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing_marker', 'accepted', 'child_id', 'exception'])
async def test_unsafe_rejection_cannot_unlock_correction(operation_db, damage):
    from dataclasses import replace
    from services.tools import ToolCall
    from services.task_execution.contracts import TaskError
    from services.task_execution.operation_store import image_rejected_before_acceptance
    from tests.test_task_execution_operation_store import native_result
    f, old = initial(operation_db)
    store = typed_store(f)
    call = ToolCall('first', 'generate_image', ARGUMENTS)
    first = await store.begin(TaskState.model_validate(old), step_id='image', effect_key='image-slot-1', effect_version=1, call=call)
    result = native_result(first.state, call, error=True)
    if damage == 'exception':
        result = replace(result, exception=TimeoutError('unknown acceptance'))
    else:
        metadata = result.raw.metadata
        if damage == 'missing_marker': metadata.pop('submission_state')
        elif damage == 'accepted': metadata['accepted'] = True
        else: metadata['task_id'] = str(uuid4())
    assert not image_rejected_before_acceptance(result)
    with pytest.raises(TaskError, match='TASK_OPERATION_REJECTION_UNVERIFIED'):
        await store.complete_invocation(first.state, operation_id=first.operation.id,
            result=result, status='failed_before_dispatch')
    assert facts(f)[0] == first.state.model_dump(mode='json')


@pytest.mark.asyncio
async def test_real_actor_async_pool_carries_task_identity_without_session_leak(operation_db):
    from core.local_db import AsyncLocalDBClient
    from core.db_scope import AsyncScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.task_execution.store import TaskCheckpointStore
    from services.task_execution.operation_store import TaskOperationStore
    from services.task_execution.reducer import consume_model_round
    from services.task_execution.contracts import TaskError
    from services.tools import ToolCall
    from services.tools.catalog import build_capability_catalog
    f,old=initial(operation_db)
    client=AsyncLocalDBClient(psycopg.conninfo.make_conninfo(operation_db,user='everydayai'),min_size=1,max_size=1)
    await client.open()
    try:
        state=consume_model_round(TaskState.model_validate(old))
        result=await TaskCheckpointStore(client).write(task_id=f['task'],execution_token=f['token'],
            boundary='before_model',payload={'messages':[],'opaque_existing_context':{'keep':True}},
            state=state,expected_revision=old['revision'])
        assert result['outcome']=='saved'
        store=TaskOperationStore(client,task_id=f['task'],execution_token=f['token'],turn_id=f['turn'],registry=build_capability_catalog())
        begun=await store.begin(state,step_id='image',effect_key='delivery-1',effect_version=1,
            call=ToolCall('raw-pool','generate_image',ARGUMENTS))
        assert begun.outcome=='execute'
        async with client._pool.connection() as connection:
            cursor=await connection.execute("SELECT current_setting('app.actor_user_id',true) actor,current_setting('app.access_kind',true) access")
            identity=await cursor.fetchone()
            assert not identity['actor'] and not identity['access']
        wrong=AsyncScopedDatabaseClient(client,DatabaseScope(str(uuid4()),None,DatabaseAccessKind.RUNTIME))
        with pytest.raises(TaskError,match='TASK_DATABASE_SCOPE_MISMATCH'):
            await TaskCheckpointStore(wrong).write(task_id=f['task'],execution_token=f['token'],boundary='before_model',
                payload={'messages':[]},state=state,expected_revision=state.revision)
    finally:
        await client.close()
