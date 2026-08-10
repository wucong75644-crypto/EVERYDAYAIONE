from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _install_final_result,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external import (
    _claim,
    _link_artifact,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b1_context_postgres_external import _apply_v2
from tests.test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external import (
    _bound_run,
    _seed_wecom_targets,
    _setup as _delivery_setup,
)
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc


pytestmark = pytest.mark.external
MIGRATION = "227_37_agent_runtime_scheduled_wecom_delivery.sql"
ROLLBACK = "rollback/227_37_agent_runtime_scheduled_wecom_delivery_rollback.sql"


def _setup(url: str) -> None:
    _delivery_setup(url)
    _apply(url, MIGRATION)
    _seed_wecom_targets(url)


def _finalize(url: str, target: dict) -> dict:
    facts = _bound_run(url, target)
    result_hash = _install_final_result(url, facts)
    assert _rpc(url, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    finalization = _claim(url)
    assert _apply_v2(
        url, finalization,
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )["outcome"] == "applied"
    return facts


@pytest.mark.parametrize(
    ("target", "channel"),
    (
        ({"type": "wecom_user", "wecom_userid": "runtime-user"}, "app"),
        ({"type": "wecom_user", "wecom_userid": "runtime-user"}, "smart_robot"),
        ({"type": "wecom_group", "chatid": "runtime-group"}, "smart_robot"),
    ),
)
def test_initializes_one_delivery_and_text_identity_per_wecom_intent(
    database: str, target: dict, channel: str,
) -> None:
    _setup(database)
    if target["type"] == "wecom_user":
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "UPDATE wecom_user_mappings SET channel=%s "
                "WHERE org_id=%s AND wecom_userid='runtime-user'",
                (channel, ORG),
            )
            conn.commit()
    _finalize(database, target)
    with psycopg.connect(database) as conn:
        delivery = conn.execute(
            "SELECT target_type,target_snapshot,status,state_version,content_identity_hash "
            "FROM agent_runtime_scheduled_wecom_deliveries",
        ).fetchone()
        item = conn.execute(
            "SELECT item_kind,ordinal,status,source_revision,source_identity_hash,item_key "
            "FROM agent_runtime_scheduled_wecom_delivery_items",
        ).fetchone()
        assert delivery[0] == target["type"] and delivery[2:4] == ("pending", 0)
        assert len(delivery[4]) == 64 and delivery[1].get("secret") is None
        assert item[:4] == ("text", 1, "pending", 1)
        assert len(item[4]) == len(item[5]) == 64


def test_web_is_ignored_and_completed_artifacts_create_identity_only_items(database: str) -> None:
    _setup(database)
    facts = _bound_run(database, {"type": "multi", "targets": [
        {"type": "web", "user_id": USER},
        {"type": "wecom_user", "wecom_userid": "runtime-user"},
    ]})
    result_hash = _install_final_result(database, facts)
    artifact = _link_artifact(database, facts)
    assert _rpc(database, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    assert _apply_v2(
        database, _claim(database),
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )["outcome"] == "applied"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_wecom_deliveries",
        ).fetchone()[0] == 1
        items = conn.execute(
            "SELECT item_kind,source_id,source_revision,source_identity_hash "
            "FROM agent_runtime_scheduled_wecom_delivery_items ORDER BY ordinal",
        ).fetchall()
        assert [row[0] for row in items] == ["text", "artifact_identity"]
        assert str(items[1][1]) == artifact["artifact_id"] and items[1][2] == 1
        serialized = json.dumps(items, default=str).lower()
        for forbidden in ("storage_ref", "inline_content", "secret", "/private/", "http"):
            assert forbidden not in serialized


def test_attempt_identity_transition_ambiguity_and_receipt_evidence_are_guarded(
    database: str,
) -> None:
    _setup(database)
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    attempt_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        item_id = conn.execute(
            "SELECT id FROM agent_runtime_scheduled_wecom_delivery_items",
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts("
            "id,item_id,attempt_number,provider_request_id,idempotency_key,provider_revision,"
            "status,dispatch_phase) VALUES(%s,%s,1,'provider-request-1',%s,1,'prepared','prepared')",
            (attempt_id, item_id, "a" * 64),
        )
        conn.commit()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="IDENTITY_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts "
                "SET provider_request_id='provider-request-2' WHERE id=%s", (attempt_id,),
            )
        conn.rollback()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="TRANSITION_INVALID"):
            conn.execute(
                "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='unknown',"
                "dispatch_phase='ambiguous',unknown_at=clock_timestamp() WHERE id=%s",
                (attempt_id,),
            )
        conn.rollback()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='dispatch_started',"
            "dispatch_phase='external_request_started',dispatch_started_at=clock_timestamp() WHERE id=%s",
            (attempt_id,),
        )
        conn.execute(
            "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='unknown',"
            "dispatch_phase='ambiguous',was_ambiguous=true,unknown_at=clock_timestamp() WHERE id=%s",
            (attempt_id,),
        )
        conn.execute(
            "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='accepted',"
            "dispatch_phase='receipt_recorded',receipt_type='typed_ack',receipt_hash=%s,"
            "receipt_code='accepted',resolved_at=clock_timestamp() WHERE id=%s",
            ("b" * 64, attempt_id),
        )
        conn.commit()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        row = conn.execute(
            "SELECT status,was_ambiguous,receipt_hash,provider_request_id,idempotency_key "
            "FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s", (attempt_id,),
        ).fetchone()
        assert row == ("accepted", True, "b" * 64, "provider-request-1", "a" * 64)
        with pytest.raises(Exception, match="TRANSITION_INVALID|RECEIPT_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET receipt_hash=%s WHERE id=%s",
                ("c" * 64, attempt_id),
            )


def test_rls_acl_backfill_and_failure_closed_rollback(database: str) -> None:
    _setup(database)
    for table in (
        "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_delivery_items",
        "agent_runtime_scheduled_wecom_dispatch_attempts",
    ):
        with psycopg.connect(database) as conn:
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True)
            for role in (
                "everydayai_wecom_runtime", "everydayai_projection_worker",
                "everydayai_agent_runtime_worker", "everydayai_worker",
            ):
                assert conn.execute(
                    "SELECT has_table_privilege(%s,%s,'SELECT')", (role, table),
                ).fetchone()[0] is False
    with psycopg.connect(database) as conn:
        for function in (
            "_agent_runtime_scheduled_wecom_identity_guard()",
            "_initialize_agent_runtime_scheduled_wecom_delivery()",
        ):
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
                (function,),
            ).fetchone()[0] is False
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    with pytest.raises(Exception, match="ROLLBACK_HAS_FACTS"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "TRUNCATE agent_runtime_scheduled_wecom_dispatch_attempts,"
            "agent_runtime_scheduled_wecom_delivery_items,"
            "agent_runtime_scheduled_wecom_deliveries",
        )
        conn.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_delivery_intents",
        ).fetchone()[0] == 1
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "TRUNCATE agent_runtime_scheduled_delivery_intents,"
            "agent_runtime_scheduled_delivery_contents,"
            "agent_runtime_scheduled_delivery_runtime_bindings,"
            "agent_runtime_scheduled_delivery_targets,"
            "agent_runtime_scheduled_delivery_snapshots",
        )
        conn.commit()
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_delivery_intents",
        ).fetchone()[0] == 0


def test_existing_wecom_intent_requires_explicit_backfill(database: str) -> None:
    _delivery_setup(database)
    _seed_wecom_targets(database)
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    with pytest.raises(Exception, match="WECOM_BACKFILL_REQUIRED"):
        _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_scheduled_wecom_deliveries')",
        ).fetchone()[0] is None
