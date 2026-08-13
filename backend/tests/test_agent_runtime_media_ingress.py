from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.media_composition import (
    build_runtime_media_composition,
)
from services.agent.runtime.media_ingress import RuntimeMediaIngress


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_65_agent_runtime_media_ingress.sql"
ROLLBACK = ROOT / "migrations/rollback/227_65_agent_runtime_media_ingress_rollback.sql"


def test_media_ingress_migration_is_additive_and_scoped():
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    assert "CREATE FUNCTION submit_agent_runtime_media_action_v1" in sql
    assert "ensure_agent_runtime_session" in sql
    assert "submit_agent_runtime_chat_action_v1" in sql
    assert "runtime_owned" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "TO everydayai_runtime, everydayai_wecom_runtime" in sql
    assert "DROP TABLE" not in sql
    assert "DROP FUNCTION submit_agent_runtime_media_action_v1" in rollback


def test_media_composition_is_disabled_by_default():
    composition = build_runtime_media_composition(
        database=object(), transport=object(), enabled=False,
    )
    assert composition.enabled is False
    assert composition.production_ready is False
    assert composition.registry.descriptors() == ()


def test_media_composition_requires_explicit_wiring():
    with pytest.raises(RuntimeError, match="WIRING_REQUIRED"):
        build_runtime_media_composition(database=object(), transport=None, enabled=True)


def test_enabled_media_composition_registers_both_actions():
    composition = build_runtime_media_composition(
        database=object(), transport=object(), enabled=True,
    )
    assert composition.enabled is True
    assert composition.production_ready is True
    assert {name for descriptor in composition.registry.descriptors()
            for name in descriptor.action_kinds} == {
                "generate_image", "generate_video",
            }


@pytest.mark.asyncio
async def test_media_ingress_calls_only_narrow_rpc():
    rpc = AsyncMock()
    rpc.execute.return_value = SimpleNamespace(data={
        "outcome": "created", "action_id": "a", "run_id": "r",
        "model_step_id": "m", "runtime_owned": True,
    })
    database = SimpleNamespace(rpc=lambda name, params: SimpleNamespace(
        execute=lambda: rpc.execute(name, params),
    ))
    receipt = await RuntimeMediaIngress(database).submit(
        conversation_id="c", org_id="o", user_id="u", scope_kind="user",
        scope_id="u", agent_definition_id="everydayai-default",
        agent_definition_revision="v1", task_id="t", input_message_id="i",
        output_message_id="m", turn_id=None, idempotency_key="media:t",
        kind="image", request={"prompt": "x"}, model_id="model",
    )
    assert receipt.accepted is True
    assert receipt.runtime_owned is True
    rpc.execute.assert_awaited_once()
    assert rpc.execute.await_args.args[0] == "submit_agent_runtime_media_action_v1"
