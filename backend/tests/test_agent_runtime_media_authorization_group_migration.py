from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.application.authorization_recovery import (
    AuthorizationRecoveryDriver,
)
from services.agent.runtime.catalog.batch_media_release import (
    build_batch_media_snapshot,
)
from services.agent.runtime.executors.specialist_registry import (
    specialist_descriptor,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.types import AuthorizationRequirement
from services.agent.runtime.policy.evaluator import PolicyEvaluator
from services.agent.runtime.ports.authorization import (
    AuthorizationRecoveryClaim,
    PolicyReceiptRecord,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_03_agent_runtime_media_authorization_group.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_03_agent_runtime_media_authorization_group_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")


def test_group_authorization_contract_is_additive_and_guarded() -> None:
    assert "ADD COLUMN confirmation_group_hash" in SQL
    assert "ADD COLUMN confirmation_group_leader_id" in SQL
    assert "open_agent_authorization_batch_v1" in SQL
    assert "claim_agent_tool_batch_confirmation_v1" in SQL
    assert "resolve_agent_tool_batch_confirmation_v1" in SQL
    assert "group_confirmation_required" in SQL
    assert "AGENT_MEDIA_AUTHORIZATION_GROUP_ROLLBACK_HAS_FACTS" in ROLLBACK_SQL
    assert "UPDATE agent_runtime_catalog_facts" not in SQL
    assert "UPDATE agent_runtime_definition_facts" not in SQL


def test_group_rpc_contract_is_fail_closed_and_least_privilege() -> None:
    for marker in (
        "member_count NOT BETWEEN 2 AND 10",
        "confirmation_group_hash IS DISTINCT FROM p_confirmation_group_hash",
        "tenant_actor_user_id() IS DISTINCT FROM p_user_id",
        "tenant_org_id() IS DISTINCT FROM p_org_id",
        "AGENT_INTERACTION_COUNT_UNDERFLOW",
        "FOR UPDATE OF candidate SKIP LOCKED",
        "SET search_path=pg_catalog,public",
    ):
        assert marker in SQL
    assert "TO everydayai_agent_runtime_worker;" in SQL
    assert "TO everydayai_projection_worker;" in SQL
    assert "TO everydayai_runtime,everydayai_wecom_runtime;" in SQL


def test_generate_image_uses_persisted_interaction_without_rewriting_v7() -> None:
    descriptor = specialist_descriptor("generate_image")
    assert descriptor.authorization is AuthorizationRequirement.PERSISTED_INTERACTION
    snapshot = build_batch_media_snapshot(
        scope="user", channel="web", gate_state="enabled",
    )
    image = next(
        tool for tool in snapshot.receipt.catalog.definitions()
        if tool.canonical_name == "generate_image"
    )
    assert image.authorization_requirement == "explicit_intent"
    assert (
        ROOT / "migrations/228_02_agent_runtime_batch_media_release.sql"
    ).is_file()


def test_batch_wrapper_has_no_unverified_explicit_intent_shortcut() -> None:
    assert "explicit_intent" not in SQL
    assert "open_agent_authorization_batch_v1" in SQL
    assert "tool_name='generate_image'" in SQL


@pytest.mark.asyncio
async def test_approved_image_action_is_activated_by_authorization_recovery() -> None:
    descriptor = specialist_descriptor("generate_image")
    registry = ExecutorRegistry([(descriptor, object())])
    claim = AuthorizationRecoveryClaim(
        interaction_id="11111111-1111-1111-1111-111111111111",
        recovery_token="22222222-2222-2222-2222-222222222222",
        state_version=2,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        action={
            "id": "33333333-3333-3333-3333-333333333333",
            "run_id": "44444444-4444-4444-4444-444444444444",
            "session_id": "55555555-5555-5555-5555-555555555555",
            "user_id": "66666666-6666-6666-6666-666666666666",
            "org_id": "77777777-7777-7777-7777-777777777777",
            "tool_name": "generate_image", "arguments_hash": "a" * 64,
            "policy_revision": "agent-runtime-v1", "state_version": 0,
        },
        grant={
            "id": "88888888-8888-8888-8888-888888888888",
            "grant_kind": "action", "effective_scope": {},
        },
    )
    repository = AsyncMock()
    repository.claim_recovery.return_value = claim
    repository.record_allow_receipt.return_value = PolicyReceiptRecord(
        receipt_id="99999999-9999-9999-9999-999999999999",
    )
    driver = AuthorizationRecoveryDriver(
        repository=repository, registry=registry,
        evaluator=PolicyEvaluator(), worker_id="authorization-1",
    )

    assert await driver.run_once() is True
    repository.record_allow_receipt.assert_awaited_once()
    repository.activate.assert_awaited_once()
