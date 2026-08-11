from concurrent.futures import ThreadPoolExecutor
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


def test_concurrent_claim_returns_only_the_locked_tenant(database: str) -> None:
    _setup(database)
    _unknown(database)
    requests = [str(uuid4()) for _ in range(30)]
    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(
            lambda pair: _claim(database, f"org-race-{pair[0]}", pair[1]),
            enumerate(requests),
        ))
    winners = [result for result in results if result["outcome"] == "claimed"]
    assert len(winners) == 1
    assert winners[0]["org_id"] == str(ORG)
    assert sum(result["outcome"] == "empty" for result in results) == 29


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
