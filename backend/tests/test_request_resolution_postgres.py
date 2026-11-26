"""Real scoped candidate reads and fenced target adoption, including no-op turns."""
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.task_execution import request_resolution as rr, content_objects as co
from services.task_execution.followup import payload, FollowupObservation
from tests.test_content_objects_postgres import (
    content_db, dispatch_db, controls_db, followup_db, continuation_db, native_result_db,
    children_db, operation_db, task_db, composition_db, ecom_db, isolated_db,
)
from tests.test_task_execution_followup_reuse_postgres import new_turn, adopt
from tests.test_task_execution_user_changes_postgres import prepared
from tests.test_skill_composition_postgres import connect
from tests.test_task_execution_postgres import save

@pytest.fixture(scope='module')
def resolution_db(content_db):
    yield content_db
    with psycopg.connect(content_db) as c:
        c.execute("UPDATE tasks SET status='completed' WHERE id IN (SELECT task_id FROM conversation_turn_checkpoints WHERE state ? '_request_context_v1')")


def chain(dsn):
    a, cp = prepared(dsn, binding_version=3)
    # Match the outer Actor commit: seed() alone leaves its first input pending.
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE messages SET status='completed',context_revision=1 WHERE id=%s", (a['message'],))
    old, b, _, _, _ = new_turn(dsn, old=a, text='保留双本子构图，在横线内页增加银色蕾丝元素')
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE tasks SET request_params=%s WHERE id=%s", (Jsonb({'_ecom_workflow': {'intent': 'edit'}}), b['task']))
        c.execute("UPDATE conversation_turn_checkpoints SET state=state || %s WHERE task_id=%s", (Jsonb({'_ecom_workflow': {'intent': 'edit'}}), b['task']))
    _, current, source, _, _ = new_turn(dsn, old=b, text='没问题，开始生成吧')
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    return a, b, current, source, snapshot


def test_unsubmitted_request_survives_beside_older_recoverable_task(resolution_db):
    a, b, current, source, snapshot = chain(resolution_db)
    # The original recovery query still finds A. The new context also includes B.
    assert source['state']['root_task_id'] == a['task']
    assert snapshot['input_message_id'] == current['message']
    row = next(r for r in snapshot['requests'] if r['message_id'] == b['message'])
    assert row['intent'] == 'edit' and not row['has_execution_state']
    assert row['text_excerpt'] == '保留双本子构图，在横线内页增加银色蕾丝元素'
    assert rr.active_request(snapshot) == b['message']


def test_authenticated_current_input_is_visible_before_outer_turn_commit(resolution_db):
    a, b, current, source, _ = chain(resolution_db)
    with psycopg.connect(resolution_db) as c:
        c.execute("UPDATE messages SET status='pending',context_revision=NULL,content=%s WHERE id=%s", (Jsonb([
            {'type': 'text', 'text': '改这张'}, {'type': 'image', 'name': 'quote.png'}]), current['message']))
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    row = next(r for r in snapshot['requests'] if r['message_id'] == current['message'])
    assert row['images'] == [{'name': 'quote.png', 'client_reference': None, 'reference': {'message_id': current['message'], 'content_index': 1}}]
    assert snapshot['catalog_version'] == 2
    assert row['turn_number'] == 3
    assert {r['message_id']: r['turn_number'] for r in snapshot['requests']} == {a['message']: 1, b['message']: 2, current['message']: 3}
    assert row['source_sha256']


def test_quotes_and_new_uploads_are_recorded_in_their_actual_turn(resolution_db, monkeypatch):
    a, b, current, _, _ = chain(resolution_db)
    with psycopg.connect(resolution_db) as c:
        c.execute("UPDATE messages SET content=%s WHERE id=%s", (Jsonb([
            {'type': 'text', 'text': '用新图作主体，参考之前那张'},
            {'type': 'image', 'name': 'new.png'},
            {'type': 'image', 'name': 'quote.png', 'source_message_id': a['message'], 'source_content_index': 1},
        ]), current['message']))
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    from tests.test_request_resolution import setup_case
    runtime, _, _, _, _ = setup_case()
    runtime.checkpoint_base[rr.CONTEXT] = snapshot
    monkeypatch.setattr(rr, 'groups', lambda _: [])
    row = rr.model_context(runtime)['current_turn']
    assert row['turn_number'] == 3
    assert [i['name'] for i in row['new_uploads']] == ['new.png']
    assert row['quoted_images'][0]['original_source']['turn_number'] == 1
    assert row['quoted_images'][0]['reference'] == {'message_id': current['message'], 'content_index': 2}
    assert row['selected_images'] == []  # A quote is not a model-selected base.


def test_turn_number_is_not_the_position_in_the_recent_twelve_requests(resolution_db):
    _, _, current, _, _ = chain(resolution_db)
    for index in range(12):
        _, current, _, _, _ = new_turn(resolution_db, old=current, text=f'独立讨论 {index}')
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    assert len(snapshot['requests']) == 12
    assert [r['turn_number'] for r in snapshot['requests']] == list(range(15, 3, -1))


@pytest.mark.parametrize('fault', ['wrong_user', 'expired_lease', 'wrong_token', 'other_conversation'])
def test_request_catalog_keeps_identity_lease_and_conversation_scope(resolution_db, fault):
    a, b, current, source, snapshot = chain(resolution_db)
    if fault == 'expired_lease':
        with psycopg.connect(resolution_db) as c:
            c.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (current['task'],))
    with connect(current) as c:
        if fault == 'wrong_user':
            c.execute("SELECT set_config('app.actor_user_id',%s,true)", (str(uuid4()),))
        if fault == 'other_conversation':
            # An unrelated conversation's messages cannot leak into the catalog.
            from tests.test_skill_composition_postgres import seed
            other = seed(resolution_db)
            result = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
            assert other['message'] not in {r['message_id'] for r in result['requests']}
        else:
            with pytest.raises(psycopg.Error, match='REQUEST_CONTEXT_SCOPE_DENIED'):
                c.execute('SELECT read_agent_request_context(%s,%s)',
                          (current['task'], str(uuid4()) if fault == 'wrong_token' else current['token']))


def test_generic_confirmation_cannot_adopt_older_task_at_sql_boundary(resolution_db):
    a, b, current, source, snapshot = chain(resolution_db)
    fresh = {'messages': [{'role': 'user', 'content': '没问题，开始生成吧'}], rr.CONTEXT: snapshot,
             rr.KEY: {'version': 1, 'input_message_id': current['message'], 'input_revision': 'a' * 64,
                      'status': 'resolved', 'execution_task_id': current['task'],
                      'target': {'kind': 'request', 'basis': 'active_request', 'source_message_id': b['message']}}}
    with connect(current) as c:
        c.execute("SELECT save_generation_checkpoint(%s,%s,'after_tool',%s)", (current['task'], current['token'], Jsonb(fresh)))
    observation = FollowupObservation(relation='continue', evidence=('没问题，开始生成吧',))
    merged = payload(source, fresh, task_id=current['task'], turn_id=current['turn'], model_round=1, usage={}, observation=observation)
    assert merged[rr.KEY] == fresh[rr.KEY] and merged[rr.CONTEXT] == snapshot
    with connect(current) as c:
        with pytest.raises(psycopg.Error, match='REQUEST_TARGET_ADOPTION_MISMATCH'):
            adopt(c, current, source, observation.model_dump(mode='json'), merged)
    with psycopg.connect(resolution_db) as c:
        assert str(c.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s', (a['task'],)).fetchone()[0]) == a['task']
        assert c.execute('SELECT count(*) FROM agent_task_events WHERE continuation_task_id=%s', (current['task'],)).fetchone()[0] == 0


@pytest.mark.parametrize('fault', ['valid', 'wrong_revision', 'wrong_item', 'changed_payload'])
def test_explicit_saved_content_can_still_adopt_its_exact_version(resolution_db, fault):
    # Reuse the production content save path, not a hand-written content record.
    import asyncio
    from dataclasses import replace
    from tests.test_model_directed_loop import case, execute
    from tests.test_content_objects import document
    from tests.test_task_execution_model_bridge import call
    from tests.test_skill_composition_postgres import seed
    a = seed(resolution_db)
    async def checkpoint(point, value):
        with connect(a) as c:
            return save(c, a, value, expected=value.get('_task_execution_expected_revision', 0))
    r, p, h, q = case(checkpoint)
    r.task_bootstrap = replace(r.task_bootstrap, task_id=UUID(a['task']), conversation_id=UUID(a['conv']),
                               actor_user_id=UUID(a['user']), input_message_id=UUID(a['message']),
                               execution_identity=r.task_bootstrap.execution_identity.model_copy(update={'workspace_owner_id': a['user']}))
    r.task_id = a['task']; r.conversation_id = a['conv']; r.turn_id = a['turn']
    p.execution_context = replace(p.execution_context, actor_user_id=a['user'], workspace_owner_id=a['user'], conversation_id=a['conv'], task_id=a['task'])
    q = replace(q, user_id=a['user'], conversation_id=a['conv'], task_id=a['task'])
    p.content_controls_available = True
    asyncio.run(execute(r, p, h, q, [call(co.SAVE, {'document': document()}, 'real-save')]))
    group = co.catalog(r.task_execution_state)[0]
    with connect(a) as c:
        c.execute("UPDATE conversation_turn_checkpoints SET state=state || %s WHERE task_id=%s", (Jsonb({
            '_inline_media_action': {'call_id': 'original-window-only'},
            '_request_response_v1': {'text': 'original response', 'input_revision': 'old'}}), a['task']))
    _, current, source, _, _ = new_turn(resolution_db, old=a, text='回到前面的方案，执行第二条')
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    target = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': group['artifact_id'],
              'revision': group['revision'], 'item_indices': [2], 'root_task_id': a['task']}
    if fault == 'wrong_revision':
        target['revision'] = group['revision'] + 1
    elif fault == 'wrong_item':
        target['item_indices'] = [100]
    fresh = {'messages': [], rr.CONTEXT: snapshot, rr.KEY: {'version': 1, 'input_message_id': current['message'],
             'input_revision': 'b' * 64, 'execution_task_id': current['task'], 'status': 'resolved', 'target': target}}
    observation = FollowupObservation(relation='continue', evidence=('回到前面的方案，执行第二条',))
    with connect(current) as c:
        c.execute("SELECT save_generation_checkpoint(%s,%s,'after_tool',%s)", (current['task'], current['token'], Jsonb(fresh)))
    merged = payload(source, fresh, task_id=current['task'], turn_id=current['turn'], model_round=1, usage={}, observation=observation)
    assert '_inline_media_action' not in merged and '_request_response_v1' not in merged
    if fault == 'changed_payload':
        merged[rr.KEY]['target']['item_indices'] = [1]
    with connect(current) as c:
        if fault != 'valid':
            with pytest.raises(psycopg.Error, match='REQUEST_TARGET_ADOPTION_MISMATCH'):
                adopt(c, current, source, observation.model_dump(mode='json'), merged)
            return
        result = adopt(c, current, source, observation.model_dump(mode='json'), merged)
        assert result['outcome'] == 'saved'
        assert adopt(c, current, source, observation.model_dump(mode='json'), merged)['payload'] == result['payload']
        assert result['payload'][rr.KEY]['target'] == target
