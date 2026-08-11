from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import _owner, _rpc
from tests.test_agent_runtime_scheduled_wecom_reconcile_claim_postgres_external import _claim
from tests.test_agent_runtime_scheduled_wecom_started_recovery_postgres_external import (
    _recover,
    _setup as _started_setup,
    _started,
)


pytestmark = pytest.mark.external
MIGRATION = "227_52_agent_runtime_scheduled_wecom_reconcile_org.sql"
ROLLBACK = "227_52_agent_runtime_scheduled_wecom_reconcile_org_rollback.sql"
CONFIGURATION_BASE = (
    "158_configuration_control_plane_foundation.sql",
    "159_configuration_management_core.sql",
    "160_configuration_resolution_core.sql",
    "160_configuration_resolution_facades.sql",
    "201_wecom_callback_inbox.sql",
)
SIGNATURES = (
    "claim_agent_runtime_scheduled_wecom_reconcile_v1(uuid,text,integer)",
    "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1(uuid,uuid,uuid,text,bigint,integer)",
    "read_agent_runtime_scheduled_wecom_reconcile_v1(uuid)",
)
HELPER_SIGNATURE = (
    "_agent_runtime_scheduled_wecom_reconcile_json("
    "agent_runtime_scheduled_wecom_reconcile_claim_requests,"
    "agent_runtime_scheduled_wecom_deliveries,"
    "agent_runtime_scheduled_wecom_delivery_items,"
    "agent_runtime_scheduled_wecom_dispatch_attempts,text)"
)


def _setup(url: str) -> None:
    _started_setup(url)
    _apply(url, "227_49_agent_runtime_scheduled_wecom_unicode_payload.sql")
    for migration in CONFIGURATION_BASE:
        _apply(url, migration)
    for migration in (
        "227_50_agent_runtime_scheduled_wecom_configuration_facade.sql",
        "227_51_agent_runtime_scheduled_wecom_prepared_payload.sql",
        MIGRATION,
    ):
        _apply(url, migration)


def _unknown(url: str) -> None:
    _started(url)
    recovered = _recover(url)
    assert recovered["outcome"] == "recovered"


def _add_unknown_tenant(url: str, org_id: str) -> str:
    intent_id = str(uuid4())
    target_key = f"wecom_user:{uuid4()}"
    target_hash = hashlib.sha256(target_key.encode()).hexdigest()
    intent_key = hashlib.sha256(f"intent:{intent_id}".encode()).hexdigest()
    attempt_key = hashlib.sha256(f"attempt:{intent_id}".encode()).hexdigest()
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO organizations(id,status) VALUES(%s,'active')", (org_id,),
        )
        connection.execute(
            "INSERT INTO agent_runtime_scheduled_delivery_targets("
            "scheduled_run_id,target_key,target_hash,target_type,target_snapshot,ordinal) "
            "SELECT scheduled_run_id,%s,%s,'wecom_user',"
            "jsonb_build_object('type','wecom_user','org_id',%s::uuid,"
            "'wecom_userid','tenant-b'),2 FROM agent_runtime_scheduled_delivery_targets LIMIT 1",
            (target_key, target_hash, org_id),
        )
        connection.execute(
            "INSERT INTO agent_runtime_scheduled_delivery_intents("
            "id,scheduled_run_id,target_key,target_hash,runtime_run_id,scheduled_task_id,org_id,"
            "user_id,terminal_status,result_hash,reason_code,content_identity_hash,"
            "finalization_request_id,finalization_application_hash,idempotency_key) "
            "SELECT %s,scheduled_run_id,%s,%s,runtime_run_id,scheduled_task_id,%s,user_id,"
            "terminal_status,result_hash,reason_code,content_identity_hash,"
            "finalization_request_id,finalization_application_hash,%s "
            "FROM agent_runtime_scheduled_delivery_intents LIMIT 1",
            (intent_id, target_key, target_hash, org_id, intent_key),
        )
        item_id = connection.execute(
            "SELECT id FROM agent_runtime_scheduled_wecom_delivery_items "
            "WHERE intent_id=%s AND ordinal=1", (intent_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts("
            "item_id,attempt_number,provider_request_id,idempotency_key,provider_revision,"
            "status,dispatch_phase,claim_request_id,lease_token,claim_worker_id,"
            "prepared_delivery_state_version,prepared_item_state_version,was_ambiguous,"
            "dispatch_started_at,unknown_at) "
            "VALUES(%s,1,%s,%s,1,'unknown','ambiguous',%s,%s,'tenant-b-dispatch',1,1,true,"
            "clock_timestamp()-interval '2 seconds',clock_timestamp()-interval '1 second')",
            (item_id, f"tenant-b-{uuid4()}", attempt_key, str(uuid4()), str(uuid4())),
        )
        connection.execute(
            "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='unknown',"
            "state_version=1,next_attempt_at=clock_timestamp()-interval '1 second' "
            "WHERE intent_id=%s", (intent_id,),
        )
        connection.execute(
            "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='unknown',"
            "state_version=1,next_attempt_at=clock_timestamp()-interval '1 second' "
            "WHERE id=%s", (item_id,),
        )
        connection.commit()
    return intent_id


def _read(url: str, request_id: str) -> dict:
    return _rpc(url, "read_agent_runtime_scheduled_wecom_reconcile_v1", (request_id,))


def _security_shape(url: str) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object("
        "'helper',(SELECT jsonb_build_object('owner',pg_get_userbyid(p.proowner),"
        "'security_definer',p.prosecdef,'volatility',p.provolatile,'config',p.proconfig,"
        "'acl',p.proacl) FROM pg_proc p WHERE p.oid=%s::regprocedure),"
        "'rpcs',(SELECT jsonb_agg(jsonb_build_object('signature',entry.signature,"
        "'owner',pg_get_userbyid(p.proowner),'security_definer',p.prosecdef,"
        "'volatility',p.provolatile,'config',p.proconfig,'acl',p.proacl) ORDER BY entry.signature)"
        " FROM unnest(%s::text[]) entry(signature) JOIN pg_proc p"
        " ON p.oid=entry.signature::regprocedure))",
        (HELPER_SIGNATURE, list(SIGNATURES)),
    )


def _facts(url: str, request_id: str) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object("
        "'request',(SELECT to_jsonb(r) FROM "
        "agent_runtime_scheduled_wecom_reconcile_claim_requests r WHERE request_id=%s),"
        "'delivery',(SELECT to_jsonb(d) FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_reconcile_claim_requests r "
        "ON r.intent_id=d.intent_id WHERE r.request_id=%s),"
        "'request_count',(SELECT count(*) FROM "
        "agent_runtime_scheduled_wecom_reconcile_claim_requests))",
        (request_id, request_id),
    )


def test_claim_read_renew_org_is_immutable_and_lease_fenced(database: str) -> None:
    _setup(database)
    _unknown(database)
    claimed = _claim(database, "org-reconciler")
    assert claimed["outcome"] == "claimed" and claimed["org_id"] == str(ORG)
    replay = _claim(database, "org-reconciler", claimed["request_id"])
    readback = _read(database, claimed["request_id"])
    assert replay["outcome"] == readback["outcome"] == "readback"
    assert replay["org_id"] == readback["org_id"] == claimed["org_id"]

    renewed = _rpc(
        database, "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
        (
            claimed["intent_id"], claimed["request_id"], claimed["reconcile_token"],
            claimed["worker_id"], claimed["delivery_state_version"], 90,
        ),
    )
    assert renewed["outcome"] == "renewed" and renewed["org_id"] == str(ORG)
    stale = _rpc(
        database, "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
        (
            claimed["intent_id"], claimed["request_id"], claimed["reconcile_token"],
            claimed["worker_id"], claimed["delivery_state_version"], 90,
        ),
    )
    assert stale == {"outcome": "fenced"}

    other_org = str(uuid4())
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO organizations(id,status) VALUES(%s,'active')", (other_org,),
        )
        with pytest.raises(psycopg.Error, match="WECOM_IDENTITY_IMMUTABLE"):
            connection.execute(
                "UPDATE agent_runtime_scheduled_wecom_deliveries SET org_id=%s "
                "WHERE intent_id=%s", (other_org, claimed["intent_id"]),
            )
    assert _read(database, claimed["request_id"])["org_id"] == str(ORG)


def test_concurrent_claims_bind_each_receipt_to_its_actual_tenant(database: str) -> None:
    _setup(database)
    _unknown(database)
    other_org = str(uuid4())
    other_intent = _add_unknown_tenant(database, other_org)
    expected = _owner(
        database,
        "SELECT jsonb_object_agg(intent_id::text,org_id::text) FROM "
        "agent_runtime_scheduled_wecom_deliveries WHERE status='unknown'",
    )
    assert len(expected) == 2 and expected[other_intent] == other_org
    requests = [str(uuid4()) for _ in range(2)]
    barrier = Barrier(2)

    def claim(pair: tuple[int, str]) -> dict:
        barrier.wait()
        return _claim(database, f"org-race-{pair[0]}", pair[1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            claim, enumerate(requests),
        ))
    assert all(result["outcome"] == "claimed" for result in results)
    assert {result["intent_id"] for result in results} == set(expected)
    for result in results:
        bound = _owner(
            database,
            "SELECT jsonb_build_object('org_id',d.org_id,'item_id',item.id,"
            "'attempt_id',attempt.id) FROM agent_runtime_scheduled_wecom_deliveries d "
            "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
            "JOIN agent_runtime_scheduled_wecom_dispatch_attempts attempt ON attempt.item_id=item.id "
            "WHERE d.intent_id=%s", (result["intent_id"],),
        )
        assert result["org_id"] == expected[result["intent_id"]] == bound["org_id"]
        assert result["item_id"] == bound["item_id"]
        assert result["attempt_id"] == bound["attempt_id"]


def test_rollback_reapply_rollback_restores_output_without_acl_or_fact_drift(
    database: str,
) -> None:
    _setup(database)
    _unknown(database)
    claimed = _claim(database, "rollback-reconciler")
    request_id = claimed["request_id"]
    security = _security_shape(database)
    facts = _facts(database, request_id)

    _rollback(database, ROLLBACK)
    assert "org_id" not in _read(database, request_id)
    assert _security_shape(database) == security and _facts(database, request_id) == facts
    _apply(database, MIGRATION)
    assert _read(database, request_id)["org_id"] == str(ORG)
    assert _security_shape(database) == security and _facts(database, request_id) == facts
    _rollback(database, ROLLBACK)
    assert "org_id" not in _read(database, request_id)
    assert _security_shape(database) == security and _facts(database, request_id) == facts
