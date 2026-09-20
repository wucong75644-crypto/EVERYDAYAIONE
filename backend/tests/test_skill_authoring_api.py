"""JWT authentication and fresh organization administrator authorization."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from api.routes import skill_admin
from core.config import get_settings
from core.database import get_db
from core.exceptions import AppException
from core.security import create_access_token
from services.skills.contracts import SkillError
from tests.test_skill_catalog import settings


@pytest.fixture
def api(monkeypatch, mock_db):
    actor, org, foreign, pid = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
    mock_db.set_table_data('users', [{'id': actor, 'status': 'active', 'role': 'user'}])
    mock_db.set_table_data('organizations', [{'id': org, 'status': 'active'}, {'id': foreign, 'status': 'active'}])
    mock_db.set_table_data('org_members', [{'org_id': org, 'user_id': actor, 'status': 'active', 'role': 'admin'}])
    mock_db.pool = object()
    config = settings(skill_catalog_enabled=True)
    app = FastAPI()
    app.include_router(skill_admin.router, prefix='/api')
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_settings] = lambda: config
    @app.exception_handler(AppException)
    async def app_error(request, error):
        return JSONResponse(status_code=error.status_code, content={'code': error.code})
    monkeypatch.setattr('core.security.get_settings', lambda: config)
    service = Mock()
    service.list.return_value = []
    service.detail.return_value = {'editable': False, 'draft': None, 'revisions': []}
    service.create.return_value = {'package_id': pid}
    service.save.return_value = {'draft': {'status': 'draft'}}
    service.transition.return_value = {'draft': {'status': 'published'}}
    factory = Mock(return_value=service)
    monkeypatch.setattr(skill_admin, 'SkillAuthoring', factory)
    token = create_access_token({'sub': actor, 'org_id': foreign, 'role': 'super_admin'})
    return SimpleNamespace(client=TestClient(app), db=mock_db, config=config, service=service, factory=factory,
        org=org, foreign=foreign, pid=pid, actor=actor, base=f'/api/skills/admin/orgs/{org}',
        auth={'Authorization': 'Bearer ' + token})


def test_exact_target_org_is_validated_ignoring_jwt_org_and_header(api):
    response = api.client.get(api.base, headers=api.auth | {'X-Org-Id': api.foreign})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    scope = api.factory.call_args.args[0].scope
    assert scope.actor_user_id == api.actor and scope.org_id == api.org
    assert api.client.get(f'/api/skills/admin/orgs/{api.foreign}', headers=api.auth).status_code == 403


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer invalid'}])
def test_authentication_required(api, headers):
    assert api.client.get(api.base, headers=headers).status_code == 401
    api.factory.assert_not_called()


@pytest.mark.parametrize('change', ['member', 'inactive_member', 'inactive_org', 'inactive_user', 'nonmember_super_admin'])
def test_current_database_permissions_required(api, change):
    if change == 'member': api.db._tables['org_members']._data[0]['role'] = 'member'
    if change == 'inactive_member': api.db._tables['org_members']._data[0]['status'] = 'disabled'
    if change == 'inactive_org': api.db._tables['organizations']._data[0]['status'] = 'disabled'
    if change == 'inactive_user': api.db._tables['users']._data[0]['status'] = 'disabled'
    if change == 'nonmember_super_admin':
        api.db._tables['users']._data[0]['role'] = 'super_admin'
        api.db.set_table_data('org_members', [])
    assert api.client.post(api.base, json={'skill_key': 'orders'}, headers=api.auth).status_code == 403
    api.factory.assert_not_called()


def test_feature_disabled_does_not_construct_writer(api):
    api.config.skill_catalog_enabled = False
    assert api.client.get(api.base, headers=api.auth).status_code == 503
    api.factory.assert_not_called()


@pytest.mark.parametrize('field', ['org_id', 'scope_kind', 'nas_path', 'approved_by', 'revision', 'content_sha256'])
def test_client_cannot_supply_control_authority(api, field):
    response = api.client.post(api.base, json={'skill_key': 'orders', field: 'forged'}, headers=api.auth)
    assert response.status_code == 422
    api.service.create.assert_not_called()


def test_create_save_review_and_history_routes(api):
    assert api.client.post(api.base, json={'skill_key': 'orders'}, headers=api.auth).status_code == 201
    for verb, suffix, data in [
        ('put', '/draft', {'expected_version': 1, 'content': {'body': 'draft'}}),
        ('post', '/transitions', {'expected_version': 2, 'action': 'submit'}),
    ]:
        assert getattr(api.client, verb)(f'{api.base}/{api.pid}{suffix}', json=data, headers=api.auth).status_code == 200
    api.service.read_revision.return_value = {'description': 'original', 'body': 'original', 'catalog_metadata': {}}
    assert api.client.get(f'{api.base}/{api.pid}/revisions/v1', headers=api.auth).json()['body'] == 'original'


@pytest.mark.parametrize('code,status', [
    ('SKILL_OWNER_SCOPE_MISMATCH', 403), ('SKILL_PACKAGE_UNAVAILABLE', 404),
    ('SKILL_VERSION_CONFLICT', 409), ('SKILL_APPROVAL_REQUIRED', 422), ('SKILL_STORAGE_WRITE_REJECTED', 503),
])
def test_safe_failures(api, code, status):
    api.service.transition.side_effect = SkillError(code)
    response = api.client.post(f'{api.base}/{api.pid}/transitions',
        json={'expected_version': 3, 'action': 'publish'}, headers=api.auth)
    assert response.status_code == status
