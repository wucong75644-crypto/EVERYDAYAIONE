"""Real JWT/HTTP boundaries, fresh server authority and summary-only responses."""

from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.routes import skills
from core.config import get_settings
from core.exceptions import AppException
from core.security import create_access_token
from services.skills import available
from tests.test_skill_catalog import settings
from tests.test_skill_resolver import ACTOR, ORG, OTHER_ORG, candidate


@pytest.fixture
def api(monkeypatch, mock_db):
    conversation = uuid4()
    mock_db.set_table_data("users", [{"id": str(ACTOR), "status": "active"}])
    mock_db.set_table_data("conversations", [{"id": str(conversation), "user_id": str(ACTOR),
        "org_id": str(ORG), "scope_type": "user", "scope_id": str(ACTOR)}])
    mock_db.set_table_data("organizations", [{"id": str(ORG), "status": "active"}])
    mock_db.set_table_data("org_members", [{"org_id": str(ORG), "user_id": str(ACTOR), "status": "active"}])
    mock_db.pool = object()
    configured = settings(skill_catalog_enabled=True)
    app = FastAPI()
    app.include_router(skills.router, prefix="/api")
    app.dependency_overrides[get_settings] = lambda: configured

    @app.exception_handler(AppException)
    async def app_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    monkeypatch.setattr("core.security.get_settings", lambda: configured)
    database = Mock(return_value=mock_db)
    monkeypatch.setattr(skills, "get_db", database)
    repository = Mock()
    repository.catalog_candidates.return_value = [candidate()]
    factory = Mock(return_value=repository)
    monkeypatch.setattr(available, "SkillRepository", factory)
    checker = Mock()
    checker.check = AsyncMock(return_value=False)
    monkeypatch.setattr(available, "PermissionChecker", Mock(return_value=checker))
    # Any accidental call to the existing body loader fails this entire suite.
    monkeypatch.setattr("services.skills.storage.SkillStorage._read", Mock(side_effect=AssertionError("no body IO")))
    headers = {"Authorization": "Bearer " + create_access_token({"sub": str(ACTOR), "org_id": str(OTHER_ORG)})}
    return TestClient(app), configured, mock_db, conversation, headers, repository, factory, checker, database


def get(api, *, headers=None, **params):
    client, _, _, conversation, auth, *_ = api
    return client.get("/api/skills/available", params={"conversation_id": str(conversation)} | params,
                      headers=auth if headers is None else headers)


def test_authenticated_summary_response_and_server_derived_org(api):
    response = get(api, headers=api[4] | {"X-Org-Id": str(OTHER_ORG)})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == [{"name": "report", "revision": "v1", "description": "报表摘要",
                                "triggers": [], "source": "platform", "model_selectable": False}]
    scope = api[6].call_args.args[1]
    assert scope.org_id == str(ORG) and scope.actor_user_id == str(ACTOR)
    assert scope.access_kind.value == "projection"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_unauthenticated_rejected_even_when_disabled(api, headers):
    api[1].skill_catalog_enabled = False
    assert get(api, headers=headers).status_code == 401
    api[8].assert_not_called()


@pytest.mark.parametrize("param", ["org_id", "actor_user_id", "permissions", "skill_id", "skill_key",
                                  "agent_domain", "execution_mode", "conversation_scope", "skill_catalog_enabled"])
def test_client_cannot_supply_authority_or_skill_ids(api, param):
    assert get(api, **{param: "forged"}).status_code == 422
    api[8].assert_not_called()


def test_disabled_has_no_database_or_catalog_access(api):
    api[1].skill_catalog_enabled = False
    assert get(api).json() == []
    api[8].assert_not_called()
    api[6].assert_not_called()


def test_empty_catalog(api):
    api[5].catalog_candidates.return_value = []
    assert get(api).json() == []


@pytest.mark.parametrize("change", [
    {"user_id": str(uuid4())}, {"scope_type": "channel", "user_id": None},
    {"scope_id": str(uuid4())},
])
def test_foreign_or_channel_conversation_is_not_a_client_principal(api, change):
    api[2]._tables["conversations"]._data[0].update(change)
    assert get(api).status_code == 404
    api[6].assert_not_called()


def test_unknown_conversation_id_rejected(api):
    assert get(api, conversation_id=str(uuid4())).status_code == 404
    api[6].assert_not_called()


@pytest.mark.parametrize("table", ["users", "organizations", "org_members"])
def test_revoked_identity_or_membership_is_reread_on_next_request(api, table):
    assert get(api).status_code == 200
    api[2]._tables[table]._data[0]["status"] = "disabled"
    api[6].reset_mock()
    assert get(api).status_code == 403
    api[6].assert_not_called()


def test_permission_changes_take_effect_without_catalog_cache(api):
    api[5].catalog_candidates.return_value = [candidate(catalog_metadata={"required_permissions": ["order.view"]})]
    api[7].check.return_value = True
    assert len(get(api).json()) == 1
    api[7].check.assert_awaited_with(str(ACTOR), str(ORG), "order.view")
    api[7].check.return_value = False
    assert get(api).json() == []


def test_real_permission_checker_reads_current_assignment(api, monkeypatch):
    from services.permissions.checker import PermissionChecker
    monkeypatch.setattr(available, "PermissionChecker", PermissionChecker)
    db = api[2]
    position = str(uuid4())
    db.set_table_data("org_member_assignments", [{"user_id": str(ACTOR), "org_id": str(ORG),
        "is_primary": True, "position_id": position, "data_scope": "all"}])
    db.set_table_data("org_positions", [{"id": position, "code": "boss"}])
    api[5].catalog_candidates.return_value = [candidate(catalog_metadata={"required_permissions": ["order.view"]})]
    assert len(get(api).json()) == 1
    db.set_table_data("org_positions", [{"id": position, "code": "member"}])
    assert get(api).json() == []


def test_unknown_permission_is_not_allowed_even_for_boss(api):
    api[5].catalog_candidates.return_value = [candidate(catalog_metadata={"required_permissions": ["unknown.permission"]})]
    api[7].check.return_value = True
    assert get(api).json() == []
    api[7].check.assert_not_called()


def test_personal_conversation_has_no_implicit_platform_assignment(api):
    api[2]._tables["conversations"]._data[0]["org_id"] = None
    assert get(api).json() == []
    api[6].assert_not_called()


def test_web_domain_mode_and_feature_flags_are_server_owned(api):
    api[5].catalog_candidates.return_value = [
        candidate(catalog_metadata={"agent_domains": ["erp"]}),
        candidate(catalog_metadata={"execution_modes": ["scheduled"]}),
        candidate(catalog_metadata={"required_feature_flags": ["sandbox_enabled"]}),
    ]
    api[1].sandbox_enabled = False
    assert get(api).json() == []
    api[1].sandbox_enabled = True
    assert len(get(api).json()) == 1
