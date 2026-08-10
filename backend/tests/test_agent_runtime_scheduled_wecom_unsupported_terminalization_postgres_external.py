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
    _apply_terminal,
    _item,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_prepare_postgres_external import (
    _identity,
    _prepare_params,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_version_readback_postgres_external import (
    _start_params,
)


pytestmark = pytest.mark.external
MIGRATION = "227_47_agent_runtime_scheduled_wecom_unsupported_terminalization.sql"
ROLLBACK = "227_47_agent_runtime_scheduled_wecom_unsupported_terminalization_rollback.sql"
FUNCTION = "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1"
SIGNATURE = f"{FUNCTION}(uuid,uuid,uuid,uuid,uuid,text,bigint,bigint)"
LEDGER = "agent_runtime_scheduled_wecom_unsupported_requests"
RESPONSE_KEYS = {
    "outcome", "request_id", "intent_id", "item_id", "reason_code", "item_status",
    "delivery_status", "delivery_state_version", "item_state_version", "terminalized_at",
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
    ):
        _apply(url, migration)
    _apply(url, MIGRATION)


def _params(claim: dict, *, request_id: str | None = None, item_id: str | None = None,
            **overrides: object) -> tuple[object, ...]:
    values = {
        "request_id": request_id or str(uuid4()), "intent_id": claim["intent_id"],
        "item_id": item_id or claim["item_id"], "claim_request_id": claim["claim_request_id"],
        "lease_token": claim["lease_token"], "worker_id": claim["worker_id"],
        "delivery_version": claim["delivery_state_version"],
        "item_version": claim["item_state_version"],
    }
    values.update(overrides)
    return (
        values["request_id"], values["intent_id"], values["item_id"],
        values["claim_request_id"], values["lease_token"], values["worker_id"],
        values["delivery_version"], values["item_version"],
    )


def _terminalize(url: str, params: tuple[object, ...]) -> dict:
    return _rpc(url, FUNCTION, params)


def _claim(url: str, worker: str = "unsupported-worker") -> dict:
    result = _continuation(url, worker=worker)
    assert result["outcome"] == "claimed"
    return result


def _dispatch(url: str, claim: dict, outcome: str) -> dict:
    item = {"id": claim["item_id"], "version": claim["item_state_version"]}
    prepared = _rpc(
        url, "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
        _prepare_params(claim, item, _identity()),
    )
    started = _rpc(
        url, "start_agent_runtime_scheduled_wecom_dispatch_v2",
        _start_params(claim, prepared),
    )
    return _rpc(
        url, "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1",
        _outcome_params(url, claim, started, outcome),
    )


def _completed_with_artifact(url: str) -> None:
    _set_user_target(url, "app")
    _apply_completed(
        url, {"type": "wecom_user", "wecom_userid": "runtime-user"}, artifact=True,
    )


def _expire_claim(url: str, intent_id: str) -> None:
    _owner(
        url, "UPDATE agent_runtime_scheduled_wecom_deliveries SET "
        "lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (intent_id,),
    )


def _state(url: str, intent_id: str) -> dict:
    return _owner(
        url,
        "SELECT jsonb_build_object('delivery',to_jsonb(d),'items',(SELECT jsonb_agg(to_jsonb(item) "
        "ORDER BY item.ordinal) FROM agent_runtime_scheduled_wecom_delivery_items item WHERE "
        "item.intent_id=d.intent_id),'attempts',(SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.id),"
        "'[]'::jsonb) FROM agent_runtime_scheduled_wecom_dispatch_attempts a JOIN "
        "agent_runtime_scheduled_wecom_delivery_items ai ON ai.id=a.item_id WHERE "
        "ai.intent_id=d.intent_id)) FROM agent_runtime_scheduled_wecom_deliveries d WHERE d.intent_id=%s",
        (intent_id,),
    )


@pytest.mark.parametrize(
    ("terminal", "reason"),
    (("failed", "wecom_failed_content_unsupported"),
     ("cancelled", "wecom_cancelled_content_unsupported")),
)
def test_non_completed_text_terminalizes_once_and_replays(
    database: str, terminal: str, reason: str,
) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_terminal(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"}, terminal,
    )
    claim = _claim(database)
    params = _params(claim)
    before_attempts = _owner(database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts")
    result = _terminalize(database, params)
    replay = _terminalize(database, params)
    assert set(result) == RESPONSE_KEYS and set(replay) == RESPONSE_KEYS
    assert result["outcome"] == "terminalized" and replay["outcome"] == "readback"
    assert result["reason_code"] == reason and result["item_status"] == "cancelled"
    assert result["delivery_status"] == "failed"
    assert replay["terminalized_at"] == result["terminalized_at"]
    assert _owner(database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts") == before_attempts == 0
    conflict = list(params)
    conflict[5] = "different-worker"
    with pytest.raises(Exception, match="UNSUPPORTED_REQUEST_CONFLICT"):
        _terminalize(database, tuple(conflict))


def test_accepted_text_then_artifact_terminalizes_partial_without_attempt(database: str) -> None:
    _setup(database)
    _completed_with_artifact(database)
    text_claim = _claim(database)
    accepted = _dispatch(database, text_claim, "accepted")
    attempt_before = _owner(
        database, "SELECT to_jsonb(a) FROM agent_runtime_scheduled_wecom_dispatch_attempts a",
    )
    _expire_claim(database, text_claim["intent_id"])
    artifact_claim = _claim(database)
    assert artifact_claim["item_id"] == _item(database, text_claim["intent_id"], 2)["id"]
    result = _terminalize(database, _params(artifact_claim))
    assert result["reason_code"] == "wecom_artifact_identity_unsupported"
    assert result["delivery_status"] == "partial" and result["item_status"] == "cancelled"
    assert accepted["item_status"] == "accepted"
    assert _owner(
        database, "SELECT to_jsonb(a) FROM agent_runtime_scheduled_wecom_dispatch_attempts a",
    ) == attempt_before


def test_multiple_artifacts_drain_by_immediate_fresh_claims_without_loop(database: str) -> None:
    _setup(database)
    _completed_with_artifact(database)
    intent_id = str(_owner(database, "SELECT intent_id FROM agent_runtime_scheduled_wecom_deliveries"))
    _owner(
        database,
        "INSERT INTO agent_runtime_scheduled_wecom_delivery_items(intent_id,item_key,ordinal,item_kind,"
        "source_role,source_id,source_revision,source_identity_hash,content_identity_hash) SELECT intent_id,"
        "%s,3,'artifact_identity','output',%s,1,%s,content_identity_hash FROM "
        "agent_runtime_scheduled_wecom_delivery_items WHERE intent_id=%s AND ordinal=2 RETURNING id",
        (uuid4().hex + uuid4().hex, str(uuid4()), "e" * 64, intent_id),
    )
    text_claim = _claim(database)
    _dispatch(database, text_claim, "accepted")
    _expire_claim(database, intent_id)
    first = _claim(database)
    first_result = _terminalize(database, _params(first))
    assert first_result["delivery_status"] == "pending"
    second = _claim(database)
    assert second["item_id"] != first["item_id"] and second["claim_kind"] == "continuation"
    second_result = _terminalize(database, _params(second))
    assert second_result["delivery_status"] == "partial"
    assert _continuation(database)["outcome"] == "empty"
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 2
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_dispatch_attempts a JOIN "
        "agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "WHERE item.intent_id=%s AND item.item_kind='artifact_identity'", (intent_id,),
    ) == 0


def test_supported_text_and_claim_identity_drift_are_fenced(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    facts = _apply_completed(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _claim(database)
    before = _state(database, claim["intent_id"])
    for override in (
        {"lease_token": str(uuid4())}, {"worker_id": "wrong-worker"},
        {"delivery_version": 999}, {"item_version": 999},
    ):
        assert _terminalize(database, _params(claim, **override)) == {"outcome": "fenced"}
    assert _terminalize(database, _params(claim, item_id=str(uuid4()))) == {"outcome": "not_found"}
    assert _terminalize(database, _params(claim)) == {"outcome": "fenced"}
    other_org = str(uuid4())
    _owner(database, "INSERT INTO organizations(id) VALUES(%s) RETURNING id", (other_org,))
    _owner(
        database, "UPDATE scheduled_task_runs SET org_id=%s WHERE id=%s RETURNING id",
        (other_org, facts["scheduled_run_id"]),
    )
    assert _terminalize(database, _params(claim)) == {"outcome": "fenced"}
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 0
    assert before["attempts"] == []


@pytest.mark.parametrize("outcome", ("accepted", "rejected", "unknown"))
def test_existing_attempt_outcomes_are_never_changed(database: str, outcome: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _claim(database)
    _dispatch(database, claim, outcome)
    before = _state(database, claim["intent_id"])
    assert _terminalize(database, _params(claim)) == {"outcome": "fenced"}
    assert _state(database, claim["intent_id"]) == before
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 0


def test_fifty_same_request_calls_make_one_fact_and_stable_readbacks(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_terminal(database, {"type": "wecom_user", "wecom_userid": "runtime-user"}, "failed")
    claim = _claim(database)
    params = _params(claim)
    barrier = Barrier(50)

    def invoke(_: int) -> str:
        barrier.wait()
        return _terminalize(database, params)["outcome"]

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(invoke, range(50)))
    assert outcomes.count("terminalized") == 1 and outcomes.count("readback") == 49
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 1


def test_fifty_different_requests_on_one_item_have_one_winner(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_terminal(database, {"type": "wecom_user", "wecom_userid": "runtime-user"}, "failed")
    claim = _claim(database)
    barrier = Barrier(50)

    def invoke(_: int) -> str:
        barrier.wait()
        return _terminalize(database, _params(claim))["outcome"]

    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(invoke, range(50)))
    assert outcomes.count("terminalized") == 1 and outcomes.count("fenced") == 49
    assert _owner(database, f"SELECT count(*) FROM {LEDGER}") == 1


def test_acl_rls_immutability_and_controlled_rollback_cycle(database: str) -> None:
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
    for role in ("PUBLIC", "everydayai_runtime", "everydayai_worker", "everydayai_agent_runtime_worker"):
        if role == "PUBLIC":
            assert _owner(
                database, "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE "
                "p.oid=%s::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')", (SIGNATURE,),
            ) is True
        else:
            assert _owner(database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')", (role, SIGNATURE))
    for role in (
        "everydayai_wecom_runtime", "everydayai_runtime", "everydayai_worker",
        "everydayai_agent_runtime_worker",
    ):
        assert _owner(
            database, "SELECT NOT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE')",
            (role, LEDGER),
        )
    with pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        _rpc(database, FUNCTION, (None,) * 8, access_kind="legacy")

    _set_user_target(database, "app")
    _apply_terminal(database, {"type": "wecom_user", "wecom_userid": "runtime-user"}, "failed")
    result = _terminalize(database, _params(_claim(database)))
    with pytest.raises(Exception, match="UNSUPPORTED_IMMUTABLE"):
        _owner(database, f"UPDATE {LEDGER} SET worker_id='changed' WHERE request_id=%s RETURNING request_id",
               (result["request_id"],))
    with pytest.raises(Exception, match="UNSUPPORTED_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(f"ALTER TABLE {LEDGER} DISABLE TRIGGER runtime_scheduled_wecom_unsupported_immutable")
        conn.execute(f"DELETE FROM {LEDGER}")
        conn.commit()
    with pytest.raises(Exception, match="UNSUPPORTED_ROLLBACK_HAS_FACTS"):
        _rollback(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='pending',"
                     "terminal_reason_code=NULL WHERE id=%s", (result["item_id"],))
        conn.commit()
    _rollback(database, ROLLBACK)
    assert _owner(database, f"SELECT to_regclass('{LEDGER}')") is None
    _apply(database, MIGRATION)
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (SIGNATURE,)) is None
