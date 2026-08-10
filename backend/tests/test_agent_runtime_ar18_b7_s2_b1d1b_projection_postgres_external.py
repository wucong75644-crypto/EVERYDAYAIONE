from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external import (
    _bound_run,
    _seed_wecom_targets,
    _setup as _delivery_setup,
    _terminal,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b1_context_postgres_external import _apply_v2


pytestmark = pytest.mark.external
MIGRATION = "227_36_agent_runtime_scheduled_web_projection.sql"
ROLLBACK = "rollback/227_36_agent_runtime_scheduled_web_projection_rollback.sql"


def _setup(url: str) -> None:
    _delivery_setup(url)
    _apply(url, MIGRATION)


def _projection_rpc(url: str, function: str, params: tuple) -> dict:
    projection_url = url.replace("postgres@", "everydayai_projection_worker@")
    placeholders = ",".join(("%s",) * len(params))
    with psycopg.connect(projection_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','projection',false)")
        return conn.execute(
            f"SELECT {function}({placeholders})", params,
        ).fetchone()[0]


def _finalized(url: str, *, terminal: str = "completed", target: dict | None = None):
    facts = _bound_run(url, target)
    _, finalization = _terminal(url, facts, terminal)
    result = _apply_v2(
        url, finalization, next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert result["outcome"] == "applied"
    return facts, result


def _claim(url: str) -> dict:
    return _projection_rpc(url, "claim_agent_runtime_scheduled_web_projection_v1", (
        "projection-test", str(uuid4()), 60,
    ))


def test_web_apply_readback_wakeup_failure_and_no_chat_side_effect(database: str) -> None:
    _setup(database)
    facts, finalization = _finalized(database)
    with psycopg.connect(database) as conn:
        before = conn.execute(
            "SELECT (SELECT count(*) FROM conversations),(SELECT count(*) FROM messages)",
        ).fetchone()
    claim = _claim(database)
    assert claim["outcome"] == "claimed"
    assert claim["task_status"] == finalization["task_status"]
    applied = _projection_rpc(
        database, "apply_agent_runtime_scheduled_web_projection_v1",
        (claim["intent_id"], claim["claim_token"], claim["state_version"]),
    )
    assert applied["outcome"] == "projected"
    assert applied["scheduled_run_id"] == facts["scheduled_run_id"]
    readback = _projection_rpc(
        database, "get_agent_runtime_scheduled_web_projection_v1",
        (claim["intent_id"],),
    )
    assert readback["outcome"] == "projected"
    assert readback["projection_receipt_hash"] == applied["projection_receipt_hash"]
    completed = _projection_rpc(
        database, "complete_agent_runtime_scheduled_web_wakeup_v1",
        (claim["intent_id"], claim["claim_token"], claim["state_version"],
         False, "ws_not_connected"),
    )
    assert completed["outcome"] == "completed"
    assert completed["wakeup_result"] == "failed"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT (SELECT count(*) FROM conversations),(SELECT count(*) FROM messages)",
        ).fetchone() == before
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_web_wakeup_attempts",
        ).fetchone()[0] == 1


def test_wecom_unclaimed_inactive_member_and_concurrent_single_winner(database: str) -> None:
    _setup(database)
    _seed_wecom_targets(database)
    _finalized(database, target={"type": "wecom_user", "wecom_userid": "runtime-user"})
    assert _claim(database)["outcome"] == "not_found"
    facts, _ = _finalized(database)

    def concurrent_claim(_index: int) -> dict:
        return _claim(database)

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(concurrent_claim, range(50)))
    winners = [row for row in results if row["outcome"] == "claimed"]
    assert len(winners) == 1
    winner = winners[0]
    applied = _projection_rpc(
        database, "apply_agent_runtime_scheduled_web_projection_v1",
        (winner["intent_id"], winner["claim_token"], winner["state_version"]),
    )
    assert applied["outcome"] == "projected"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_scheduled_web_projection_receipts "
            "SET claim_lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE intent_id=%s", (winner["intent_id"],),
        )
        conn.commit()
    recovered = _claim(database)
    assert recovered["outcome"] == "claimed"
    assert recovered["projected_at"] is not None
    assert recovered["projection_receipt_hash"] == applied["projection_receipt_hash"]
    _projection_rpc(
        database, "complete_agent_runtime_scheduled_web_wakeup_v1",
        (recovered["intent_id"], recovered["claim_token"], recovered["state_version"],
         True, None),
    )

    inactive_facts, _ = _finalized(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",
            (ORG, USER),
        )
        conn.commit()
    unavailable = _claim(database)
    assert unavailable["outcome"] == "unavailable"
    assert unavailable["reason_code"] == "delivery_member_unavailable"
    assert unavailable["intent_id"] is not None
    assert inactive_facts["scheduled_run_id"]


def test_acl_rls_rollback_guard_cleanup_reapply_and_rollback(database: str) -> None:
    _setup(database)
    with psycopg.connect(database) as conn:
        for table in (
            "agent_runtime_scheduled_web_projection_receipts",
            "agent_runtime_scheduled_web_wakeup_attempts",
        ):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True)
            for role in (
                "everydayai_worker", "everydayai_agent_runtime_worker",
                "everydayai_projection_worker", "everydayai_authorization_worker",
                "everydayai_sandbox_worker",
            ):
                assert conn.execute(
                    "SELECT has_table_privilege(%s,%s,'SELECT')", (role, table),
                ).fetchone()[0] is False
        functions = (
            "claim_agent_runtime_scheduled_web_projection_v1(text,uuid,integer)",
            "apply_agent_runtime_scheduled_web_projection_v1(uuid,uuid,bigint)",
            "read_agent_runtime_scheduled_web_projection_claim_v1(uuid)",
            "get_agent_runtime_scheduled_web_projection_v1(uuid)",
            "complete_agent_runtime_scheduled_web_wakeup_v1(uuid,uuid,bigint,boolean,text)",
        )
        for function in functions:
            assert conn.execute(
                "SELECT proconfig FROM pg_proc WHERE oid=%s::regprocedure", (function,),
            ).fetchone()[0] == ["search_path=pg_catalog, public"]
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_projection_worker',%s,'EXECUTE')",
                (function,),
            ).fetchone()[0] is True
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')",
                (function,),
            ).fetchone()[0] is False

    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _finalized(database)
    assert _claim(database)["outcome"] == "claimed"
    with pytest.raises(Exception, match="PROJECTION_ROLLBACK_HAS_FACTS"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_runtime_scheduled_web_wakeup_attempts")
        conn.execute("DELETE FROM agent_runtime_scheduled_web_projection_receipts")
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("org_id", str(uuid4())),
        ("runtime_run_id", str(uuid4())),
        ("target_hash", "e" * 64),
        ("content_identity_hash", "f" * 64),
    ),
)
def test_apply_tenant_target_content_and_run_fences(
    database: str, field: str, bad_value: str,
) -> None:
    _setup(database)
    _finalized(database)
    claim = _claim(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET session_replication_role=replica")
        conn.execute(
            f"UPDATE agent_runtime_scheduled_web_projection_receipts SET {field}=%s "
            "WHERE intent_id=%s", (bad_value, claim["intent_id"]),
        )
        conn.commit()
    with pytest.raises(Exception, match="PROJECTION_APPLY_FENCED"):
        _projection_rpc(
            database, "apply_agent_runtime_scheduled_web_projection_v1",
            (claim["intent_id"], claim["claim_token"], claim["state_version"]),
        )


def test_expired_old_claim_cannot_apply_or_complete_after_reclaim(database: str) -> None:
    _setup(database)
    _finalized(database)
    old_claim = _claim(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_scheduled_web_projection_receipts "
            "SET claim_lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE intent_id=%s", (old_claim["intent_id"],),
        )
        conn.commit()
    winner = _claim(database)
    assert winner["outcome"] == "claimed"
    assert winner["claim_token"] != old_claim["claim_token"]
    assert winner["claim_request_id"] != old_claim["claim_request_id"]
    applied = _projection_rpc(
        database, "apply_agent_runtime_scheduled_web_projection_v1",
        (winner["intent_id"], winner["claim_token"], winner["state_version"]),
    )
    assert applied["outcome"] == "projected"
    with pytest.raises(Exception, match="PROJECTION_CLAIM_FENCED"):
        _projection_rpc(
            database, "apply_agent_runtime_scheduled_web_projection_v1",
            (old_claim["intent_id"], old_claim["claim_token"],
             old_claim["state_version"]),
        )
    with pytest.raises(Exception, match="WAKEUP_CLAIM_FENCED"):
        _projection_rpc(
            database, "complete_agent_runtime_scheduled_web_wakeup_v1",
            (old_claim["intent_id"], old_claim["claim_token"],
             old_claim["state_version"], True, None),
        )
    completed = _projection_rpc(
        database, "complete_agent_runtime_scheduled_web_wakeup_v1",
        (winner["intent_id"], winner["claim_token"], winner["state_version"],
         True, None),
    )
    assert completed["outcome"] == "completed"
    with pytest.raises(Exception, match="WAKEUP_CLAIM_FENCED"):
        _projection_rpc(
            database, "complete_agent_runtime_scheduled_web_wakeup_v1",
            (old_claim["intent_id"], old_claim["claim_token"],
             old_claim["state_version"], True, None),
        )
