from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import _finalize
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _hash,
    _outcome_params,
    _owner,
    _rpc,
    _setup as _outcome_setup,
    _start,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _identity,
    _prepare_params,
    _seed,
    _start_params,
)


pytestmark = pytest.mark.external
MIGRATION_41 = "227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql"
MIGRATION = "227_42_agent_runtime_scheduled_wecom_continuation_claim.sql"
ROLLBACK = "227_42_agent_runtime_scheduled_wecom_continuation_claim_rollback.sql"
V1_SIGNATURE = "claim_agent_runtime_scheduled_wecom_delivery_v1(uuid,text,integer)"
V2_SIGNATURE = "claim_agent_runtime_scheduled_wecom_delivery_v2(uuid,text,integer)"


def _setup_base(url: str) -> None:
    _outcome_setup(url)
    _apply(url, MIGRATION_41)


def _add_second(url: str, first: dict) -> dict:
    second_id = str(uuid4())
    _owner(
        url,
        "INSERT INTO agent_runtime_scheduled_wecom_delivery_items(id,intent_id,item_key,ordinal,item_kind,"
        "source_role,source_id,source_revision,source_identity_hash,content_identity_hash) "
        "SELECT %s,intent_id,%s,2,'artifact_identity','output',%s,1,%s,content_identity_hash "
        "FROM agent_runtime_scheduled_wecom_delivery_items WHERE id=%s RETURNING id",
        (second_id, uuid4().hex + uuid4().hex, str(uuid4()), "e" * 64, first["id"]),
    )
    return {"id": second_id, "version": 0}


def _terminal_first(url: str, outcome: str) -> tuple[dict, dict, dict]:
    claim, first = _seed(url)
    second = _add_second(url, first)
    prepared = _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, first, _identity()),
    )
    started = _rpc(
        url, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(url, claim, first, prepared),
    )
    result = _rpc(
        url, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(url, claim, started, outcome),
    )
    assert result["delivery_status"] == "claimed"
    _owner(
        url,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    return claim, second, started


def _continuation(url: str, request_id: str | None = None, worker: str = "continuation") -> dict:
    return _rpc(
        url, "claim_agent_runtime_scheduled_wecom_delivery_v2",
        (request_id or str(uuid4()), worker, 60),
    )


def test_initial_claim_v2_replaces_v1_without_creating_an_attempt(database: str) -> None:
    _setup_base(database)
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    before = _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    )
    _apply(database, MIGRATION)
    claimed = _continuation(database)
    assert claimed["outcome"] == "claimed" and claimed["claim_kind"] == "initial"
    assert claimed["item_id"] == str(_owner(
        database,
        "SELECT id FROM agent_runtime_scheduled_wecom_delivery_items "
        "WHERE intent_id=%s ORDER BY ordinal LIMIT 1",
        (claimed["intent_id"],),
    ))
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    ) == before == 0


@pytest.mark.parametrize("first_outcome", ("accepted", "rejected"))
def test_crash_between_items_claims_only_unattempted_strict_next_item(
    database: str, first_outcome: str,
) -> None:
    _setup_base(database)
    old_claim, second, first_attempt = _terminal_first(database, first_outcome)
    before_attempts = _owner(
        database,
        "SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM agent_runtime_scheduled_wecom_dispatch_attempts a",
    )
    _apply(database, MIGRATION)
    claimed = _continuation(database)
    assert claimed["outcome"] == "claimed"
    assert claimed["claim_kind"] == "continuation"
    assert claimed["item_id"] == second["id"]
    assert claimed["previous_claim_request_id"] == old_claim["claim_request_id"]
    assert _owner(
        database,
        "SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM agent_runtime_scheduled_wecom_dispatch_attempts a",
    ) == before_attempts
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claimed, second, _identity()),
    )
    assert prepared["outcome"] == "prepared" and prepared["item_id"] == second["id"]
    assert _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s",
        (first_attempt["attempt_id"],),
    ) == 1


def test_response_loss_replay_is_fact_stable(database: str) -> None:
    _setup_base(database)
    _terminal_first(database, "accepted")
    _apply(database, MIGRATION)
    request_id = str(uuid4())
    first = _continuation(database, request_id, "stable-worker")
    before = _owner(
        database,
        "SELECT jsonb_build_object('delivery',to_jsonb(d),'ledger',to_jsonb(r)) "
        "FROM agent_runtime_scheduled_wecom_continuation_claim_requests r "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=r.intent_id "
        "WHERE r.request_id=%s",
        (request_id,),
    )
    replay = _continuation(database, request_id, "stable-worker")
    assert first["outcome"] == "claimed" and replay["outcome"] == "readback"
    assert replay["lease_token"] == first["lease_token"]
    assert _owner(
        database,
        "SELECT jsonb_build_object('delivery',to_jsonb(d),'ledger',to_jsonb(r)) "
        "FROM agent_runtime_scheduled_wecom_continuation_claim_requests r "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=r.intent_id "
        "WHERE r.request_id=%s",
        (request_id,),
    ) == before
    with pytest.raises(Exception, match="CONTINUATION_CLAIM_IMMUTABLE"):
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_continuation_claim_requests "
            "SET worker_id='changed' WHERE request_id=%s RETURNING request_id",
            (request_id,),
        )
    with pytest.raises(Exception, match="CONTINUATION_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)



def test_fifty_concurrent_claimers_have_one_winner(database: str) -> None:
    _setup_base(database)
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    _apply(database, MIGRATION)
    request_ids = [str(uuid4()) for _ in range(50)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(lambda rid: _continuation(database, rid), request_ids))
    assert sum(row["outcome"] == "claimed" for row in outcomes) == 1
    assert sum(row["outcome"] == "empty" for row in outcomes) == 49
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_continuation_claim_requests",
    ) == 1
    assert next(row for row in outcomes if row["outcome"] == "claimed")["claim_kind"] == "initial"


def test_initial_target_invalidation_is_durably_unavailable(database: str) -> None:
    _setup_base(database)
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    intent_id = _owner(
        database, "SELECT intent_id FROM agent_runtime_scheduled_wecom_deliveries",
    )
    _owner(
        database, "UPDATE org_members SET status='disabled' WHERE org_id=%s RETURNING user_id", (ORG,),
    )
    _apply(database, MIGRATION)
    assert _continuation(database)["outcome"] == "empty"
    state = _owner(
        database,
        "SELECT jsonb_build_object('delivery',d.status,'items',array_agg(item.status ORDER BY ordinal)) "
        "FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "WHERE d.intent_id=%s GROUP BY d.status",
        (intent_id,),
    )
    assert state == {"delivery": "unavailable", "items": ["cancelled"]}
    assert _continuation(database)["outcome"] == "empty"
    assert _owner(
        database,
        "SELECT jsonb_build_object('delivery',d.status,'items',array_agg(item.status ORDER BY ordinal)) "
        "FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "WHERE d.intent_id=%s GROUP BY d.status",
        (intent_id,),
    ) == state
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_continuation_claim_requests",
    ) == 0


@pytest.mark.parametrize(
    ("first_outcome", "delivery_status"), (("accepted", "partial"), ("rejected", "failed")),
)
def test_continuation_target_invalidation_cancels_remaining_and_aggregates(
    database: str, first_outcome: str, delivery_status: str,
) -> None:
    _setup_base(database)
    _, second, first_attempt = _terminal_first(database, first_outcome)
    before_attempt = _owner(
        database, "SELECT to_jsonb(a) FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE id=%s",
        (first_attempt["attempt_id"],),
    )
    _owner(
        database, "UPDATE org_members SET status='disabled' WHERE org_id=%s RETURNING user_id", (ORG,),
    )
    _apply(database, MIGRATION)
    assert _continuation(database)["outcome"] == "empty"
    state = _owner(
        database,
        "SELECT jsonb_build_object('delivery',d.status,'item',item.status,'worker',d.claim_worker_id) "
        "FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "WHERE item.id=%s",
        (second["id"],),
    )
    assert state == {"delivery": delivery_status, "item": "cancelled", "worker": None}
    assert _owner(
        database, "SELECT to_jsonb(a) FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE id=%s",
        (first_attempt["attempt_id"],),
    ) == before_attempt


def test_reconcile_resolved_shape_reuses_continuation_entry(database: str) -> None:
    _setup_base(database)
    claim, first = _seed(database)
    second = _add_second(database, first)
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, first, _identity()),
    )
    started = _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(database, claim, first, prepared),
    )
    unknown = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, claim, started, "unknown"),
    )
    reconcile = _rpc(
        database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (str(uuid4()), "reconciler", 60),
    )
    metadata = {"http_status": 200, "wecom_errcode": 0, "provider_message_id": "msg-resolved"}
    receipt_hash = _hash(database, started, "accepted", "wecom_app", "ok", metadata)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='accepted',"
        "dispatch_phase='receipt_recorded',receipt_type='wecom_app',receipt_hash=%s,receipt_code='ok',"
        "resolved_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s RETURNING id",
        (receipt_hash, started["attempt_id"]),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='accepted',"
        "state_version=state_version+1,next_attempt_at=NULL,terminal_reason_code=NULL,"
        "updated_at=clock_timestamp() WHERE id=%s RETURNING id",
        (first["id"],),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='pending',"
        "state_version=state_version+1,reconcile_worker_id=NULL,reconcile_request_id=NULL,"
        "reconcile_token=NULL,reconcile_lease_expires_at=NULL,next_attempt_at=NULL,"
        "terminal_reason_code=NULL,updated_at=clock_timestamp() WHERE intent_id=%s "
        "AND reconcile_token=%s RETURNING intent_id",
        (claim["intent_id"], reconcile["reconcile_token"]),
    )
    assert unknown["delivery_status"] == "unknown"
    _apply(database, MIGRATION)
    continued = _continuation(database)
    assert continued["outcome"] == "claimed" and continued["item_id"] == second["id"]


def test_request_namespace_is_bidirectional_across_legacy_ledgers(database: str) -> None:
    _setup_base(database)
    old_claim, _, first_attempt = _terminal_first(database, "accepted")
    outcome_request_id = _owner(
        database,
        "SELECT request_id FROM agent_runtime_scheduled_wecom_outcome_requests WHERE attempt_id=%s",
        (first_attempt["attempt_id"],),
    )
    unknown_claim, _, unknown_started = _start(database)
    _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(
            database,
            unknown_claim,
            unknown_started,
            "unknown",
        ),
    )
    reconcile_request_id = str(uuid4())
    assert _rpc(
        database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (reconcile_request_id, "reconcile-owner", 60),
    )["outcome"] == "claimed"
    second_unknown_claim, _, second_unknown_started = _start(database)
    _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, second_unknown_claim, second_unknown_started, "unknown"),
    )
    _apply(database, MIGRATION)
    for request_id in (
        old_claim["claim_request_id"], str(outcome_request_id), reconcile_request_id,
    ):
        with pytest.raises(Exception, match="CONTINUATION_REQUEST_CONFLICT"):
            _continuation(database, request_id)
    continuation_request_id = str(uuid4())
    assert _continuation(database, continuation_request_id)["outcome"] == "claimed"
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _rpc(
            database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
            (continuation_request_id, "reconcile-racer", 60),
        )


def test_acl_namespace_immutability_and_pristine_rollback_restore_v1(database: str) -> None:
    _setup_base(database)
    _apply(database, MIGRATION)
    assert _owner(
        database,
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid='agent_runtime_scheduled_wecom_continuation_claim_requests'::regclass",
    ) is True
    assert _owner(
        database, "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (V2_SIGNATURE,),
    ) is True
    assert _owner(
        database, "SELECT NOT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (V1_SIGNATURE,),
    ) is True
    for role in ("everydayai_wecom_runtime", "everydayai_runtime", "everydayai_worker"):
        assert _owner(
            database,
            "SELECT NOT has_table_privilege(%s,"
            "'agent_runtime_scheduled_wecom_continuation_claim_requests',"
            "'SELECT,INSERT,UPDATE,DELETE')",
            (role,),
        ) is True
    _rollback(database, ROLLBACK)
    assert _owner(
        database, "SELECT to_regclass('agent_runtime_scheduled_wecom_continuation_claim_requests')",
    ) is None
    assert _owner(
        database, "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (V1_SIGNATURE,),
    ) is True
    _apply(database, MIGRATION)
    with pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        _rpc(
            database, "claim_agent_runtime_scheduled_wecom_delivery_v2",
            (str(uuid4()), "worker", 60), access_kind="legacy",
        )
