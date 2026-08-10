from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import _finalize
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import _claim
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _fact_state,
    _identity,
    _owner,
    _prepare_params,
    _read_params,
    _rpc,
    _seed,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_version_readback_postgres_external import (
    _setup,
    _start_params,
)


pytestmark = pytest.mark.external
VERSION_KEYS = {"delivery_state_version", "item_state_version"}
RACE_LOCK_KEY = 2274501


def _authority(url: str, intent_id: str, item_id: str) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object('delivery_state_version',d.state_version,"
        "'item_state_version',item.state_version,'attempt_count',count(a.id),"
        "'started_count',count(a.id) FILTER(WHERE a.status='dispatch_started')) "
        "FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "LEFT JOIN agent_runtime_scheduled_wecom_dispatch_attempts a ON a.item_id=item.id "
        "WHERE d.intent_id=%s AND item.id=%s GROUP BY d.state_version,item.state_version",
        (intent_id, item_id),
    )


def _assert_no_authority(result: dict) -> None:
    assert result["outcome"] in {"fenced", "not_found", "empty"}
    assert set(result) == {"outcome"}
    assert VERSION_KEYS.isdisjoint(result)


def _call_without_authority(url: str, name: str, params: tuple) -> str:
    try:
        result = _rpc(url, name, params)
    except Exception as error:  # noqa: BLE001 - a typed SQL contract error is allowed here.
        message = str(error)
        assert "state_version" not in message
        assert "intent_id" not in message and "item_id" not in message
        return "error"
    _assert_no_authority(result)
    return result["outcome"]


def _item_for_claim(url: str, claim: dict) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object('id',item.id,'version',item.state_version) "
        "FROM agent_runtime_scheduled_wecom_delivery_items item "
        "WHERE item.intent_id=%s ORDER BY item.ordinal LIMIT 1",
        (claim["intent_id"],),
    )


def test_fifty_prepare_v2_calls_share_attempt_versions_and_conflicts_add_no_attempt(
    database: str,
) -> None:
    _setup(database)
    claim, item = _seed(database)
    params = _prepare_params(claim, item, _identity())
    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(
            lambda _: _rpc(
                database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2", params,
            ),
            range(50),
        ))
    assert sum(result["outcome"] == "prepared" for result in results) == 1
    assert sum(result["outcome"] == "readback" for result in results) == 49
    assert len({result["attempt_id"] for result in results}) == 1
    assert len({result["delivery_state_version"] for result in results}) == 1
    assert len({result["item_state_version"] for result in results}) == 1
    authority = _authority(database, claim["intent_id"], item["id"])
    assert authority["attempt_count"] == 1
    assert {
        "delivery_state_version": results[0]["delivery_state_version"],
        "item_state_version": results[0]["item_state_version"],
    } == {key: authority[key] for key in VERSION_KEYS}

    conflict = list(params)
    conflict[7] = f"provider-{uuid4()}"
    before = _fact_state(database)
    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(
            lambda _: _call_without_authority(
                database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2", tuple(conflict),
            ),
            range(50),
        ))
    assert outcomes == ["error"] * 50
    assert _fact_state(database) == before
    assert _authority(database, claim["intent_id"], item["id"])["attempt_count"] == 1


def test_fifty_start_v2_calls_make_one_transition_and_return_one_authority(
    database: str,
) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepared = _rpc(
        database,
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        _prepare_params(claim, item, _identity()),
    )
    params = _start_params(claim, prepared)
    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(
            lambda _: _rpc(
                database, "start_agent_runtime_scheduled_wecom_dispatch_v2", params,
            ),
            range(50),
        ))
    assert sum(result["outcome"] == "dispatch_started" for result in results) == 1
    assert sum(result["outcome"] == "readback" for result in results) == 49
    assert len({result["attempt_id"] for result in results}) == 1
    assert len({result["delivery_state_version"] for result in results}) == 1
    assert len({result["item_state_version"] for result in results}) == 1
    authority = _authority(database, claim["intent_id"], item["id"])
    assert authority["attempt_count"] == authority["started_count"] == 1
    assert authority["delivery_state_version"] == prepared["delivery_state_version"] + 1
    assert authority["item_state_version"] == prepared["item_state_version"] + 1
    assert {
        "delivery_state_version": results[0]["delivery_state_version"],
        "item_state_version": results[0]["item_state_version"],
    } == {key: authority[key] for key in VERSION_KEYS}


def test_prepare_start_and_read_v2_field_fence_matrix_never_returns_authority(
    database: str,
) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepare = _prepare_params(claim, item, _identity())
    prepared = _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2", prepare)
    before = _fact_state(database)
    prepare_changes = {
        "intent_id": (0, str(uuid4())),
        "item_id": (1, str(uuid4())),
        "claim_request_id": (2, str(uuid4())),
        "lease_token": (3, str(uuid4())),
        "worker_id": (4, "fenced-worker"),
        "expected_delivery_state_version": (5, prepare[5] + 1),
        "expected_item_state_version": (6, prepare[6] + 1),
        "provider_request_id": (7, f"provider-{uuid4()}"),
        "idempotency_key": (8, uuid4().hex + uuid4().hex),
        "provider_revision": (9, prepare[9] + 1),
    }
    for field, (index, replacement) in prepare_changes.items():
        changed = list(prepare)
        changed[index] = replacement
        outcome = _call_without_authority(
            database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2", tuple(changed),
        )
        assert outcome in {"fenced", "not_found", "error"}, field

    start = _start_params(claim, prepared)
    start_changes = {
        "intent_id": (0, str(uuid4())),
        "item_id": (1, str(uuid4())),
        "attempt_id": (2, str(uuid4())),
        "claim_request_id": (3, str(uuid4())),
        "lease_token": (4, str(uuid4())),
        "worker_id": (5, "fenced-worker"),
        "expected_delivery_state_version": (6, start[6] + 1),
        "expected_item_state_version": (7, start[7] + 1),
        "provider_request_id": (8, f"provider-{uuid4()}"),
        "idempotency_key": (9, uuid4().hex + uuid4().hex),
        "provider_revision": (10, start[10] + 1),
    }
    for field, (index, replacement) in start_changes.items():
        changed = list(start)
        changed[index] = replacement
        outcome = _call_without_authority(
            database, "start_agent_runtime_scheduled_wecom_dispatch_v2", tuple(changed),
        )
        assert outcome in {"fenced", "not_found", "error"}, field

    read = _read_params(claim, prepared)
    read_changes = {
        "intent_id": (0, str(uuid4())),
        "item_id": (1, str(uuid4())),
        "attempt_id": (2, str(uuid4())),
        "claim_request_id": (3, str(uuid4())),
        "lease_token": (4, str(uuid4())),
        "worker_id": (5, "fenced-worker"),
        "provider_request_id": (6, f"provider-{uuid4()}"),
        "idempotency_key": (7, uuid4().hex + uuid4().hex),
        "provider_revision": (8, read[8] + 1),
    }
    for field, (index, replacement) in read_changes.items():
        changed = list(read)
        changed[index] = replacement
        outcome = _call_without_authority(
            database,
            "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
            tuple(changed),
        )
        assert outcome in {"fenced", "not_found", "error"}, field
    assert _fact_state(database) == before


def _install_prepare_boundary_barrier(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(f"""
            CREATE FUNCTION _test_22745_prepare_boundary() RETURNS TRIGGER
            LANGUAGE plpgsql SET search_path=pg_catalog,public AS $test$
            BEGIN
              IF OLD.status='claimed' AND NEW.status='claimed'
              AND NEW.claim_request_id IS NOT DISTINCT FROM OLD.claim_request_id
              AND NEW.state_version=OLD.state_version+1 THEN
                NEW.lease_expires_at:=clock_timestamp()-interval '1 second';
                PERFORM pg_advisory_xact_lock({RACE_LOCK_KEY});
              END IF;
              RETURN NEW;
            END $test$;
            CREATE TRIGGER test_22745_prepare_boundary
            BEFORE UPDATE OF state_version ON agent_runtime_scheduled_wecom_deliveries
            FOR EACH ROW EXECUTE FUNCTION _test_22745_prepare_boundary()
        """)
        conn.commit()


def _wait_for_advisory_barrier(url: str) -> None:
    deadline = monotonic() + 5
    pause = Event()
    while monotonic() < deadline:
        waiting = _owner(
            url,
            "SELECT EXISTS(SELECT 1 FROM pg_locks "
            "WHERE locktype='advisory' AND NOT granted)",
        )
        if waiting:
            return
        pause.wait(0.01)
    raise AssertionError("prepare v2 did not reach the advisory transition/helper barrier")


def test_transition_helper_boundary_blocks_takeover_then_fences_old_claim(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepare = _prepare_params(claim, item, _identity())
    _install_prepare_boundary_barrier(database)
    recovery_request = str(uuid4())
    with psycopg.connect(database) as barrier:
        barrier.execute("SELECT pg_advisory_lock(%s)", (RACE_LOCK_KEY,))
        with ThreadPoolExecutor(max_workers=2) as pool:
            old_future = pool.submit(
                _rpc,
                database,
                "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
                prepare,
            )
            try:
                _wait_for_advisory_barrier(database)
                concurrent = pool.submit(
                    _rpc,
                    database,
                    "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
                    (recovery_request, "new-owner", 60),
                ).result(timeout=5)
                _assert_no_authority(concurrent)
                assert concurrent["outcome"] == "empty"
            finally:
                barrier.execute("SELECT pg_advisory_unlock(%s)", (RACE_LOCK_KEY,))
            old = old_future.result(timeout=5)
    assert old["outcome"] == "prepared"
    recovered = _rpc(
        database,
        "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (recovery_request, "new-owner", 60),
    )
    assert recovered["outcome"] == "recovered"
    _assert_no_authority(_rpc(
        database,
        "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
        _read_params(claim, old),
    ))
    new_start = (
        recovered["intent_id"], old["item_id"], old["attempt_id"],
        recovered["claim_request_id"], recovered["lease_token"], recovered["worker_id"],
        recovered["delivery_state_version"], recovered["item_state_version"],
        old["provider_request_id"], old["idempotency_key"], old["provider_revision"],
    )
    started = _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v2", new_start,
    )
    authority = _authority(database, recovered["intent_id"], old["item_id"])
    assert started["outcome"] == "dispatch_started"
    assert {key: started[key] for key in VERSION_KEYS} == {
        key: authority[key] for key in VERSION_KEYS
    }


def test_cross_tenant_intent_item_attempt_bindings_are_rejected(database: str) -> None:
    _setup(database)
    first_claim, first_item = _seed(database)
    first = _rpc(
        database,
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        _prepare_params(first_claim, first_item, _identity()),
    )
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    second_claim = _claim(database, worker="tenant-two-worker")
    second_item = _item_for_claim(database, second_claim)
    second = _rpc(
        database,
        "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        _prepare_params(second_claim, second_item, _identity()),
    )
    tenant_two = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO organizations(id) VALUES(%s)", (tenant_two,))
        conn.execute(
            "ALTER TABLE agent_runtime_scheduled_delivery_intents "
            "DISABLE TRIGGER runtime_scheduled_delivery_intent_immutable",
        )
        conn.execute(
            "ALTER TABLE agent_runtime_scheduled_wecom_deliveries "
            "DISABLE TRIGGER runtime_scheduled_wecom_delivery_identity_guard",
        )
        conn.execute(
            "UPDATE agent_runtime_scheduled_delivery_intents SET org_id=%s WHERE id=%s",
            (tenant_two, second_claim["intent_id"]),
        )
        conn.execute(
            "UPDATE agent_runtime_scheduled_wecom_deliveries SET org_id=%s WHERE intent_id=%s",
            (tenant_two, second_claim["intent_id"]),
        )
        conn.execute(
            "ALTER TABLE agent_runtime_scheduled_delivery_intents "
            "ENABLE TRIGGER runtime_scheduled_delivery_intent_immutable",
        )
        conn.execute(
            "ALTER TABLE agent_runtime_scheduled_wecom_deliveries "
            "ENABLE TRIGGER runtime_scheduled_wecom_delivery_identity_guard",
        )
        conn.commit()
    assert _owner(
        database,
        "SELECT count(DISTINCT org_id) FROM agent_runtime_scheduled_wecom_deliveries "
        "WHERE intent_id IN(%s,%s)",
        (first_claim["intent_id"], second_claim["intent_id"]),
    ) == 2

    first_read = list(_read_params(first_claim, first))
    crossings = {
        "tenant_intent": (0, second_claim["intent_id"]),
        "tenant_item": (1, second["item_id"]),
        "tenant_attempt": (2, second["attempt_id"]),
    }
    for field, (index, replacement) in crossings.items():
        changed = first_read.copy()
        changed[index] = replacement
        outcome = _call_without_authority(
            database,
            "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
            tuple(changed),
        )
        assert outcome in {"fenced", "not_found", "error"}, field
    all_second_with_first_claim = (
        second_claim["intent_id"], second["item_id"], second["attempt_id"],
        first_claim["claim_request_id"], first_claim["lease_token"], first_claim["worker_id"],
        second["provider_request_id"], second["idempotency_key"], second["provider_revision"],
    )
    _assert_no_authority(_rpc(
        database,
        "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
        all_second_with_first_claim,
    ))
