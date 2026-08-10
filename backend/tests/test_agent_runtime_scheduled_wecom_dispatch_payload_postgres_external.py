from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import (
    _rpc as _runtime_rpc,
)
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _install_final_result,
    _request_rpc,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external import (
    _claim as _finalization_claim,
    _link_artifact,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b1_context_postgres_external import _apply_v2
from tests.test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external import _bound_run
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _claim as _delivery_claim,
    _owner,
    _rpc,
    _set_user_target,
    _setup as _claim_setup,
)


pytestmark = pytest.mark.external
MIGRATION = "227_46_agent_runtime_scheduled_wecom_dispatch_payload.sql"
ROLLBACK = "227_46_agent_runtime_scheduled_wecom_dispatch_payload_rollback.sql"
FUNCTION = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"
SIGNATURE = f"{FUNCTION}(uuid,uuid,uuid,uuid,text,bigint,bigint)"
SUCCESS_KEYS = {
    "outcome", "payload_revision", "scheduled_run_id", "intent_id", "item_id",
    "item_key", "ordinal", "item_kind", "source_role", "source_revision",
    "source_identity_hash", "content_identity_hash", "result_hash", "target_hash",
    "channel", "target", "provider_revision", "delivery_state_version",
    "item_state_version", "message_type", "text", "payload_hash",
}


def _setup(url: str) -> None:
    _claim_setup(url)
    _apply(url, MIGRATION)


def _hash(url: str, value: str) -> str:
    return str(_owner(
        url, "SELECT encode(digest(convert_to(%s,'UTF8'),'sha256'),'hex')", (value,),
    ))


def _apply_completed(
    url: str, target: dict, *, output_kind: str = "text",
    summary: str = "scheduled result", artifact: bool = False,
) -> dict:
    facts = _bound_run(url, target)
    _install_final_result(url, facts)
    result_hash = _hash(url, f"{output_kind}:{summary}")
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        if output_kind == "text":
            conn.execute(
                "UPDATE agent_model_results SET output_kind='text',text_content=%s,"
                "structured_content=NULL,schema_revision=NULL,content_hash=%s WHERE run_id=%s",
                (summary, result_hash, facts["run_id"]),
            )
        else:
            conn.execute(
                "UPDATE agent_model_results SET output_kind='structured',text_content=NULL,"
                "structured_content=%s,schema_revision='v1',content_hash=%s WHERE run_id=%s",
                (Jsonb({"summary": summary}), result_hash, facts["run_id"]),
            )
        conn.commit()
    if artifact:
        _link_artifact(url, facts)
    assert _runtime_rpc(url, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    assert _apply_v2(
        url, _finalization_claim(url),
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )["outcome"] == "applied"
    return facts


def _apply_terminal(url: str, target: dict, terminal: str) -> None:
    facts = _bound_run(url, target)
    if terminal == "failed":
        result = _runtime_rpc(url, "fail_agent_run", (
            facts["run_id"], facts["token"], facts["version"], "provider_error",
        ))
    else:
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "UPDATE agent_runtime_sessions SET scope_kind='user',scope_id=%s WHERE id=("
                "SELECT session_id FROM agent_runs WHERE id=%s)",
                (USER, facts["run_id"]),
            )
            conn.commit()
        result = _request_rpc(url, "test_b1a_cancel_agent_run", (
            facts["run_id"], facts["version"], "runtime_cancel",
        ))
    assert result["outcome"] == terminal
    assert _apply_v2(
        url, _finalization_claim(url),
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )["outcome"] == "applied"


def _item(url: str, intent_id: str, ordinal: int = 1) -> dict:
    return dict(_owner(
        url,
        "SELECT jsonb_build_object('id',id,'version',state_version) FROM "
        "agent_runtime_scheduled_wecom_delivery_items WHERE intent_id=%s AND ordinal=%s",
        (intent_id, ordinal),
    ))


def _payload(url: str, claim: dict, item: dict, **overrides: object) -> dict:
    values = {
        "intent_id": claim["intent_id"], "item_id": item["id"],
        "claim_request_id": claim["claim_request_id"],
        "lease_token": claim["lease_token"], "worker_id": claim["worker_id"],
        "delivery_version": claim["state_version"], "item_version": item["version"],
    }
    values.update(overrides)
    return _rpc(url, FUNCTION, (
        values["intent_id"], values["item_id"], values["claim_request_id"],
        values["lease_token"], values["worker_id"], values["delivery_version"],
        values["item_version"],
    ))


def _state(url: str, intent_id: str) -> object:
    return _owner(
        url,
        "SELECT jsonb_build_object('delivery',to_jsonb(d),'items',(SELECT jsonb_agg("
        "to_jsonb(item) ORDER BY item.ordinal) FROM agent_runtime_scheduled_wecom_delivery_items "
        "item WHERE item.intent_id=d.intent_id),'attempts',(SELECT count(*) FROM "
        "agent_runtime_scheduled_wecom_dispatch_attempts a JOIN "
        "agent_runtime_scheduled_wecom_delivery_items item ON item.id=a.item_id "
        "WHERE item.intent_id=d.intent_id)) FROM agent_runtime_scheduled_wecom_deliveries d "
        "WHERE d.intent_id=%s", (intent_id,),
    )


@pytest.mark.parametrize("channel", ("app", "smart_robot"))
def test_completed_text_returns_exact_safe_transport_payload_without_mutation(
    database: str, channel: str,
) -> None:
    _setup(database)
    _set_user_target(database, channel)
    _apply_completed(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _delivery_claim(database)
    item = _item(database, claim["intent_id"])
    before = _state(database, claim["intent_id"])
    result = _payload(database, claim, item)
    assert _state(database, claim["intent_id"]) == before
    assert set(result) == SUCCESS_KEYS
    assert result["outcome"] == "payload" and result["text"] == "scheduled result"
    assert result["message_type"] == "text" and len(result["payload_hash"]) == 64
    assert result["delivery_state_version"] == claim["state_version"]
    assert result["item_state_version"] == item["version"]
    if channel == "app":
        assert set(result["target"]) == {"corp_id", "wecom_userid"}
        assert result["target"]["wecom_userid"] == "runtime-user"
    else:
        assert result["target"] == {"chatid": "runtime-chat"}
    serialized = json.dumps(result, sort_keys=True).lower()
    for forbidden in (
        "mapping_id", "target_id", "user_id", "secret", "token", "password",
        "credential", "storage_ref", "object_path", "http://", "https://", "/private/",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("output_kind", "source", "expected"),
    (
        ("text", "Authorization: Bearer private-value", "Runtime scheduled task completed"),
        ("structured", "  structured\n\t summary  ", "structured summary"),
    ),
)
def test_completed_summary_uses_existing_sanitized_projection(
    database: str, output_kind: str, source: str, expected: str,
) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"},
        output_kind=output_kind, summary=source,
    )
    claim = _delivery_claim(database)
    result = _payload(database, claim, _item(database, claim["intent_id"]))
    assert result["text"] == expected
    assert "private-value" not in json.dumps(result)


def test_artifact_identity_is_fixed_unsupported_and_read_only(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"},
        artifact=True,
    )
    claim = _delivery_claim(database)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='accepted',"
        "state_version=state_version+1 WHERE intent_id=%s AND ordinal=1 RETURNING id",
        (claim["intent_id"],),
    )
    artifact = _item(database, claim["intent_id"], 2)
    before = _state(database, claim["intent_id"])
    assert _payload(database, claim, artifact) == {
        "outcome": "unsupported", "reason_code": "wecom_artifact_identity_unsupported",
    }
    assert _state(database, claim["intent_id"]) == before


@pytest.mark.parametrize(
    ("terminal", "reason"),
    (
        ("failed", "wecom_failed_content_unsupported"),
        ("cancelled", "wecom_cancelled_content_unsupported"),
    ),
)
def test_non_completed_content_is_fixed_unsupported_without_notification(
    database: str, terminal: str, reason: str,
) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_terminal(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"}, terminal,
    )
    claim = _delivery_claim(database)
    result = _payload(database, claim, _item(database, claim["intent_id"]))
    assert result == {"outcome": "unsupported", "reason_code": reason}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("lease_token", lambda: str(uuid4())),
        ("worker_id", lambda: "wrong-worker"),
        ("delivery_version", lambda: 999),
        ("item_version", lambda: 999),
    ),
)
def test_claim_and_version_drift_are_fenced(database: str, field: str, value) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _delivery_claim(database)
    item = _item(database, claim["intent_id"])
    assert _payload(database, claim, item, **{field: value()}) == {"outcome": "fenced"}
    assert _payload(database, claim, item, item_id=str(uuid4())) == {"outcome": "not_found"}


def test_null_parameter_matrix_is_stable_invalid(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _delivery_claim(database)
    item = _item(database, claim["intent_id"])
    params = [
        claim["intent_id"], item["id"], claim["claim_request_id"], claim["lease_token"],
        claim["worker_id"], claim["state_version"], item["version"],
    ]
    for index in range(len(params)):
        invalid = params.copy()
        invalid[index] = None
        with pytest.raises(Exception, match="AGENT_RUNTIME_SCHEDULED_WECOM_PAYLOAD_INVALID"):
            _rpc(database, FUNCTION, tuple(invalid))


def test_inherited_pre_attempt_unavailable_cancellation_is_narrow(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    claim = _delivery_claim(database)
    item = _item(database, claim["intent_id"])
    _set_user_target(database, "smart_robot")
    result = _payload(database, claim, item)
    assert result == {
        "outcome": "unavailable", "reason_code": "wecom_target_unavailable",
    }
    state = _state(database, claim["intent_id"])
    assert state["delivery"]["status"] == "unavailable"
    assert [row["status"] for row in state["items"]] == ["cancelled"]
    assert "target" not in result and "delivery_state_version" not in result


def test_existing_cross_intent_item_is_fenced(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _apply_completed(database, {"type": "multi", "targets": [
        {"type": "wecom_user", "wecom_userid": "runtime-user"},
        {"type": "wecom_group", "chatid": "runtime-group"},
    ]})
    claim = _delivery_claim(database)
    other = dict(_owner(
        database,
        "SELECT jsonb_build_object('id',id,'version',state_version) FROM "
        "agent_runtime_scheduled_wecom_delivery_items WHERE intent_id<>%s ORDER BY id LIMIT 1",
        (claim["intent_id"],),
    ))
    assert _payload(database, claim, other) == {"outcome": "fenced"}


@pytest.mark.parametrize("drift", ("tenant", "target"))
def test_post_attempt_tenant_and_target_drift_are_fenced(
    database: str, drift: str,
) -> None:
    _setup(database)
    _set_user_target(database, "app")
    facts = _apply_completed(
        database, {"type": "wecom_user", "wecom_userid": "runtime-user"},
    )
    claim = _delivery_claim(database)
    item = _item(database, claim["intent_id"])
    _owner(
        database,
        "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts(item_id,attempt_number,"
        "provider_request_id,idempotency_key,provider_revision,status,dispatch_phase) "
        "VALUES(%s,1,%s,%s,1,'prepared','prepared') RETURNING id",
        (item["id"], f"provider-{uuid4()}", uuid4().hex + uuid4().hex),
    )
    if drift == "target":
        _set_user_target(database, "smart_robot")
    else:
        other_org = str(uuid4())
        _owner(database, "INSERT INTO organizations(id) VALUES(%s) RETURNING id", (other_org,))
        _owner(
            database, "UPDATE scheduled_task_runs SET org_id=%s WHERE id=%s RETURNING id",
            (other_org, facts["scheduled_run_id"]),
        )
    before = _state(database, claim["intent_id"])
    assert _payload(database, claim, item) == {"outcome": "fenced"}
    assert _state(database, claim["intent_id"]) == before


def test_acl_search_path_and_exact_rollback_cycle(database: str) -> None:
    _setup(database)
    assert _owner(
        database,
        "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
        "FROM pg_proc WHERE oid=%s::regprocedure", (SIGNATURE,),
    ) is True
    assert _owner(
        database,
        "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    for role in (
        "PUBLIC", "everydayai", "everydayai_runtime", "everydayai_worker",
        "everydayai_agent_runtime_worker", "everydayai_projection_worker",
    ):
        if role == "PUBLIC":
            denied = _owner(
                database,
                "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
                "WHERE p.oid=%s::regprocedure AND acl.grantee=0 "
                "AND acl.privilege_type='EXECUTE')", (SIGNATURE,),
            )
        else:
            denied = _owner(
                database, "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')",
                (role, SIGNATURE),
            )
        assert denied is True
    for table in (
        "scheduled_task_runs", "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_delivery_items",
    ):
        assert _owner(
            database,
            "SELECT NOT has_table_privilege('everydayai_wecom_runtime',%s,'SELECT')",
            (table,),
        ) is True
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (SIGNATURE,)) is None
    _apply(database, MIGRATION)
    assert _owner(database, "SELECT to_regprocedure(%s) IS NOT NULL", (SIGNATURE,)) is True
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (SIGNATURE,)) is None
