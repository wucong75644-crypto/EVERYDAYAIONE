from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _owner,
    _rpc,
    _set_user_target,
)
from tests.test_agent_runtime_scheduled_wecom_continuation_claim_postgres_external import (
    _continuation,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _outcome_params,
    _setup as _outcome_setup,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_payload_postgres_external import (
    _apply_completed,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _identity,
    _owner_execute,
    _prepare_params,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_version_readback_postgres_external import (
    _start_params,
)


pytestmark = pytest.mark.external
MIGRATION = "227_48_agent_runtime_scheduled_wecom_started_recovery.sql"
ROLLBACK = "227_48_agent_runtime_scheduled_wecom_started_recovery_rollback.sql"
FUNCTION = "recover_agent_runtime_scheduled_wecom_started_dispatch_v1"
SIGNATURE = f"{FUNCTION}(uuid,text)"
LEDGER = "agent_runtime_scheduled_wecom_started_recovery_requests"
RESPONSE_KEYS = {
    "outcome", "request_id", "recovery_worker_id", "org_id", "intent_id", "item_id",
    "attempt_id", "outcome_request_id", "dispatch_outcome", "attempt_status",
    "dispatch_phase", "item_status", "delivery_status", "delivery_state_version",
    "item_state_version", "recovered_at",
}


def _setup(url: str) -> None:
    _outcome_setup(url)
    for migration in (
        "227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql",
        "227_42_agent_runtime_scheduled_wecom_continuation_claim.sql",
        "227_43_agent_runtime_scheduled_wecom_reconcile_still_unknown.sql",
        "227_44_agent_runtime_scheduled_wecom_reconcile_definitive.sql",
        "227_45_agent_runtime_scheduled_wecom_dispatch_version_readback.sql",
        "227_46_agent_runtime_scheduled_wecom_dispatch_payload.sql",
        "227_47_agent_runtime_scheduled_wecom_unsupported_terminalization.sql",
        MIGRATION,
    ):
        _apply(url, migration)


def _prepare(url: str) -> tuple[dict, dict]:
    _set_user_target(url, "app")
    _apply_completed(url, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _continuation(url, worker="dispatch-worker")
    assert claim["outcome"] == "claimed"
    item = {"id": claim["item_id"], "version": claim["item_state_version"]}
    prepared = _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        _prepare_params(claim, item, _identity()),
    )
    assert prepared["outcome"] == "prepared"
    return claim, prepared


def _started(url: str, *, expire: bool = True) -> tuple[dict, dict]:
    claim, prepared = _prepare(url)
    started = _rpc(
        url, "start_agent_runtime_scheduled_wecom_dispatch_v2",
        _start_params(claim, prepared),
    )
    assert started["outcome"] == "dispatch_started"
    if expire:
        _owner(
            url, "UPDATE agent_runtime_scheduled_wecom_deliveries SET "
            "lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
            (claim["intent_id"],),
        )
    return claim, started


def _recover(url: str, request_id: str | None = None, worker: str = "recovery-worker") -> dict:
    return _rpc(url, FUNCTION, (request_id or str(uuid4()), worker))


def _state(url: str, intent_id: str) -> dict:
    return _owner(
        url, "SELECT jsonb_build_object('delivery',to_jsonb(d),'item',to_jsonb(item),"
        "'attempt',to_jsonb(a),'attempt_count',(SELECT count(*) FROM "
        "agent_runtime_scheduled_wecom_dispatch_attempts all_attempts WHERE all_attempts.item_id=item.id)) "
        "FROM agent_runtime_scheduled_wecom_deliveries d JOIN "
        "agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id JOIN "
        "agent_runtime_scheduled_wecom_dispatch_attempts a ON a.item_id=item.id WHERE d.intent_id=%s",
        (intent_id,),
    )


def test_expired_started_attempt_recovers_atomically_and_becomes_reconcile_only(database: str) -> None:
    _setup(database)
    claim, started = _started(database)
    stale = _state(database, claim["intent_id"])
    assert stale["delivery"]["status"] == "dispatching"
    assert stale["attempt"]["dispatch_phase"] == "external_request_started"
    request_id = str(uuid4())

    recovered = _recover(database, request_id)
    replay = _recover(database, request_id)

    assert set(recovered) == RESPONSE_KEYS and set(replay) == RESPONSE_KEYS
    assert recovered["outcome"] == "recovered" and replay["outcome"] == "readback"
    assert recovered["attempt_id"] == started["attempt_id"]
    assert recovered["outcome_request_id"] != request_id
    assert recovered["dispatch_outcome"] == "unknown"
    assert recovered["attempt_status"] == recovered["item_status"] == "unknown"
    assert recovered["dispatch_phase"] == "ambiguous" and recovered["delivery_status"] == "unknown"
    assert replay["recovered_at"] == recovered["recovered_at"]
    ledger = _owner(database, f"SELECT to_jsonb(r) FROM {LEDGER} r WHERE request_id=%s", (request_id,))
    outcome = _owner(
        database, "SELECT to_jsonb(r) FROM agent_runtime_scheduled_wecom_outcome_requests r "
        "WHERE request_id=%s", (recovered["outcome_request_id"],),
    )
    assert ledger["outcome_request_id"] == recovered["outcome_request_id"]
    assert outcome["dispatch_outcome"] == "unknown" and outcome["receipt_metadata"] == {}
    state = _state(database, claim["intent_id"])
    assert state["attempt_count"] == 1
    assert state["attempt"]["status"] == state["item"]["status"] == "unknown"
    assert state["delivery"]["status"] == "unknown" and state["delivery"]["claim_request_id"] is None
    assert _continuation(database)["outcome"] == "empty"
    reconcile = _rpc(
        database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (str(uuid4()), "reconcile-worker", 60),
    )
    assert reconcile["outcome"] == "claimed" and reconcile["attempt_id"] == started["attempt_id"]


def test_not_expired_and_prepared_attempts_are_empty_without_mutation(database: str) -> None:
    _setup(database)
    claim, _ = _started(database, expire=False)
    before = _state(database, claim["intent_id"])
    assert _recover(database)["outcome"] == "empty"
    assert _state(database, claim["intent_id"]) == before
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 0


def test_expired_prepared_attempt_is_not_recovered(database: str) -> None:
    _setup(database)
    claim, _ = _prepare(database)
    _owner(
        database, "UPDATE agent_runtime_scheduled_wecom_deliveries SET "
        "lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    before = _state(database, claim["intent_id"])
    assert before["attempt"]["status"] == "prepared"
    assert _recover(database)["outcome"] == "empty"
    assert _state(database, claim["intent_id"]) == before


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "unknown"])
def test_terminal_attempt_states_are_never_recovered(database: str, outcome: str) -> None:
    _setup(database)
    claim, started = _started(database, expire=False)
    _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(database, claim, started, outcome),
    )
    before = _state(database, claim["intent_id"])
    assert _recover(database)["outcome"] == "empty"
    assert _state(database, claim["intent_id"]) == before


@pytest.mark.parametrize("drift", ["claim", "provider", "version", "target", "org"])
def test_identity_and_live_target_drift_are_fenced_without_mutation(database: str, drift: str) -> None:
    _setup(database)
    claim, _ = _started(database)
    if drift == "claim":
        _owner(
            database, "UPDATE agent_runtime_scheduled_wecom_deliveries SET claim_worker_id='drift' "
            "WHERE intent_id=%s RETURNING intent_id", (claim["intent_id"],),
        )
    elif drift == "provider":
        _owner_execute(
            database, "ALTER TABLE agent_runtime_scheduled_wecom_deliveries DISABLE TRIGGER "
            "runtime_scheduled_wecom_delivery_identity_guard",
        )
        _owner(
            database, "UPDATE agent_runtime_scheduled_wecom_deliveries SET provider_revision=provider_revision+1 "
            "WHERE intent_id=%s RETURNING intent_id", (claim["intent_id"],),
        )
    elif drift == "version":
        _owner(
            database, "UPDATE agent_runtime_scheduled_wecom_deliveries SET state_version=state_version+1 "
            "WHERE intent_id=%s RETURNING intent_id", (claim["intent_id"],),
        )
    elif drift == "target":
        _owner(
            database, "UPDATE wecom_user_mappings SET wecom_userid='drifted-user' "
            "WHERE org_id=(SELECT org_id FROM agent_runtime_scheduled_wecom_deliveries WHERE intent_id=%s) "
            "RETURNING id", (claim["intent_id"],),
        )
    else:
        _owner(
            database, "UPDATE organizations SET status='inactive' WHERE id=(SELECT org_id FROM "
            "agent_runtime_scheduled_wecom_deliveries WHERE intent_id=%s) RETURNING id",
            (claim["intent_id"],),
        )
    before = _state(database, claim["intent_id"])
    assert _recover(database)["outcome"] == "empty"
    assert _state(database, claim["intent_id"]) == before
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 0


def test_same_request_replays_and_worker_conflict_fails_closed(database: str) -> None:
    _setup(database)
    _started(database)
    request_id = str(uuid4())
    assert _recover(database, request_id)["outcome"] == "recovered"
    assert _recover(database, request_id)["outcome"] == "readback"
    with pytest.raises(Exception, match="STARTED_RECOVERY_REQUEST_CONFLICT"):
        _recover(database, request_id, "different-worker")
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 1


def test_fifty_distinct_requests_on_one_attempt_have_one_recovery(database: str) -> None:
    _setup(database)
    _started(database)
    barrier = Barrier(50)

    def invoke(_: int) -> str:
        barrier.wait()
        return _recover(database)["outcome"]

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(invoke, range(50)))
    assert outcomes.count("recovered") == 1 and outcomes.count("empty") == 49
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 1
    assert _owner(database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_outcome_requests") == 1


def test_fifty_same_request_calls_make_one_stable_result(database: str) -> None:
    _setup(database)
    _started(database)
    request_id = str(uuid4())
    barrier = Barrier(50)

    def invoke(_: int) -> dict:
        barrier.wait()
        return _recover(database, request_id)

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(invoke, range(50)))
    assert [result["outcome"] for result in results].count("recovered") == 1
    assert [result["outcome"] for result in results].count("readback") == 49
    assert len({result["outcome_request_id"] for result in results}) == 1
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 1


def test_acl_rls_immutability_actor_and_controlled_rollback_cycle(database: str) -> None:
    _setup(database)
    assert _owner(
        database, f"SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='{LEDGER}'::regclass",
    ) is True
    assert _owner(
        database, "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
        "FROM pg_proc WHERE oid=%s::regprocedure", (SIGNATURE,),
    ) is True
    assert _owner(
        database, "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')", (SIGNATURE,),
    ) is True
    assert _owner(
        database, "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
        "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE "
        "p.oid=%s::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')", (SIGNATURE,),
    ) is True
    for role in ("everydayai_runtime", "everydayai_worker", "everydayai_agent_runtime_worker"):
        assert _owner(database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')", (role, SIGNATURE))
        assert _owner(
            database, "SELECT NOT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE')", (role, LEDGER),
        )
    assert _owner(
        database, "SELECT NOT has_table_privilege('everydayai_wecom_runtime',%s,'SELECT,INSERT,UPDATE,DELETE')",
        (LEDGER,),
    )
    with pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        _rpc(database, FUNCTION, (str(uuid4()), "worker"), access_kind="legacy")
    with pytest.raises(psycopg.Error, match="permission denied"):
        _rpc(
            database, FUNCTION, (str(uuid4()), "worker"),
            role="everydayai_runtime", access_kind="worker",
        )

    claim, _ = _started(database)
    recovered = _recover(database)
    with pytest.raises(Exception, match="STARTED_RECOVERY_IMMUTABLE"):
        _owner(
            database, f"UPDATE {LEDGER} SET recovery_worker_id='changed' WHERE request_id=%s RETURNING request_id",
            (recovered["request_id"],),
        )
    with pytest.raises(Exception, match="STARTED_RECOVERY_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
    ledger = _owner(database, f"SELECT to_jsonb(r) FROM {LEDGER} r")
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(f"ALTER TABLE {LEDGER} DISABLE TRIGGER runtime_scheduled_wecom_started_recovery_immutable")
        conn.execute(f"DELETE FROM {LEDGER}")
        conn.execute("ALTER TABLE agent_runtime_scheduled_wecom_outcome_requests DISABLE TRIGGER "
                     "runtime_scheduled_wecom_outcome_request_immutable")
        conn.execute("DELETE FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=%s",
                     (ledger["outcome_request_id"],))
        for table, trigger in (
            ("agent_runtime_scheduled_wecom_dispatch_attempts", "runtime_scheduled_wecom_attempt_identity_guard"),
            ("agent_runtime_scheduled_wecom_delivery_items", "runtime_scheduled_wecom_item_identity_guard"),
            ("agent_runtime_scheduled_wecom_deliveries", "runtime_scheduled_wecom_delivery_identity_guard"),
            ("agent_runtime_scheduled_wecom_deliveries", "runtime_scheduled_wecom_delivery_global_request_guard"),
        ):
            conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
        conn.execute("UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='dispatch_started',"
                     "dispatch_phase='external_request_started',was_ambiguous=FALSE,unknown_at=NULL,updated_at=clock_timestamp() "
                     "WHERE id=%s", (ledger["attempt_id"],))
        conn.execute("UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='dispatching',state_version=%s,"
                     "terminal_reason_code=NULL WHERE id=%s", (ledger["original_item_state_version"], ledger["item_id"]))
        conn.execute("UPDATE agent_runtime_scheduled_wecom_deliveries SET status='dispatching',state_version=%s,"
                     "claim_request_id=%s,lease_token=%s,claim_worker_id=%s,"
                     "lease_expires_at=clock_timestamp()-interval '1 second',terminal_reason_code=NULL WHERE intent_id=%s",
                     (ledger["original_delivery_state_version"], ledger["claim_request_id"], ledger["lease_token"],
                      ledger["claim_worker_id"], claim["intent_id"]))
        for table, trigger in (
            ("agent_runtime_scheduled_wecom_dispatch_attempts", "runtime_scheduled_wecom_attempt_identity_guard"),
            ("agent_runtime_scheduled_wecom_delivery_items", "runtime_scheduled_wecom_item_identity_guard"),
            ("agent_runtime_scheduled_wecom_deliveries", "runtime_scheduled_wecom_delivery_identity_guard"),
            ("agent_runtime_scheduled_wecom_deliveries", "runtime_scheduled_wecom_delivery_global_request_guard"),
        ):
            conn.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
        conn.execute("ALTER TABLE agent_runtime_scheduled_wecom_outcome_requests ENABLE TRIGGER "
                     "runtime_scheduled_wecom_outcome_request_immutable")
        conn.commit()
    _rollback(database, ROLLBACK)
    assert _owner(database, f"SELECT to_regclass('{LEDGER}')") is None
    _apply(database, MIGRATION)
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (SIGNATURE,)) is None
