import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _claim as _delivery_claim,
    _owner,
    _set_user_target,
)
from tests.test_agent_runtime_scheduled_wecom_dispatch_payload_postgres_external import (
    FUNCTION,
    SUCCESS_KEYS,
    _apply_completed,
    _item,
    _payload,
    _setup as _payload_setup,
    _state,
)


pytestmark = pytest.mark.external
MIGRATION = "227_49_agent_runtime_scheduled_wecom_unicode_payload.sql"
ROLLBACK = "227_49_agent_runtime_scheduled_wecom_unicode_payload_rollback.sql"
SIGNATURE = f"{FUNCTION}(uuid,uuid,uuid,uuid,text,bigint,bigint)"
HASH_FUNCTION = "_agent_runtime_scheduled_wecom_payload_hash_v2"
HASH_SIGNATURE = (
    f"{HASH_FUNCTION}(uuid,uuid,uuid,text,integer,bigint,text,text,text,text,uuid,text,"
    "jsonb,bigint,bigint,bigint,text,text)"
)


def _setup(url: str) -> None:
    _payload_setup(url)
    _apply(url, MIGRATION)


def _completed_payload(url: str, summary: str, channel: str = "app") -> tuple[dict, dict, dict]:
    _set_user_target(url, channel)
    _apply_completed(
        url, {"type": "wecom_user", "wecom_userid": "runtime-user"}, summary=summary,
    )
    claim = _delivery_claim(url)
    item = _item(url, claim["intent_id"])
    return claim, item, _payload(url, claim, item)


@pytest.mark.parametrize("channel", ["app", "smart_robot"])
@pytest.mark.parametrize("summary", ["任务已完成：这是安全的中文摘要。", "scheduled result"])
def test_v2_returns_utf8_and_ascii_safe_payload_deterministically(
    database: str, channel: str, summary: str,
) -> None:
    _setup(database)
    claim, item, first = _completed_payload(database, summary, channel)
    before = _state(database, claim["intent_id"])
    second = _payload(database, claim, item)

    assert first == second
    assert set(first) == SUCCESS_KEYS
    assert first["payload_revision"] == 2
    assert first["text"] == summary
    assert len(first["payload_hash"]) == 64
    assert _state(database, claim["intent_id"]) == before


def _hash_v2(url: str, *, text: str, target: dict, target_hash: str = "e" * 64) -> str:
    values = (
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444", "a" * 64, 1, 1, "b" * 64,
        "c" * 64, "d" * 64, target_hash,
        "22222222-2222-2222-2222-222222222222", "smart_robot", Jsonb(target),
        1, 3, 2, "text", text,
    )
    return str(_owner(
        url,
        "SELECT _agent_runtime_scheduled_wecom_payload_hash_v2(%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        values,
    ))


def test_v2_hash_accepts_unicode_opaque_target_and_binds_text_and_target(database: str) -> None:
    _setup(database)
    target = {
        "org_id": "22222222-2222-2222-2222-222222222222",
        "chatid": "群聊-甲",
    }
    first = _hash_v2(database, text="中文摘要", target=target)
    repeat = _hash_v2(database, text="中文摘要", target=target)
    changed_text = _hash_v2(database, text="另一段中文摘要", target=target)
    changed_target = _hash_v2(
        database, text="中文摘要", target={**target, "chatid": "群聊-乙"},
    )
    changed_frozen_target = _hash_v2(
        database, text="中文摘要", target=target, target_hash="f" * 64,
    )

    assert first == repeat
    assert len({first, changed_text, changed_target, changed_frozen_target}) == 4
    assert all(len(value) == 64 for value in {
        first, changed_text, changed_target, changed_frozen_target,
    })


def test_v2_preserves_summary_and_target_tamper_fences(database: str) -> None:
    _setup(database)
    claim, item, result = _completed_payload(database, "原始中文摘要")
    assert result["payload_revision"] == 2
    _owner(
        database,
        "UPDATE scheduled_task_runs SET result_summary='替换后的安全摘要' WHERE id=%s RETURNING id",
        (result["scheduled_run_id"],),
    )
    assert _payload(database, claim, item) == {
        "outcome": "unavailable", "reason_code": "wecom_safe_text_unavailable",
    }

    other_claim, other_item, other = _completed_payload(database, "第二条中文摘要")
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "ALTER TABLE agent_runtime_scheduled_wecom_deliveries DISABLE TRIGGER "
            "runtime_scheduled_wecom_delivery_identity_guard",
        )
        conn.execute(
            "UPDATE agent_runtime_scheduled_wecom_deliveries SET target_snapshot="
            "jsonb_set(target_snapshot,'{wecom_userid}',to_jsonb('tampered'::text)) "
            "WHERE intent_id=%s", (other_claim["intent_id"],),
        )
        conn.execute(
            "ALTER TABLE agent_runtime_scheduled_wecom_deliveries ENABLE TRIGGER "
            "runtime_scheduled_wecom_delivery_identity_guard",
        )
        conn.commit()
    assert _payload(database, other_claim, other_item)["outcome"] in {"fenced", "unavailable"}
    assert other["payload_revision"] == 2


def test_acl_and_rollback_restore_revision_one_then_reapply(database: str) -> None:
    _setup(database)
    claim, item, result = _completed_payload(database, "scheduled result")
    assert result["payload_revision"] == 2
    for signature in (SIGNATURE, HASH_SIGNATURE):
        assert _owner(
            database,
            "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
            "FROM pg_proc WHERE oid=%s::regprocedure", (signature,),
        ) is True
    assert _owner(
        database,
        "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    assert _owner(
        database,
        "SELECT NOT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (HASH_SIGNATURE,),
    ) is True
    for table in (
        "scheduled_task_runs", "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_delivery_items",
    ):
        assert _owner(
            database,
            "SELECT NOT has_table_privilege('everydayai_wecom_runtime',%s,'SELECT')", (table,),
        ) is True

    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (HASH_SIGNATURE,)) is None
    assert _payload(database, claim, item)["payload_revision"] == 1
    _apply(database, MIGRATION)
    assert _payload(database, claim, item)["payload_revision"] == 2
    _rollback(database, ROLLBACK)
    assert _payload(database, claim, item)["payload_revision"] == 1
