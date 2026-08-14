from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_scheduled_wecom_dispatch_outcome_postgres_external import (
    _setup as _outcome_setup,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _identity,
    _owner,
    _prepare_params,
    _read_params,
    _rpc,
    _seed,
)


pytestmark = pytest.mark.external
MIGRATION = "227_45_agent_runtime_scheduled_wecom_dispatch_version_readback.sql"
ROLLBACK = "227_45_agent_runtime_scheduled_wecom_dispatch_version_readback_rollback.sql"
V1_SIGNATURES = (
    "prepare_agent_runtime_scheduled_wecom_dispatch_v1(uuid,uuid,uuid,uuid,text,bigint,bigint,text,text,bigint)",
    "start_agent_runtime_scheduled_wecom_dispatch_v1(uuid,uuid,uuid,uuid,uuid,text,bigint,bigint,text,text,bigint)",
    "read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(uuid,uuid,uuid,uuid,uuid,text,text,text,bigint)",
)
V2_SIGNATURES = tuple(signature.replace("_v1", "_v2") for signature in V1_SIGNATURES)
HELPER_SIGNATURE = (
    "_agent_runtime_scheduled_wecom_dispatch_versioned_json(jsonb,uuid,uuid,uuid,uuid,uuid,"
    "text,text,text,bigint)"
)


def _setup(url: str) -> None:
    _outcome_setup(url)
    _apply(url, MIGRATION)


def _prepare_v2(url: str) -> tuple[dict, dict, tuple, dict]:
    claim, item = _seed(url)
    params = _prepare_params(claim, item, _identity())
    prepared = _rpc(url, "prepare_agent_runtime_scheduled_wecom_dispatch_v2", params)
    return claim, item, params, prepared


def _start_params(claim: dict, prepared: dict) -> tuple:
    return (
        claim["intent_id"], prepared["item_id"], prepared["attempt_id"],
        claim["claim_request_id"], claim["lease_token"], claim["worker_id"],
        prepared["delivery_state_version"], prepared["item_state_version"],
        prepared["provider_request_id"], prepared["idempotency_key"],
        prepared["provider_revision"],
    )


def _outcome_params(claim: dict, started: dict) -> tuple:
    return (
        str(uuid4()), claim["intent_id"], started["item_id"], started["attempt_id"],
        claim["claim_request_id"], claim["lease_token"], claim["worker_id"],
        started["delivery_state_version"], started["item_state_version"],
        started["provider_request_id"], started["idempotency_key"],
        started["provider_revision"], "unknown", None, None, None, Jsonb({}),
    )


def test_v2_prepare_start_readback_and_outcome_close_without_table_version_read(
    database: str,
) -> None:
    _setup(database)
    claim, item, prepare_params, prepared = _prepare_v2(database)
    assert prepared["outcome"] == "prepared"
    assert prepared["delivery_state_version"] == claim["state_version"] + 1
    assert prepared["item_state_version"] == item["version"] + 1
    assert isinstance(prepared["delivery_state_version"], int)
    assert isinstance(prepared["item_state_version"], int)

    prepare_replay = _rpc(
        database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2", prepare_params,
    )
    assert prepare_replay["outcome"] == "readback"
    assert prepare_replay["attempt_id"] == prepared["attempt_id"]
    assert prepare_replay["delivery_state_version"] == prepared["delivery_state_version"]
    assert prepare_replay["item_state_version"] == prepared["item_state_version"]

    start_params = _start_params(claim, prepared)
    started = _rpc(database, "start_agent_runtime_scheduled_wecom_dispatch_v2", start_params)
    assert started["outcome"] == "dispatch_started"
    assert started["delivery_state_version"] == prepared["delivery_state_version"] + 1
    assert started["item_state_version"] == prepared["item_state_version"] + 1
    start_replay = _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v2", start_params,
    )
    assert start_replay["outcome"] == "readback"
    assert start_replay["attempt_id"] == started["attempt_id"]
    assert start_replay["delivery_state_version"] == started["delivery_state_version"]
    assert start_replay["item_state_version"] == started["item_state_version"]

    readback = _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
        _read_params(claim, started),
    )
    assert readback["outcome"] == "readback"
    assert readback["delivery_state_version"] == started["delivery_state_version"]
    assert readback["item_state_version"] == started["item_state_version"]
    result = _rpc(
        database, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(claim, started),
    )
    assert result["outcome"] == "recorded" and result["dispatch_outcome"] == "unknown"


def test_v2_fencing_identity_drift_and_not_found_do_not_disclose_versions(database: str) -> None:
    _setup(database)
    claim, _, _, prepared = _prepare_v2(database)
    valid_read = list(_read_params(claim, prepared))
    wrong_provider = valid_read.copy()
    wrong_provider[6] = f"provider-{uuid4()}"
    fenced = _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
        tuple(wrong_provider),
    )
    assert fenced == {"outcome": "fenced"}

    not_found = valid_read.copy()
    not_found[2] = str(uuid4())
    missing = _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2", tuple(not_found),
    )
    assert missing == {"outcome": "not_found"}

    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET claim_worker_id='drift-worker',"
        "state_version=state_version+1 WHERE intent_id=%s RETURNING intent_id",
        (claim["intent_id"],),
    )
    drifted = _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2", tuple(valid_read),
    )
    assert drifted == {"outcome": "fenced"}


def test_v2_acl_rls_actor_and_fixed_search_path(database: str) -> None:
    _setup(database)
    claim, item = _seed(database)
    for signature in V2_SIGNATURES:
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
        for role in (
            "everydayai", "everydayai_runtime", "everydayai_worker",
            "everydayai_agent_runtime_worker",
        ):
            assert _owner(
                database,
                "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')", (role, signature),
            ) is True
        assert _owner(
            database,
            "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
            "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
            "WHERE p.oid=%s::regprocedure AND acl.grantee=0 "
            "AND acl.privilege_type='EXECUTE')",
            (signature,),
        ) is True
    for signature in V1_SIGNATURES:
        assert _owner(
            database,
            "SELECT NOT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
            (signature,),
        ) is True
    assert _owner(
        database,
        "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
        "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
        "WHERE p.oid=%s::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
        (HELPER_SIGNATURE,),
    ) is True
    assert _owner(
        database,
        "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
        "FROM pg_proc WHERE oid=%s::regprocedure",
        (HELPER_SIGNATURE,),
    ) is True
    for table in (
        "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_delivery_items",
        "agent_runtime_scheduled_wecom_dispatch_attempts",
    ):
        assert _owner(
            database,
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            (table,),
        ) is True
        assert _owner(
            database,
            "SELECT NOT has_table_privilege('everydayai_wecom_runtime',%s,'SELECT')", (table,),
        ) is True
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _rpc(
            database, "prepare_agent_runtime_scheduled_wecom_dispatch_v1",
            _prepare_params(claim, item, _identity()),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _rpc(
            database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
            _prepare_params(claim, item, _identity()),
            role="everydayai_agent_runtime_worker", access_kind="agent_runtime",
        )
    with pytest.raises(Exception, match="AGENT_RUNTIME_SCHEDULED_WECOM_SCOPE_REQUIRED"):
        _rpc(
            database, "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
            _prepare_params(claim, item, _identity()), access_kind="runtime",
        )


def test_rollback_reapply_and_final_rollback_restore_v1_acl(database: str) -> None:
    _setup(database)
    _rollback(database, ROLLBACK)
    for v1, v2 in zip(V1_SIGNATURES, V2_SIGNATURES, strict=True):
        assert _owner(
            database,
            "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')", (v1,),
        ) is True
        assert _owner(database, "SELECT to_regprocedure(%s)", (v2,)) is None
    _apply(database, MIGRATION)
    for v1, v2 in zip(V1_SIGNATURES, V2_SIGNATURES, strict=True):
        assert _owner(
            database,
            "SELECT NOT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')", (v1,),
        ) is True
        assert _owner(database, "SELECT to_regprocedure(%s) IS NOT NULL", (v2,)) is True
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (V2_SIGNATURES[0],)) is None
    assert _owner(
        database,
        "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (V1_SIGNATURES[0],),
    ) is True
