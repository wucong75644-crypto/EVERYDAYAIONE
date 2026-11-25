"""Preparation latency mechanisms, authority freshness and scoped diagnostics."""
import asyncio
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.permissions.checker import PermissionChecker
from services.permissions.permission_points import PERMISSIONS
from services.skills.observability import measure_skill, skill_span
from services.skills.retry import PinnedIntentSource, SkillIntent, capture_intent
from services.skills.runtime import SkillBindingError
from services.skills.selection import SkillSelection
from services.agent.observability.chat_lifecycle import capture_chat_lifecycle, chat_lifecycle_scope, chat_phase
from tests.test_skill_runtime import Source, item, state
from tests.test_skill_resolver import ACTOR, ORG, context
from tests.test_skill_retry_intent import pinned_source
from tests.test_skill_manual_selection import execution, SELECTION  # noqa: F401
from tests.test_chat_execution_engine import _request
from tests.test_skill_runtime_actor import actor, handler
from services.handlers.chat.execution_engine import execute_chat
from services.handlers.chat.execution_sink import CollectingExecutionSink


@pytest.mark.parametrize('role,department', [('boss', 'ops'), ('vp', 'ops'), ('manager', 'ops'),
                                          ('manager', 'hr'), ('member', 'ops'), ('deputy', 'finance'), ('none', 'ops')])
async def test_batched_permissions_equal_individual_checks_with_one_fresh_read(role, department):
    checker = PermissionChecker(Mock())
    checker._get_assignment = AsyncMock(return_value={
        'position_code': role, 'department_type': department, 'data_scope': 'all'})
    expected = {code for code in PERMISSIONS if await checker.check('u', 'o', code)}
    checker._get_assignment.reset_mock()
    assert await checker.check_many('u', 'o', PERMISSIONS) == expected
    checker._get_assignment.assert_awaited_once_with('u', 'o')
    checker._get_assignment.return_value = None
    assert await checker.check_many('u', 'o', PERMISSIONS) == frozenset()


async def test_empty_and_personal_permission_batches_never_query_db():
    checker = PermissionChecker(Mock())
    checker._get_assignment = AsyncMock(side_effect=AssertionError('no DB'))
    assert await checker.check_many('u', 'o', ()) == frozenset()
    assert await checker.check_many('u', None, PERMISSIONS) == frozenset()
    checker._get_assignment.assert_not_awaited()


async def test_slow_assignment_reads_do_not_block_the_event_loop():
    started, release = threading.Event(), threading.Event()
    checker = PermissionChecker(Mock())
    def load(*_):
        started.set()
        assert release.wait(2), 'event loop could not release synchronous DB worker'
        return {'position_code': 'boss'}
    checker._load_assignment = load
    task = asyncio.create_task(checker.check_many('u', 'o', ['order.view']))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        assert not task.done()
        release.set()
        assert await task == {'order.view'}
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_simulated_db_benchmark_reports_query_reduction_without_latency_threshold():
    class DB:
        def __init__(self):
            self.table_name = None
            self.queries = 0
        def table(self, name):
            self.table_name = name
            return self
        def __getattr__(self, _):
            return lambda *a, **kw: self
        def execute(self):
            self.queries += 1
            time.sleep(.003)  # Controlled IO fixture, not a production measurement.
            rows = {'org_member_assignments': [{'position_id': 'p', 'department_id': 'd'}],
                    'org_positions': {'code': 'boss'}, 'org_departments': {'type': 'ops', 'name': 'ops'}}
            return SimpleNamespace(data=rows[self.table_name])
    codes = list(PERMISSIONS)[:8]
    db = DB()
    checker = PermissionChecker(db)
    start = time.monotonic()
    sequential = {code for code in codes if await checker.check('u', 'o', code)}
    before = (time.monotonic() - start) * 1000
    old_queries = db.queries
    db.queries = 0
    start = time.monotonic()
    batched = await checker.check_many('u', 'o', codes)
    after = (time.monotonic() - start) * 1000
    assert batched == sequential
    assert old_queries == 24 and db.queries == 3
    print('\nSKILL_PREPARATION_BENCHMARK ' + json.dumps({
        'fixture': '8 permissions, 3ms simulated IO per query',
        'before_queries': old_queries, 'after_queries': db.queries,
        'before_ms': round(before, 3), 'after_ms': round(after, 3)}))


async def test_explicit_only_runtime_does_not_scan_unrelated_skills():
    source = Source([item(), item('other')])
    runtime = state(source)
    await runtime.initialize(selection=SELECTION, discover_catalog=False)
    source.discover.assert_not_awaited()
    source.discover_selection.assert_awaited_once_with(SELECTION)
    assert set(runtime.directory) == {'report'}
    assert (await runtime.activate_manual(SELECTION))['ok']


async def test_capture_manual_selection_uses_filtered_current_catalog(monkeypatch):
    from core.config import get_settings
    configured = get_settings().model_copy(update={'skill_catalog_enabled': True})
    monkeypatch.setattr('core.config.get_settings', lambda: configured)
    source = Source([item()])
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', lambda *a, **kw: source)
    intent = await capture_intent(SimpleNamespace(org_id=str(ORG)), user_id=str(ACTOR),
        conversation_id='conv', task_mode='smart', selection=SELECTION)
    assert intent.package_ids['report'] == source.candidates[0].package_id
    source.discover.assert_not_awaited()
    source.discover_selection.assert_awaited_once_with(SELECTION)


async def test_first_queued_attempt_keeps_captured_revision_and_dynamic_catalog():
    source, _, intent = pinned_source()
    other = item('other')
    source.discover.return_value = [item(revision='v2'), other]
    runtime = state(PinnedIntentSource(source, intent, retry=False))
    await runtime.initialize(selection=intent.selected_skill)
    assert runtime.directory['report'].revision == 'v1'
    assert 'other' in runtime.directory
    assert (await runtime.activate_manual(intent.selected_skill))['ok']
    source.repository.pinned_candidates.return_value = []
    with pytest.raises(SkillBindingError):
        await state(PinnedIntentSource(source, intent, retry=False)).initialize(selection=intent.selected_skill)


async def test_pinned_package_never_switches_to_new_same_named_source():
    source, _, intent = pinned_source()
    original = source.repository.pinned_candidates.return_value[0]
    foreign = original.model_copy(update={'package_id': item('different').package_id})
    intent = intent.model_copy(update={'package_ids': {'report': original.package_id}})
    source.repository.pinned_candidates.return_value = [foreign]
    with pytest.raises(SkillBindingError):
        await state(PinnedIntentSource(source, intent)).initialize(selection=intent.selected_skill)
    source.load.assert_not_awaited()


@pytest.mark.parametrize('selection,fixed', [(SELECTION, False), (None, True)])
async def test_explicit_methods_skip_optional_recommendation_io(execution, monkeypatch, selection, fixed):
    source, model, configured = execution
    configured.skill_recommendations_enabled = True
    if fixed:
        source.session_bindings.return_value = source.candidates
    recommendations = AsyncMock(side_effect=AssertionError('recommendation cannot delay explicit selection'))
    monkeypatch.setattr('services.skills.recommendation_service.model_recommendations', recommendations)
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=selection), runtime=actor())
    recommendations.assert_not_awaited()
    model.assert_awaited_once()
    assert result.parts


async def test_stream_acknowledges_before_skill_discovery_but_not_activation_success(execution):
    source, _, _ = execution
    sink = CollectingExecutionSink()
    sink.start = AsyncMock()
    async def discover():
        sink.start.assert_awaited_once()
        assert not sink.blocks
        return source.candidates
    source.discover.side_effect = discover
    await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor(), sink=sink)
    sink.start.assert_awaited_once()


async def test_skill_diagnostics_are_scoped_bounded_and_do_not_expose_content():
    rows = []
    source = Source(body='private content, not logged')
    runtime = state(source)
    with capture_chat_lifecycle(rows), chat_lifecycle_scope('task', turn_id='turn-1', attempt=2):
        await runtime.initialize(selection=SELECTION)
        assert (await runtime.activate_manual(SELECTION))['ok']
        runtime.model_messages([{'role': 'user', 'content': 'private input'}], [])
        chat_phase('skill.context', metrics={'body': 'SECRET', 'rendered_bytes': 42, 'tool_count': -1})
    serialized = json.dumps(rows)
    assert all(secret not in serialized for secret in ['private content', 'private input', 'SECRET'])
    assert all(row['task_id'] == 'task' and row['attempt'] == 2 for row in rows)
    size = next(row for row in rows if row['phase'] == 'skill.context')
    assert size['metrics']['rendered_bytes'] == len(source.body.encode())
    assert rows[-1]['metrics'] == {'rendered_bytes': 42}


async def test_skill_diagnostic_refusal_error_and_cancel_preserve_semantics():
    rows = []
    @measure_skill('activate')
    async def refuse():
        return {'ok': False, 'code': 'SAFE'}
    with capture_chat_lifecycle(rows):
        assert not (await refuse())['ok']
        with pytest.raises(RuntimeError, match='unchanged'):
            with skill_span('body'):
                raise RuntimeError('unchanged')
        with pytest.raises(asyncio.CancelledError):
            with skill_span('assets'):
                raise asyncio.CancelledError
    ends = [row for row in rows if row['phase'].endswith('.end')]
    assert all(not row['metrics']['ok'] for row in ends)
    assert ends[-1]['metrics']['cancelled']


async def test_unchanged_captured_selection_does_not_add_an_extra_pin_query():
    source = Source()
    selected = source.candidates[0]
    source.repository = SimpleNamespace(pinned_candidates=Mock(side_effect=AssertionError('unneeded DB')))
    intent = SkillIntent(task_mode='smart', selected_skill=SELECTION,
                         package_ids={'report': selected.package_id})
    pinned = PinnedIntentSource(source, intent)
    assert await pinned.discover() == source.candidates
    source.repository.pinned_candidates.assert_not_called()


async def test_stale_capture_is_rejected_before_enqueue(monkeypatch):
    from fastapi import HTTPException
    from core.config import get_settings
    configured = get_settings().model_copy(update={'skill_catalog_enabled': True})
    monkeypatch.setattr('core.config.get_settings', lambda: configured)
    source = Source([item(revision='v2')])
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', lambda *a, **kw: source)
    with pytest.raises(HTTPException) as error:
        await capture_intent(SimpleNamespace(org_id=str(ORG)), user_id=str(ACTOR),
            conversation_id='conv', task_mode='smart', selection=SELECTION)
    assert error.value.status_code == 409
    source.load.assert_not_awaited()


async def test_pre_enqueue_diagnostics_inherit_turn_without_inventing_task_id():
    rows = []
    with capture_chat_lifecycle(rows):
        with skill_span('intent', turn_id='pre-enqueue-turn'):
            with skill_span('permissions'):
                pass
        with skill_span('permissions'):
            pass
    assert all(row['turn_id'] == 'pre-enqueue-turn' and row['task_id'] is None for row in rows[:4])
    assert all(row['turn_id'] is None for row in rows[4:])

