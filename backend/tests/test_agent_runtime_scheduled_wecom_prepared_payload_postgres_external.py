from copy import deepcopy
from uuid import uuid4

import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _claim, _owner, _rpc, _set_user_target, _setup as _claim_setup,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_payload_postgres_external import (
    SUCCESS_KEYS, _apply_completed, _item, _payload, _state,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _identity, _prepare_params,
)


pytestmark = pytest.mark.external
MIGRATION = "227_51_agent_runtime_scheduled_wecom_prepared_payload.sql"
ROLLBACK = "227_51_agent_runtime_scheduled_wecom_prepared_payload_rollback.sql"
FUNCTION = "read_agent_runtime_scheduled_wecom_prepared_payload_v1"
SIGNATURE = (
    f"{FUNCTION}(uuid,uuid,uuid,uuid,integer,uuid,uuid,text,bigint,bigint,text,text,bigint)"
)
HELPER_SIGNATURE = (
    "_agent_runtime_scheduled_wecom_safe_payload_v2(jsonb,"
    "agent_runtime_scheduled_wecom_delivery_items,bigint,bigint)"
)


def _setup(
    url: str, summary: str = "prepared recovery 中文摘要",
) -> tuple[dict, dict, dict, dict, dict]:
    _claim_setup(url)
    for migration in (
        "227_39_agent_runtime_scheduled_wecom_dispatch_prepare.sql",
        "227_45_agent_runtime_scheduled_wecom_dispatch_version_readback.sql",
        "227_46_agent_runtime_scheduled_wecom_dispatch_payload.sql",
        "227_49_agent_runtime_scheduled_wecom_unicode_payload.sql",
        MIGRATION,
    ):
        _apply(url, migration)
    _set_user_target(url, "app")
    _apply_completed(
        url, {"type": "wecom_user", "wecom_userid": "runtime-user"}, summary=summary,
    )
    claim = _claim(url)
    item = _item(url, claim["intent_id"])
    initial_payload = _payload(url, claim, item)
    assert initial_payload["outcome"] == "payload"
    prepared = _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        _prepare_params(claim, item, _identity()),
    )
    _owner(
        url, "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE intent_id=%s RETURNING intent_id", (claim["intent_id"],),
    )
    recovery = _rpc(
        url, "recover_agent_runtime_scheduled_wecom_prepared_dispatch_v1",
        (str(uuid4()), "prepared-payload-worker", 60),
    )
    assert recovery["outcome"] == "recovered"
    return claim, item, prepared, recovery, initial_payload


def _params(recovery: dict, **overrides: object) -> tuple:
    values = {
        "recovery_request_id": recovery["claim_request_id"],
        "intent_id": recovery["intent_id"], "item_id": recovery["item_id"],
        "attempt_id": recovery["attempt_id"],
        "attempt_number": recovery["attempt_number"],
        "claim_request_id": recovery["claim_request_id"],
        "lease_token": recovery["lease_token"], "worker_id": recovery["worker_id"],
        "delivery_version": recovery["delivery_state_version"],
        "item_version": recovery["item_state_version"],
        "provider_request_id": recovery["provider_request_id"],
        "idempotency_key": recovery["idempotency_key"],
        "provider_revision": recovery["provider_revision"],
    }
    values.update(overrides)
    return tuple(values[key] for key in (
        "recovery_request_id", "intent_id", "item_id", "attempt_id", "attempt_number",
        "claim_request_id", "lease_token", "worker_id", "delivery_version", "item_version",
        "provider_request_id", "idempotency_key", "provider_revision",
    ))


def _prepared_payload(url: str, recovery: dict, **overrides: object) -> dict:
    return _rpc(url, FUNCTION, _params(recovery, **overrides))


def test_prepared_recovery_readback_is_unicode_safe_read_only_and_normal_read_fences(
    database: str,
) -> None:
    claim, item, prepared, recovery, initial_payload = _setup(database)
    before = _state(database, claim["intent_id"])

    result = _prepared_payload(database, recovery)
    repeat = _prepared_payload(database, recovery)

    assert result == repeat == initial_payload and set(result) == SUCCESS_KEYS
    assert result["payload_revision"] == 2
    assert result["text"] == "prepared recovery 中文摘要"
    assert result["provider_revision"] == recovery["provider_revision"]
    prepared_versions = _owner(
        database, "SELECT jsonb_build_object('delivery',prepared_delivery_state_version,"
        "'item',prepared_item_state_version) FROM "
        "agent_runtime_scheduled_wecom_dispatch_attempts WHERE id=%s",
        (recovery["attempt_id"],),
    )
    assert result["delivery_state_version"] == prepared_versions["delivery"]
    assert result["item_state_version"] == prepared_versions["item"]
    assert prepared_versions["item"] == 0
    assert recovery["item_state_version"] >= 1
    assert result["delivery_state_version"] != recovery["delivery_state_version"]
    assert result["item_state_version"] != recovery["item_state_version"]
    assert _state(database, claim["intent_id"]) == before
    recovered_claim = {
        **claim, "claim_request_id": recovery["claim_request_id"],
        "lease_token": recovery["lease_token"], "worker_id": recovery["worker_id"],
        "state_version": recovery["delivery_state_version"],
    }
    recovered_item = {**item, "version": recovery["item_state_version"]}
    assert _payload(database, recovered_claim, recovered_item) == {"outcome": "fenced"}
    started = _rpc(
        database, "start_agent_runtime_scheduled_wecom_dispatch_v2",
        (
            recovery["intent_id"], recovery["item_id"], recovery["attempt_id"],
            recovery["claim_request_id"], recovery["lease_token"], recovery["worker_id"],
            recovery["delivery_state_version"], recovery["item_state_version"],
            prepared["provider_request_id"], prepared["idempotency_key"],
            prepared["provider_revision"],
        ),
    )
    assert started["outcome"] == "dispatch_started"


def test_every_wrong_recovery_fence_or_provider_identity_discloses_no_payload(database: str) -> None:
    _, _, _, recovery, _ = _setup(database)
    wrong = {
        "recovery_request_id": str(uuid4()), "intent_id": str(uuid4()),
        "item_id": str(uuid4()), "attempt_id": str(uuid4()), "attempt_number": 99,
        "claim_request_id": str(uuid4()), "lease_token": str(uuid4()),
        "worker_id": "wrong-worker", "delivery_version": recovery["delivery_state_version"] + 1,
        "item_version": recovery["item_state_version"] + 1,
        "provider_request_id": "wrong-provider-request",
        "idempotency_key": "f" * 64, "provider_revision": recovery["provider_revision"] + 1,
    }
    for field, value in wrong.items():
        result = _prepared_payload(database, recovery, **{field: value})
        assert result["outcome"] in {"not_found", "fenced"}
        assert set(result) == {"outcome"}


def test_target_and_tenant_drift_fail_closed_without_payload(database: str) -> None:
    _, _, _, recovery, _ = _setup(database)
    _owner(
        database, "UPDATE wecom_user_mappings SET wecom_userid='drifted-user' "
        "WHERE org_id=(SELECT org_id FROM agent_runtime_scheduled_wecom_deliveries "
        "WHERE intent_id=%s) RETURNING id", (recovery["intent_id"],),
    )
    target_drift = _prepared_payload(database, recovery)
    assert target_drift["outcome"] == "unavailable" and "text" not in target_drift

    _owner(
        database, "UPDATE wecom_user_mappings SET wecom_userid='runtime-user' "
        "WHERE wecom_userid='drifted-user' RETURNING id",
    )
    _owner(
        database, "UPDATE organizations SET status='suspended' WHERE id=(SELECT org_id FROM "
        "agent_runtime_scheduled_wecom_deliveries WHERE intent_id=%s) RETURNING id",
        (recovery["intent_id"],),
    )
    tenant_drift = _prepared_payload(database, recovery)
    assert tenant_drift["outcome"] == "unavailable" and "text" not in tenant_drift


def test_acl_search_path_no_direct_table_rights_and_rollback_reapply(database: str) -> None:
    _, _, _, recovery, _ = _setup(database)
    for signature, volatility in ((SIGNATURE, "v"), (HELPER_SIGNATURE, "s")):
        assert _owner(
            database, "SELECT prosecdef AND provolatile=%s AND "
            "proconfig=ARRAY['search_path=pg_catalog, public'] FROM pg_proc "
            "WHERE oid=%s::regprocedure", (volatility, signature),
        ) is True
    assert _owner(
        database, "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    assert _owner(
        database, "SELECT NOT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (HELPER_SIGNATURE,),
    ) is True
    for role in ("everydayai_runtime", "everydayai_worker", "everydayai"):
        assert _owner(
            database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')",
            (role, SIGNATURE),
        ) is True
    assert _owner(
        database, "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
        "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
        "WHERE p.oid=%s::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
        (SIGNATURE,),
    ) is True
    for table in (
        "agent_runtime_scheduled_wecom_prepared_recovery_requests",
        "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_delivery_items",
        "agent_runtime_scheduled_wecom_dispatch_attempts",
    ):
        assert _owner(
            database, "SELECT NOT has_table_privilege('everydayai_wecom_runtime',%s,'SELECT')",
            (table,),
        ) is True

    expected = deepcopy(_prepared_payload(database, recovery))
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (SIGNATURE,)) is None
    assert _owner(
        database, "SELECT to_regprocedure(%s)",
        ("read_agent_runtime_scheduled_wecom_dispatch_payload_v1(uuid,uuid,uuid,uuid,text,bigint,bigint)",),
    ) is not None
    _apply(database, MIGRATION)
    assert _prepared_payload(database, recovery) == expected
