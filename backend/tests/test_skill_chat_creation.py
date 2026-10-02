"""Candidate creation and isolated-trial trust boundaries."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.skills.chat_creation import (
    ChatSkillCandidate,
    content_digest,
    create_proposal,
)
from services.skills.trials import (
    TrialConflict,
    estimate_image_trial,
    run_trial,
    validate_reference_images,
)


def settings(**changes):
    values = dict(
        skill_catalog_enabled=True,
        skill_chat_creation_enabled=True,
        skill_draft_trial_enabled=True,
        agent_loop_model="test-model",
        agent_loop_timeout=1,
    )
    values.update(changes)
    return SimpleNamespace(**values)


class FakeQuery:
    def __init__(self, data):
        self.data = data

    def select(self, *_args): return self
    def eq(self, *_args): return self
    def in_(self, *_args): return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, *_args): return self
    def maybe_single(self): return self
    def execute(self): return SimpleNamespace(data=self.data)


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.queried = []

    def table(self, name):
        self.queried.append(name)
        return FakeQuery(self.rows.get(name))


def admin_db():
    return FakeDb({
        "organizations": {"status": "active"},
        "org_members": {"status": "active", "role": "admin"},
        "users": {"status": "active"},
        "conversations": {"id": "conversation-1", "user_id": "actor-1",
                           "org_id": "org-1", "scope_type": "user"},
        "messages": [],
    })


def test_candidate_overrides_every_authority_field_and_drops_assets():
    candidate = ChatSkillCandidate.model_validate({
        "name": "白底商品图",
        "description": "为商品生成电商白底图",
        "body": "保留产品结构，使用纯白背景。",
        "task_modes": ["image-i2i"],
        "catalog_metadata": {
            "model_selectable": True,
            "tool_policy": "restricted",
            "allowed_tool_names": ["erp_write"],
            "required_permissions": ["orders.write"],
            "required_feature_flags": ["billing_admin"],
            "conversation_scopes": ["channel"],
            "execution_modes": ["scheduled"],
        },
            "template_variables": {"secret": {"type": "string", "source": "conversation_scope"}},
    })

    content = candidate.draft_content()
    metadata = content.catalog_metadata
    assert metadata.name == "白底商品图"
    assert metadata.model_selectable is False
    assert metadata.tool_policy == "platform"
    assert metadata.allowed_tool_names == ()
    assert metadata.required_permissions == ()
    assert metadata.required_feature_flags == ()
    assert metadata.conversation_scopes == ("user",)
    assert metadata.execution_modes == ("interactive",)
    assert content.assets == ()
    assert content.template_variables == {}
    assert content_digest(content) == content_digest(content.model_copy())


def test_candidate_rejects_unknown_publish_or_storage_fields():
    with pytest.raises(ValidationError):
        ChatSkillCandidate.model_validate({
            "name": "Skill", "body": "可复用流程。", "published": True,
        })


def test_feature_flag_off_blocks_before_any_database_access():
    db = FakeDb({})
    with pytest.raises(PermissionError, match="SKILL_CHAT_CREATION_DISABLED"):
        create_proposal(
            db, settings(skill_chat_creation_enabled=False), actor_id="actor-1",
            org_id="org-1", conversation_id="conversation-1",
            arguments={"name": "方案", "body": "规则"},
        )
    assert db.queried == []


def test_non_admin_organization_member_cannot_prepare_persisted_candidate():
    db = admin_db()
    db.rows["org_members"] = {"status": "active", "role": "member"}
    with pytest.raises(PermissionError, match="SKILL_ORG_ADMIN_REQUIRED"):
        create_proposal(
            db, settings(), actor_id="actor-1", org_id="org-1",
            conversation_id="conversation-1",
            arguments={"name": "方案", "body": "规则"},
        )
    assert "conversations" not in db.queried


def test_initial_model_advertisement_uses_trusted_admin_fact_and_fails_closed():
    import asyncio
    from services.tools import LegacyAdvertisement, ToolContext, ToolPolicy, build_legacy_catalog
    from services.tools.runtime_context import prepare_initial_context

    db = admin_db()
    handler = SimpleNamespace(db=db, execution_scope=None, channel_scope_id=None)
    context = ToolContext(
        actor_user_id="actor-1", workspace_owner_id="actor-1", org_id="org-1",
        conversation_id="conversation-1", context_scope="user", personal_context_allowed=True,
        agent_domain="general", permission_mode="auto", execution_mode="interactive",
        feature_flags={"skill_catalog_enabled": True, "skill_chat_creation_enabled": True},
    )
    prepared = asyncio.run(prepare_initial_context(handler, context))
    registry = build_legacy_catalog()
    assert prepared.feature_flags["skill_org_admin"] is True
    from config.chat_tools import get_core_tools
    assert "prepare_skill_draft" not in {
        tool["function"]["name"] for tool in get_core_tools("org-1")
    }
    resolved = registry.resolve(
        prepared, policy=ToolPolicy(registry), advertisement=LegacyAdvertisement(),
    )
    assert "prepare_skill_draft" in resolved.allowed
    assert "prepare_skill_draft" in resolved.advertised

    db.rows["org_members"] = {"status": "active", "role": "member"}
    non_admin = asyncio.run(prepare_initial_context(handler, context))
    assert non_admin.feature_flags["skill_org_admin"] is False
    assert "prepare_skill_draft" not in registry.resolve(
        non_admin, policy=ToolPolicy(registry), advertisement=LegacyAdvertisement(),
    ).allowed
    assert "prepare_skill_draft" not in registry.resolve(
        non_admin, policy=ToolPolicy(registry), advertisement=LegacyAdvertisement(),
    ).advertised

    disabled = replace(context, feature_flags={
        "skill_catalog_enabled": True, "skill_chat_creation_enabled": False,
    })
    no_lookup_db = FakeDb({})
    disabled_context = asyncio.run(prepare_initial_context(
        SimpleNamespace(db=no_lookup_db, execution_scope=None, channel_scope_id=None), disabled,
    ))
    assert disabled_context.feature_flags["skill_org_admin"] is False
    assert no_lookup_db.queried == []


def test_candidate_is_audited_and_staged_without_creating_or_publishing_skill(monkeypatch):
    import services.skills.chat_creation as creation

    class Repository:
        def __init__(self, _db):
            self.row = None
            self.checks = []

        def get_by_idempotency_key(self, **_kwargs): return None
        def create(self, payload):
            self.row = {**payload, "created_by": payload["actor_id"], "status": "draft", "revision": 0}
            return self.row
        def transition(self, *, next_status, **_kwargs):
            self.row = {**self.row, "status": next_status, "revision": self.row["revision"] + 1}
            return self.row
        def list_checks(self, *_args): return self.checks
        def record_check(self, **kwargs):
            self.checks.append(kwargs)
            return kwargs

    monkeypatch.setattr(creation, "ChangeSetRepository", Repository)
    monkeypatch.setattr(creation, "ChangeSetService", lambda _repo: object())
    monkeypatch.setattr(creation, "current_dispatch_call_id", lambda: "call-1")
    db = admin_db()
    db.rows["messages"] = [{"id": "message-1", "role": "user",
                            "content": [{"type": "text", "text": "规则"}]}]

    result = create_proposal(
        db, settings(), actor_id="actor-1", org_id="org-1",
        conversation_id="conversation-1",
        arguments={"name": "白底图 Skill", "description": "处理商品白底图",
                   "body": "保留商品轮廓，输出纯白背景。", "task_modes": ["image-i2i"]},
    )
    candidate = result.metadata["change_set"]
    assert candidate["resource_type"] == "skill_draft"
    assert candidate["status"] == "awaiting_approval"
    assert candidate["proposed_snapshot"]["content"]["catalog_metadata"]["model_selectable"] is False
    assert candidate["proposed_snapshot"]["content"]["catalog_metadata"]["tool_policy"] == "platform"
    assert candidate["audit_subject"]["source_message_refs"][0]["message_id"] == "message-1"
    assert "text" not in candidate["audit_subject"]["source_message_refs"][0]
    assert candidate["audit_subject"]["source_scope"] == "recent_40_messages"
    assert candidate["policy_snapshot"]["submission"]["mode"] == "explicit_skill_draft_confirmation"


def test_explicit_existing_draft_name_resolves_to_server_owned_package_and_version(monkeypatch):
    import services.skills.chat_creation as creation

    package_id = str(uuid4())

    class SkillAuthoringStub:
        def __init__(self, *_args): pass
        def list(self):
            return [{"package_id": package_id, "skill_key": "product-photo", "name": "商品白底图",
                     "scope_kind": "org", "status": "draft", "version": 7}]
        def detail(self, _package_id):
            return {"skill_key": "product-photo", "editable": True,
                    "draft": {"version": 7, "status": "draft", "content": {}}}

    class ChangeSetRepositoryStub:
        def __init__(self, _db): self.row = None
        def get_by_idempotency_key(self, **_kwargs): return None
        def create(self, payload):
            self.row = {**payload, "created_by": payload["actor_id"], "status": "draft", "revision": 0}
            return self.row
        def transition(self, *, next_status, **_kwargs):
            self.row = {**self.row, "status": next_status, "revision": self.row["revision"] + 1}
            return self.row
        def list_checks(self, *_args): return []
        def record_check(self, **kwargs): return kwargs

    monkeypatch.setattr(creation, "SkillRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(creation, "SkillAuthoring", SkillAuthoringStub)
    monkeypatch.setattr(creation, "ChangeSetRepository", ChangeSetRepositoryStub)
    monkeypatch.setattr(creation, "ChangeSetService", lambda _repo: object())
    monkeypatch.setattr(creation, "current_dispatch_call_id", lambda: "update-call")

    actor_id, org_id = str(uuid4()), str(uuid4())
    db = admin_db()
    db.pool = object()
    db.rows["org_members"] = {"status": "active", "role": "admin"}
    db.rows["conversations"] = {"id": "conversation-1", "user_id": actor_id,
                                 "org_id": org_id, "scope_type": "user"}
    result = create_proposal(
        db, settings(), actor_id=actor_id, org_id=org_id, conversation_id="conversation-1",
        arguments={"name": "更清晰的商品白底图流程", "body": "保留商品结构，输出纯白背景。",
                   "target_skill_name": "商品白底图"},
    )

    candidate = result.metadata["change_set"]
    assert candidate["operation"] == "update"
    assert candidate["resource_id"] == package_id
    assert candidate["base_revision"] == "7"
    assert candidate["proposed_snapshot"]["target_skill"] == {
        "package_id": package_id, "skill_key": "product-photo",
        "name": "商品白底图", "expected_version": 7,
    }


def test_duplicate_candidate_tool_call_replays_the_original_candidate(monkeypatch):
    import services.skills.chat_creation as creation

    content = ChatSkillCandidate(name="方案", body="规则").draft_content()
    digest = content_digest(content)
    existing = {
        "id": str(uuid4()), "org_id": "org-1", "resource_type": "skill_draft",
        "created_by": "actor-1", "status": "awaiting_approval",
        "audit_subject": {
            "conversation_id": "conversation-1", "candidate_sha256": digest,
            "candidate_operation": "create", "target_package_id": None, "target_version": None,
        },
    }
    repository = SimpleNamespace(
        get_by_idempotency_key=Mock(return_value=existing), create=Mock(),
    )
    monkeypatch.setattr(creation, "ChangeSetRepository", lambda _db: repository)
    monkeypatch.setattr(creation, "current_dispatch_call_id", lambda: "same-call")

    result = create_proposal(
        admin_db(), settings(), actor_id="actor-1", org_id="org-1",
        conversation_id="conversation-1", arguments={"name": "方案", "body": "规则"},
    )

    assert result.metadata["change_set"] is existing
    repository.create.assert_not_called()


def test_candidate_tool_call_rejects_idempotency_key_with_different_content(monkeypatch):
    import services.skills.chat_creation as creation

    existing = {
        "id": str(uuid4()), "org_id": "org-1", "resource_type": "skill_draft",
        "created_by": "actor-1", "status": "awaiting_approval",
        "audit_subject": {
            "conversation_id": "conversation-1", "candidate_sha256": "0" * 64,
        },
    }
    repository = SimpleNamespace(
        get_by_idempotency_key=Mock(return_value=existing), create=Mock(),
    )
    monkeypatch.setattr(creation, "ChangeSetRepository", lambda _db: repository)
    monkeypatch.setattr(creation, "current_dispatch_call_id", lambda: "same-call")

    with pytest.raises(ValueError, match="SKILL_PROPOSAL_IDEMPOTENCY_CONFLICT"):
        create_proposal(
            admin_db(), settings(), actor_id="actor-1", org_id="org-1",
            conversation_id="conversation-1", arguments={"name": "方案", "body": "规则"},
        )
    repository.create.assert_not_called()


def test_candidate_revision_uses_actor_scoped_runtime_admin_rpc(monkeypatch):
    import services.skills.chat_creation as creation

    calls = {}

    class Repository:
        def __init__(self, scoped_db):
            calls["scope"] = scoped_db.scope

        def replace_skill_proposal(self, **kwargs):
            calls["kwargs"] = kwargs
            return {"id": kwargs["change_set_id"], "revision": kwargs["expected_revision"] + 1}

    monkeypatch.setattr(creation, "ChangeSetRepository", Repository)
    result = creation.replace_candidate(
        admin_db(), settings(), actor_id="00000000-0000-0000-0000-000000000001",
        org_id="00000000-0000-0000-0000-000000000002",
        change_set={
            "id": str(uuid4()), "created_by": "00000000-0000-0000-0000-000000000001",
            "resource_type": "skill_draft", "status": "awaiting_approval",
            "proposed_snapshot": {"skill_key": "chat-skill-1"},
        },
        arguments={"name": "方案", "body": "规则"}, expected_revision=2,
    )

    assert result["revision"] == 3
    assert calls["scope"].actor_user_id == "00000000-0000-0000-0000-000000000001"
    assert calls["scope"].org_id == "00000000-0000-0000-0000-000000000002"
    assert calls["scope"].access_kind.value == "runtime_admin"
    assert calls["kwargs"]["expected_revision"] == 2


def test_trial_rejects_stale_candidate_before_claiming_run():
    content = ChatSkillCandidate(name="方案", body="按步骤处理。").draft_content()
    digest = content_digest(content)
    changeset = {
        "id": str(uuid4()), "created_by": "actor-1", "resource_type": "skill_draft",
        "status": "cancelled", "revision": 4,
        "proposed_snapshot": {"content": content.model_dump(mode="json"), "content_sha256": digest},
        "audit_subject": {"candidate_sha256": digest},
    }
    db = FakeDb({})
    with pytest.raises(TrialConflict, match="SKILL_TRIAL_CANDIDATE_UNAVAILABLE"):
        import asyncio
        asyncio.run(run_trial(
            db, settings(), actor_id="actor-1", org_id="org-1", change_set=changeset,
            expected_revision=4, content_sha256=digest, mode="text", user_input="测试",
            idempotency_key=uuid4(),
        ))
    assert db.queried == []


def test_trial_rejects_expired_candidate_before_claiming_run():
    content = ChatSkillCandidate(name="方案", body="按步骤处理。").draft_content()
    digest = content_digest(content)
    changeset = {
        "id": str(uuid4()), "created_by": "actor-1", "resource_type": "skill_draft",
        "status": "awaiting_approval", "revision": 4,
        "expires_at": "2000-01-01T00:00:00Z",
        "proposed_snapshot": {"content": content.model_dump(mode="json"), "content_sha256": digest},
        "audit_subject": {"candidate_sha256": digest},
    }
    db = FakeDb({})
    with pytest.raises(TrialConflict, match="SKILL_TRIAL_CANDIDATE_UNAVAILABLE"):
        import asyncio
        asyncio.run(run_trial(
            db, settings(), actor_id="actor-1", org_id="org-1", change_set=changeset,
            expected_revision=4, content_sha256=digest, mode="text", user_input="测试",
            idempotency_key=uuid4(),
        ))
    assert db.queried == []


def test_trial_feature_flag_off_blocks_before_candidate_or_model_work():
    db = FakeDb({})
    with pytest.raises(PermissionError, match="SKILL_DRAFT_TRIAL_DISABLED"):
        import asyncio
        asyncio.run(run_trial(
            db, settings(skill_draft_trial_enabled=False), actor_id="actor-1", org_id="org-1",
            change_set={}, expected_revision=0, content_sha256="0" * 64,
            mode="text", user_input="测试", idempotency_key=uuid4(),
        ))
    assert db.queried == []


def test_trial_replays_a_completed_idempotency_key_without_model_or_image_calls(monkeypatch):
    import asyncio
    import services.skills.trials as trials

    content = ChatSkillCandidate(name="方案", body="遵循本次任务规则。").draft_content()
    digest = content_digest(content)
    run_id = str(uuid4())
    changeset = {
        "id": str(uuid4()), "created_by": "actor-1", "resource_type": "skill_draft",
        "status": "awaiting_approval", "revision": 3,
        "proposed_snapshot": {"content": content.model_dump(mode="json"), "content_sha256": digest},
        "audit_subject": {"candidate_sha256": digest},
    }
    monkeypatch.setattr(trials, "_claim_run", lambda _db, row, **_kwargs: ({
        **row, "id": run_id, "status": "completed",
        "result": {"mode": "text", "output": "已完成试用", "model_id": "test-model"},
    }, False))
    result = asyncio.run(trials.run_trial(
        FakeDb({}), settings(), actor_id="actor-1", org_id="org-1", change_set=changeset,
        expected_revision=3, content_sha256=digest, mode="text", user_input="资料",
        idempotency_key=uuid4(),
    ))
    assert result == {"trial_id": run_id, "candidate_revision": 3, "content_sha256": digest,
                      "mode": "text", "output": "已完成试用",
                      "model_id": "test-model", "replayed": True}


def test_confirm_recovers_a_committing_candidate_after_a_lost_response(monkeypatch):
    import asyncio
    import api.routes.change_sets as route
    from services.skills.chat_creation import ChatSkillCandidate
    from api.routes.change_sets import SkillDraftConfirmation

    content = ChatSkillCandidate(name="方案", body="按步骤执行。").draft_content()
    digest = content_digest(content)
    cs_id = str(uuid4())
    actor_id = str(uuid4())
    org_id = str(uuid4())
    row = {
        "id": cs_id, "org_id": org_id, "resource_type": "skill_draft",
        "resource_id": str(uuid4()), "operation": "create", "base_revision": "0",
        "base_snapshot": {}, "proposed_snapshot": {
            "skill_key": "chat-skill-test", "content": content.model_dump(mode="json"),
            "content_sha256": digest,
        },
        "patch": [], "diff": {}, "risk_level": "low", "policy_snapshot": {},
        "plan_snapshot": None, "tool_policy_snapshot": None,
        "status": "committing", "idempotency_key": "key", "expires_at": "2099-01-01T00:00:00Z",
        "created_by": actor_id, "created_by_type": "user", "audit_subject": {"candidate_sha256": digest},
        "revision": 6, "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z",
    }

    class Repository:
        def __init__(self, _db): self.row = dict(row)
        def get(self, *_args): return self.row
        def list_checks(self, *_args): return []
        def record_check(self, **_kwargs): return {}
        def transition(self, **_kwargs):
            self.row = {**self.row, "status": "applied", "revision": 7}
            return self.row

    receipt = {"package_id": row["resource_id"], "draft_revision": "v1", "draft_version": 1}
    commit = Mock(return_value=receipt)
    monkeypatch.setattr(route, "ChangeSetRepository", Repository)
    monkeypatch.setattr(route, "SkillRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(route, "commit_draft", commit)
    monkeypatch.setattr(route, "_org_admin", lambda *_args: None)
    monkeypatch.setattr(route, "_to_dto", lambda value, _checks: value)
    monkeypatch.setattr(route, "get_settings", lambda: settings())

    result = asyncio.run(route.confirm_change_set(
        change_set_id=cs_id, user_id=actor_id,
        org_ctx=SimpleNamespace(org_id=org_id, org_role="admin"),
        scoped_db=object(), db=SimpleNamespace(pool=object()),
        confirmation=SkillDraftConfirmation(
            expected_change_set_revision=5, content_sha256=digest,
        ),
    ))
    assert result["data"]["status"] == "applied"
    commit.assert_called_once()


def test_confirm_marks_stale_skill_update_conflicted_instead_of_leaving_it_committing(monkeypatch):
    import asyncio
    import api.routes.change_sets as route
    from fastapi import HTTPException
    from api.routes.change_sets import SkillDraftConfirmation
    from services.skills.contracts import SkillError

    content = ChatSkillCandidate(name="方案", body="按步骤执行。").draft_content()
    digest = content_digest(content)
    cs_id, actor_id, org_id = str(uuid4()), str(uuid4()), str(uuid4())
    row = {
        "id": cs_id, "org_id": org_id, "resource_type": "skill_draft",
        "resource_id": str(uuid4()), "operation": "update", "base_revision": "2",
        "proposed_snapshot": {"skill_key": "existing-skill", "content": content.model_dump(mode="json"),
                              "content_sha256": digest},
        "status": "committing", "created_by": actor_id,
        "audit_subject": {"candidate_sha256": digest}, "revision": 6,
    }
    transitions, transitioned_statuses = [], []

    class Repository:
        def __init__(self, _db): self.row = dict(row)
        def get(self, *_args): return self.row
        def list_checks(self, *_args): return []
        def record_check(self, **kwargs): transitions.append(("check", kwargs)); return {}
        def transition(self, **kwargs):
            transitions.append(("transition", kwargs))
            transitioned_statuses.append(kwargs["next_status"])
            self.row = {**self.row, "status": kwargs["next_status"]}
            return self.row

    monkeypatch.setattr(route, "ChangeSetRepository", Repository)
    monkeypatch.setattr(route, "SkillRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(route, "commit_draft", Mock(side_effect=SkillError("SKILL_VERSION_CONFLICT")))
    monkeypatch.setattr(route, "_org_admin", lambda *_args: None)
    monkeypatch.setattr(route, "get_settings", lambda: settings())

    with pytest.raises(HTTPException) as error:
        asyncio.run(route.confirm_change_set(
            change_set_id=cs_id, user_id=actor_id,
            org_ctx=SimpleNamespace(org_id=org_id, org_role="admin"),
            scoped_db=object(), db=SimpleNamespace(pool=object()),
            confirmation=SkillDraftConfirmation(
                expected_change_set_revision=5, content_sha256=digest,
            ),
        ))

    assert error.value.status_code == 409
    assert transitioned_statuses == ["conflicted"]


def test_confirm_is_blocked_when_skill_chat_creation_flag_is_off(monkeypatch):
    import asyncio
    import api.routes.change_sets as route
    from fastapi import HTTPException
    from api.routes.change_sets import SkillDraftConfirmation

    cs_id, actor_id, org_id = str(uuid4()), str(uuid4()), str(uuid4())

    class Repository:
        def __init__(self, _db): pass
        def get(self, *_args):
            return {"id": cs_id, "org_id": org_id, "resource_type": "skill_draft",
                    "status": "awaiting_approval", "created_by": actor_id}

    monkeypatch.setattr(route, "ChangeSetRepository", Repository)
    monkeypatch.setattr(route, "get_settings", lambda: settings(skill_chat_creation_enabled=False))

    with pytest.raises(HTTPException) as error:
        asyncio.run(route.confirm_change_set(
            change_set_id=cs_id, user_id=actor_id,
            org_ctx=SimpleNamespace(org_id=org_id, org_role="admin"),
            scoped_db=object(), db=SimpleNamespace(pool=object()),
            confirmation=SkillDraftConfirmation(expected_change_set_revision=0,
                                                content_sha256="0" * 64),
        ))

    assert error.value.status_code == 404


def test_image_trial_estimate_matches_model_selection_and_only_one_image():
    text_image = estimate_image_trial([])
    reference_image = estimate_image_trial(["selected-reference"])
    assert text_image["image_count"] == reference_image["image_count"] == 1
    assert text_image["model_id"] != reference_image["model_id"]
    assert text_image["estimated_credits"] > 0
    assert reference_image["estimated_credits"] > 0


def test_image_trial_accepts_only_currently_available_user_images():
    selected = "https://cdn.example.com/user-upload.png"
    available = [{"url": selected, "message_id": "user-message"}]
    assert validate_reference_images([selected], available) == [selected]
    with pytest.raises(PermissionError, match="SKILL_TRIAL_REFERENCE_IMAGE_UNAVAILABLE"):
        validate_reference_images(["https://other.example/image.png"], available)
