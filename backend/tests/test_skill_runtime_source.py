"""Real controlled NAS reads and current Actor permission revalidation."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock

import pytest

from core.config import get_settings
from services.skills.contracts import SkillError
from services.skills.runtime import SkillReplayError
from services.skills.runtime_source import ActorSkillSource
from services.tools import ToolContext
from tests.test_skill_resolver import ACTOR, ORG, candidate, context
from tests.test_skill_runtime import activate, state
from tests.test_skill_storage import storage, PACKAGE, BODY, DOCUMENT, publication, write_skill  # noqa: F401


def source_context():
    return ToolContext(actor_user_id=str(ACTOR), workspace_owner_id=str(ACTOR), org_id=str(ORG),
                       conversation_id="conversation", task_id="task", context_scope="user",
                       personal_context_allowed=True, permission_mode="auto", agent_domain="general",
                       execution_mode="interactive")


@pytest.mark.parametrize("damage", ["missing", "hash"])
async def test_restore_requires_original_nas_file_and_hash(storage, damage):
    document = DOCUMENT.replace("description: 报表说明\n", "description: 报表说明\ncatalog:\n  model_selectable: true\n")
    path = write_skill(storage, document)
    validated = storage.validate(PACKAGE, publication(document))
    c = candidate(catalog_metadata=validated.catalog_metadata)
    settings = get_settings().model_copy(update={
        "skill_runtime_enabled": True, "skill_catalog_enabled": True,
        "skill_storage_root": str(storage.root), "file_workspace_root": str(storage.workspace_root),
    })
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=object())), source_context(), settings)
    source._resolution_context = AsyncMock(return_value=context())
    source.repository = SimpleNamespace(
        catalog_candidates=Mock(return_value=[c]), get_package=Mock(return_value=PACKAGE),
        assigned_revision=Mock(return_value=SimpleNamespace(
            revision="v1", content_sha256=validated.content_sha256, body_sha256=validated.body_sha256,
            nas_path=validated.nas_path, catalog_metadata=validated.catalog_metadata,
        )),
    )
    runtime = state(source)
    await runtime.initialize()
    source.repository.assigned_revision.assert_not_called()
    assert (await runtime.activate(activate()))["ok"]
    checkpoint = runtime.checkpoint()
    if damage == "missing":
        path.unlink()
    else:
        path.write_text(document + "drift")
    with pytest.raises(SkillReplayError, match="READ_REJECTED|HASH_MISMATCH"):
        await state(source).initialize(checkpoint)
    assert source.repository.assigned_revision.call_args.args == (c.package_id, "v1")


async def test_actor_source_checks_identity_permissions_and_flags_before_body(monkeypatch):
    settings = get_settings().model_copy(update={"skill_catalog_enabled": True, "skill_runtime_enabled": True})
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
        SimpleNamespace(data={"status": "active"})
    )
    source = ActorSkillSource(SimpleNamespace(db=db), source_context(), settings)
    c = candidate(catalog_metadata={"required_permissions": ["order.view"], "model_selectable": True})
    source.repository.catalog_candidates = Mock(return_value=[c])
    source.repository.assigned_revision = Mock(side_effect=AssertionError("must not read"))
    identity = Mock()
    monkeypatch.setattr("services.skills.runtime_source._check_identity", identity)
    checker = SimpleNamespace(check=AsyncMock(return_value=True))
    monkeypatch.setattr("services.skills.runtime_source.PermissionChecker", lambda _: checker)
    assert await source.discover() == [c]
    identity.assert_called_once()
    checker.check.return_value = False
    with pytest.raises(SkillError, match="ACCESS_DENIED"):
        await source.load(c)
    source.repository.assigned_revision.assert_not_called()
    identity.side_effect = PermissionError("/secret/server/path")
    with pytest.raises(SkillError, match="IDENTITY_UNAVAILABLE"):
        await source.load(c)


async def test_actor_source_keeps_channel_scope_from_trusted_tool_context(monkeypatch):
    settings = get_settings().model_copy(update={"skill_catalog_enabled": True})
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = (
        SimpleNamespace(data={"status": "active"})
    )
    scope = SimpleNamespace(channel_scope_id="channel")
    channel = replace(source_context(), workspace_owner_id="channel-workspace",
                      context_scope="channel", personal_context_allowed=False)
    source = ActorSkillSource(SimpleNamespace(db=db, execution_scope=scope), channel, settings)
    user_only = candidate()
    channel_skill = candidate(skill_key="channel", catalog_metadata={"conversation_scopes": ["channel"]})
    source.repository.catalog_candidates = Mock(return_value=[user_only, channel_skill])
    identity = Mock()
    monkeypatch.setattr("services.skills.runtime_source._check_identity", identity)
    assert await source.discover() == [channel_skill]
    identity_actor, identity_context = identity.call_args.args
    assert identity_actor.execution_scope is scope and identity_context == channel
