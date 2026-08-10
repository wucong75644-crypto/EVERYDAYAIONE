from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import _finalize
from tests.test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external import (
    _bound_run,
    _terminal,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b1_context_postgres_external import _apply_v2
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _claim,
    _context,
    _set_user_target,
    _setup,
)


pytestmark = pytest.mark.external


def _corrupt_terminal_contract(url: str, scheduled_run_id: str, mutation: str) -> None:
    statements: dict[str, tuple[str, tuple[object, ...]]] = {
        "finalization_status": (
            "UPDATE agent_runtime_scheduled_finalization_intents SET status='reconcile_required',"
            "application_request_id=NULL,application_hash=NULL,application_receipt=NULL,applied_at=NULL "
            "WHERE scheduled_run_id=%s",
            (scheduled_run_id,),
        ),
        "binding_owner_status": (
            "UPDATE agent_runtime_scheduled_run_bindings SET owner_status='reconcile_required' "
            "WHERE scheduled_run_id=%s",
            (scheduled_run_id,),
        ),
        "agent_run_status": (
            "UPDATE agent_runs SET status='failed' WHERE id=(SELECT runtime_run_id "
            "FROM agent_runtime_scheduled_finalization_intents WHERE scheduled_run_id=%s)",
            (scheduled_run_id,),
        ),
        "agent_run_state_version": (
            "UPDATE agent_runs SET state_version=state_version+1 WHERE id=(SELECT runtime_run_id "
            "FROM agent_runtime_scheduled_finalization_intents WHERE scheduled_run_id=%s)",
            (scheduled_run_id,),
        ),
        "scheduled_run_status": (
            "UPDATE scheduled_task_runs SET status='failed' WHERE id=%s",
            (scheduled_run_id,),
        ),
        "application_request": (
            "UPDATE agent_runtime_scheduled_finalization_intents SET application_request_id=%s "
            "WHERE scheduled_run_id=%s",
            (str(uuid4()), scheduled_run_id),
        ),
        "application_hash": (
            "UPDATE agent_runtime_scheduled_finalization_intents SET application_hash=%s "
            "WHERE scheduled_run_id=%s",
            ("f" * 64, scheduled_run_id),
        ),
        "application_receipt": (
            "UPDATE agent_runtime_scheduled_finalization_intents SET application_receipt="
            "jsonb_set(application_receipt,'{scheduled_run_id}',to_jsonb(%s::TEXT)) "
            "WHERE scheduled_run_id=%s",
            (str(uuid4()), scheduled_run_id),
        ),
    }
    sql, params = statements[mutation]
    with psycopg.connect(url) as conn:
        conn.execute("SET session_replication_role=replica")
        conn.execute(sql, params)
        conn.commit()


@pytest.mark.parametrize(
    ("terminal_status", "scheduled_run_status"),
    (("completed", "success"), ("failed", "failed"), ("cancelled", "skipped")),
)
def test_valid_terminal_mapping_remains_dispatchable(
    database: str, terminal_status: str, scheduled_run_status: str,
) -> None:
    _setup(database)
    _set_user_target(database, "app")
    facts = _bound_run(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"},
    )
    _, finalization = _terminal(database, facts, terminal_status)
    applied = _apply_v2(
        database,
        finalization,
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert applied["outcome"] == "applied"
    claimed = _claim(database, worker=f"terminal-valid-{terminal_status}")
    assert claimed["outcome"] == "claimed"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status FROM scheduled_task_runs WHERE id=%s",
            (facts["scheduled_run_id"],),
        ).fetchone()[0] == scheduled_run_status


@pytest.mark.parametrize(
    "mutation",
    (
        "finalization_status",
        "binding_owner_status",
        "agent_run_status",
        "agent_run_state_version",
        "scheduled_run_status",
        "application_request",
        "application_hash",
        "application_receipt",
    ),
)
def test_terminal_or_application_drift_blocks_only_affected_intent(
    database: str, mutation: str,
) -> None:
    _setup(database)
    _set_user_target(database, "app")
    invalid = _finalize(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"},
    )
    valid = _finalize(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"},
    )
    invalid_claim = _claim(database, worker=f"terminal-invalid-{mutation}")
    with psycopg.connect(database) as conn:
        assert str(conn.execute(
            "SELECT scheduled_run_id FROM agent_runtime_scheduled_wecom_deliveries "
            "WHERE intent_id=%s", (invalid_claim["intent_id"],),
        ).fetchone()[0]) == invalid["scheduled_run_id"]
    _corrupt_terminal_contract(database, invalid["scheduled_run_id"], mutation)
    blocked = _context(database, invalid_claim)
    assert blocked == {
        "outcome": "unavailable",
        "intent_id": invalid_claim["intent_id"],
        "reason_code": "wecom_contract_unavailable",
    }

    claimed = _claim(database, worker=f"terminal-fence-{mutation}")
    assert claimed["outcome"] == "claimed"
    with psycopg.connect(database) as conn:
        rows = conn.execute(
            "SELECT scheduled_run_id,status,terminal_reason_code FROM "
            "agent_runtime_scheduled_wecom_deliveries WHERE scheduled_run_id IN(%s,%s)",
            (invalid["scheduled_run_id"], valid["scheduled_run_id"]),
        ).fetchall()
        statuses = {str(row[0]): (row[1], row[2]) for row in rows}
        claimed_run = conn.execute(
            "SELECT scheduled_run_id FROM agent_runtime_scheduled_wecom_deliveries "
            "WHERE intent_id=%s", (claimed["intent_id"],),
        ).fetchone()[0]
        invalid_items = conn.execute(
            "SELECT array_agg(item.status ORDER BY item.ordinal) FROM "
            "agent_runtime_scheduled_wecom_delivery_items item JOIN "
            "agent_runtime_scheduled_wecom_deliveries delivery ON delivery.intent_id=item.intent_id "
            "WHERE delivery.scheduled_run_id=%s", (invalid["scheduled_run_id"],),
        ).fetchone()[0]
    assert statuses[invalid["scheduled_run_id"]] == (
        "unavailable", "wecom_contract_unavailable",
    )
    assert statuses[valid["scheduled_run_id"]][0] == "claimed"
    assert str(claimed_run) == valid["scheduled_run_id"]
    assert invalid_items == ["cancelled"]
