"""Real PostgreSQL: task facts and Actor checkpoint are one fenced transaction."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_skill_composition_postgres import composition_db, ecom_db, isolated_db, seed, connect
from tests.test_task_execution_reducer import state, plan

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module', params=['everydayai', 'everydayai_owner'])
def task_db(composition_db, request):
    with psycopg.connect(composition_db) as c:
        c.execute('GRANT REFERENCES ON tasks,conversations TO everydayai')
        c.execute(f'ALTER TABLE conversation_turn_checkpoints OWNER TO {request.param}')
        c.execute('GRANT SELECT,INSERT,UPDATE ON conversation_turn_checkpoints TO everydayai')
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/340_agent_task_state.sql').read_text())
    yield composition_db
    # Each controlled-owner variant gets the same complete test set.
    with psycopg.connect(composition_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_states')
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute((ROOT/'migrations/rollback/340_agent_task_state_rollback.sql').read_text())


def task_payload(f):
    from services.task_execution.contracts import TaskState
    s = state()
    raw = {**s.model_dump(mode='json'), 'root_task_id': f['task'], 'actor_user_id': f['user'],
           'conversation_id': f['conv'], 'source_message_ids': [f['message']]}
    raw['requirements'][0]['source_message_id'] = f['message']
    raw['execution_identity']['workspace_owner_id'] = f['user']
    return {'messages': [], '_task_execution': plan(TaskState.model_validate(raw)).model_dump(mode='json')}


def save(c, f, payload, expected=0, token=None):
    return c.execute('SELECT save_agent_task_checkpoint(%s,%s,%s,%s,%s)',
        (f['task'], token or f['token'], 'before_model', Jsonb(payload), expected)).fetchone()[0]


def test_state_and_actor_checkpoint_are_committed_and_replayed_together(task_db):
    f = seed(task_db)
    p = task_payload(f)
    with connect(f) as c:
        assert save(c, f, p)['outcome'] == 'saved'
        assert save(c, f, p)['outcome'] == 'saved'
        row = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s', (f['task'],)).fetchone()
        checkpoint = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s', (f['task'],)).fetchone()
        assert row[0] == checkpoint[0]['_task_execution'] == p['_task_execution']


@pytest.mark.asyncio
async def test_actual_chat_control_batch_commits_text_and_tool_pairs_to_postgres(task_db):
    import json
    from services.task_execution.contracts import TaskState
    from tests.test_task_execution_model_bridge import setup_case, execute, call
    f = seed(task_db)
    initial = TaskState.model_validate(task_payload(f)['_task_execution'])

    async def checkpoint(point, payload):
        # Exiting this actual transaction includes the deferred guards before
        # returning saved, exactly as the production ScopedRpcCaller does.
        with connect(f) as c:
            return save(c, f, payload, expected=payload['_task_execution_expected_revision'])

    runtime, prepared, handler, sink = setup_case(checkpoint, initial)
    blocks = []
    await execute(runtime, prepared, handler, sink, [call('save_task_text', {
        'expected_revision': initial.revision, 'step_id': 'research',
        'text': '真实保存的研究成果'}, 'saved-text')], blocks)
    with connect(f) as c:
        logical = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',
            (f['task'],)).fetchone()[0]
        saved = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
            (f['task'],)).fetchone()[0]
    assert logical == saved['_task_execution'] == runtime.task_execution_state.model_dump(mode='json')
    assert logical['artifacts'][0]['inline_text'] == '真实保存的研究成果'
    assert saved['messages'][-2]['tool_calls'][0]['id'] == saved['messages'][-1]['tool_call_id']
    assert json.loads(saved['messages'][-1]['content'])['ok']
    assert saved['content_blocks'][-1]['task_artifact_id'] == logical['artifacts'][0]['id']
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_phase_handoff_and_skill_composition_commit_together(task_db, monkeypatch):
    from services.task_execution.contracts import TaskState
    from tests.test_task_execution_methods import bound, document
    from tests.test_task_execution_model_bridge import setup_case, execute, call
    f = seed(task_db)
    initial, skills = await bound(monkeypatch)
    raw = initial.model_dump(mode='json')
    raw.update(root_task_id=f['task'], actor_user_id=f['user'], conversation_id=f['conv'],
               source_message_ids=[f['message']])
    raw['execution_identity']['workspace_owner_id'] = f['user']
    for requirement in raw['requirements']:
        requirement['source_message_id'] = f['message']
    initial = TaskState.model_validate(raw)
    skills.turn_id = f['turn']
    skills.composition = skills._compose()

    async def checkpoint(point, payload):
        with connect(f) as c:
            return save(c, f, payload, expected=payload['_task_execution_expected_revision'])

    runtime, prepared, handler, sink = setup_case(checkpoint, initial)
    runtime.skill_runtime = skills
    runtime.turn_id = f['turn']
    blocks = []
    await execute(runtime, prepared, handler, sink, [call('save_task_text', {
        'expected_revision': initial.revision, 'step_id': 'planning', 'text': document()}, 'plan')], blocks)
    current = runtime.task_execution_state
    await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
        'expected_revision': current.revision, 'step_id': 'planning',
        'artifact_ids': [current.artifacts[0].id]}, 'complete')], blocks)
    with connect(f) as c:
        logical = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',
            (f['task'],)).fetchone()[0]
        saved = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
            (f['task'],)).fetchone()[0]
        composition = c.execute("SELECT request_params->'_skill_composition' FROM tasks WHERE id=%s",
            (f['task'],)).fetchone()[0]
    assert logical == saved['_task_execution']
    assert saved['skill_runtime']['manual_skill_id'] == 'report'
    assert saved['skill_runtime']['active'] == []
    assert set(saved['skill_runtime']['effective_allowed_tool_names']) == {'generate_image', 'file_search'}
    assert composition == saved['skill_runtime']['composition'] == saved['_skill_composition']
    assert logical['results'][0]['step_id'] == 'planning'


def bind_seed(state, f):
    from services.task_execution.contracts import TaskState
    raw = state.model_dump(mode='json')
    raw.update(root_task_id=f['task'], actor_user_id=f['user'], conversation_id=f['conv'],
               source_message_ids=[f['message']])
    raw['execution_identity']['workspace_owner_id'] = f['user']
    for requirement in raw['requirements']:
        requirement['source_message_id'] = f['message']
    return TaskState.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('text_only', [False, True])
async def test_dynamic_method_activation_appends_obligations_in_actual_transaction(task_db, text_only):
    from services.conversation_commands import SafePoint
    from tests.test_task_execution_dynamic_methods import dynamic_setup, activate_method
    f = seed(task_db)
    async def checkpoint(point, payload):
        with connect(f) as c:
            return save(c, f, payload, expected=payload['_task_execution_expected_revision'])
    runtime, prepared, handler, sink = await dynamic_setup(checkpoint, text_only=text_only)
    runtime.task_execution_state = bind_seed(runtime.task_execution_state, f)
    runtime.task_id, runtime.conversation_id, runtime.turn_id = f['task'], f['conv'], f['turn']
    runtime.skill_runtime.turn_id = f['turn']
    runtime.skill_runtime.composition = runtime.skill_runtime._compose()
    await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': [], 'content_blocks': []})
    before = runtime.task_execution_state
    await activate_method(runtime, prepared, handler, sink, [])
    with connect(f) as c:
        saved = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',
            (f['task'],)).fetchone()[0]
    assert saved['requirements'][:len(before.requirements)] == [r.model_dump(mode='json') for r in before.requirements]
    assert saved['method_bindings'][0]['contract_source'] == 'declared'
    assert saved['method_bindings'][0]['image_count'] == 5
    assert len(saved['capability_gates']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['user_requirement', 'goal', 'gate', 'pin', 'evidence', 'unrelated_requirement', 'fake_reviewed', 'swapped_profile'])
async def test_method_append_cannot_change_existing_facts_or_omit_prerequisites(task_db, corruption):
    from services.task_execution.methods import append_active_methods
    from tests.test_task_execution_dynamic_methods import dynamic_setup
    from tests.test_skill_runtime import activate
    f = seed(task_db)
    runtime, prepared, handler, sink = await dynamic_setup()
    before = bind_seed(runtime.task_execution_state, f)
    with connect(f) as c:
        save(c, f, {'messages': [], '_task_execution': before.model_dump(mode='json')})
    skills = runtime.skill_runtime
    skills.turn_id = f['turn']
    assert (await skills.activate(activate('new-method')))['ok']
    after = append_active_methods(before, skills)
    raw = after.model_dump(mode='json')
    if corruption == 'user_requirement':
        raw['requirements'][0]['description'] = '偷偷改掉原要求'
    elif corruption == 'goal':
        raw['original_goal'] = '替换目标'
    elif corruption == 'gate':
        raw['capability_gates'].pop()
    elif corruption == 'pin':
        raw['method_bindings'][0]['body_sha256'] = '0' * 64
    elif corruption == 'evidence':
        raw['correction_count'] += 1
    elif corruption == 'unrelated_requirement':
        raw['requirements'].append({**raw['requirements'][-1], 'id': 'unrelated'})
    elif corruption == 'fake_reviewed':
        raw['method_bindings'][0]['contract_source'] = 'reviewed'
    checkpoint = skills.checkpoint()
    if corruption == 'fake_reviewed':
        checkpoint['directory'][0]['catalog_metadata'].pop('task_stage')
    if corruption == 'swapped_profile':
        key, revision, checksum, package = ('ecommerce-image-prompts', 'v5d3a01973e41404981324860769e3750',
            '7f8e8de78b18105a2456a327e8bb9ee38edee9ed5d1ddae48a2e0a2c634df69e',
            '4c7d133e-33e5-455a-9216-4b6cff0ce892')
        rid = f'method.{key}.planning'
        raw['method_bindings'][0].update(skill_key=key, revision=revision, body_sha256=checksum,
            package_id=package, requirement_ids=[rid], contract_source='reviewed')
        raw['requirements'][-1]['id'] = rid
        for gate in raw['capability_gates']:
            gate['requirement_ids'] = [rid]
        raw['applicable_method_ids'] = [key]
        checkpoint['active'][0].update(skill_key=key, revision=revision, body_sha256=checksum)
        checkpoint['directory'][0].update(skill_key=key, revision=revision, package_id=package)
        checkpoint['directory'][0]['catalog_metadata'].pop('task_stage')
    payload = {'messages': [], '_task_execution': raw, 'skill_runtime': checkpoint,
        '_skill_composition': checkpoint['composition'], '_skill_composition_expected_revision': 0}
    with connect(f) as c, pytest.raises(psycopg.errors.InsufficientPrivilege, match='REQUIREMENTS_IMMUTABLE'):
        save(c, f, payload, expected=before.revision)
    with connect(f) as c:
        saved = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',
            (f['task'],)).fetchone()[0]
    assert saved == before.model_dump(mode='json')


async def reuse_fixture(task_db, monkeypatch):
    import json
    from types import SimpleNamespace
    from tests.test_task_execution_methods import bound, document
    from tests.test_task_execution_model_bridge import setup_case, execute, call
    from services.conversation_commands import SafePoint
    from services.task_execution.artifact_reader import TaskArtifactReader
    target, source = seed(task_db), seed(task_db)
    with psycopg.connect(task_db) as c:
        c.execute('UPDATE tasks SET user_id=%s,conversation_id=%s WHERE id=%s',
            (target['user'], target['conv'], source['task']))
        c.execute('UPDATE messages SET conversation_id=%s WHERE id=%s', (target['conv'], source['message']))
        c.execute('UPDATE tasks SET base_context_revision=5 WHERE id=%s', (target['task'],))
    source.update(user=target['user'], conv=target['conv'])

    async def runtime_for(f):
        initial, skills = await bound(monkeypatch)
        initial = bind_seed(initial, f)
        async def checkpoint(point, payload):
            with connect(f) as c:
                return save(c, f, payload, expected=payload['_task_execution_expected_revision'])
        runtime, prepared, handler, sink = setup_case(checkpoint, initial)
        runtime.turn_id = skills.turn_id = f['turn']
        skills.composition = skills._compose()
        runtime.skill_runtime = skills
        return runtime, prepared, handler, sink

    old, p, h, sink = await runtime_for(source)
    await execute(old, p, h, sink, [call('save_task_text', {
        'expected_revision': old.task_execution_state.revision, 'step_id': 'planning', 'text': document()}, 'plan')], [])
    artifact = old.task_execution_state.artifacts[0]
    await execute(old, p, h, sink, [call('complete_task_step', {
        'expected_revision': old.task_execution_state.revision, 'step_id': 'planning',
        'artifact_ids': [artifact.id]}, 'complete')], [])
    message_id = str(uuid4())
    with psycopg.connect(task_db) as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,status,content,context_revision,message_kind) VALUES(%s,%s,'assistant','completed','[]',3,'conversation')",
            (message_id, source['conv']))
        c.execute("UPDATE tasks SET status='completed',assistant_message_id=%s WHERE id=%s", (message_id, source['task']))
    runtime, prepared, handler, sink = await runtime_for(target)
    await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': [], 'content_blocks': []})

    class DB:
        def rpc(self, name, parameters):
            assert name == 'read_agent_task_text_artifacts'
            async def execute_read():
                keys = ('p_task_id','p_execution_token','p_logical_task_id','p_expected_revision',
                        'p_source_logical_task_id','p_artifact_id','p_limit')
                with connect(target) as c:
                    result = c.execute('SELECT read_agent_task_text_artifacts(%s,%s,%s,%s,%s,%s,%s)',
                        tuple(parameters[k] for k in keys)).fetchone()[0]
                return SimpleNamespace(data=result)
            return SimpleNamespace(execute=execute_read)
    runtime.task_artifact_reader = TaskArtifactReader(DB(), task_id=target['task'], execution_token=target['token'])
    return target, source, runtime, prepared, handler, sink, artifact, message_id


@pytest.mark.asyncio
async def test_closed_saved_planning_reused_without_reanalysis_in_actual_database(task_db, monkeypatch):
    from tests.test_task_execution_model_bridge import execute, call
    f, source, runtime, prepared, handler, sink, artifact, message = await reuse_fixture(task_db, monkeypatch)
    reader = runtime.task_artifact_reader
    found = await reader.list_text(runtime.task_execution_state, limit=10)
    assert any(item['artifact_id'] == artifact.id for item in found)
    blocks = []
    await execute(runtime, prepared, handler, sink, [call('reuse_task_text', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'source_logical_task_id': source['task'], 'source_artifact_id': artifact.id}, 'reuse')], blocks)
    reused = runtime.task_execution_state.artifacts[0]
    assert reused.inline_text == artifact.inline_text and reused.source_task_id == artifact.source_task_id
    await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'artifact_ids': [reused.id]}, 'complete')], blocks)
    assert 'generate_image' in runtime.skill_runtime.effective_allowed_tool_names
    assert not any(b.get('type') == 'text' for b in blocks)


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['future', 'different_conversation', 'unclosed', 'wrong_token'])
async def test_saved_artifact_reader_enforces_frozen_scope_and_claim(task_db, monkeypatch, boundary):
    from services.conversation_state import ConversationStopRequested
    f, source, runtime, prepared, handler, sink, artifact, message = await reuse_fixture(task_db, monkeypatch)
    with psycopg.connect(task_db) as c:
        if boundary == 'future':
            c.execute('UPDATE tasks SET base_context_revision=2 WHERE id=%s', (f['task'],))
        elif boundary == 'different_conversation':
            c.execute('UPDATE messages SET conversation_id=%s WHERE id=%s', (str(uuid4()), message))
        elif boundary == 'unclosed':
            c.execute("UPDATE messages SET status='streaming' WHERE id=%s", (message,))
    if boundary == 'wrong_token':
        runtime.task_artifact_reader.execution_token = str(uuid4())
        with pytest.raises(ConversationStopRequested):
            await runtime.task_artifact_reader.list_text(runtime.task_execution_state, limit=10)
    else:
        assert await runtime.task_artifact_reader.list_text(runtime.task_execution_state, limit=10) == []


@pytest.mark.asyncio
async def test_reused_artifact_cannot_expose_future_or_running_carrier_task(task_db, monkeypatch):
    from tests.test_task_execution_methods import bound
    from tests.test_task_execution_model_bridge import execute, call
    from services.task_execution.artifact_reader import TaskArtifactReader
    f, source, runtime, prepared, handler, sink, artifact, message = await reuse_fixture(task_db, monkeypatch)
    await execute(runtime, prepared, handler, sink, [call('reuse_task_text', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'source_logical_task_id': source['task'], 'source_artifact_id': artifact.id}, 'reuse')], [])
    reused = runtime.task_execution_state.artifacts[0]
    await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'artifact_ids': [reused.id]}, 'complete')], [])
    assert str(reused.source_task_id) == source['task'] and str(reused.delivery_task_id) == f['task']
    frozen = seed(task_db)
    with psycopg.connect(task_db) as c:
        c.execute('UPDATE tasks SET user_id=%s,conversation_id=%s,base_context_revision=5 WHERE id=%s',
            (f['user'], f['conv'], frozen['task']))
        c.execute('UPDATE messages SET conversation_id=%s WHERE id=%s', (f['conv'], frozen['message']))
    frozen.update(user=f['user'], conv=f['conv'])
    initial, _ = await bound(monkeypatch)
    state = bind_seed(initial, frozen)
    with connect(frozen) as c:
        save(c, frozen, {'messages': [], '_task_execution': state.model_dump(mode='json')})
    reader = TaskArtifactReader(runtime.task_artifact_reader.db, task_id=frozen['task'], execution_token=frozen['token'])
    found = await reader.list_text(state, limit=20)
    assert source['task'] in {row['source_logical_task_id'] for row in found}
    assert f['task'] not in {row['source_logical_task_id'] for row in found}  # still running
    with psycopg.connect(task_db) as c:
        later_message = str(uuid4())
        c.execute("INSERT INTO messages(id,conversation_id,role,status,content,context_revision,message_kind) VALUES(%s,%s,'assistant','completed','[]',8,'conversation')",
            (later_message, f['conv']))
        c.execute("UPDATE tasks SET status='completed',assistant_message_id=%s WHERE id=%s", (later_message, f['task']))
    found = await reader.list_text(state, limit=20)
    assert f['task'] not in {row['source_logical_task_id'] for row in found}  # closed after frozen boundary
    with psycopg.connect(task_db) as c:
        c.execute('UPDATE tasks SET base_context_revision=8 WHERE id=%s', (frozen['task'],))
    found = await reader.list_text(state, limit=20)
    assert f['task'] in {row['source_logical_task_id'] for row in found}


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['delivery', 'completion'])
async def test_artifact_delivery_and_step_completion_cannot_be_backdated(task_db, monkeypatch, corruption):
    from tests.test_task_execution_model_bridge import execute, call
    from services.task_execution.reducer import complete_step
    f, source, runtime, prepared, handler, sink, artifact, message = await reuse_fixture(task_db, monkeypatch)
    await execute(runtime, prepared, handler, sink, [call('reuse_task_text', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'source_logical_task_id': source['task'], 'source_artifact_id': artifact.id}, 'reuse')], [])
    before = runtime.task_execution_state
    if corruption == 'delivery':
        raw = before.model_dump(mode='json')
        raw['revision'] += 1
        raw['artifacts'][0]['delivery_task_id'] = source['task']
    else:
        raw = complete_step(before, 'planning', [before.artifacts[0].id], owner_task_id=source['task']).model_dump(mode='json')
    with connect(f) as c, pytest.raises(psycopg.errors.InsufficientPrivilege, match='DELIVERY_IMMUTABLE'):
        save(c, f, {'messages': [], '_task_execution': raw}, expected=before.revision)


def test_concurrent_writers_only_one_revision_wins(task_db):
    f = seed(task_db)
    p = task_payload(f)
    with connect(f) as c:
        save(c, f, p)
    current = p['_task_execution']['revision']

    def attempt(rounds):
        import copy
        candidate = copy.deepcopy(p)
        candidate['_task_execution'].update(revision=current+1, model_rounds_used=rounds)
        try:
            with connect(f) as c:
                save(c, f, candidate, expected=current)
            return 'saved'
        except psycopg.errors.SerializationFailure:
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [1, 2])) == ['saved', 'stale']


def test_wrong_fence_and_expired_lease_do_not_write(task_db):
    f = seed(task_db)
    p = task_payload(f)
    with connect(f) as c:
        assert save(c, f, p, token=str(uuid4()))['outcome'] == 'ownership_lost'
        assert c.execute('SELECT count(*) FROM agent_task_states WHERE root_task_id=%s', (f['task'],)).fetchone()[0] == 0
    with psycopg.connect(task_db) as c:
        c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 minute' WHERE id=%s", (f['task'],))
    with connect(f) as c:
        assert save(c, f, p)['outcome'] == 'lease_expired'


def test_logical_write_failure_rolls_back_existing_checkpoint(task_db):
    f = seed(task_db)
    p = task_payload(f)
    with psycopg.connect(task_db) as c:
        c.execute("""CREATE FUNCTION reject_task_state() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected task failure'; END $$""")
        c.execute('CREATE TRIGGER reject_task_state BEFORE INSERT ON agent_task_states FOR EACH ROW EXECUTE FUNCTION reject_task_state()')
    try:
        with connect(f) as c, pytest.raises(psycopg.Error, match='injected task failure'):
            save(c, f, p)
        with psycopg.connect(task_db) as c:
            assert c.execute('SELECT count(*) FROM conversation_turn_checkpoints WHERE task_id=%s', (f['task'],)).fetchone()[0] == 0
    finally:
        with psycopg.connect(task_db) as c:
            c.execute('DROP TRIGGER reject_task_state ON agent_task_states')
            c.execute('DROP FUNCTION reject_task_state()')


def test_requirements_cannot_be_changed_through_checkpoint(task_db):
    f = seed(task_db)
    p = task_payload(f)
    with connect(f) as c:
        save(c, f, p)
    current = p['_task_execution']['revision']
    p['_task_execution']['requirements'][0]['description'] = 'skip the report'
    p['_task_execution']['revision'] += 1
    with connect(f) as c, pytest.raises(psycopg.errors.InsufficientPrivilege, match='REQUIREMENTS_IMMUTABLE'):
        save(c, f, p, expected=current)


def test_cross_actor_scope_and_fabricated_sources_are_denied(task_db):
    f = seed(task_db)
    p = task_payload(f)
    p['_task_execution']['actor_user_id'] = str(uuid4())
    with connect(f) as c, pytest.raises(psycopg.errors.InsufficientPrivilege, match='SCOPE_DENIED'):
        save(c, f, p)
    p = task_payload(f)
    p['_task_execution']['source_message_ids'].append(str(uuid4()))
    with connect(f) as c, pytest.raises(psycopg.errors.InsufficientPrivilege, match='ANCHOR_INVALID'):
        save(c, f, p)
    with connect(f) as c:
        save(c, f, task_payload(f))
    other = seed(task_db)
    with connect(other) as c:
        assert c.execute('SELECT count(*) FROM agent_task_states WHERE root_task_id=%s', (f['task'],)).fetchone()[0] == 0


def test_migration_owners_rls_and_public_function_access(task_db):
    f = seed(task_db)
    with connect(f) as runtime_connection:
        save(runtime_connection, f, task_payload(f))
    with psycopg.connect(task_db) as c:
        rows = c.execute("SELECT relname,pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('agent_task_states'::regclass,'agent_task_events'::regclass)").fetchall()
        assert len(rows) == 2 and all(row[1:] == ('everydayai', True, True) for row in rows)
        row = c.execute("SELECT prosecdef,has_function_privilege('image_untrusted',oid,'EXECUTE') FROM pg_proc WHERE proname='save_agent_task_checkpoint'").fetchone()
        assert row == (False, False)
        with pytest.raises(psycopg.Error, match='ROLLBACK_REQUIRES_DRAIN'):
            c.execute((ROOT/'migrations/rollback/340_agent_task_state_rollback.sql').read_text())


@pytest.mark.parametrize('remove_new_protocol', [True, False])
def test_legacy_checkpoint_rpc_cannot_drop_or_bypass_logical_state(task_db, remove_new_protocol):
    import copy
    f = seed(task_db)
    original = task_payload(f)
    with connect(f) as c:
        save(c, f, original)
    changed = copy.deepcopy(original)
    if remove_new_protocol:
        changed = {'messages': []}
        expected_error = 'TASK_EXECUTION_DOWNGRADE_DENIED'
    else:
        changed['_task_execution']['revision'] += 1
        changed['_task_execution']['model_rounds_used'] += 1
        expected_error = 'TASK_EXECUTION_CHECKPOINT_INCONSISTENT'
    # Constraint checks fire at commit: the entire write must roll back.
    with pytest.raises(psycopg.Error, match=expected_error):
        with connect(f) as c:
            c.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s)",
                      (f['task'], f['token'], Jsonb(changed)))
    with connect(f) as c:
        logical = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s', (f['task'],)).fetchone()[0]
        checkpoint = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s', (f['task'],)).fetchone()[0]
        assert logical == checkpoint['_task_execution'] == original['_task_execution']


def test_never_enabled_legacy_checkpoint_still_writes(task_db):
    f = seed(task_db)
    with connect(f) as c:
        result = c.execute("SELECT save_generation_checkpoint(%s,%s,'before_model',%s)",
                           (f['task'], f['token'], Jsonb({'messages': []}))).fetchone()[0]
        assert result['outcome'] == 'saved'


def test_multiple_new_writes_in_one_transaction_check_final_consistent_state(task_db):
    f = seed(task_db)
    p = task_payload(f)
    with connect(f) as c:
        save(c, f, p)
        previous = p['_task_execution']['revision']
        p['_task_execution']['revision'] += 1
        p['_task_execution']['model_rounds_used'] += 1
        save(c, f, p, expected=previous)
    with connect(f) as c:
        assert c.execute('SELECT revision FROM agent_task_states WHERE root_task_id=%s', (f['task'],)).fetchone()[0] == previous + 1


def test_deleting_logical_state_cannot_orphan_a_new_checkpoint(task_db):
    f = seed(task_db)
    with connect(f) as c:
        save(c, f, task_payload(f))
    with pytest.raises(psycopg.Error, match='STATE_DELETE_DENIED'):
        with connect(f) as c:
            c.execute('DELETE FROM agent_task_states WHERE root_task_id=%s', (f['task'],))
