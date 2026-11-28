"""Exercise native QueryBuilder, permissions and pagination, not a Supabase mock."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from pathlib import Path
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


def test_upload_generate_redisplay_and_edit_have_one_canonical_identity(window_db):
    first, cp = prepared(window_db, binding_version=3)
    upload, result, show_upload, show_result, show_again = [str(uuid4()) for _ in range(5)]
    with psycopg.connect(window_db) as c:
        c.execute("UPDATE messages SET status='completed',context_revision=1 WHERE id=%s", (first['message'],))
        rows = [(upload, 'user', {'type': 'image', 'name': '星星原图.jpg'}),
                (result, 'assistant', {'type': 'image', 'name': '蓝白灯塔.png'}),
                (show_upload, 'assistant', {'type': 'image', 'source_message_id': upload, 'source_content_index': 0}),
                (show_result, 'assistant', {'type': 'image', 'source_message_id': result, 'source_content_index': 0}),
                (show_again, 'assistant', {'type': 'image', 'source_message_id': show_result, 'source_content_index': 0})]
        for revision, (mid, role, part) in enumerate(rows, 2):
            c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,%s,%s,'completed',%s,%s)",
                      (mid, first['conv'], role, Jsonb([part]), revision, first['turn']))
    _, current, _, _, _ = new_turn(window_db, old=first, text='还是最早那张灯塔图，只把桌布换成浅蓝色。')
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    assert snapshot['image_provenance_version'] == 1
    images = snapshot['image_results']
    assert {i['source_message_id'] for i in images} == {upload, result}
    blue = next(i for i in images if i['source_message_id'] == result)
    star = next(i for i in images if i['source_message_id'] == upload)
    assert star['origin_kind'] == 'user_upload' and star['original_turn'] == 2
    assert {a['message_id'] for a in blue['displayed_in']} == {show_result, show_again}
    assert all(a['turn_number'] == 2 for a in blue['displayed_in'])
    assert blue['name'] == '蓝白灯塔.png'
    snapshot['image_reference_version'] = 1
    runtime = SimpleNamespace(checkpoint_base={'_request_context_v1': snapshot}, task_id=current['task'])
    from services.task_execution import request_resolution as rr
    from services.task_execution.contracts import TaskError
    refs = for_runtime(runtime)
    canonical = {'message_id': result, 'content_index': 0}
    alias = {'message_id': show_again, 'content_index': 0}
    handle = refs.encode(canonical)
    assert refs.encode(alias) == handle
    assert refs.decode(handle) == canonical
    assert len({c['image_ref'] for c in refs.candidates}) == len(refs.candidates)
    assert any(c.get('displayed_in') and c['displayed_in'][0]['display_turn'] == 2 for c in refs.candidates)
    target = {'kind': 'image', 'basis': 'explicit_reference', 'source_message_id': result, 'content_index': 0}
    with pytest.raises(TaskError, match='REQUEST_TARGET_IMAGE_REQUIRES_VIEW'):
        rr.freeze(runtime, SimpleNamespace(context_anchor=None), target)
    image_history.mark_viewed(runtime, canonical)
    assert rr.freeze(runtime, SimpleNamespace(context_anchor=None), target)['target']['source_message_id'] == result
    # The next input must independently verify its image; a previous task's
    # display/read receipt cannot authorize editing a different picture.
    snapshot['input_message_id'] = str(uuid4())
    assert image_history.requires_view(runtime, canonical)


def test_provenance_migration_rolls_back_and_reapplies_without_changing_messages(window_db):
    root = Path(__file__).parents[1]
    with psycopg.connect(window_db) as c:
        before = c.execute('SELECT count(*),md5(string_agg(content::text,\'\' ORDER BY id)) FROM messages').fetchone()
        c.execute((root/'migrations/rollback/388_agent_image_provenance_rollback.sql').read_text())
        definition = c.execute("SELECT pg_get_functiondef('public.read_agent_request_context(uuid,uuid)'::regprocedure)").fetchone()[0]
        assert 'image_provenance_version' not in definition
        c.execute((root/'migrations/388_agent_image_provenance.sql').read_text())
        assert c.execute('SELECT count(*),md5(string_agg(content::text,\'\' ORDER BY id)) FROM messages').fetchone() == before


@pytest.mark.parametrize('fault', ['cycle', 'foreign_conversation', 'invalid_index'])
def test_unverified_display_source_never_becomes_an_edit_candidate(window_db, fault):
    from tests.test_skill_composition_postgres import seed
    first, cp = prepared(window_db, binding_version=3)
    other = seed(window_db)
    displayed = str(uuid4())
    with psycopg.connect(window_db) as c:
        c.execute("UPDATE messages SET status='completed',context_revision=1 WHERE id=%s", (first['message'],))
        c.execute("UPDATE messages SET status='completed',context_revision=1,content=%s WHERE id=%s",
                  (Jsonb([{'type': 'image', 'name': '外部会话图'}]), other['message']))
        source = other['message'] if fault == 'foreign_conversation' else displayed
        index = -1 if fault == 'invalid_index' else 0
        c.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,turn_id) VALUES(%s,%s,'assistant',%s,'completed',2,%s)",
                  (displayed, first['conv'], Jsonb([{'type': 'image', 'source_message_id': source,
                    'source_content_index': index}]), first['turn']))
    _, current, _, _, _ = new_turn(window_db, old=first)
    with connect(current) as c:
        snapshot = c.execute('SELECT read_agent_request_context(%s,%s)', (current['task'], current['token'])).fetchone()[0]
    assert snapshot['image_results'] == []
