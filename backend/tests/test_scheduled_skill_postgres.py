"""Real PostgreSQL/NAS publication, immutable task revisions, claims and retries."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import psycopg
import pytest

from services.skills.authoring_contracts import CreateSkill, DraftContent, SaveDraft, reviewed_document
from services.skills.contracts import SkillError, PackageCreate
from services.skills.scheduled import bind_scheduled_skills, validate_scheduled_skills, source_for_executor
from services.skills.runtime import SkillRuntime, SkillReplayError
from services.agent.tool_executor import ToolExecutor
from tests.test_skill_authoring_postgres import environment, postgres_socket, action, publish, MIGRATIONS  # noqa: F401
from tests.test_scheduled_task_upgrade_postgres import claim, start, finish, taskrow


@pytest.fixture
def configured(environment, monkeypatch):
    env = environment
    with env.pool.connection(privileged=True) as conn:
        conn.execute("CREATE TABLE users(id uuid PRIMARY KEY)")
        conn.execute("INSERT INTO users VALUES (%s)", (env.actor,))
        for name in ('069_scheduled_tasks.sql', '071_scheduled_task_schedule_type.sql',
                     '242_scheduled_task_delivery_outbox.sql', '244_scheduled_task_preflight_workflow.sql',
                     '245_scheduled_task_lifecycle_integrity.sql', '249_scheduled_task_changeset_adapter.sql',
                     '255_scheduled_task_schedule_intent.sql', '264_scheduled_skill_snapshots.sql'):
            conn.execute((MIGRATIONS / name).read_text())
    env.config = env.config.model_copy(update={'skill_runtime_enabled': True, 'file_workspace_enabled': True})
    monkeypatch.setattr('core.config.get_settings', lambda: env.config)
    env.permissions = AsyncMock(return_value=True)
    monkeypatch.setattr('services.permissions.checker.PermissionChecker.check', env.permissions)
    env.db = MagicMock(pool=env.pool)
    env.db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(data={'status': 'active'})
    # org_members has two filters.
    env.db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(data={'status': 'active'})
    env.content = DraftContent(description='scheduled method', body='Use the locked method.\n', catalog_metadata={
        'execution_modes': ['scheduled'], 'allowed_tool_names': ['file_search'], 'required_permissions': ['task.execute']})
    svc = env.service()
    env.pid = svc.create(CreateSkill(skill_key='orders', content=env.content))['package_id']
    env.revision = publish(svc, env.pid)['draft']['revision']
    env.task = uuid4()
    return env


async def bind(env, revision=None, **kwargs):
    return await bind_scheduled_skills(env.db, owner=str(env.actor), org=str(env.org), task_id=str(env.task),
        selections=[{'skill_id': 'orders', 'revision': revision or env.revision}], **kwargs)


async def check(env, pins):
    await validate_scheduled_skills(env.db, owner=str(env.actor), org=str(env.org), task_id=str(env.task),
        snapshot=pins, policy={'version': 1, 'allowed_tools': ['file_search']})


def seed(conn, env, pins):
    return conn.execute("""INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,schedule_type,
        push_target,next_run_at,execution_policy,skill_revision_snapshot)
        VALUES(%s,%s,%s,'test','original instruction','0 9 * * *','daily','{}',NOW()-INTERVAL '1 minute',
        '{"version":1,"allowed_tools":["file_search"]}',%s) RETURNING config_revision""",
        (env.task, env.org, env.actor, json.dumps(pins))).fetchone()[0]


async def test_reviewed_revision_is_explicit_not_latest_and_deprecated_only_works_for_existing_pins(configured):
    env = configured
    pins = await bind(env)
    svc = env.service()
    action(svc, env.pid, 'start_draft')
    version = svc.detail(env.pid)['draft']['version']
    svc.save(env.pid, SaveDraft(expected_version=version, content=env.content.model_copy(update={'body': 'Different method.\n'})))
    v2 = publish(svc, env.pid)['draft']['revision']
    assert (await bind(env)) == pins  # explicit v1 still published, despite new assignment
    assert (await bind(env, v2))['skills'][0]['candidate']['revision'] == v2
    await check(env, pins)
    action(svc, env.pid, 'deprecate')
    await check(env, pins)
    with pytest.raises(SkillError, match='SELECTION_UNAVAILABLE'):
        await bind(env)
    revision = svc.repository.assigned_revision(env.pid, env.revision, restoring=True)
    svc.repository.set_assignment(env.pid, revision.id, enabled=False)
    with pytest.raises(SkillError, match='UNAVAILABLE'):
        await check(env, pins)


@pytest.mark.parametrize('change', ['permission', 'organization', 'hash', 'assignment', 'retired'])
async def test_permission_identity_hash_and_status_changes_stop_execution(configured, change):
    env = configured
    pins = await bind(env)
    if change == 'permission':
        env.permissions.return_value = False
    elif change == 'organization':
        pins['skills'][0]['candidate']['assignment_org_id'] = str(env.other)
    elif change == 'hash':
        pins['skills'][0]['body_sha256'] = '0' * 64
    elif change == 'assignment':
        revision = env.service().repository.assigned_revision(env.pid, env.revision)
        env.service().repository.set_assignment(env.pid, revision.id, enabled=False)
    else:
        revision = env.service().repository.assigned_revision(env.pid, env.revision)
        env.service().repository.retire_revision(env.pid, revision.id)
    with pytest.raises(SkillError):
        await check(env, pins)


async def test_dangerous_tool_and_non_scheduled_method_rejected_at_configuration(configured):
    env = configured
    svc = env.service()
    for metadata in ({'execution_modes': ['scheduled'], 'allowed_tool_names': ['file_delete']},
                     {'execution_modes': ['interactive'], 'allowed_tool_names': ['file_search']}):
        action(svc, env.pid, 'start_draft')
        version = svc.detail(env.pid)['draft']['version']
        svc.save(env.pid, SaveDraft(expected_version=version, content=DraftContent(
            description='method', body='method', catalog_metadata=metadata)))
        version = publish(svc, env.pid)['draft']['revision']
        with pytest.raises(SkillError, match='TOOL_DENIED|SELECTION_UNAVAILABLE'):
            await bind(env, version)


async def test_delayed_claim_retry_and_history_keep_original_config_after_edit(configured):
    env = configured
    pins = await bind(env)
    with env.pool.connection(privileged=True) as conn:
        original = seed(conn, env, pins)
        claimed = claim(conn, env.task, env.org)
        assert claimed['run_config_revision'] == str(original)
        # A delayed worker uses the claim, even if a trusted writer changes the definition.
        conn.execute("UPDATE scheduled_tasks SET prompt='future instruction',skill_revision_snapshot='{" + '"version":1,"skills":[]' + "}' WHERE id=%s", (env.task,))
        newer = taskrow(conn, env.task)['config_revision']
        assert newer != str(original)
        started = start(conn, env.task, env.org, claimed['run_token'])
        assert started['definition_snapshot']['prompt'] == 'original instruction'
        assert started['definition_snapshot']['skill_revision_snapshot'] == pins
        assert start(conn, env.task, env.org, claimed['run_token'])['outcome'] == 'already_started'
        conn.execute("SELECT finish_scheduled_task_failure(%s,%s,%s,%s,'retry',0,1)",
            (env.task, env.org, claimed['run_token'], json.dumps({'status': 'active', 'next_run_at': '2030-01-01', 'retry_scheduled': True})))
        assert taskrow(conn, env.task)['retry_config_revision'] == str(original)
        retried = claim(conn, env.task, env.org, manual=True)
        resumed = start(conn, env.task, env.org, retried['run_token'])
        assert resumed['config_revision'] == str(original)
        assert resumed['definition_snapshot'] == started['definition_snapshot']
        finish(conn, env.task, env.org, retried['run_token'], success=True)
        assert taskrow(conn, env.task)['retry_config_revision'] is None
        following = claim(conn, env.task, env.org, manual=True)
        assert start(conn, env.task, env.org, following['run_token'])['config_revision'] == newer
        with pytest.raises(psycopg.errors.CheckViolation, match='IMMUTABLE'):
            with conn.transaction():
                conn.execute("UPDATE scheduled_task_runs SET skill_revision_snapshot='{}'")
        with pytest.raises(psycopg.errors.CheckViolation, match='IMMUTABLE'):
            with conn.transaction():
                conn.execute("UPDATE scheduled_task_config_revisions SET definition_snapshot='{}'")


async def test_actor_recovery_rechecks_real_review_and_pinned_body(configured):
    env = configured
    pins = await bind(env)
    executor = ToolExecutor(db=env.db, user_id=str(env.actor), org_id=str(env.org), conversation_id=None,
        task_id=str(env.task), execution_mode='scheduled', allowed_tool_names={'file_search'},
        tool_policy_snapshot={'version': 1, 'allowed_tools': ['file_search']})
    def runtime():
        return SkillRuntime(turn_id='turn', source=source_for_executor(executor, env.config, pins),
            execution_mode='scheduled', platform_tool_names={'file_search','file_delete'},
            authorized_tool_names={'file_search'}, cancellation_event=asyncio.Event())
    first = runtime()
    await first.initialize_scheduled(pins)
    saved = first.checkpoint()
    action(env.service(), env.pid, 'deprecate')
    recovered = runtime()
    await recovered.initialize_scheduled(pins, saved)
    assert recovered.checkpoint() == saved
    revision = env.service().repository.assigned_revision(env.pid, env.revision, restoring=True)
    path = env.root / revision.nas_path
    path.chmod(0o644)
    path.write_text(path.read_text() + 'drift')
    with pytest.raises(SkillReplayError):
        await runtime().initialize_scheduled(pins, saved)


def test_empty_upgrade_and_guarded_rollback(configured):
    env = configured
    with env.pool.connection(privileged=True) as conn:
        original = seed(conn, env, {'version': 1, 'skills': []})
        assert original
        conn.execute((MIGRATIONS / 'rollback/264_scheduled_skill_snapshots_rollback.sql').read_text())
        assert conn.execute('SELECT count(*) FROM scheduled_tasks').fetchone()[0] == 1
        conn.execute((MIGRATIONS / '264_scheduled_skill_snapshots.sql').read_text())
        assert taskrow(conn, env.task)['revision'] == 0


async def test_rollback_refuses_to_erase_bound_history(configured):
    env = configured
    pins = await bind(env)
    with env.pool.connection(privileged=True) as conn:
        seed(conn, env, pins)
        with pytest.raises(psycopg.errors.RaiseException, match='EMPTY_HISTORY'):
            with conn.transaction():
                conn.execute((MIGRATIONS / 'rollback/264_scheduled_skill_snapshots_rollback.sql').read_text())
        assert taskrow(conn, env.task)['skill_revision_snapshot'] == pins


async def test_unreviewed_legacy_publication_and_forged_review_flag_are_rejected(configured):
    env = configured
    svc = env.service()
    package = svc.repository.create_package(PackageCreate(skill_key='legacy', source='test', scope_kind='org', org_id=env.org))
    publication, raw = reviewed_document(package, 'v1', env.content)
    validated = svc._storage().publish(package, publication, raw)
    revision = svc.repository.publish_revision(package.id, validated)
    assert revision.reviewed is False
    svc.repository.set_assignment(package.id, revision.id)
    with pytest.raises(SkillError, match='SELECTION_UNAVAILABLE'):
        await bind_scheduled_skills(env.db, owner=str(env.actor), org=str(env.org), task_id=str(env.task),
            selections=[{'skill_id': 'legacy', 'revision': 'v1'}])
    with env.pool.connection(privileged=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match='IMMUTABLE'):
            with conn.transaction():
                conn.execute('UPDATE skill_revisions SET reviewed=TRUE WHERE id=%s', (revision.id,))


async def test_changeset_rpc_creates_new_config_without_rewriting_old_snapshot(configured):
    env = configured
    pins = await bind(env)
    definition = {'name': 'test', 'prompt': 'test', 'schedule_type': 'daily', 'cron_expr': '0 9 * * *',
        'push_target': {'type': 'web', 'user_id': str(env.actor)}, 'next_run_at': '2030-01-01T00:00:00Z',
        'execution_policy': {'version': 1, 'allowed_tools': ['file_search']}, 'skill_revision_snapshot': pins}
    with env.pool.connection(privileged=True) as conn:
        def commit(operation, base, value, key):
            return conn.execute('SELECT commit_scheduled_task_changeset(%s,%s,%s,%s,%s,%s,%s,%s)',
                (uuid4(), env.org, env.actor, env.task, operation, base, json.dumps(value), key)).fetchone()[0]
        created = commit('create', 0, definition, 'create')
        assert created['outcome'] == 'created'
        original = created['task']['config_revision']
        assert created['task']['skill_revision_snapshot'] == pins
        changed = {**definition, 'skill_revision_snapshot': {'version': 1, 'skills': []}}
        updated = commit('update', created['new_revision'], changed, 'update')
        assert updated['outcome'] == 'updated' and updated['task']['config_revision'] != original
        assert commit('update', created['new_revision'], changed, 'update')['outcome'] == 'duplicate'
        old = conn.execute('SELECT definition_snapshot FROM scheduled_task_config_revisions WHERE id=%s', (original,)).fetchone()[0]
        assert old['skill_revision_snapshot'] == pins
        assert conn.execute('SELECT count(*) FROM scheduled_task_config_revisions WHERE task_id=%s', (env.task,)).fetchone()[0] == 2


async def test_headless_agent_injects_pinned_method_and_limits_advertised_and_executed_tools(configured, monkeypatch):
    env = configured
    pins = await bind(env)
    from services.agent.scheduled_task_agent import ScheduledTaskAgent
    gateway = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr('services.model_gateway.get_model_gateway', lambda: SimpleNamespace(open_chat=lambda request: gateway))
    loop = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        text='complete', total_tokens=5, turns=1, emit_payloads=[], stop_reason='', is_llm_synthesis=True,
        tools_called=['file_search'], tool_outcomes=[{'tool_name': 'file_search', 'status': 'success'}])))
    captured = []
    def build_loop(self, model, executor, tools, timeout):
        captured.append(executor)
        assert [t['function']['name'] for t in tools] == ['file_search']
        return loop, SimpleNamespace()
    monkeypatch.setattr(ScheduledTaskAgent, '_build_tool_loop', build_loop)
    task = {'id': str(env.task), 'org_id': str(env.org), 'user_id': str(env.actor),
        'prompt': 'run method', 'skill_revision_snapshot': pins,
        'execution_policy': {'version': 1, 'allowed_tools': ['file_search', 'web_search'], 'required_tools': ['file_search']}}
    agent = ScheduledTaskAgent(env.db, task)
    result = await agent.execute()
    assert result.status == 'success', result.error_message
    assert env.content.body.strip() in str(loop.run.await_args.kwargs['messages'])
    executor = captured[0]
    assert executor.allowed_tool_names == {'file_search'}
    assert executor.tool_policy_snapshot['required_permissions'] == ['task.execute']
    denied = await executor.tool_runtime.execute('activate_skill', {'skill_id': 'orders'})
    assert denied.decision.outcome == 'deny'
    assert denied.decision.reason == 'SKILL_SCHEDULED_ACTIVATION_FORBIDDEN'
    env.permissions.return_value = False
    denied = await executor.tool_runtime.execute('file_search', {'query': 'test'})
    assert denied.decision.outcome == 'deny'
