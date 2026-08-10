from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG
from tests.test_agent_runtime_scheduled_wecom_continuation_claim_postgres_external import (
    _add_second,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _owner,
    _rpc,
)
from tests.test_agent_runtime_scheduled_wecom_reconcile_still_unknown_postgres_external import (
    _readback_hash,
    _record as _record_still_unknown,
    _result_params as _still_unknown_params,
    _setup as _still_unknown_setup,
    _unknown_claim,
)


pytestmark = pytest.mark.external
MIGRATION = "227_44_agent_runtime_scheduled_wecom_reconcile_definitive.sql"
ROLLBACK = "227_44_agent_runtime_scheduled_wecom_reconcile_definitive_rollback.sql"
SIGNATURE = (
    "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1(uuid,uuid,uuid,"
    "uuid,uuid,uuid,text,bigint,bigint,text,text,bigint,text,text,text,text,jsonb)"
)


def _setup(url: str) -> None:
    _still_unknown_setup(url)
    _apply(url, MIGRATION)


def _params(
    url: str,
    claim: dict,
    result: str,
    *,
    request_id: str | None = None,
    metadata: dict | None = None,
    code: str | None = None,
) -> tuple[object, ...]:
    metadata = metadata or {
        "http_status": 200 if result == "accepted" else 400,
        "wecom_errcode": 0 if result == "accepted" else 40013,
        "provider_message_id": f"reconcile-{uuid4().hex}",
    }
    code = code or ("ok" if result == "accepted" else "provider_rejected")
    readback_hash = _readback_hash(url, claim, metadata, code, result=result)
    return (
        request_id or str(uuid4()),
        claim["request_id"],
        claim["intent_id"],
        claim["item_id"],
        claim["attempt_id"],
        claim["reconcile_token"],
        claim["worker_id"],
        claim["delivery_state_version"],
        claim["item_state_version"],
        claim["provider_request_id"],
        claim["idempotency_key"],
        claim["provider_revision"],
        result,
        "wecom_app",
        readback_hash,
        code,
        Jsonb(metadata),
    )


def _record(url: str, params: tuple[object, ...]) -> dict:
    return _rpc(
        url,
        "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1",
        params,
    )


def _state(url: str, attempt_id: str) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object('attempt',a.status,'phase',a.dispatch_phase,"
        "'receipt_type',a.receipt_type,'receipt_hash',a.receipt_hash,'receipt_code',a.receipt_code,"
        "'unknown_at',a.unknown_at,'resolved_at',a.resolved_at,'item',item.status,"
        "'delivery',d.status,'claim_request',d.claim_request_id,'lease',d.lease_token,"
        "'reconcile_request',d.reconcile_request_id,'reconcile_token',d.reconcile_token) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=item.intent_id WHERE a.id=%s",
        (attempt_id,),
    )


@pytest.mark.parametrize(
    ("result", "item_status", "delivery_status"),
    (("accepted", "accepted", "completed"), ("rejected", "failed", "failed")),
)
def test_definitive_result_is_atomic_append_only_and_response_loss_safe(
    database: str,
    result: str,
    item_status: str,
    delivery_status: str,
) -> None:
    _setup(database)
    started, _, claim = _unknown_claim(database)
    unknown_at = _state(database, started["attempt_id"])["unknown_at"]
    params = _params(database, claim, result)
    recorded = _record(database, params)
    replay = _record(database, params)
    assert recorded["outcome"] == "recorded" and replay["outcome"] == "readback"
    assert recorded["attempt_status"] == result
    assert recorded["item_status"] == item_status
    assert recorded["delivery_status"] == delivery_status
    assert replay["readback_hash"] == recorded["readback_hash"]
    state = _state(database, started["attempt_id"])
    assert state["attempt"] == result and state["phase"] == "receipt_recorded"
    assert state["item"] == item_status and state["delivery"] == delivery_status
    assert state["unknown_at"] == unknown_at and state["resolved_at"] is not None
    assert all(
        state[key] is None
        for key in ("claim_request", "lease", "reconcile_request", "reconcile_token")
    )
    conflicting = _params(database, claim, "rejected" if result == "accepted" else "accepted",
                          request_id=str(params[0]))
    with pytest.raises(Exception, match="DEFINITIVE_REQUEST_CONFLICT"):
        _record(database, conflicting)


def test_fifty_concurrent_accepted_vs_rejected_have_one_winner(database: str) -> None:
    _setup(database)
    _, _, claim = _unknown_claim(database)
    barrier = Barrier(50)

    def compete(index: int) -> str:
        result = "accepted" if index % 2 == 0 else "rejected"
        params = _params(database, claim, result)
        barrier.wait()
        try:
            return _record(database, params)["reconcile_result"]
        except Exception as error:  # noqa: BLE001 - expected one-result-per-claim conflict.
            assert "DEFINITIVE_REQUEST_CONFLICT" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(compete, range(50)))
    assert outcomes.count("conflict") == 49
    assert outcomes.count("accepted") + outcomes.count("rejected") == 1
    assert _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests",
    ) == 1


def test_target_drift_and_expired_lease_still_record(database: str) -> None:
    _setup(database)
    _, _, claim = _unknown_claim(database)
    params = _params(database, claim, "accepted")
    _owner(database, "UPDATE org_members SET status='disabled' WHERE org_id=%s RETURNING user_id", (ORG,))
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
    params = _params(database, claim, "accepted")
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
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests",
    ) == 0


def test_accepted_result_releases_to_v2_actual_next_item_claim(database: str) -> None:
    _setup(database)
    started, _, claim = _unknown_claim(database)
    second = _add_second(database, {"id": started["item_id"]})
    recorded = _record(database, _params(database, claim, "accepted"))
    assert recorded["delivery_status"] == "pending"
    state = _state(database, started["attempt_id"])
    assert all(
        state[key] is None
        for key in ("claim_request", "lease", "reconcile_request", "reconcile_token")
    )
    continuation = _rpc(
        database,
        "claim_agent_runtime_scheduled_wecom_delivery_v2",
        (str(uuid4()), "continuation-worker", 60),
    )
    assert continuation["outcome"] == "claimed"
    assert continuation["claim_kind"] == "continuation"
    assert continuation["item_id"] == second["id"]
    assert _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    ) == 1


@pytest.mark.parametrize(
    ("result", "expected_delivery"),
    (("accepted", "partial"), ("rejected", "failed")),
)
def test_cancelled_tail_uses_terminal_aggregation(
    database: str,
    result: str,
    expected_delivery: str,
) -> None:
    _setup(database)
    started, _, claim = _unknown_claim(database)
    second = _add_second(database, {"id": started["item_id"]})
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='cancelled' "
        "WHERE id=%s RETURNING id",
        (second["id"],),
    )
    recorded = _record(database, _params(database, claim, result))
    assert recorded["delivery_status"] == expected_delivery


def test_null_hash_metadata_and_result_validation_are_zero_fact(database: str) -> None:
    _setup(database)
    started, _, claim = _unknown_claim(database)
    valid = list(_params(database, claim, "accepted"))
    invalid_cases: list[list[object]] = []
    for index in range(len(valid)):
        if index == 15:  # readback_code is intentionally optional.
            continue
        invalid = valid.copy()
        invalid[index] = None
        invalid_cases.append(invalid)
    for index, value in ((12, "still_unknown"), (14, "0" * 64)):
        invalid = valid.copy()
        invalid[index] = value
        invalid_cases.append(invalid)
    bad_metadata = valid.copy()
    bad_metadata[16] = Jsonb({"message": "free text is forbidden"})
    invalid_cases.append(bad_metadata)
    before = _state(database, started["attempt_id"])
    for invalid in invalid_cases:
        with pytest.raises(Exception, match="DEFINITIVE_INVALID"):
            _record(database, tuple(invalid))
    assert _state(database, started["attempt_id"]) == before
    assert _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests",
    ) == 0


def test_request_namespace_is_bidirectional_with_227_43(database: str) -> None:
    _setup(database)
    _, unknown, claim = _unknown_claim(database)
    with pytest.raises(Exception, match="DEFINITIVE_REQUEST_CONFLICT"):
        _record(database, _params(database, claim, "accepted", request_id=unknown["request_id"]))
    definitive_id = str(uuid4())
    assert _record(
        database,
        _params(database, claim, "accepted", request_id=definitive_id),
    )["outcome"] == "recorded"
    _, _, second_claim = _unknown_claim(database)
    with pytest.raises(Exception, match="RECONCILE_RESULT_REQUEST_CONFLICT"):
        _record_still_unknown(
            database,
            _still_unknown_params(database, second_claim, request_id=definitive_id),
        )


def test_acl_rls_search_path_immutability_and_exact_rollback(database: str) -> None:
    _setup(database)
    assert _owner(
        database,
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid='agent_runtime_scheduled_wecom_reconcile_definitive_requests'::regclass",
    ) is True
    assert _owner(
        database,
        "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    assert _owner(
        database,
        "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
        "FROM pg_proc WHERE oid=%s::regprocedure",
        (SIGNATURE,),
    ) is True
    for role in ("everydayai_runtime", "everydayai_worker"):
        assert _owner(database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')", (role, SIGNATURE))
        assert _owner(
            database,
            "SELECT NOT has_table_privilege(%s,'agent_runtime_scheduled_wecom_reconcile_definitive_requests',"
            "'SELECT,INSERT,UPDATE,DELETE')",
            (role,),
        ) is True
    with pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        _rpc(
            database,
            "record_agent_runtime_scheduled_wecom_reconcile_definitive_result_v1",
            (None,) * 17,
            access_kind="legacy",
        )
    _rollback(database, ROLLBACK)
    assert _owner(
        database,
        "SELECT to_regclass('agent_runtime_scheduled_wecom_reconcile_definitive_requests')",
    ) is None
    _apply(database, MIGRATION)
    _, _, claim = _unknown_claim(database)
    recorded = _record(database, _params(database, claim, "accepted"))
    with pytest.raises(Exception, match="DEFINITIVE_IMMUTABLE"):
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_reconcile_definitive_requests "
            "SET readback_code='changed' WHERE request_id=%s RETURNING request_id",
            (recorded["request_id"],),
        )
    with pytest.raises(Exception, match="DEFINITIVE_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
