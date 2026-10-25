"""Personal journal uses real runtime-role transactions in disposable PostgreSQL."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_knowledge_derivation import (derived_db, cleanup_db, resize_db, manual_db, summary_db,
    geometry_db, groups_db, editing_db, entry_db, rev, directional_db, structure_db, current, visual_db,
    tools_db, payload, local_db, command, layout_db, before_db, create, toggle, theme_db, rich_db)
from services.knowledge_workspace.ai_journal import JournalStore
from services.knowledge_workspace.ai_journal_extract import validated_entries
from services.knowledge_workspace.ai_journal_worker import process


@pytest.fixture(scope='module')
def journal_db(derived_db):
    make, actors = derived_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute('SET LOCAL ROLE everydayai')
        c.execute('ALTER TABLE messages ADD COLUMN content text')
        c.execute("ALTER TABLE tasks ADD COLUMN user_id uuid, ADD COLUMN org_id uuid, ADD COLUMN conversation_id uuid, ADD COLUMN status text DEFAULT 'completed', ADD COLUMN input_message_id uuid, ADD COLUMN assistant_message_id uuid, ADD COLUMN updated_at timestamptz DEFAULT now()")
        c.execute("ALTER TABLE conversations ADD COLUMN user_id uuid, ADD COLUMN org_id uuid, ADD COLUMN scope_type text DEFAULT 'user'")
        c.execute("CREATE TABLE tool_audit_log(task_id text,tool_name text,status text,tool_call_id text,created_at timestamptz DEFAULT now())")
        c.execute("CREATE TABLE tool_invocations(task_id uuid,conversation_id uuid,tool_call_id text,tool_name text,status text,result jsonb,updated_at timestamptz DEFAULT now())")
        c.execute("CREATE TABLE knowledge_nodes(id uuid PRIMARY KEY,category text,title text,content text,scope text,org_id uuid,source text,is_deleted boolean DEFAULT false,updated_at timestamptz DEFAULT now())")
        c.execute('RESET ROLE')
        c.execute((Path(__file__).resolve().parents[1] / 'migrations/333_knowledge_ai_journal.sql').read_text())
    return make, actors


def event(make, repo, user='以后报告先给结论再给明细', status='completed', tools=()):
    tid, mid, aid, cid = [str(uuid4()) for _ in range(4)]
    with psycopg.connect(**make.admin_args) as c:
        c.execute('INSERT INTO conversations(id,user_id,org_id) VALUES(%s,%s,%s)', (cid, repo.scope.actor_user_id, repo.scope.org_id))
        for message, role, text in [(mid, 'user', user), (aid, 'assistant', '已处理')]:
            c.execute('INSERT INTO messages(id,conversation_id,role,content,actor_id,org_id) VALUES(%s,%s,%s,%s,%s,%s)',
                      (message, cid, role, json.dumps([{'type': 'text', 'text': text}], ensure_ascii=False), repo.scope.actor_user_id, repo.scope.org_id))
        c.execute('INSERT INTO tasks(id,user_id,org_id,conversation_id,input_message_id,assistant_message_id,status) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                  (tid, repo.scope.actor_user_id, repo.scope.org_id, cid, mid, aid, status))
        for name, tool_status in tools:
            c.execute('INSERT INTO tool_audit_log(task_id,tool_name,status) VALUES(%s,%s,%s)', (tid, name, tool_status))
        result = c.execute('INSERT INTO kb_ai_events(task_id,actor_id,org_id,task_status) VALUES(%s,%s,%s,%s) RETURNING id',
                  (tid, repo.scope.actor_user_id, repo.scope.org_id, status))
        eid = result.fetchone()[0]
    return eid


def proposal(content='报告先给结论，再给明细'):
    return {'entries': [{'category': 'preference', 'key': 'report_order', 'title': '报告表达顺序',
                         'content': content, 'quote': '以后报告先给结论再给明细'}]}


def test_sql_creation_and_personal_branch_isolation(journal_db):
    make, _ = journal_db
    owner, other = make(), make(1)
    with owner.cursor() as c:
        c.execute('SELECT public.kb_ai_ensure_branch() AS value'); one = c.fetchone()['value']
        c.execute('SELECT public.kb_ai_ensure_branch() AS value'); assert c.fetchone()['value']['id'] == one['id']
    assert owner.object(one['root_id'])['title'] == 'AI 工作记录与使用习惯'
    with other.cursor() as c:
        c.execute('SELECT count(*) AS n FROM kb_ai_branches'); assert c.fetchone()['n'] == 0
    with make(0, 1).cursor() as c:
        c.execute('SELECT count(*) AS n FROM kb_ai_branches'); assert c.fetchone()['n'] == 0
    assert other.object(one['root_id']) is None


def test_worker_updates_same_entry_and_replay_is_noop(journal_db):
    make, _ = journal_db; repo = make(); store = JournalStore(repo)
    async def generate(*_): return proposal()
    eid = event(make, repo)
    claimed = store.claim(eid)
    store.revalidate(claimed)
    store.save(claimed, validated_entries(proposal(), claimed)[0])
    store.finish(claimed, 'done')
    asyncio.run(process(store, eid, generate)); first = store.entries('preference')[0]
    asyncio.run(process(store, eid, generate)); assert store.entries('preference')[0]['object_id'] == first['object_id']
    async def update(*_):
        value = proposal('报告先列明细最后给结论')
        value['entries'][0]['quote'] = '以后报告先列明细最后给结论'
        return value
    asyncio.run(process(store, event(make, repo, user='以后报告先列明细最后给结论'), update))
    second = store.entries('preference')[0]
    assert second['object_id'] == first['object_id'] and second['body'] != first['body']
    assert store.preferences()['preferences']


def test_user_edits_and_deletions_are_not_overwritten(journal_db):
    make, _ = journal_db; repo = make(); store = JournalStore(repo)
    async def generate(*_): return proposal()
    asyncio.run(process(store, event(make, repo), generate))
    entry = store.entries('preference')[0]; obj = repo.object(entry['object_id'])
    repo.command(command('save_document', {'object_id': obj['id'], 'expected_revision': obj['revision'], 'body': '用户人工维护的格式'}))
    asyncio.run(process(store, event(make, repo), generate))
    assert repo.object(obj['id'])['body'] == '用户人工维护的格式'
    assert '用户人工维护' in store.preferences()['preferences'][0]['body']
    with psycopg.connect(**make.admin_args) as c:
        c.execute('UPDATE kb_objects SET undo_hidden=true WHERE id=%s', (obj['id'],))
    asyncio.run(process(store, event(make, repo), generate))
    assert store.entries('preference') == [] and store.preferences()['managed']


def test_event_lease_and_candidate_evidence(journal_db):
    make, _ = journal_db; store = JournalStore(make())
    eid = event(make, make(), tools=[('code_execute', 'success')])
    claim = store.claim(eid)
    assert store.claim(eid) is None and JournalStore(make(1)).claim(eid) is None
    assert not JournalStore(make(1)).finish(claim, 'done')
    fake = {'entries': [{'category': 'preference', 'key': 'style', 'title': '假偏好', 'content': '偏好内容', 'quote': '今后随便编造'}]}
    assert validated_entries(fake, claim) == []
    method = {'entries': [{'category': 'method', 'key': 'query', 'title': '查询方法', 'content': '先核验字段再查询', 'tools': ['code_execute']}]}
    assert validated_entries(method, claim)[0]['verification'] == 'candidate'
    method['entries'][0]['tools'] = ['erp_execute']
    assert validated_entries(method, claim) == []
    assert store.finish(claim, 'done')


def test_import_requires_real_personal_ownership_and_is_idempotent(journal_db):
    make, _ = journal_db; repo = make()
    ids = [str(uuid4()) for _ in range(3)]
    with psycopg.connect(**make.admin_args) as c:
        for lid, scope in zip(ids, ['user:' + repo.scope.actor_user_id, 'org', 'global']):
            c.execute("INSERT INTO knowledge_nodes(id,category,title,content,scope,org_id,source) VALUES(%s,'experience','历史方法','经人工整理的历史步骤',%s,%s,'manual')", (lid, scope, repo.scope.org_id))
        fingerprint = c.execute('SELECT md5(jsonb_build_array(id,title,content,scope,org_id,category,source,is_deleted,updated_at)::text) FROM knowledge_nodes WHERE id=%s', (ids[0],)).fetchone()[0]
    with repo.cursor() as c:
        c.execute('SELECT kb_ai_import_entry(%s,%s) AS result', (ids[0], 'outdated'))
        assert c.fetchone()['result']['status'] == 'source_changed'
        c.execute('SELECT kb_ai_import_entry(%s,%s) AS result', (ids[0], fingerprint)); first = c.fetchone()['result']
        assert first['status'] == 'saved'
        c.execute('SELECT kb_ai_import_entry(%s,%s) AS result', (ids[0], fingerprint)); assert c.fetchone()['result']['status'] == 'unchanged'
        for lid in ids[1:]:
            c.execute('SELECT kb_ai_import_entry(%s) AS result', (lid,)); assert c.fetchone()['result']['status'] == 'review_required'
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT kb_ai_upsert_entry('x','method','x','x','verified','{}')")
    with make(1).cursor() as c:
        c.execute('SELECT kb_ai_import_entry(%s) AS result', (ids[0],)); assert c.fetchone()['result']['status'] == 'review_required'


def test_expired_lease_recovery_and_task_changed_during_model(journal_db):
    make, _ = journal_db; repo = make(); store = JournalStore(repo)
    eid = event(make, repo); old = store.claim(eid)
    assert store.claim(eid) is None
    with psycopg.connect(**make.admin_args) as c:
        c.execute("UPDATE kb_ai_events SET lease_until=now()-interval '1 second' WHERE id=%s", (eid,))
    new = store.claim(eid)
    assert new['token'] != old['token'] and new['attempts'] == 2
    with pytest.raises(PermissionError):
        store.save(old, validated_entries(proposal(), old)[0])
    with psycopg.connect(**make.admin_args) as c:
        c.execute("UPDATE tasks SET status='cancelled' WHERE id=%s", (new['task_id'],))
    with pytest.raises(PermissionError):
        store.revalidate(new)


def test_model_failure_is_recoverable_not_success(journal_db):
    make, _ = journal_db; repo = make(); store = JournalStore(repo); eid = event(make, repo)
    async def fail(*_): raise TimeoutError('synthetic')
    asyncio.run(process(store, eid, fail))
    with repo.cursor() as c:
        c.execute('SELECT state,error_code,attempts FROM kb_ai_events WHERE id=%s', (eid,)); state = c.fetchone()
        assert state == {'state': 'failed', 'error_code': 'TimeoutError', 'attempts': 1}


def test_discovery_selection_is_not_authorization():
    from services.knowledge_workspace.scope_selection import select
    nodes = [{'id': 'personal', 'kind': 'personal', 'title': '个人'}, {'id': 'team', 'kind': 'team', 'title': '团队'},
             {'id': 'project', 'kind': 'project', 'title': '春季上新'}]
    assert select(nodes, '你好')[0] == []
    assert select(nodes, '翻译下面的文字：hello')[0] == []
    assert {n['id'] for n in select(nodes, '退款怎么处理？')[0]} == {'personal', 'team'}
    assert len(select(nodes, '春季上新项目的流程？')[0]) == 3
    selected, reason = select(nodes + [dict(nodes[-1], id='another')], '春季上新项目的流程？')
    assert len(selected) == 2 and reason == 'ambiguous_project'


def test_legacy_review_never_infers_global_or_org_ownership():
    from services.knowledge_workspace.legacy_migration import review
    actor = str(uuid4()); node = dict(id='n', category='experience', content='步骤', title='方法', scope='org', org_id='org', source='auto')
    assert review([node], actor, 'org')[0]['decision'] == 'review_ownership'
    node['scope'] = 'user:' + actor
    assert review([node], actor, 'org')[0]['decision'] == 'import_candidate_method'
    node['content'] = 'password=synthetic-secret'
    report = review([node], actor, 'org')[0]
    assert report['decision'] == 'review_sensitive' and 'content' not in report


@pytest.mark.asyncio
async def test_preferences_refresh_removes_stale_mem0_but_keeps_custom_instructions(journal_db, monkeypatch):
    from types import SimpleNamespace
    from services.knowledge_workspace.ai_journal import apply_preferences
    from core.config import get_settings
    make, _ = journal_db; repo = make(); store = JournalStore(repo)
    monkeypatch.setattr(get_settings(), 'knowledge_workspace_enabled', True)
    monkeypatch.setattr(get_settings(), 'knowledge_ai_journal_enabled', True)
    monkeypatch.setattr(get_settings(), 'knowledge_workspace_user_ids', '')
    monkeypatch.setattr(get_settings(), 'knowledge_workspace_org_ids', '')
    original = [{'role': 'system', 'content': '<user_preferences>明确指令</user_preferences>\n<user_facts>旧事实</user_facts><user_memory>旧习惯</user_memory>'},
                {'role': 'user', 'content': '当前指令'}]
    handler = SimpleNamespace(db=SimpleNamespace(pool=repo.pool), org_id=repo.scope.org_id, _personal_context_allowed=True)
    refreshed = await apply_preferences(original, handler, repo.scope.actor_user_id)
    assert '旧习惯' not in refreshed[0]['content'] and '旧事实' not in refreshed[0]['content']
    assert '明确指令' in refreshed[0]['content'] and original[0]['content'].endswith('</user_memory>')
    assert refreshed[-1]['role'] == 'system' and '个人知识库使用习惯' in refreshed[-1]['content']
    monkeypatch.setattr(get_settings(), 'knowledge_ai_journal_enabled', False)
    assert await apply_preferences(original, handler, repo.scope.actor_user_id) == refreshed
    handler._personal_context_allowed = False
    assert await apply_preferences(original, handler, repo.scope.actor_user_id) == original


@pytest.mark.asyncio
async def test_discovery_returns_persisted_snapshot_and_respects_actor(journal_db, monkeypatch):
    from types import SimpleNamespace
    from core.config import get_settings
    from services.agent.tool_executor import ToolExecutor
    make, _ = journal_db; repo = make(); eid = event(make, repo)
    monkeypatch.setattr(get_settings(), 'knowledge_workspace_enabled', True)
    monkeypatch.setattr(get_settings(), 'knowledge_default_scope_enabled', True)
    monkeypatch.setattr(get_settings(), 'knowledge_derivation_enabled', True)
    monkeypatch.setattr(get_settings(), 'knowledge_workspace_user_ids', '')
    monkeypatch.setattr(get_settings(), 'knowledge_workspace_org_ids', '')
    with psycopg.connect(**make.admin_args) as c:
        c.execute('GRANT SELECT ON tasks TO everydayai_runtime')
        tid = c.execute('SELECT task_id FROM kb_ai_events WHERE id=%s', (eid,)).fetchone()[0]
        cid = c.execute('SELECT conversation_id FROM tasks WHERE id=%s', (tid,)).fetchone()[0]
    executor = ToolExecutor(SimpleNamespace(pool=repo.pool), repo.scope.actor_user_id, str(cid), repo.scope.org_id)
    executor.task_id = str(tid)
    executor._task_id = str(tid)
    result = await executor._knowledge_discover({'query': '知识库中的报告规则是什么？'})
    assert result.status == 'success' and executor.knowledge_run.snapshot['automatic']
    assert executor.knowledge_run.snapshot['scope_reason'] == 'task_scope'
    old = executor.knowledge_run.id
    await executor._knowledge_discover({'query': '换个范围'})
    assert executor.knowledge_run.id == old
    executor.personal_context_allowed = False
    from core.exceptions import AppException
    with pytest.raises(AppException):
        await executor._knowledge_discover({'query': '知识库报告规则'})


def test_scanner_honors_rollout_before_pagination_and_finds_durable_tasks(journal_db):
    from services.knowledge_workspace.repository import Repository
    from core.db_scope import DatabaseScope, DatabaseAccessKind
    make, _ = journal_db; repo = make(); eid = event(make, repo)
    with psycopg.connect(**make.admin_args) as c:
        c.execute('DELETE FROM kb_ai_events WHERE id=%s', (eid,))
    worker = Repository(repo.pool, DatabaseScope(None, None, DatabaseAccessKind.WORKER))
    with worker.cursor() as c:
        c.execute("SELECT set_config('app.knowledge_users',%s,true)", (str(uuid4()),))
        c.execute('SELECT kb_ai_candidates() AS result'); assert c.fetchone()['result'] == []
    with worker.cursor() as c:
        c.execute("SELECT set_config('app.knowledge_users',%s,true)", (repo.scope.actor_user_id,))
        c.execute('SELECT kb_ai_candidates() AS result'); jobs = c.fetchone()['result']
        assert jobs and all(j['actor_id'] == repo.scope.actor_user_id for j in jobs)
    with repo.cursor() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('SELECT kb_ai_candidates()')


@pytest.mark.asyncio
async def test_legacy_default_stop_does_not_connect_and_metrics_still_record(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from core.config import get_settings, Settings
    from services.knowledge_workspace.ai_journal_extract import worthwhile
    import services.knowledge_service as legacy
    import services.knowledge_metrics as metrics
    settings = get_settings()
    for name in ('kb_legacy_search_enabled', 'kb_legacy_seed_enabled', 'kb_legacy_extraction_enabled', 'kb_legacy_scoring_enabled'):
        assert Settings.model_fields[name].default is False
        monkeypatch.setattr(settings, name, False)
    connect = AsyncMock(side_effect=AssertionError('legacy must not open a connection'))
    monkeypatch.setattr(legacy, 'get_pg_connection', connect)
    assert await legacy.search_relevant('历史方法') == []
    assert await legacy.load_seed_knowledge() == 0
    assert await legacy.add_knowledge(category='experience', node_type='routing_pattern', title='方法', content='步骤') is None
    connect.assert_not_awaited()
    monkeypatch.setattr(settings, 'kb_enabled', False)
    monkeypatch.setattr(settings, 'kb_metrics_enabled', True)
    cursor = AsyncMock(); conn = AsyncMock()
    conn.cursor = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=cursor)))
    context = AsyncMock(__aenter__=AsyncMock(return_value=conn))
    monkeypatch.setattr(metrics, 'get_pg_connection', AsyncMock(return_value=context))
    await metrics.record_metric(task_type='chat', model_id='synthetic', status='success')
    cursor.execute.assert_awaited_once()
    assert not worthwhile({'input': '你好', 'tools': []})


def test_semantic_failure_is_not_a_negative_search(journal_db, monkeypatch):
    from services.knowledge_workspace.chat import KnowledgeRun
    from core.config import get_settings
    from tests.test_knowledge_chat_v3 import start, ref
    make, _ = journal_db; repo = make()
    with repo.cursor() as c:
        c.execute("SELECT id,container_id FROM kb_objects WHERE kind='topic' AND parent_id IS NULL LIMIT 1"); root = c.fetchone()
    run, _, _ = start(make, repo, [ref(root)])
    monkeypatch.setattr(get_settings(), 'knowledge_semantic_enabled', True)
    monkeypatch.setattr(get_settings(), 'dashscope_api_key', 'synthetic-not-used')
    monkeypatch.setattr('services.knowledge_workspace.semantic.query_embedding', lambda _: None)
    for _ in range(2):
        result = run.execute('search', {'root_handle': run.snapshot['roots'][0]['handle'], 'query': '不存在的合成词XYZ'})
        assert result['retrieval_status'] == 'semantic_unavailable'
        assert result['stop_reason'] is None


def test_preference_body_is_the_users_words_not_model_invention():
    e = {'input': '以后报告先给结论再给明细', 'task_id': 't', 'input_message_id': 'm', 'conversation_id': 'c', 'id': 'e', 'tools': [], 'task_status': 'completed'}
    value = proposal('捏造的完全不同习惯')
    saved = validated_entries(value, e)
    assert len(saved) == 1 and '以后报告先给结论再给明细' in saved[0]['body']
    assert '捏造' not in saved[0]['body']


def test_batch_is_atomic_with_completion_and_rejects_changed_import(journal_db):
    make, _ = journal_db; repo = make(); store = JournalStore(repo)
    e = store.claim(event(make, repo))
    entries = validated_entries(proposal(), e)
    entries[0]['key'] = 'preference:atomic_unique'
    broken = {**entries[0], 'key': 'preference:broken', 'body': 'x' * 6001}
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        store.save_batch(e, [entries[0], broken])
    assert not any(r['entry_key'] == 'preference:atomic_unique' for r in store.entries())
    with repo.cursor() as c:
        c.execute('SELECT state FROM kb_ai_events WHERE id=%s', (e['id'],)); assert c.fetchone()['state'] == 'processing'
        c.execute('SELECT kb_ai_import_entry(%s,%s) AS result', (str(uuid4()), 'outdated')); assert c.fetchone()['result']['status'] == 'unavailable'
    store.save_batch(e, entries)
    assert store.claim(e['id']) is None


def test_repeated_fact_with_a_new_source_and_key_does_not_create_a_document(journal_db):
    make, _ = journal_db; repo = make(); store = JournalStore(repo)
    before = [(str(r['object_id']), repo.object(r['object_id'])['revision']) for r in store.entries('preference')]
    async def repeat(*_):
        value = proposal(); value['entries'][0]['key'] = 'different_key_same_fact'
        return value
    asyncio.run(process(store, event(make, repo), repeat))
    after = [(str(r['object_id']), repo.object(r['object_id'])['revision']) for r in store.entries('preference')]
    assert after == before


def test_concurrent_branch_initialization_is_singleton(journal_db):
    from concurrent.futures import ThreadPoolExecutor
    make, _ = journal_db; repo = make(1)
    def initialize(_):
        with repo.cursor() as c:
            c.execute('SELECT kb_ai_ensure_branch() AS value'); return c.fetchone()['value']
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(pool.map(initialize, range(2)))
    assert one['id'] == two['id'] and one['root_id'] == two['root_id']
    assert len(one['categories']) == 3


def test_existing_method_context_never_clips_away_conditions():
    from services.knowledge_workspace.ai_journal_extract import existing_context
    rows = [dict(entry_key=f'method:{n}', category='method', title='方法', body='完整条件' * 1000, user_edited=False) for n in range(5)]
    context = existing_context(rows)
    assert sum(len(r.get('body', '')) for r in context) <= 12000
    assert all(r.get('body') == rows[i]['body'] or r['protected'] for i, r in enumerate(context))
    assert any(r['protected'] and 'body' not in r for r in context)


def test_offline_cli_dry_run_reviewed_apply_and_replay(journal_db, tmp_path):
    import os
    import subprocess
    import sys
    from psycopg.conninfo import make_conninfo
    make, _ = journal_db; repo = make(); lid = str(uuid4())
    with psycopg.connect(**make.admin_args) as c:
        c.execute("INSERT INTO knowledge_nodes(id,category,title,content,scope,org_id,source) VALUES(%s,'experience','CLI合成步骤','先核对范围再操作',%s,%s,'manual')", (lid, 'user:' + repo.scope.actor_user_id, repo.scope.org_id))
    script = str(Path(__file__).resolve().parents[1] / 'scripts/migrate_knowledge_journal.py')
    env = {**os.environ, 'DATABASE_URL': make_conninfo(**make.admin_args)}
    args = [sys.executable, script, '--actor', repo.scope.actor_user_id, '--org', repo.scope.org_id]
    reviewed, applied, replayed = [tmp_path / name for name in ('review.json', 'apply.json', 'replay.json')]
    subprocess.run([*args, '--report', str(reviewed)], env=env, check=True, capture_output=True)
    report = json.loads(reviewed.read_text()); assert any(r['id'] == lid and r['decision'] == 'import_candidate_method' for r in report['items'])
    assert reviewed.stat().st_mode & 0o777 == 0o600
    assert '先核对范围' not in reviewed.read_text()
    for path, expected in [(applied, 'saved'), (replayed, 'unchanged')]:
        subprocess.run([*args, '--apply-reviewed', str(reviewed), '--report', str(path)], env=env, check=True, capture_output=True)
        imported = next(r for r in json.loads(path.read_text())['items'] if r['id'] == lid)
        assert imported['result']['status'] == expected


def test_durable_actor_results_precede_async_audit_and_business_errors_are_not_success(journal_db):
    make, _ = journal_db; repo = make(); eid = event(make, repo)
    variants = [
        ('legacy_ok', 'succeeded', {'kind': 'agent_result', 'status': 'success'}),
        ('legacy_error', 'succeeded', {'kind': 'agent_result', 'status': 'error'}),
        ('v1_ok', 'succeeded', {'tool_result': {'status': 'success', 'execution': {'status': 'succeeded', 'cancelled': False}, 'error': None}}),
        ('v1_business_error', 'succeeded', {'status': 'success', 'tool_result': {'status': 'error', 'execution': {'status': 'succeeded'}}}),
        ('v1_execution_error', 'succeeded', {'tool_result': {'status': 'success', 'execution': {'status': 'failed'}}}),
        ('uncertain', 'uncertain', {'kind': 'agent_result', 'status': 'success'}),
        ('unstructured', 'succeeded', {'kind': 'scalar', 'value': 'cannot establish business outcome'}),
    ]
    with psycopg.connect(**make.admin_args) as c:
        tid = c.execute('SELECT task_id FROM kb_ai_events WHERE id=%s', (eid,)).fetchone()[0]
        cid = c.execute('SELECT conversation_id FROM tasks WHERE id=%s', (tid,)).fetchone()[0]
        for name, state, result in variants:
            c.execute('INSERT INTO tool_invocations(task_id,conversation_id,tool_call_id,tool_name,status,result) VALUES(%s,%s,%s,%s,%s,%s)', (tid, cid, name, name, state, Jsonb(result)))
        c.execute("INSERT INTO tool_audit_log(task_id,tool_name,status,tool_call_id) VALUES(%s,'uncertain','success','uncertain')", (str(tid),))
        c.execute("INSERT INTO tool_audit_log(task_id,tool_name,status,tool_call_id) VALUES(%s,'safe_read','success','audit-only')", (str(tid),))
    claimed = JournalStore(repo).claim(eid)
    assert {t['name'] for t in claimed['tools'] if t['status'] == 'success'} == {'legacy_ok', 'v1_ok', 'safe_read'}
    assert next(t for t in claimed['tools'] if t['name'] == 'uncertain')['status'] == 'uncertain'
    assert next(t for t in claimed['tools'] if t['name'] == 'safe_read')['source'] == 'audit'
    assert len(claimed['tools']) == len(variants) + 1
