"""Real SQL ownership transfer: new receipts replace old, forged ones cannot."""
import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.task_execution.followup import payload, FollowupObservation, WINDOW_CONTEXT_KEYS
from tests.test_content_objects_postgres import content_db
from tests.test_task_execution_followup_reuse_postgres import new_turn, adopt
from tests.test_skill_composition_postgres import connect

pytest_plugins = ['tests.test_content_objects_postgres']


@pytest.mark.parametrize('keep_new', [True, False])
def test_fresh_read_and_recovery_window_replaces_prior_without_changing_old_facts(content_db, keep_new):
    old, new, source, _, _ = new_turn(content_db)
    previous_receipts = {'_content_revision_reads_v1': {'version': 1, 'owner_task_id': old['task'],
        'input_revision': 'old-input', 'items': {'old-draft:3': {'revision': 1}}},
        '_existing_image_recovery_v1': {'input_revision': 'old-input', 'phase': 'located'}}
    with psycopg.connect(content_db) as connection:
        connection.execute('UPDATE conversation_turn_checkpoints SET state=state||%s WHERE task_id=%s',
            (Jsonb(previous_receipts), old['task']))
    with connect(new) as connection:
        source = connection.execute('SELECT read_agent_task_followup(%s,%s)', (new['task'], new['token'])).fetchone()[0]['source']
    fresh = {'messages': [{'role': 'user', 'content': source['message']}], 'next_model_round': 2}
    if keep_new:
        fresh.update({'_content_revision_reads_v1': {'version': 1, 'owner_task_id': new['task'],
            'input_revision': 'new-input', 'items': {'new-draft:3': {'revision': 2}}},
            '_existing_image_recovery_v1': {'input_revision': 'new-input', 'phase': 'located'}})
    with connect(new) as connection:
        connection.execute("SELECT save_generation_checkpoint(%s,%s,'after_tool',%s)",
            (new['task'], new['token'], Jsonb(fresh)))
    observation = FollowupObservation(relation='amend', evidence=[source['message']])
    merged = payload(source, fresh, task_id=new['task'], turn_id=new['turn'], model_round=2,
        usage={}, observation=observation)
    with connect(new) as connection:
        result = adopt(connection, new, source, observation.model_dump(mode='json'), merged)
        assert result['outcome'] == 'saved'
        for key in previous_receipts:
            assert result['payload'].get(key) == fresh.get(key)
        assert result['payload']['_task_execution']['artifacts'] == source['state']['artifacts']
        assert result['payload']['_task_execution']['operations'] == source['state']['operations']
        stored_old = connection.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
            (old['task'],)).fetchone()[0]
        assert all(stored_old[key] == value for key, value in previous_receipts.items())
        assert connection.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s', (new['task'],)).fetchone()[0] == 0


@pytest.mark.parametrize('key', WINDOW_CONTEXT_KEYS)
def test_transfer_rejects_forged_new_window_context(content_db, key):
    old, new, source, observation, merged = new_turn(content_db)
    merged[key] = {'forged': True}
    expected = 'TASK_FOLLOWUP_FACTS_CHANGED' if key == '_historical_image_catalog_v1' else 'TASK_FOLLOWUP_WINDOW_CONTEXT_CHANGED'
    with pytest.raises(psycopg.Error, match=expected):
        with connect(new) as connection:
            adopt(connection, new, source, observation, merged)
    with psycopg.connect(content_db) as connection:
        assert str(connection.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s',
            (old['task'],)).fetchone()[0]) == old['task']


def test_python_sql_window_key_contract_agrees(content_db):
    import re
    with psycopg.connect(content_db) as connection:
        definition = connection.execute("SELECT pg_get_functiondef('adopt_agent_task_followup(uuid,uuid,uuid,bigint,text,jsonb,jsonb)'::regprocedure)").fetchone()[0]
    guard = re.search(r'FOREACH k IN ARRAY ARRAY\[(.*?)\] LOOP', definition, re.S).group(1)
    assert tuple(re.findall(r"'([^']+)'", guard)) == WINDOW_CONTEXT_KEYS
