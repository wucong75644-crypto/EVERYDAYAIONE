from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import _finalize
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _claim,
    _owner,
    _rpc,
    _set_user_target,
    _setup as _claim_setup,
)


pytestmark = pytest.mark.external
MIGRATION = "227_39_agent_runtime_scheduled_wecom_dispatch_prepare.sql"
ROLLBACK = "227_39_agent_runtime_scheduled_wecom_dispatch_prepare_rollback.sql"
SIGNATURES = (
    "prepare_agent_runtime_scheduled_wecom_dispatch_v1(uuid,uuid,uuid,uuid,text,bigint,bigint,text,text,bigint)",
    "start_agent_runtime_scheduled_wecom_dispatch_v1(uuid,uuid,uuid,uuid,uuid,text,bigint,bigint,text,text,bigint)",
    "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(uuid,uuid,uuid,uuid,uuid,text,text,text,bigint)",
)


def _setup(url: str) -> None:
    _claim_setup(url)
    _apply(url, MIGRATION)
    _set_user_target(url, "app")


def _owner_execute(url: str, sql: str, params: tuple = ()) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(sql, params)
        conn.commit()


def _seed(url: str) -> tuple[dict, dict]:
    _finalize(url, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _claim(url)
    item = _owner(
        url,
        "SELECT jsonb_build_object('id',item.id,'version',item.state_version) "
        "FROM agent_runtime_scheduled_wecom_delivery_items item "
        "WHERE item.intent_id=%s ORDER BY ordinal LIMIT 1",
        (claim["intent_id"],),
    )
    return claim, item


def _identity() -> tuple[str, str]:
    return f"provider-{uuid4()}", uuid4().hex + uuid4().hex


def _prepare_params(claim: dict, item: dict, identity: tuple[str, str]) -> tuple:
    return (
        claim["intent_id"], item["id"], claim["claim_request_id"], claim["lease_token"],
        claim["worker_id"], claim["state_version"], item["version"], identity[0], identity[1], 1,
    )


def _start_params(url: str, claim: dict, item: dict, prepared: dict) -> tuple:
    versions = _owner(
        url,
        "SELECT jsonb_build_object('delivery',d.state_version,'item',item.state_version) "
        "FROM agent_runtime_scheduled_wecom_deliveries d "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.intent_id=d.intent_id "
        "WHERE d.intent_id=%s AND item.id=%s",
        (claim["intent_id"], item["id"]),
    )
    return (
        claim["intent_id"], item["id"], prepared["attempt_id"], claim["claim_request_id"],
        claim["lease_token"], claim["worker_id"], versions["delivery"], versions["item"],
        prepared["provider_request_id"], prepared["idempotency_key"], 1,
    )


def _read_params(claim: dict, prepared: dict) -> tuple:
    return (
        claim["intent_id"], prepared["item_id"], prepared["attempt_id"],
        claim["claim_request_id"], claim["lease_token"], claim["worker_id"],
        prepared["provider_request_id"], prepared["idempotency_key"], 1,
    )


def test_prepare_start_and_pure_response_loss_readback(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepare_params = _prepare_params(claim, item, _identity())
    prepared = _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", prepare_params)
    assert prepared["outcome"] == "prepared" and prepared["status"] == "prepared"
    assert _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", prepare_params,
    )["outcome"] == "readback"
    start_params = _start_params(database, claim, item, prepared)
    started = _rpc(database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params)
    assert started["outcome"] == "dispatch_started" and started["status"] == "dispatch_started"
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params,
    )["outcome"] == "readback"
    before = _owner(
        database,
        "SELECT jsonb_build_object('attempt',to_jsonb(a),'item',to_jsonb(item),'delivery',to_jsonb(d)) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=item.intent_id WHERE a.id=%s",
        (prepared["attempt_id"],),
    )
    readback = _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1",
        _read_params(claim, prepared),
    )
    after = _owner(
        database,
        "SELECT jsonb_build_object('attempt',to_jsonb(a),'item',to_jsonb(item),'delivery',to_jsonb(d)) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=item.intent_id WHERE a.id=%s",
        (prepared["attempt_id"],),
    )
    assert readback["outcome"] == "readback" and readback["status"] == "dispatch_started"
    assert before == after


def test_fifty_concurrent_prepare_and_start_have_one_transition(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepare_params = _prepare_params(claim, item, _identity())
    with ThreadPoolExecutor(max_workers=50) as pool:
        prepared = list(pool.map(
            lambda _: _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", prepare_params),
            range(50),
        ))
    assert sum(row["outcome"] == "prepared" for row in prepared) == 1
    assert sum(row["outcome"] == "readback" for row in prepared) == 49
    attempt = prepared[0]
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts",
    ) == 1
    start_params = _start_params(database, claim, item, attempt)
    with ThreadPoolExecutor(max_workers=50) as pool:
        started = list(pool.map(
            lambda _: _rpc(database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params),
            range(50),
        ))
    assert sum(row["outcome"] == "dispatch_started" for row in started) == 1
    assert sum(row["outcome"] == "readback" for row in started) == 49
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts "
        "WHERE status='dispatch_started'",
    ) == 1


def test_old_lease_takeover_conflicts_and_cross_identity_are_fenced(database: str) -> None:
    _setup(database)
    old_claim, item = _seed(database)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (old_claim["intent_id"],),
    )
    new_claim = _claim(database, worker="takeover-worker")
    assert new_claim["outcome"] == "claimed"
    assert _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(old_claim, item, _identity()),
    )["outcome"] == "fenced"
    fresh_item = {"id": item["id"], "version": item["version"]}
    params = _prepare_params(new_claim, fresh_item, _identity())
    prepared = _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", params)
    assert prepared["outcome"] == "prepared"
    conflict_provider = list(params)
    conflict_provider[8] = uuid4().hex + uuid4().hex
    with pytest.raises(Exception, match="PREPARE_CONFLICT"):
        _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", tuple(conflict_provider))
    conflict_key = list(params)
    conflict_key[7] = f"provider-{uuid4()}"
    with pytest.raises(Exception, match="PREPARE_CONFLICT"):
        _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", tuple(conflict_key))
    wrong_revision = list(_start_params(database, new_claim, fresh_item, prepared))
    wrong_revision[-1] = 2
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", tuple(wrong_revision),
    )["outcome"] == "fenced"


def test_pure_readback_fences_expired_and_replaced_current_claim(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    assert _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1",
        _read_params(claim, prepared),
    )["outcome"] == "fenced"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='claimed',claim_request_id=%s,"
        "lease_token=%s,claim_worker_id='replacement-worker',"
        "lease_expires_at=clock_timestamp()+interval '1 minute',state_version=state_version+1 "
        "WHERE intent_id=%s RETURNING intent_id",
        (str(uuid4()), str(uuid4()), claim["intent_id"]),
    )
    assert _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1",
        _read_params(claim, prepared),
    )["outcome"] == "fenced"


def test_started_replay_fences_expired_and_replaced_current_claim(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    start_params = _start_params(database, claim, item, prepared)
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params,
    )["outcome"] == "dispatch_started"
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params,
    )["outcome"] == "readback"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params,
    )["outcome"] == "fenced"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='claimed',claim_request_id=%s,"
        "lease_token=%s,claim_worker_id='replacement-worker',"
        "lease_expires_at=clock_timestamp()+interval '1 minute',state_version=state_version+1 "
        "WHERE intent_id=%s RETURNING intent_id",
        (str(uuid4()), str(uuid4()), claim["intent_id"]),
    )
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", start_params,
    )["outcome"] == "fenced"


def test_split_provider_and_idempotency_hits_return_contract_conflict(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    first = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    second_provider, second_key = _identity()
    _owner(
        database,
        "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts(item_id,attempt_number,"
        "provider_request_id,idempotency_key,provider_revision,status,dispatch_phase,claim_request_id,"
        "lease_token,claim_worker_id,prepared_delivery_state_version,prepared_item_state_version) "
        "VALUES(%s,2,%s,%s,1,'prepared','prepared',%s,%s,%s,%s,%s) RETURNING id",
        (
            item["id"], second_provider, second_key, claim["claim_request_id"],
            claim["lease_token"], claim["worker_id"], claim["state_version"], item["version"],
        ),
    )
    split_identity = (
        claim["intent_id"], item["id"], claim["claim_request_id"], claim["lease_token"],
        claim["worker_id"], claim["state_version"], item["version"],
        first["provider_request_id"], second_key, 1,
    )
    with pytest.raises(Exception, match="PREPARE_CONFLICT"):
        _rpc(database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", split_identity)


def test_ordering_cross_item_and_live_target_start_fence_preserve_prepared_fact(database: str) -> None:
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
    second = {"id": second_id, "version": 0}
    assert _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, second, _identity()),
    )["outcome"] == "fenced"
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, first, _identity()),
    )
    _owner(
        database,
        "UPDATE org_members SET status='disabled' WHERE org_id=%s RETURNING user_id", (ORG,),
    )
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1",
        _start_params(database, claim, first, prepared),
    )["outcome"] == "fenced"
    state = _owner(
        database,
        "SELECT jsonb_build_object('attempt',a.status,'item',item.status,'delivery',d.status) "
        "FROM agent_runtime_scheduled_wecom_dispatch_attempts a "
        "JOIN agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "JOIN agent_runtime_scheduled_wecom_deliveries d ON d.intent_id=item.intent_id",
    )
    assert state == {"attempt": "prepared", "item": "dispatching", "delivery": "claimed"}


def test_intent_item_attempt_crossing_is_rejected(database: str) -> None:
    _setup(database)
    first_claim, first_item = _seed(database)
    first_attempt = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(first_claim, first_item, _identity()),
    )
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    second_item = _owner(
        database,
        "SELECT jsonb_build_object('id',item.id,'intent_id',item.intent_id) "
        "FROM agent_runtime_scheduled_wecom_delivery_items item "
        "WHERE item.intent_id<>%s ORDER BY item.created_at DESC LIMIT 1",
        (first_claim["intent_id"],),
    )
    crossed_prepare = list(_prepare_params(first_claim, first_item, _identity()))
    crossed_prepare[1] = second_item["id"]
    assert _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1", tuple(crossed_prepare),
    )["outcome"] == "fenced"
    crossed_read = list(_read_params(first_claim, first_attempt))
    crossed_read[0] = second_item["intent_id"]
    assert _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1", tuple(crossed_read),
    )["outcome"] == "not_found"
    crossed_start = list(_start_params(database, first_claim, first_item, first_attempt))
    crossed_start[1] = second_item["id"]
    assert _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v1", tuple(crossed_start),
    )["outcome"] == "fenced"


def test_acl_scope_cross_intent_and_rollback_cleanup_reapply(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    for signature in SIGNATURES:
        assert _owner(
            database,
            "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
            "FROM pg_proc WHERE oid=%s::regprocedure",
            (signature,),
        ) is True
        assert _owner(
            database,
            "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
            (signature,),
        ) is True
        assert _owner(
            database,
            "SELECT NOT has_function_privilege('everydayai_runtime',%s,'EXECUTE')",
            (signature,),
        ) is True
        assert _owner(
            database,
            "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
            "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
            "WHERE p.oid=%s::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
            (signature,),
        ) is True
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _rpc(
            database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
            _prepare_params(claim, item, _identity()), access_kind="runtime",
        )
    prepared = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
        _prepare_params(claim, item, _identity()),
    )
    with pytest.raises(Exception, match="DISPATCH_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
    _owner_execute(database, "TRUNCATE agent_runtime_scheduled_wecom_dispatch_attempts")
    with pytest.raises(Exception, match="DISPATCH_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='pending',state_version=0 "
        "WHERE id=%s RETURNING id", (prepared["item_id"],),
    )
    _rollback(database, ROLLBACK)
    assert _owner(
        database, "SELECT to_regprocedure(%s)", (SIGNATURES[0],),
    ) is None
    _apply(database, MIGRATION)
    _rollback(database, ROLLBACK)
