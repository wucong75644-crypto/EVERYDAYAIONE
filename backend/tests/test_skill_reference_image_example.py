"""Validate the shipped example through real storage and the chat activation boundary."""

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from schemas.message import serialize_content_parts
from services.handlers.chat.execution_engine import execute_chat
from services.skills.contracts import PackageCreate, PublishRevision
from services.skills.renderer import render
from services.skills.resolver import SkillResolver
from services.skills.selection import SkillSelection
from services.skills.storage import SkillStorage
from tests.test_chat_execution_engine import _request
from tests.test_skill_manual_selection import execution  # noqa: F401
from tests.test_skill_resolver import candidate, context
from tests.test_skill_runtime_actor import actor, handler, prepared


@pytest.fixture
def example(tmp_path):
    root = Path(__file__).resolve().parents[2] / "examples/skills/catalog"
    raw = (root / "platform/reference-image-prompts/v1/SKILL.md").read_bytes()
    body = raw.split(b"---\n", 2)[2]
    publication = PublishRevision(
        revision="v1", content_sha256=hashlib.sha256(raw).hexdigest(),
        body_sha256=hashlib.sha256(body).hexdigest(),
    )
    package = PackageCreate(skill_key="reference-image-prompts", source="user-example", scope_kind="platform")
    storage = SkillStorage(str(root), workspace_root=str(tmp_path / "workspace"))
    return storage.validate(package, publication)


@pytest.mark.parametrize("changes,visible", [
    ({}, True),
    ({"org_id": "99999999-9999-9999-9999-999999999999"}, False),
    ({"conversation_scope": "channel"}, False),
    ({"agent_domain": "erp"}, False),
    ({"execution_mode": "scheduled"}, False),
    ({"enabled_feature_flags": frozenset()}, False),
])
def test_example_is_visible_only_in_assigned_interactive_chat(example, changes, visible):
    entry = candidate(skill_key=example.skill_key, description=example.summary,
                      catalog_metadata=example.catalog_metadata)
    summaries = SkillResolver().resolve(context(**changes), [entry])
    assert bool(summaries) is visible
    if visible:
        assert summaries[0].name == "参考图多方案提示词"
        assert summaries[0].model_selectable is False


async def test_real_example_activates_before_model_without_losing_image_or_granting_tools(
    example, execution, monkeypatch,
):
    source, model, _ = execution
    source.discover.return_value = [candidate(
        skill_key=example.skill_key, description=example.summary,
        catalog_metadata=example.catalog_metadata,
    )]
    source.load.side_effect = None
    source.load.return_value = example
    # Fails if the actual package exceeds the runtime cap or needs template args.
    assert render(example.body, {}) == example.body
    image = {"type": "image_url", "image_url": {"url": "https://example.invalid/reference.png"}}
    user_message = {"role": "user", "content": [{"type": "text", "text": "生成六套方案"}, image]}

    def prepare(**kwargs):
        result = prepared()
        result.messages.append(user_message)
        return result

    monkeypatch.setattr("services.handlers.chat.execution_engine.prepare_chat_stream",
                        AsyncMock(side_effect=prepare))
    result = await execute_chat(
        handler=handler(), runtime=actor(),
        request=replace(_request(), selected_skill=SkillSelection(skill_id=example.skill_key, revision="v1")),
    )
    messages, tools = model.await_args.args[0].messages, model.await_args.args[1]
    assert user_message in messages
    assert any(example.body in m.get("content", "") for m in messages if isinstance(m.get("content"), str))
    assert tools == []
    expected = {"type": "skill_step", "step_id": "manual-skill", "status": "completed",
                "name": "参考图多方案提示词", "revision": "v1"}
    assert serialize_content_parts(result.parts)[0] == expected

    ordinary = await execute_chat(handler=handler(), request=_request(), runtime=actor())
    assert all(block["type"] != "skill_step" for block in ordinary.content_blocks)
    source.load.assert_awaited_once()
