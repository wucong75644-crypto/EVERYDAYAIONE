from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.media_composition import (
    build_runtime_media_composition,
)
from services.agent.runtime.media_ingress import RuntimeMediaIngress
from api.routes.message_media_runtime import submit_runtime_media_ingress


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
        build_runtime_media_composition(
            database=object(), transport=None, credentials=object(),
            enabled=True, provider_probe_passed=True,
        )


def test_enabled_media_composition_without_provider_readiness_fails_closed():
    database = SimpleNamespace(scope=DatabaseScope(
        actor_user_id=None, org_id=None,
        access_kind=DatabaseAccessKind.AGENT_RUNTIME,
    ))
    composition = build_runtime_media_composition(
        database=database, transport=object(), enabled=True,
    )
    assert composition.enabled is False
    assert composition.production_ready is False
    assert composition.registry.descriptors() == ()


def test_enabled_media_composition_registers_both_actions():
    database = SimpleNamespace(scope=DatabaseScope(
        actor_user_id=None, org_id=None,
        access_kind=DatabaseAccessKind.AGENT_RUNTIME,
    ))
    facts = object()
    composition = build_runtime_media_composition(
        database=database, transport=object(), enabled=True,
        credentials=object(), provider_probe_passed=True,
        specialist_facts=facts,
    )
    assert composition.enabled is True
    assert composition.production_ready is False
    assert composition.error_code == "PRODUCTION_READINESS_DISABLED"
    assert {name for descriptor in composition.registry.descriptors()
            for name in descriptor.action_kinds} == {
            "generate_image", "generate_video",
            }
    assert composition.registry.specialist_facts is facts
    _, executor = composition.registry.resolve("generate_image")
    assert executor.provider.production_ready is False
    assert executor.provider.recovery_ready is True


def test_local_production_flag_does_not_claim_database_readiness():
    database = SimpleNamespace(scope=DatabaseScope(
        actor_user_id=None, org_id=None,
        access_kind=DatabaseAccessKind.AGENT_RUNTIME,
    ))
    composition = build_runtime_media_composition(
        database=database, transport=object(), enabled=True,
        credentials=object(), provider_probe_passed=True,
        production_ready=True,
    )
    assert composition.production_ready is False
    assert composition.error_code == "DATABASE_READINESS_UNVERIFIED"
    _, executor = composition.registry.resolve("generate_image")
    assert executor.provider.production_ready is True


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


@pytest.mark.asyncio
async def test_media_ingress_rejects_credit_and_internal_identity_arguments():
    database = SimpleNamespace(rpc=lambda *_: pytest.fail("RPC must not run"))
    with pytest.raises(RuntimeError, match="INTERNAL_ARGUMENT_FORBIDDEN"):
        await RuntimeMediaIngress(database).submit(
            conversation_id="c", org_id="o", user_id="u",
            scope_kind="user", scope_id="u",
            agent_definition_id="everydayai-default",
            agent_definition_revision="v1", task_id="t",
            input_message_id="i", output_message_id="m", turn_id=None,
            idempotency_key="media:t", kind="video",
            request={"prompt": "x", "reserved_credits": 31},
            model_id="sora-2-text-to-video",
        )


@pytest.mark.asyncio
async def test_database_gate_false_wins_over_local_media_flags(monkeypatch):
    monkeypatch.setattr(
        "core.config.get_settings",
        lambda: SimpleNamespace(
            agent_runtime_media_enabled=True,
            agent_runtime_media_provider_probe_passed=True,
            agent_runtime_media_production_ready=True,
            agent_runtime_agent_definition_id="everydayai-default",
            agent_runtime_agent_definition_revision="v1",
        ),
    )
    class Query:
        data = {"scope_type": "user", "scope_id": "u"}

        def select(self, *_args): return self
        def eq(self, *_args): return self
        def single(self): return self
        def execute(self): return self

    rpc = AsyncMock(return_value=SimpleNamespace(data={
        "outcome": "media_not_ready", "runtime_owned": False,
    }))
    database = SimpleNamespace(
        table=lambda *_: Query(),
        rpc=lambda *_: SimpleNamespace(execute=rpc),
    )
    receipt = await submit_runtime_media_ingress(
        db=database,
        conversation_id="c", user_id="u", org_id="o", task_id="t",
        input_message_id="i", output_message_id="m", turn_id=None,
        idempotency_key="media:t", kind="image", request={"prompt": "x"},
        model_id="google/nano-banana",
    )
    assert receipt.outcome == "media_not_ready"
    assert receipt.runtime_owned is False
    rpc.assert_awaited_once()
