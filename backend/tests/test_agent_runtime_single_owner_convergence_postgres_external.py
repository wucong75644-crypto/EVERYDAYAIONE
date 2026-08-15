from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_c7_b32a_postgres_external import (
    _activation_params,
    _apply,
    _rollback,
    _seed_safe_action,
    _worker_call,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
PREREQUISITES = (
    "227_01_agent_runtime_production_closure.sql",
    "227_06_agent_runtime_tenant_kill_control.sql",
    "227_07_agent_runtime_kill_epoch_fence.sql",
    "227_09_agent_runtime_claim_fence_ambiguity_fix.sql",
    "227_13_agent_runtime_additive_ingress_compatibility.sql",
    "227_14_agent_runtime_owner_transition.sql",
    "227_15_agent_runtime_owner_rpc_acl_closure.sql",
    "227_16_agent_runtime_safe_read_release.sql",
    "227_17_agent_runtime_safe_policy_activation.sql",
    "227_61_agent_runtime_web_ingress_required.sql",
    "228_08j_agent_runtime_web_scope_owner_atomicity.sql",
    "228_08k_agent_runtime_web_ingress_binding_terminal.sql",
)
MIGRATION = "228_08q_agent_runtime_single_owner_convergence.sql"
ROLLBACK = "228_08q_agent_runtime_single_owner_convergence_rollback.sql"


def test_expired_safe_claim_recovers_without_rollout_or_new_activation(
    database: str,
) -> None:
    with psycopg.connect(database) as connection:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
            "source TEXT DEFAULT 'web'",
        )
        connection.commit()
    for name in PREREQUISITES:
        _apply(database, name)
    ids = _seed_safe_action(database)
    snapshot = ids["snapshot"]
    activation = _worker_call(
        database, "activate_agent_safe_action", _activation_params(snapshot),
    )
    assert activation["outcome"] == "activated"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_action_attempts "
            "SET lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s",
            (snapshot["id"],),
        )
        connection.execute(
            "UPDATE agent_runtime_control SET "
            "action_dispatch_enabled=TRUE,safe_actions_enabled=TRUE",
        )
        connection.execute(
            "DELETE FROM agent_runtime_org_rollout WHERE org_id=%s",
            (ids["snapshot"]["org_id"],),
        )
        connection.commit()

    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        privileges = (
            ("everydayai_runtime", "runtime_submit_ingress_v4(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb)", False),
            ("everydayai_runtime", "submit_runtime_ingress_required_v1(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb,uuid,text,uuid,uuid,uuid,text)", True),
            ("everydayai_wecom_runtime", "enqueue_wecom_runtime_turn_v3(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text)", False),
            ("everydayai_wecom_runtime", "enqueue_wecom_runtime_turn_v4(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text,text,text,text,text)", False),
            ("everydayai_wecom_runtime", "enqueue_wecom_runtime_turn_v5(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text,text,text,text,text)", False),
            ("everydayai_wecom_runtime", "enqueue_wecom_runtime_turn_v6(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text,text,text,text,text)", False),
            ("everydayai_wecom_runtime", "enqueue_wecom_runtime_turn_required_v1(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text,text,text,text,text)", True),
            ("everydayai_agent_runtime_worker", "gate_agent_action_dispatch_v2(uuid,uuid,bigint,text,uuid,text,integer,text,text)", False),
            ("everydayai_agent_runtime_worker", "gate_agent_action_dispatch_final_v1(uuid,uuid,bigint,text,uuid,text,integer,text,text)", True),
        )
        for role, signature, expected in privileges:
            actual = connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                (role, signature),
            ).fetchone()[0]
            assert actual is expected, (role, signature, actual)
    claim = _worker_call(
        database,
        "claim_agent_action_dispatch_final_v1",
        ("final-worker", f"claim-{uuid4()}", 1, 120),
    )
    assert claim["outcome"] == "claimed"
    recovered = claim["snapshots"][0]
    assert recovered["action_id"] == ids["action"]
    assert recovered["attempt_number"] == 2
    assert recovered["id"] != snapshot["id"]

    gate = _worker_call(database, "gate_agent_action_dispatch_final_v1", (
        recovered["id"], recovered["execution_token"],
        recovered["state_version"], recovered["request_hash"], None,
        "runtime_read:search_knowledge", 1, "agent-runtime-policy-v1",
        "idempotent_replay",
    ))
    assert gate["outcome"] == "dispatch_authorized"

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT status FROM agent_action_attempts WHERE id=%s",
            (snapshot["id"],),
        ).fetchone()[0] == "failed"
        assert connection.execute(
            "SELECT count(*) FROM agent_safe_action_activations "
            "WHERE action_id=%s",
            (ids["action"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM agent_policy_receipts "
            "WHERE attempt_id=%s",
            (recovered["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_org_rollout WHERE org_id=%s",
            (ids["snapshot"]["org_id"],),
        ).fetchone()[0] == 0

    _rollback(database, ROLLBACK)
    _apply(database, MIGRATION)
