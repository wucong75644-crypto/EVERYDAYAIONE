from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import _finalize
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _facts,
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
)


pytestmark = pytest.mark.external
MIGRATION = "227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql"
ROLLBACK = "227_41_agent_runtime_scheduled_wecom_reconcile_claim_rollback.sql"
SIGNATURES = (
    "claim_agent_runtime_scheduled_wecom_reconcile_v1(uuid,text,integer)",
    "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1(uuid,uuid,uuid,text,bigint,integer)",
    "read_agent_runtime_scheduled_wecom_reconcile_v1(uuid)",
)


def _setup(url: str) -> None:
    _outcome_setup(url)
    _apply(url, MIGRATION)


def _unknown(url: str) -> tuple[dict, dict]:
    claim, _, started = _start(url)
    result = _rpc(
        url,
        "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(url, claim, started, "unknown"),
    )
    assert result["delivery_status"] == "unknown"
    return started, result


def _claim(url: str, worker: str, request_id: str | None = None, lease: int = 60) -> dict:
    return _rpc(
        url,
        "claim_agent_runtime_scheduled_wecom_reconcile_v1",
        (request_id or str(uuid4()), worker, lease),
    )


def test_fifty_claimers_have_one_winner_and_freeze_unknown_identity(database: str) -> None:
    _setup(database)
    started, _ = _unknown(database)
    before = _owner(
        database,
        "SELECT jsonb_build_object('attempt',to_jsonb(a),'count',"
        "(SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts)) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE a.id=%s",
        (started["attempt_id"],),
    )
    requests = [str(uuid4()) for _ in range(50)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(
            lambda pair: _claim(database, f"reconciler-{pair[0]}", pair[1]),
            enumerate(requests),
        ))
    winners = [result for result in results if result["outcome"] == "claimed"]
    assert len(winners) == 1
    assert sum(result["outcome"] == "empty" for result in results) == 49
    winner = winners[0]
    assert winner["attempt_id"] == started["attempt_id"]
    assert winner["provider_request_id"] == started["provider_request_id"]
    assert winner["idempotency_key"] == started["idempotency_key"]
    assert winner["provider_revision"] == started["provider_revision"]
    assert winner["attempt_status"] == "unknown" and winner["dispatch_phase"] == "ambiguous"
    after = _owner(
        database,
        "SELECT jsonb_build_object('attempt',to_jsonb(a),'count',"
        "(SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts)) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a WHERE a.id=%s",
        (started["attempt_id"],),
    )
    assert after == before
    assert all(key not in str(winner).lower() for key in ("payload", "secret", "raw_body"))


def test_request_replay_conflict_and_pure_readback(database: str) -> None:
    _setup(database)
    started, _ = _unknown(database)
    prepared_request_id = _owner(
        database,
        "SELECT claim_request_id FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s",
        (started["attempt_id"],),
    )
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _claim(database, "reconciler", str(prepared_request_id), 60)
    request_id = str(uuid4())
    claimed = _claim(database, "reconciler", request_id, 60)
    before = _facts(database)
    replay = _claim(database, "reconciler", request_id, 60)
    readback = _rpc(
        database, "read_agent_runtime_scheduled_wecom_reconcile_v1", (request_id,),
    )
    assert replay["outcome"] == "readback" and readback["outcome"] == "readback"
    assert replay["attempt_id"] == claimed["attempt_id"] == readback["attempt_id"]
    assert _facts(database) == before
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _claim(database, "other-worker", request_id, 60)
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _claim(database, "reconciler", request_id, 90)
    assert _rpc(
        database, "read_agent_runtime_scheduled_wecom_reconcile_v1", (str(uuid4()),),
    )["outcome"] == "not_found"


@pytest.mark.parametrize(
    ("delivery_offset", "item_offset", "expected"),
    ((-1, -1, "claimed"), (3600, -1, "empty"), (-1, 3600, "empty"),
     (3600, 3600, "empty")),
)
def test_delivery_and_item_due_time_matrix(
    database: str, delivery_offset: int, item_offset: int, expected: str,
) -> None:
    _setup(database)
    started, _ = _unknown(database)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET next_attempt_at=clock_timestamp()+make_interval(secs=>%s) "
        "WHERE intent_id=(SELECT item.intent_id FROM agent_runtime_scheduled_wecom_delivery_items item "
        "WHERE item.id=%s) RETURNING intent_id",
        (delivery_offset, started["item_id"]),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items "
        "SET next_attempt_at=clock_timestamp()+make_interval(secs=>%s) "
        "WHERE id=%s RETURNING id",
        (item_offset, started["item_id"]),
    )
    assert _claim(database, "matrix-worker")["outcome"] == expected
    if expected == "empty":
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_deliveries "
            "SET next_attempt_at=clock_timestamp()-interval '1 second' "
            "WHERE intent_id=(SELECT intent_id FROM agent_runtime_scheduled_wecom_delivery_items "
            "WHERE id=%s) RETURNING intent_id",
            (started["item_id"],),
        )
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_delivery_items "
            "SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=%s RETURNING id",
            (started["item_id"],),
        )
        assert _claim(database, "due-worker")["outcome"] == "claimed"


def _prepare_expired_recovery(url: str) -> None:
    claim, item = _seed(url)
    _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    _owner(
        url,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )


def test_reconcile_first_rejects_all_three_legacy_request_writers(database: str) -> None:
    _setup(database)
    _unknown(database)
    request_id = str(uuid4())
    assert _claim(database, "reconcile-first", request_id)["outcome"] == "claimed"

    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _rpc(
            database, "claim_agent_runtime_scheduled_wecom_delivery_v1",
            (request_id, "legacy-claim", 60),
        )

    _prepare_expired_recovery(database)
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _rpc(
            database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
            (request_id, "legacy-recovery", 60),
        )

    claim, _, started = _start(database)
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _rpc(
            database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
            _outcome_params(database, claim, started, "accepted", request_id=request_id),
        )


def test_all_three_legacy_request_writers_first_reject_reconcile(database: str) -> None:
    _setup(database)
    _unknown(database)

    claim_request_id = str(uuid4())
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    assert _rpc(
        database, "claim_agent_runtime_scheduled_wecom_delivery_v1",
        (claim_request_id, "legacy-claim", 60),
    )["outcome"] == "claimed"
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _claim(database, "reconcile", claim_request_id)

    recovery_request_id = str(uuid4())
    _prepare_expired_recovery(database)
    assert _rpc(
        database, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (recovery_request_id, "legacy-recovery", 60),
    )["outcome"] == "recovered"
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _claim(database, "reconcile", recovery_request_id)

    outcome_request_id = str(uuid4())
    claim, _, started = _start(database)
    assert _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(
            database, claim, started, "accepted", request_id=outcome_request_id,
        ),
    )["outcome"] == "recorded"
    with pytest.raises(Exception, match="RECONCILE_REQUEST_CONFLICT"):
        _claim(database, "reconcile", outcome_request_id)


def test_fifty_reconcile_vs_outcome_same_request_has_one_namespace_winner(
    database: str,
) -> None:
    _setup(database)
    _unknown(database)
    claim, _, started = _start(database)
    request_id = str(uuid4())
    outcome_params = _outcome_params(
        database, claim, started, "accepted", request_id=request_id,
    )
    barrier = Barrier(50)

    def compete(index: int) -> str:
        barrier.wait()
        try:
            if index % 2 == 0:
                return _claim(database, "race-reconciler", request_id)["outcome"]
            return _rpc(
                database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
                outcome_params,
            )["outcome"]
        except Exception as error:  # noqa: BLE001 - expected namespace conflict under race.
            assert "RECONCILE_REQUEST_CONFLICT" in str(error)
            return "conflict"

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(compete, range(50)))
    assert "conflict" in results
    ledger_count = _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_claim_requests "
        "WHERE request_id=%s",
        (request_id,),
    )
    outcome_count = _owner(
        database,
        "SELECT count(*) FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=%s",
        (request_id,),
    )
    assert (ledger_count, outcome_count) in ((1, 0), (0, 1))


def test_renew_expiry_takeover_and_old_request_stays_fenced(database: str) -> None:
    _setup(database)
    _unknown(database)
    old = _claim(database, "old-worker")
    renewed = _rpc(
        database,
        "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
        (
            old["intent_id"], old["request_id"], old["reconcile_token"], old["worker_id"],
            old["delivery_state_version"], 90,
        ),
    )
    assert renewed["outcome"] == "renewed"
    assert renewed["delivery_state_version"] == old["delivery_state_version"] + 1
    assert _rpc(
        database,
        "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
        (
            old["intent_id"], old["request_id"], old["reconcile_token"], old["worker_id"],
            old["delivery_state_version"], 90,
        ),
    )["outcome"] == "fenced"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET reconcile_lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id",
        (old["intent_id"],),
    )
    takeover = _claim(database, "new-worker")
    assert takeover["outcome"] == "claimed"
    assert takeover["intent_id"] == old["intent_id"]
    assert takeover["attempt_id"] == old["attempt_id"]
    assert takeover["reconcile_token"] != old["reconcile_token"]
    old_readback = _rpc(
        database, "read_agent_runtime_scheduled_wecom_reconcile_v1", (old["request_id"],),
    )
    assert old_readback["outcome"] == "fenced"
    assert old_readback["attempt_id"] == old["attempt_id"]
    assert old_readback["reconcile_token"] == old["reconcile_token"]
    assert _claim(database, "old-worker", old["request_id"])["outcome"] == "fenced"
    assert _rpc(
        database,
        "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
        (
            old["intent_id"], old["request_id"], old["reconcile_token"], old["worker_id"],
            renewed["delivery_state_version"], 90,
        ),
    )["outcome"] == "fenced"


def test_null_matrix_and_ineligible_facts_create_no_claim(database: str) -> None:
    _setup(database)
    started, _ = _unknown(database)
    claim_values = [str(uuid4()), "worker", 60]
    for index in range(len(claim_values)):
        invalid = claim_values.copy()
        invalid[index] = None
        with pytest.raises(Exception, match="RECONCILE_CLAIM_INVALID"):
            _rpc(database, "claim_agent_runtime_scheduled_wecom_reconcile_v1", tuple(invalid))
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_reconcile_claim_requests",
    ) == 0
    claimed = _claim(database, "worker")
    renew_values = [
        claimed["intent_id"], claimed["request_id"], claimed["reconcile_token"], "worker",
        claimed["delivery_state_version"], 60,
    ]
    for index in range(len(renew_values)):
        invalid = renew_values.copy()
        invalid[index] = None
        with pytest.raises(Exception, match="RECONCILE_RENEW_INVALID"):
            _rpc(
                database,
                "renew_agent_runtime_scheduled_wecom_reconcile_lease_v1",
                tuple(invalid),
            )
    with pytest.raises(Exception, match="RECONCILE_READBACK_INVALID"):
        _rpc(database, "read_agent_runtime_scheduled_wecom_reconcile_v1", (None,))
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET reconcile_worker_id=NULL,"
        "reconcile_request_id=NULL,reconcile_token=NULL,reconcile_lease_expires_at=NULL "
        "WHERE intent_id=%s RETURNING intent_id",
        (claimed["intent_id"],),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET status='accepted',"
        "dispatch_phase='receipt_recorded',receipt_type='test',receipt_hash=%s,"
        "resolved_at=clock_timestamp() WHERE id=%s RETURNING id",
        ("f" * 64, started["attempt_id"]),
    )
    assert _claim(database, "ineligible")["outcome"] == "empty"


def test_acl_rls_immutability_and_apply_rollback_reapply(database: str) -> None:
    _setup(database)
    assert _owner(
        database,
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid='agent_runtime_scheduled_wecom_reconcile_claim_requests'::regclass",
    ) is True
    assert _owner(
        database,
        "SELECT NOT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL "
        "aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl "
        "WHERE c.oid='agent_runtime_scheduled_wecom_reconcile_claim_requests'::regclass "
        "AND acl.grantee=0 AND acl.privilege_type IN('SELECT','INSERT','UPDATE','DELETE'))",
    ) is True
    for role in (
        "everydayai_runtime", "everydayai_wecom_runtime", "everydayai_worker",
        "everydayai_agent_runtime_worker",
    ):
        assert _owner(
            database,
            "SELECT NOT has_table_privilege(%s,"
            "'agent_runtime_scheduled_wecom_reconcile_claim_requests','SELECT,INSERT,UPDATE,DELETE')",
            (role,),
        ) is True
    for signature in SIGNATURES:
        assert _owner(
            database,
            "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
            "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
            "WHERE p.oid=%s::regprocedure AND acl.grantee=0 "
            "AND acl.privilege_type='EXECUTE')",
            (signature,),
        ) is True
        assert _owner(
            database,
            "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
            (signature,),
        ) is True
        for role in ("everydayai_runtime", "everydayai_worker"):
            assert _owner(
                database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')",
                (role, signature),
            ) is True
    with pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        _rpc(
            database, "claim_agent_runtime_scheduled_wecom_reconcile_v1",
            (str(uuid4()), "worker", 60), access_kind="legacy",
        )
    _rollback(database, ROLLBACK)
    assert _owner(
        database, "SELECT to_regclass('agent_runtime_scheduled_wecom_reconcile_claim_requests')",
    ) is None
    _apply(database, MIGRATION)
    _unknown(database)
    claimed = _claim(database, "worker")
    with pytest.raises(Exception, match="RECONCILE_CLAIM_IMMUTABLE"):
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_reconcile_claim_requests SET worker_id='changed' "
            "WHERE request_id=%s RETURNING request_id",
            (claimed["request_id"],),
        )
    with pytest.raises(Exception, match="RECONCILE_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
