from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import _finalize
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _facts,
    _outcome_params,
    _owner,
    _rpc,
    _setup as _outcome_setup,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _identity,
    _prepare_params,
    _start_params,
)


pytestmark = pytest.mark.external
MIGRATION_41 = "227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql"
MIGRATION_42 = "227_42_agent_runtime_scheduled_wecom_continuation_claim.sql"
MIGRATION = "227_43_agent_runtime_scheduled_wecom_reconcile_still_unknown.sql"
ROLLBACK = "227_43_agent_runtime_scheduled_wecom_reconcile_still_unknown_rollback.sql"
SIGNATURE = (
    "record_agent_runtime_scheduled_wecom_reconcile_result_v1(uuid,uuid,uuid,uuid,uuid,"
    "uuid,text,bigint,bigint,text,text,bigint,text,text,text,text,jsonb,integer)"
)


def _setup(url: str) -> None:
    _outcome_setup(url)
    _apply(url, MIGRATION_41)
    _apply(url, MIGRATION_42)
    _apply(url, MIGRATION)


def _start_v2(url: str) -> tuple[dict, dict]:
    _finalize(url, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _rpc(
        url, "claim_agent_runtime_scheduled_wecom_delivery_v2",
        (str(uuid4()), "dispatch-worker", 60),
    )
    assert claim["outcome"] == "claimed"
    item = _owner(
        url,
        "SELECT jsonb_build_object('id',item.id,'version',item.state_version) "
        "FROM agent_runtime_scheduled_wecom_delivery_items item "
        "WHERE item.intent_id=%s ORDER BY ordinal LIMIT 1",
        (claim["intent_id"],),
    )
    prepared = _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    started = _rpc(
        url, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(url, claim, item, prepared),
    )
    return claim, started


def _unknown_claim(url: str, worker: str = "reconciler") -> tuple[dict, dict, dict]:
    dispatch_claim, started = _start_v2(url)
    unknown = _rpc(
        url,
        "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(url, dispatch_claim, started, "unknown"),
    )
    claim = _rpc(
        url,
        "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (str(uuid4()), worker, 60),
    )
    assert claim["outcome"] == "claimed"
    return started, unknown, claim


def _readback_hash(
    url: str, claim: dict, metadata: dict, code: str | None = "not_found",
    result: str = "still_unknown", readback_type: str = "wecom_app",
) -> str:
    return str(_owner(
        url,
        "SELECT _agent_runtime_scheduled_wecom_reconcile_readback_hash("
        "%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (
            result, readback_type, code, Jsonb(metadata), claim["provider_request_id"],
            claim["idempotency_key"], claim["provider_revision"],
        ),
    ))


def _result_params(
    url: str, claim: dict, *, request_id: str | None = None, delay: int = 300,
    metadata: dict | None = None, code: str | None = "not_found",
) -> tuple[object, ...]:
    metadata = metadata or {"http_status": 200, "wecom_errcode": 0, "trace_id": "readback-1"}
    readback_hash = _readback_hash(url, claim, metadata, code)
    return (
        request_id or str(uuid4()), claim["request_id"], claim["intent_id"], claim["item_id"],
        claim["attempt_id"], claim["reconcile_token"], claim["worker_id"],
        claim["delivery_state_version"], claim["item_state_version"],
        claim["provider_request_id"], claim["idempotency_key"], claim["provider_revision"],
        "still_unknown", "wecom_app", readback_hash, code, Jsonb(metadata), delay,
    )


def _record(url: str, params: tuple[object, ...]) -> dict:
    return _rpc(url, "record_agent_runtime_scheduled_wecom_reconcile_result_v1", params)


def test_still_unknown_is_atomic_append_only_and_response_loss_safe(database: str) -> None:
    _setup(database)
    started, _, claim = _unknown_claim(database)
    attempt_before = _owner(
        database,
        "SELECT to_jsonb(a) FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE id=%s",
        (started["attempt_id"],),
    )
    attempt_count = _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    )
    params = _result_params(database, claim)
    recorded = _record(database, params)
    replay = _record(database, params)
    assert recorded["outcome"] == "recorded" and replay["outcome"] == "readback"
    assert replay["readback_hash"] == recorded["readback_hash"]
    assert recorded["delivery_status"] == recorded["item_status"] == "reconcile_required"
    state = _owner(
        database,
        "SELECT jsonb_build_object('delivery',d.status,'item',item.status,"
        "'delivery_next',d.next_attempt_at,'item_next',item.next_attempt_at,"
        "'reconcile_request',d.reconcile_request_id,'reconcile_token',d.reconcile_token,"
        "'reconcile_worker',d.reconcile_worker_id,'reconcile_expiry',d.reconcile_lease_expires_at) "
        "FROM agent_runtime_scheduled_wecom_deliveries d JOIN "
        "agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "WHERE item.id=%s",
        (started["item_id"],),
    )
    assert state == {
        "delivery": "reconcile_required", "item": "reconcile_required",
        "delivery_next": recorded["next_attempt_at"], "item_next": recorded["next_attempt_at"],
        "reconcile_request": None, "reconcile_token": None, "reconcile_worker": None,
        "reconcile_expiry": None,
    }
    assert _owner(
        database,
        "SELECT to_jsonb(a) FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE id=%s",
        (started["attempt_id"],),
    ) == attempt_before
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    ) == attempt_count


def test_fifty_concurrent_results_have_one_transition(database: str) -> None:
    _setup(database)
    _, _, claim = _unknown_claim(database)
    barrier = Barrier(50)

    def compete(index: int) -> str:
        params = _result_params(database, claim, request_id=str(uuid4()))
        barrier.wait()
        try:
            return _record(database, params)["outcome"]
        except Exception as error:  # noqa: BLE001 - expected one-result-per-claim conflict.
            assert "RECONCILE_RESULT_REQUEST_CONFLICT" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(compete, range(50)))
    assert outcomes.count("recorded") == 1 and outcomes.count("conflict") == 49
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_result_requests",
    ) == 1


def test_target_drift_and_just_expired_lease_still_record(database: str) -> None:
    _setup(database)
    _, _, claim = _unknown_claim(database)
    params = _result_params(database, claim)
    _owner(
        database, "UPDATE org_members SET status='disabled' WHERE org_id=%s RETURNING user_id", (ORG,),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET "
        "reconcile_lease_expires_at=clock_timestamp()-interval '1 millisecond' "
        "WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    assert _record(database, params)["outcome"] == "recorded"


@pytest.mark.parametrize("drift", ("token", "worker", "delivery_version", "item_version"))
def test_takeover_identity_and_state_versions_fence(database: str, drift: str) -> None:
    _setup(database)
    _, _, claim = _unknown_claim(database)
    params = _result_params(database, claim)
    assignments = {
        "token": "reconcile_token=gen_random_uuid()",
        "worker": "reconcile_worker_id='takeover-worker'",
        "delivery_version": "state_version=state_version+1",
    }
    if drift == "item_version":
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_delivery_items SET state_version=state_version+1 "
            "WHERE id=%s RETURNING id",
            (claim["item_id"],),
        )
    else:
        _owner(
            database,
            f"UPDATE agent_runtime_scheduled_wecom_deliveries SET {assignments[drift]} "
            "WHERE intent_id=%s RETURNING intent_id",
            (claim["intent_id"],),
        )
    assert _record(database, params)["outcome"] == "fenced"
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_result_requests",
    ) == 0


def test_expired_takeover_fences_old_claim_and_new_claim_records(database: str) -> None:
    _setup(database)
    _, _, old = _unknown_claim(database, "old-worker")
    old_params = _result_params(database, old)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET "
        "reconcile_lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id",
        (old["intent_id"],),
    )
    new = _rpc(
        database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (str(uuid4()), "new-worker", 60),
    )
    assert new["outcome"] == "claimed" and new["attempt_id"] == old["attempt_id"]
    assert _record(database, old_params)["outcome"] == "fenced"
    assert _record(database, _result_params(database, new))["outcome"] == "recorded"


def test_two_still_unknown_rounds_reenter_227_41_without_attempt_or_resubmit(
    database: str,
) -> None:
    _setup(database)
    started, _, claim = _unknown_claim(database)
    attempt_before = _owner(
        database,
        "SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM agent_runtime_scheduled_wecom_dispatch_attempts a",
    )
    unknown_at = _owner(
        database,
        "SELECT unknown_at FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s",
        (started["attempt_id"],),
    )
    first_result = _record(database, _result_params(database, claim, delay=5))
    assert first_result["outcome"] == "recorded"
    assert _rpc(
        database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (str(uuid4()), "too-early", 60),
    )["outcome"] == "empty"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET next_attempt_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET next_attempt_at=clock_timestamp()-interval '1 second' "
        "WHERE id=%s RETURNING id",
        (claim["item_id"],),
    )
    reclaimed = _rpc(
        database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (str(uuid4()), "due-again", 60),
    )
    assert reclaimed["outcome"] == "claimed" and reclaimed["attempt_id"] == started["attempt_id"]
    assert reclaimed["item_status"] == "reconcile_required"
    second_result = _record(database, _result_params(database, reclaimed, delay=5))
    assert second_result["outcome"] == "recorded"
    assert second_result["request_id"] != first_result["request_id"]
    assert _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_result_requests "
        "WHERE attempt_id=%s",
        (started["attempt_id"],),
    ) == 2
    assert _owner(
        database,
        "SELECT unknown_at FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s",
        (started["attempt_id"],),
    ) == unknown_at
    assert _owner(
        database,
        "SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM agent_runtime_scheduled_wecom_dispatch_attempts a",
    ) == attempt_before


def test_hash_metadata_result_delay_and_null_validation_are_zero_fact(database: str) -> None:
    _setup(database)
    _, _, claim = _unknown_claim(database)
    valid = list(_result_params(database, claim))
    invalid_cases: list[list[object]] = []
    for index in range(len(valid)):
        invalid = valid.copy()
        invalid[index] = None
        invalid_cases.append(invalid)
    for index, value in ((12, "accepted"), (14, "0" * 64), (17, 4), (17, 86401)):
        invalid = valid.copy()
        invalid[index] = value
        invalid_cases.append(invalid)
    bad_metadata = valid.copy()
    bad_metadata[16] = Jsonb({"message": "free text is forbidden"})
    invalid_cases.append(bad_metadata)
    before = _facts(database)
    for invalid in invalid_cases:
        with pytest.raises(Exception, match="RECONCILE_RESULT_INVALID"):
            _record(database, tuple(invalid))
    assert _facts(database) == before
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_result_requests",
    ) == 0


def test_result_request_namespace_is_bidirectional(database: str) -> None:
    _setup(database)
    _, unknown, claim = _unknown_claim(database)
    with pytest.raises(Exception, match="RECONCILE_RESULT_REQUEST_CONFLICT"):
        _record(database, _result_params(database, claim, request_id=unknown["request_id"]))
    result_request_id = str(uuid4())
    assert _record(
        database, _result_params(database, claim, request_id=result_request_id),
    )["outcome"] == "recorded"

    dispatch_claim, started = _start_v2(database)
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _rpc(
            database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
            _outcome_params(
                database, dispatch_claim, started, "accepted", request_id=result_request_id,
            ),
        )


def test_acl_rls_immutability_and_exact_rollback(database: str) -> None:
    _setup(database)
    assert _owner(
        database,
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid='agent_runtime_scheduled_wecom_reconcile_result_requests'::regclass",
    ) is True
    assert _owner(
        database,
        "SELECT NOT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL "
        "aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl "
        "WHERE c.oid='agent_runtime_scheduled_wecom_reconcile_result_requests'::regclass "
        "AND acl.grantee=0 AND acl.privilege_type IN('SELECT','INSERT','UPDATE','DELETE'))",
    ) is True
    assert _owner(
        database,
        "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    for role in ("everydayai_runtime", "everydayai_worker"):
        assert _owner(database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')", (role, SIGNATURE))
        assert _owner(
            database,
            "SELECT NOT has_table_privilege(%s,'agent_runtime_scheduled_wecom_reconcile_result_requests',"
            "'SELECT,INSERT,UPDATE,DELETE')",
            (role,),
        ) is True
    with pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        _rpc(database, "record_agent_runtime_scheduled_wecom_reconcile_result_v1", (None,) * 18,
             access_kind="legacy")
    _rollback(database, ROLLBACK)
    assert _owner(
        database, "SELECT to_regclass('agent_runtime_scheduled_wecom_reconcile_result_requests')",
    ) is None
    _apply(database, MIGRATION)
    _, _, claim = _unknown_claim(database)
    recorded = _record(database, _result_params(database, claim))
    with pytest.raises(Exception, match="RECONCILE_RESULT_IMMUTABLE"):
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_reconcile_result_requests SET delay_seconds=60 "
            "WHERE request_id=%s RETURNING request_id",
            (recorded["request_id"],),
        )
    with pytest.raises(Exception, match="RECONCILE_RESULT_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
