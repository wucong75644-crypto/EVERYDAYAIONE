"""Exercise native QueryBuilder, permissions and pagination, not a Supabase mock."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_request_execution_windows_postgres import (
    window_db, resolution_db, content_db, dispatch_db, controls_db, followup_db, continuation_db,
    native_result_db, children_db, operation_db, task_db, composition_db, ecom_db, isolated_db,
)
from tests.test_task_execution_user_changes_postgres import prepared
from tests.test_task_execution_followup_reuse_postgres import new_turn
from tests.test_skill_composition_postgres import connect
from tests.test_model_directed_loop import case
from core.local_db import LocalDBClient
from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from services.task_execution import image_history
from services.task_execution.image_reference_catalog import for_runtime


@pytest.mark.parametrize('order', ['oldest_first', 'newest_first'])
def test_real_native_history_pages_cross_recent_window_with_stable_refs(window_db, order):
    first, cp = prepared(window_db, binding_version=3)
    with psycopg.connect(window_db) as c:
        c.execute("UPDATE messages SET status='completed',context_revision=1 WHERE id=%s", (first['message'],))
        for index in range(35):
            c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,%s,%s,'completed',%s,%s)",
                (str(uuid4()), first['conv'], 'user' if index == 0 else 'assistant',
                 Jsonb([{'type': 'image', 'name': f'历史图{index}'}]), index + 2, first['turn']))
    _, current, _, _, _ = new_turn(window_db, old=first)
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    snapshot['image_reference_version'] = 1
    runtime = SimpleNamespace(checkpoint_base={'_request_context_v1': snapshot}, task_id=current['task'])
    _, p, _, _ = case()
    context = replace(p.execution_context, actor_user_id=current['user'], workspace_owner_id=current['user'],
                      org_id=None, conversation_id=current['conv'], task_id=current['task'])
    db = LocalDBClient(psycopg.conninfo.make_conninfo(window_db, user='everydayai'), min_size=1, max_size=2)
    handler = SimpleNamespace(db=ScopedDatabaseClient(db, DatabaseScope(current['user'], None, DatabaseAccessKind.RUNTIME)))
    request = SimpleNamespace(task_id=current['task'], conversation_id=current['conv'], params={})
    previous = dict(for_runtime(runtime).references)
    try:
        seen = []
        args = {'order': order, 'source': 'all', 'limit': 7}
        while True:
            result = image_history.page(handler, request, context, runtime, args)
            seen += [row['image_ref'] for row in result['images']]
            assert all(for_runtime(runtime).references[k] == v for k, v in previous.items())
            if not result['has_more']: break
            args['cursor'] = result['next_cursor']
        assert len(seen) == len(set(seen)) == 35
        assert len(image_history.recovered(runtime)) == 35
        uploaded = next(i for i in image_history.recovered(runtime) if i['provenance'] == 'user_upload')
        assert uploaded['source_turn'] == 2
        with pytest.raises(PermissionError):
            image_history.page(handler, request, replace(context, personal_context_allowed=False), runtime, {})
    finally:
        db.close()
