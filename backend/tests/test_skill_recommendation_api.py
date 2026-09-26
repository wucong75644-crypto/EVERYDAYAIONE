"""HTTP authentication, strict fact inputs, optional failure and feedback ownership."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.skills import recommendation_service as service
from services.skills import runtime_source
from tests.test_skill_available_api import api  # noqa: F401
from tests.test_skill_resolver import context, candidate, OTHER_ORG


@pytest.fixture
def rec_api(api, monkeypatch):
    api[1].skill_recommendations_enabled = True
    source = Mock()
    source.settings = api[1]
    source.handler.db = api[2]
    source.context.conversation_id = str(api[3])
    source.discover = AsyncMock(return_value=[candidate()])
    source.session_bindings = AsyncMock(return_value=[])
    source._resolution_context = AsyncMock(return_value=context(enabled_feature_flags={
        "skill_catalog_enabled", "skill_recommendations_enabled"}))
    factory = Mock(return_value=source)
    monkeypatch.setattr(runtime_source, "ActorSkillSource", factory)
    monkeypatch.setattr(service, "available_capabilities", AsyncMock(return_value={"file_search"}))
    repository = Mock()
    repository.record.return_value = uuid4()
    repository.feedback.return_value = True
    monkeypatch.setattr(service, "SkillRecommendationRepository", Mock(return_value=repository))
    return api, source, factory, repository


def post(rec_api, **data):
    api = rec_api[0]
    return api[0].post("/api/skills/recommendations", headers=api[4],
                       json={"conversation_id": str(api[3]), **data})


def test_response_and_audit_only_typed_facts(rec_api):
    response = post(rec_api, selected_file_types=["pdf"])
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["candidates"][0]["skill_id"] == "report"
    assert "body" not in response.text and "nas_path" not in response.text
    f = rec_api[3].record.call_args.args[3]
    assert f.selected_file_types == {"pdf"}
    assert f.available_tool_names == {"file_search"}


@pytest.mark.parametrize("field", ["org_id", "permissions", "available_tool_names", "model_output", "body",
                                  "uploaded_content", "web_content", "execution_mode", "agent_domain", "session_bindings"])
def test_untrusted_or_forged_fields_rejected(rec_api, field):
    assert post(rec_api, **{field: "forged"}).status_code == 422
    rec_api[0][8].assert_not_called()


def test_invalid_file_type_rejected(rec_api):
    assert post(rec_api, selected_file_types=["pdf; publish"]).status_code == 422


@pytest.mark.parametrize("flag", ["skill_catalog_enabled", "skill_recommendations_enabled"])
def test_flag_off_no_database_or_discovery(rec_api, flag):
    setattr(rec_api[0][1], flag, False)
    response = post(rec_api)
    assert response.json() == {"status": "disabled", "recommendation_id": None, "candidates": []}
    rec_api[0][8].assert_not_called()
    rec_api[2].assert_not_called()
    rec_api[3].record.assert_not_called()


def test_unauthenticated_even_when_disabled(rec_api):
    api = rec_api[0]
    api[1].skill_recommendations_enabled = False
    assert api[0].post("/api/skills/recommendations", json={"conversation_id": str(api[3])}).status_code == 401
    api[8].assert_not_called()


@pytest.mark.parametrize("table", ["users", "organizations", "org_members"])
def test_no_permission_organization_before_discovery(rec_api, table):
    rec_api[0][2]._tables[table]._data[0]["status"] = "disabled"
    assert post(rec_api).status_code == 403
    rec_api[2].assert_not_called()


def test_foreign_conversation_and_candidates_are_isolated(rec_api):
    assert post(rec_api, conversation_id=str(uuid4())).status_code == 404
    rec_api[1].discover.return_value = [candidate(assignment_org_id=OTHER_ORG)]
    assert post(rec_api).json()["candidates"] == []


def test_empty_candidates_are_audited_and_personal_org_has_no_grant(rec_api):
    rec_api[1].discover.return_value = []
    assert post(rec_api).json()["status"] == "ready"
    assert rec_api[3].record.call_args.args[-1] == []
    rec_api[0][2]._tables["conversations"]._data[0]["org_id"] = None
    rec_api[2].reset_mock()
    assert post(rec_api).json()["candidates"] == []
    rec_api[2].assert_not_called()


def test_discovery_or_audit_failure_does_not_return_unaudited_candidates(rec_api):
    rec_api[3].record.side_effect = RuntimeError("db down")
    assert post(rec_api).json()["status"] == "unavailable"
    rec_api[1].discover.side_effect = RuntimeError("discovery failed")
    assert post(rec_api).json()["candidates"] == []


def feedback(rec_api, **changes):
    api = rec_api[0]
    return api[0].post(f"/api/skills/recommendations/{uuid4()}/feedback", headers=api[4], json={
        "conversation_id": str(api[3]), "skill_id": "report", "revision": "v1", "feedback": "not_relevant", **changes})


def test_feedback_requires_exact_audience_identity_and_current_access(rec_api):
    assert feedback(rec_api).status_code == 204
    rec_api[3].feedback.return_value = False
    assert feedback(rec_api).status_code == 404
    rec_api[0][2]._tables["org_members"]._data[0]["status"] = "disabled"
    assert feedback(rec_api).status_code == 403


def test_feedback_never_accepts_model_output_or_authority(rec_api):
    assert feedback(rec_api, feedback="activated").status_code == 422
    assert feedback(rec_api, body="publish").status_code == 422
    rec_api[0][1].skill_recommendations_enabled = False
    assert feedback(rec_api).status_code == 503
    rec_api[0][8].assert_not_called()
