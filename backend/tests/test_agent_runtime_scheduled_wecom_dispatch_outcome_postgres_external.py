from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _fact_state as _prepare_fact_state,
    _identity,
    _owner,
    _prepare_params,
    _rpc,
    _seed,
    _setup as _prepare_setup,
    _start_params,
)


pytestmark = pytest.mark.external
MIGRATION = "227_40_agent_runtime_scheduled_wecom_dispatch_outcomes.sql"
ROLLBACK = "227_40_agent_runtime_scheduled_wecom_dispatch_outcomes_rollback.sql"
SIGNATURE = (
    "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(uuid,uuid,uuid,uuid,uuid,uuid,"
    "text,bigint,bigint,text,text,bigint,text,text,text,text,jsonb)"
)


def _setup(url: str) -> None:
    _prepare_setup(url)
    _apply(url, MIGRATION)


def _facts(url: str) -> tuple[object, ...]:
    return _prepare_fact_state(url) + (
        _owner(
            url,
            "SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY request_id),'[]'::jsonb) "
            "FROM agent_runtime_scheduled_wecom_outcome_requests r",
        ),
    )


def _versions(url: str, intent_id: str, item_id: str) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object('delivery',d.state_version,'item',item.state_version) "
        "FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "WHERE d.intent_id=%s AND item.id=%s",
        (intent_id, item_id),
    )


def _start(url: str) -> tuple[dict, dict, dict]:
    claim, item = _seed(url)
    prepared = _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    started = _rpc(
        url, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(url, claim, item, prepared),
    )
    assert started["outcome"] == "dispatch_started"
    return claim, item, started


def _hash(
    url: str, started: dict, outcome: str, receipt_type: str, code: str | None, metadata: dict,
) -> str:
    return _owner(
        url,
        "SELECT _agent_runtime_scheduled_wecom_receipt_hash(%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (
            outcome, receipt_type, code, Jsonb(metadata), started["provider_request_id"],
            started["idempotency_key"], started["provider_revision"],
        ),
    )


def _outcome_params(
    url: str, claim: dict, started: dict, outcome: str,
    *, request_id: str | None = None, metadata: dict | None = None,
) -> tuple:
    versions = _versions(url, claim["intent_id"], started["item_id"])
    if outcome == "unknown":
        receipt_type = receipt_hash = code = None
        metadata = {}
    else:
        receipt_type = "wecom_app"
        code = "ok" if outcome == "accepted" else "provider_rejected"
        metadata = metadata or {
            "http_status": 200 if outcome == "accepted" else 400,
            "wecom_errcode": 0 if outcome == "accepted" else 40013,
            "provider_message_id": f"msg-{uuid4().hex}",
        }
        receipt_hash = _hash(url, started, outcome, receipt_type, code, metadata)
    return (
        request_id or str(uuid4()), claim["intent_id"], started["item_id"],
        started["attempt_id"], claim["claim_request_id"], claim["lease_token"],
        claim["worker_id"], versions["delivery"], versions["item"],
        started["provider_request_id"], started["idempotency_key"],
        started["provider_revision"], outcome, receipt_type, receipt_hash, code, Jsonb(metadata),
    )


@pytest.mark.parametrize(
    ("outcome", "item_status", "delivery_status"),
    (("accepted", "accepted", "completed"), ("rejected", "failed", "failed"),
     ("unknown", "unknown", "unknown")),
)
def test_single_item_atomic_mapping_and_response_loss_readback(
    database: str, outcome: str, item_status: str, delivery_status: str,
) -> None:
    _setup(database)
    claim, _, started = _start(database)
    params = _outcome_params(database, claim, started, outcome)
    recorded = _rpc(database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", params)
    assert recorded["outcome"] == "recorded"
    assert recorded["attempt_status"] == outcome
    assert recorded["item_status"] == item_status
    assert recorded["delivery_status"] == delivery_status
    replay = _rpc(database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", params)
    assert replay["outcome"] == "readback"
    state = _owner(
        database,
        "SELECT jsonb_build_object('attempt',a.status,'phase',a.dispatch_phase,'item',item.status,"
        "'delivery',d.status,'worker',d.claim_worker_id,'lease',d.lease_token) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=item.intent_id WHERE a.id=%s",
        (started["attempt_id"],),
    )
    assert state["attempt"] == outcome and state["item"] == item_status
    assert state["delivery"] == delivery_status
    assert state["phase"] == ("ambiguous" if outcome == "unknown" else "receipt_recorded")
    assert state["worker"] is None and state["lease"] is None


def test_accepted_with_later_pending_preserves_claim_then_rejected_aggregates_partial(
    database: str,
) -> None:
    _setup(database)
    claim, first = _seed(database)
    second_id = str(uuid4())
    _owner(
        database,
        "INSERT INTO agent_runtime_scheduled_wecom_delivery_items(id,intent_id,item_key,ordinal,item_kind,"
        "source_role,source_id,source_revision,source_identity_hash,content_identity_hash) "
        "SELECT %s,intent_id,%s,2,'artifact_identity','output',%s,1,%s,content_identity_hash "
        "FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=%s RETURNING id",
        (second_id, "d" * 64, str(uuid4()), "e" * 64, first["id"]),
    )
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, first, _identity()),
    )
    first_started = _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(database, claim, first, prepared),
    )
    accepted = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, claim, first_started, "accepted"),
    )
    assert accepted["delivery_status"] == "claimed"
    current = _owner(
        database,
        "SELECT jsonb_build_object('intent_id',intent_id,'state_version',state_version,"
        "'claim_request_id',claim_request_id,"
        "'lease_token',lease_token,'worker_id',claim_worker_id) "
        "FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=%s",
        (claim["intent_id"],),
    )
    assert current["claim_request_id"] == claim["claim_request_id"]
    assert current["lease_token"] == claim["lease_token"]
    second = {"id": second_id, "version": 0}
    prepared2 = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(current, second, _identity()),
    )
    started2 = _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(database, current, second, prepared2),
    )
    rejected = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, current, started2, "rejected"),
    )
    assert rejected["delivery_status"] == "partial"


def test_unknown_clears_claim_and_cannot_be_redispatched(database: str) -> None:
    _setup(database)
    claim, _, started = _start(database)
    _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, claim, started, "unknown"),
    )
    assert _rpc(
        database, "claim_agent_runtime_scheduled_wecom_delivery_v1",
        (str(uuid4()), "replacement-worker", 60),
    )["outcome"] == "empty"
    assert _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE item_id=%s",
        (started["item_id"],),
    ) == 1


def test_hash_metadata_and_null_rejection_are_zero_fact(database: str) -> None:
    _setup(database)
    claim, _, started = _start(database)
    valid = _outcome_params(database, claim, started, "accepted")
    before = _facts(database)
    mismatch = list(valid)
    mismatch[14] = "0" * 64
    with pytest.raises(Exception, match="OUTCOME_INVALID"):
        _rpc(database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", tuple(mismatch))
    for metadata in (
        {"unknown_key": "x"}, {"provider_message_id": {"nested": True}},
        {"wecom_errmsg": "provider token leaked"}, {"payload": "raw"},
    ):
        invalid = list(valid)
        invalid[16] = Jsonb(metadata)
        with pytest.raises(Exception, match="OUTCOME_INVALID"):
            _rpc(database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", tuple(invalid))
    required = tuple(index for index in range(len(valid)) if index != 15)
    for index in required:
        invalid = list(valid)
        invalid[index] = None
        with pytest.raises(Exception, match="OUTCOME_INVALID"):
            _rpc(database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", tuple(invalid))
    assert _facts(database) == before


def test_fifty_mixed_outcomes_compete_to_one_durable_result(database: str) -> None:
    _setup(database)
    claim, _, started = _start(database)
    candidates = [
        _outcome_params(database, claim, started, ("accepted", "rejected", "unknown")[i % 3])
        for i in range(50)
    ]

    def call(params: tuple) -> str:
        try:
            return _rpc(
                database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", params,
            )["outcome"]
        except Exception as error:  # noqa: BLE001 - SQL conflict is the contract under race.
            assert "OUTCOME_REQUEST_CONFLICT" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(call, candidates))
    assert outcomes.count("recorded") == 1 and outcomes.count("conflict") == 49
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_outcome_requests",
    ) == 1


@pytest.mark.parametrize("outcome", ("accepted", "unknown"))
def test_recovery_start_then_outcome_uses_current_claim_not_prepared_by_claim(
    database: str, outcome: str,
) -> None:
    _setup(database)
    old_claim, item = _seed(database)
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(old_claim, item, _identity()),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (old_claim["intent_id"],),
    )
    recovered = _rpc(
        database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (str(uuid4()), "recovery-worker", 60),
    )
    assert recovered["outcome"] == "recovered"
    start_params = (
        recovered["intent_id"], prepared["item_id"], prepared["attempt_id"],
        recovered["claim_request_id"], recovered["lease_token"], recovered["worker_id"],
        recovered["delivery_state_version"], recovered["item_state_version"],
        prepared["provider_request_id"], prepared["idempotency_key"], prepared["provider_revision"],
    )
    started = _rpc(database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params)
    assert started["outcome"] == "dispatch_started"
    result = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, recovered, started, outcome),
    )
    assert result["dispatch_outcome"] == outcome


def test_current_claim_versions_and_provider_identity_are_fenced(database: str) -> None:
    _setup(database)
    claim, _, started = _start(database)
    valid = _outcome_params(database, claim, started, "accepted")
    before = _facts(database)
    for index, replacement in ((7, valid[7] + 1), (8, valid[8] + 1)):
        invalid = list(valid)
        invalid[index] = replacement
        assert _rpc(
            database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", tuple(invalid),
        )["outcome"] == "fenced"
    crossed = dict(started)
    crossed["provider_request_id"] = f"provider-{uuid4()}"
    assert _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, claim, crossed, "accepted"),
    )["outcome"] == "fenced"
    assert _facts(database) == before


@pytest.mark.parametrize("outcome", ("accepted", "rejected", "unknown"))
def test_post_start_context_drift_still_records_external_fact(
    database: str, outcome: str,
) -> None:
    _setup(database)
    claim, _, started = _start(database)
    params = _outcome_params(database, claim, started, outcome)
    _owner(
        database, "UPDATE org_members SET status='disabled' WHERE org_id=%s RETURNING user_id", (ORG,),
    )
    result = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", params,
    )
    assert result["outcome"] == "recorded" and result["dispatch_outcome"] == outcome


def test_expired_unchanged_claim_still_records_receipt(database: str) -> None:
    _setup(database)
    claim, _, started = _start(database)
    params = _outcome_params(database, claim, started, "accepted")
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    result = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", params,
    )
    assert result["outcome"] == "recorded"


def test_changed_state_version_fences_stale_receipt(database: str) -> None:
    _setup(database)
    claim, _, started = _start(database)
    stale = _outcome_params(database, claim, started, "accepted")
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1 "
        "WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    assert _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1", stale,
    )["outcome"] == "fenced"


def test_acl_rollback_guard_and_pristine_reapply(database: str) -> None:
    _setup(database)
    assert _owner(
        database,
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid='agent_runtime_scheduled_wecom_outcome_requests'::regclass",
    ) is True
    for role in ("everydayai_wecom_runtime", "everydayai_runtime", "everydayai_worker"):
        assert _owner(
            database,
            "SELECT NOT has_table_privilege(%s,'agent_runtime_scheduled_wecom_outcome_requests',"
            "'SELECT,INSERT,UPDATE,DELETE')",
            (role,),
        ) is True
    assert _owner(
        database, "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    assert _owner(
        database, "SELECT NOT has_function_privilege('everydayai_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    _rollback(database, ROLLBACK)
    assert _owner(
        database, "SELECT to_regclass('agent_runtime_scheduled_wecom_outcome_requests')",
    ) is None
    _apply(database, MIGRATION)
    claim, _, started = _start(database)
    _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, claim, started, "accepted"),
    )
    with pytest.raises(Exception, match="OUTCOME_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
