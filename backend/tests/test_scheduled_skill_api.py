"""HTTP inputs contain explicit intent only; all snapshots come from the server."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from services.skills.contracts import SkillError
from tests.test_scheduled_tasks_routes import FakeDB, _build_app


def payload():
    return {'name': 'test', 'prompt': 'test', 'schedule_type': 'daily', 'time_str': '09:00',
        'push_target': {'type': 'web', 'user_id': 'user_1'},
        'skills': [{'skill_id': 'report', 'revision': 'v1'}]}


def test_legacy_draft_creation_forwards_explicit_revision(monkeypatch):
    preflight = AsyncMock(return_value={'id': 'draft'})
    monkeypatch.setattr('services.scheduler.scheduled_task_workflow.create_draft_and_preflight', preflight)
    monkeypatch.setattr('api.routes.scheduled_tasks.check_permission', AsyncMock(return_value=True))
    response = TestClient(_build_app(FakeDB())).post('/api/scheduled-tasks/drafts', json=payload())
    assert response.status_code == 200
    assert preflight.await_args.kwargs['definition']['skills'] == payload()['skills']


@pytest.mark.parametrize('extra', ['body', 'allowed_tool_names', 'org_id'])
def test_client_cannot_smuggle_authority_into_skill_selection(monkeypatch, extra):
    monkeypatch.setattr('api.routes.scheduled_tasks.check_permission', AsyncMock(return_value=True))
    body = payload()
    body['skills'][0][extra] = 'forged'
    response = TestClient(_build_app(FakeDB())).post('/api/scheduled-tasks/drafts', json=body)
    assert response.status_code == 422


@pytest.mark.parametrize('editing', [False, True])
def test_legacy_binding_failure_is_an_actionable_4xx(monkeypatch, editing):
    monkeypatch.setattr('services.scheduler.scheduled_task_workflow.create_draft_and_preflight',
        AsyncMock(side_effect=SkillError('SKILL_SCHEDULED_SELECTION_UNAVAILABLE')))
    monkeypatch.setattr('api.routes.scheduled_tasks.check_permission', AsyncMock(return_value=True))
    db = FakeDB()
    if editing:
        db.add('scheduled_tasks', {**payload(), 'id': 'task', 'user_id': 'owner', 'org_id': 'org_1'})
    client = TestClient(_build_app(db))
    response = client.patch('/api/scheduled-tasks/task', json=payload()) if editing else client.post('/api/scheduled-tasks/drafts', json=payload())
    assert response.status_code == 422
    assert 'SKILL_SCHEDULED_SELECTION_UNAVAILABLE' in response.text


def test_admin_options_are_resolved_using_task_owner_not_editor(monkeypatch):
    resolver = AsyncMock(return_value=[])
    monkeypatch.setattr('services.skills.scheduled.scheduled_skill_options', resolver)
    monkeypatch.setattr('api.routes.scheduled_tasks.check_permission', AsyncMock(return_value=True))
    db = FakeDB()
    db.add('scheduled_tasks', {'id': 'task', 'user_id': 'owner', 'org_id': 'org_1'})
    response = TestClient(_build_app(db)).get('/api/scheduled-tasks/skill-options?task_id=task')
    assert response.status_code == 200
    assert resolver.await_args.kwargs == {'owner': 'owner', 'org': 'org_1', 'task_id': 'task'}
