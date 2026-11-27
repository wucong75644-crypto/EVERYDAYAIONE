"""Validate the actual new-window admission fence and reversible migration."""
from pathlib import Path
import copy

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_request_resolution_postgres import (
    resolution_db, content_db, dispatch_db, controls_db, followup_db, continuation_db, native_result_db,
    children_db, operation_db, task_db, composition_db, ecom_db, isolated_db,
)
from tests.test_task_execution_followup_reuse_postgres import new_turn, adopt
from tests.test_task_execution_user_changes_postgres import prepared
from tests.test_skill_composition_postgres import connect

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module')
def window_db(resolution_db):
    yield resolution_db


@pytest.mark.parametrize('fault', [None, 'wrong_input', 'free_rounds', 'reset_lifetime', 'lost_history'])
def test_exhausted_old_task_can_resume_only_with_real_new_window(window_db, fault):
    old, checkpoint = prepared(window_db, binding_version=3)
    checkpoint['_task_execution']['model_rounds_used'] = 35
    with psycopg.connect(window_db) as c:
        c.execute('UPDATE agent_task_states SET state=%s WHERE root_task_id=%s', (Jsonb(checkpoint['_task_execution']), old['task']))
        c.execute('UPDATE conversation_turn_checkpoints SET state=%s WHERE task_id=%s', (Jsonb(checkpoint), old['task']))
    old, new, source, observation, merged = new_turn(window_db, old=old)
    assert source['execution_window_version'] == 1
    assert merged['_task_execution']['model_rounds_used'] == 36
    assert merged['_task_execution']['execution_window']['model_rounds_used'] == 1
    if fault == 'wrong_input': merged['_task_execution']['execution_window']['input_message_id'] = old['message']
    if fault == 'free_rounds': merged['_task_execution']['execution_window']['model_rounds_used'] = 0
    if fault == 'reset_lifetime': merged['_task_execution']['model_rounds_used'] = 1
    if fault == 'lost_history': merged['_historical_image_catalog_v1'] = {'images': []}
    with connect(new) as c:
        if fault:
            with pytest.raises(psycopg.Error), c.transaction():
                adopt(c, new, source, observation, merged)
        else:
            result = adopt(c, new, source, observation, merged)
            assert result['outcome'] == 'saved'
            assert adopt(c, new, source, observation, merged) == result
