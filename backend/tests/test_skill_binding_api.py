"""JWT scope, explicit writes and immutable revision selection."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.skills import bindings
from services.skills.binding_repository import SkillBindingRepository
from tests.test_skill_available_api import api  # noqa: F401
from tests.test_skill_resolver import ACTOR, ORG, OTHER_ORG, candidate


@pytest.fixture
def binding_api(api, monkeypatch):
    repository = Mock()
    repository.candidate = SkillBindingRepository.candidate
    c = candidate()
    binding_id = uuid4()
    repository.bindings.return_value = [c.model_dump() | {"id": binding_id, "available": True}]
    repository.catalog_candidates.return_value = [c]
    repository.add_binding.return_value = binding_id
    factory = Mock(return_value=repository)
    monkeypatch.setattr(bindings, "SkillBindingRepository", factory)
    checker = Mock(check=AsyncMock(return_value=True))
    monkeypatch.setattr(bindings, "PermissionChecker", lambda _: checker)
    return api, repository, factory, checker


def call(env, method="GET", **kwargs):
    api, *_ = env
    client, _, _, conversation, headers, *_ = api
    suffix = kwargs.pop("suffix", "")
    return client.request(method, f"/api/skills/conversations/{conversation}/bindings{suffix}",
                          headers=kwargs.pop("headers", headers), **kwargs)


def test_owner_reads_and_explicitly_adds_fixed_revision(binding_api):
    api, repo, factory, _ = binding_api
    assert call(binding_api).status_code == 200
    row = call(binding_api).json()[0]
    assert row["revision"] == "v1" and row["available"] is True
    assert not {"package_id", "catalog_metadata", "created_by", "nas_path", "body"} & row.keys()
    response = call(binding_api, "POST", json={"skill_id": "report", "revision": "v1"})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    scope = factory.call_args.args[1]
    assert scope.org_id == str(ORG) and scope.actor_user_id == str(ACTOR)
    repo.add_binding.assert_called_once_with(api[3], repo.catalog_candidates.return_value[0])
    assert call(binding_api, "DELETE", suffix="/" + response.json()["binding_id"]).status_code == 204
    repo.remove_binding.assert_called_once()


@pytest.mark.parametrize("field,value", [("scope", "session"), ("org_id", str(OTHER_ORG)),
                                        ("source", "model"), ("allowed_tool_names", ["file_delete"]),
                                        ("created_by", str(ACTOR)), ("body", "instructions")])
def test_add_contract_rejects_forged_authority(binding_api, field, value):
    response = call(binding_api, "POST", json={"skill_id": "report", "revision": "v1", field: value})
    assert response.status_code == 422
    binding_api[1].add_binding.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_jwt_required_for_all_operations(binding_api, method):
    kwargs = {"json": {"skill_id": "report", "revision": "v1"}} if method == "POST" else {}
    if method == "DELETE":
        kwargs["suffix"] = "/" + str(uuid4())
    assert call(binding_api, method, headers={}, **kwargs).status_code == 401
    binding_api[2].assert_not_called()


@pytest.mark.parametrize("role,expected", [("member", 403), ("admin", 201), ("owner", 201)])
def test_only_current_org_admin_can_configure_another_users_conversation(binding_api, role, expected):
    api, _, _, checker = binding_api
    owner = str(uuid4())
    api[2]._tables["conversations"]._data[0].update(user_id=owner, scope_id=owner)
    api[2]._tables["org_members"]._data[0]["role"] = role
    binding_api[1].catalog_candidates.return_value = [
        candidate(catalog_metadata={"required_permissions": ["order.view"]}),
    ]
    assert call(binding_api, "POST", json={"skill_id": "report", "revision": "v1"}).status_code == expected
    if expected == 201:
        checker.check.assert_awaited_with(owner, str(ORG), "order.view")


@pytest.mark.parametrize("table", ["users", "org_members", "organizations"])
def test_identity_revocation_is_checked_on_every_write(binding_api, table):
    api, repo, *_ = binding_api
    api[2]._tables[table]._data[0]["status"] = "disabled"
    assert call(binding_api, "POST", json={"skill_id": "report", "revision": "v1"}).status_code == 403
    assert call(binding_api, "DELETE", suffix="/" + str(uuid4())).status_code == 403
    repo.add_binding.assert_not_called()
    repo.remove_binding.assert_not_called()


def test_stale_revision_is_rejected_and_revoked_binding_remains_removable(binding_api):
    _, repo, _, checker = binding_api
    repo.catalog_candidates.return_value = [candidate(revision="v2")]
    assert call(binding_api, "POST", json={"skill_id": "report", "revision": "v1"}).status_code == 409
    repo.add_binding.assert_not_called()
    c = candidate(catalog_metadata={"required_permissions": ["order.view"]})
    repo.bindings.return_value = [c.model_dump() | {"id": uuid4(), "available": True}]
    checker.check.return_value = False
    response = call(binding_api)
    assert response.json()[0]["available"] is False
    assert response.json()[0]["revision"] == "v1"
    assert call(binding_api, "DELETE", suffix="/" + response.json()[0]["binding_id"]).status_code == 204


@pytest.mark.parametrize("change", [
    {"org_id": str(OTHER_ORG)}, {"scope_type": "channel", "user_id": None}, {"scope_id": str(uuid4())},
])
def test_other_org_and_untrusted_channel_scope_rejected(binding_api, change):
    binding_api[0][2]._tables["conversations"]._data[0].update(change)
    assert call(binding_api).status_code in (403, 404)
    binding_api[2].assert_not_called()


def test_disabled_feature_cannot_write_or_open_database(binding_api):
    binding_api[0][1].skill_catalog_enabled = False
    assert call(binding_api, "POST", json={"skill_id": "report", "revision": "v1"}).status_code == 503
    binding_api[0][8].assert_not_called()
