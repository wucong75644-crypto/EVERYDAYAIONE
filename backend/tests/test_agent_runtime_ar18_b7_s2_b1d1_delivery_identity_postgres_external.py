from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _install_final_result,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external import (
    _claim,
    _link_artifact,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b1_context_postgres_external import _apply_v2
from tests.test_agent_runtime_ar18_b7_s2_b1d1_delivery_postgres_external import (
    _bound_run,
    _projection_read,
    _seed_wecom_targets,
    _setup,
)


pytestmark = pytest.mark.external


@pytest.mark.parametrize(
    ("target", "mutation"),
    (
        ({"type": "web", "user_id": USER},
         "UPDATE org_members SET status='disabled' WHERE org_id=%s AND user_id=%s"),
        ({"type": "wecom_user", "wecom_userid": "runtime-user"},
         "UPDATE wecom_user_mappings SET channel='oauth' WHERE org_id=%s AND user_id=%s"),
        ({"type": "wecom_user", "wecom_userid": "runtime-user"},
         "DELETE FROM wecom_user_mappings WHERE org_id=%s AND user_id=%s"),
        ({"type": "wecom_user", "wecom_userid": "runtime-user"},
         "UPDATE wecom_user_mappings SET corp_id='rebound-corp' WHERE org_id=%s AND user_id=%s"),
        ({"type": "wecom_group", "chatid": "runtime-group"},
         "UPDATE wecom_chat_targets SET is_active=false WHERE org_id=%s AND %s::uuid IS NOT NULL"),
        ({"type": "wecom_group", "chatid": "runtime-group"},
         "UPDATE wecom_chat_targets SET corp_id='other-corp' WHERE org_id=%s AND %s::uuid IS NOT NULL"),
    ),
)
def test_projection_readback_fences_revoked_or_rebound_target(
    database: str, target: dict, mutation: str,
) -> None:
    _setup(database)
    _seed_wecom_targets(database)
    facts = _bound_run(database, target)
    assert _projection_read(database, facts)["outcome"] == "found"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(mutation, (ORG, USER))
        conn.commit()
    read = _projection_read(database, facts)
    assert read["outcome"] == "unavailable"
    assert read["reason_code"] in {
        "delivery_member_unavailable", "delivery_target_unavailable",
    }
    assert "targets" not in read and "intents" not in read


def test_wecom_mapping_user_rebind_is_unavailable(database: str) -> None:
    _setup(database)
    _seed_wecom_targets(database)
    facts = _bound_run(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    replacement = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO users(id) VALUES(%s)", (replacement,))
        conn.execute(
            "INSERT INTO org_members(org_id,user_id,status) VALUES(%s,%s,'active')",
            (ORG, replacement),
        )
        conn.execute(
            "UPDATE wecom_user_mappings SET user_id=%s WHERE org_id=%s AND wecom_userid='runtime-user'",
            (replacement, ORG),
        )
        conn.commit()
    assert _projection_read(database, facts)["outcome"] == "unavailable"


@pytest.mark.parametrize(
    "target",
    (
        {"type": "multi", "targets": [
            {"type": "multi", "targets": [{"type": "web", "user_id": USER}]},
        ]},
        {"type": "multi", "targets": [
            {"type": "web", "user_id": USER} for _ in range(21)
        ]},
        {"type": "multi", "targets": [
            {"type": "web", "user_id": USER}, {"type": "web", "user_id": USER},
        ]},
    ),
)
def test_multi_rejects_nested_overflow_and_duplicates(database: str, target: dict) -> None:
    _setup(database)
    with pytest.raises(Exception, match="DELIVERY_(NESTED_MULTI_DENIED|TARGET_DENIED|TARGET_DUPLICATE)"):
        _bound_run(database, target)


def _add_second_artifact(url: str, facts: dict) -> str:
    artifact_id = str(uuid4())
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        action_id, attempt_id, conversation_id = conn.execute(
            "SELECT a.id,l.attempt_id,s.conversation_id FROM agent_actions a "
            "JOIN agent_action_artifact_links l ON l.action_id=a.id "
            "JOIN agent_runtime_sessions s ON s.id=a.session_id WHERE a.run_id=%s LIMIT 1",
            (facts["run_id"],),
        ).fetchone()
        conn.execute(
            "INSERT INTO conversation_artifacts(id,conversation_id,org_id,content_hash,storage_ref,"
            "inline_content,sensitivity) VALUES(%s,%s,%s,%s,%s,%s,'restricted')",
            (artifact_id, conversation_id, ORG, "e" * 64,
             Jsonb({"url": "https://secret.invalid", "path": "/private/result"}),
             Jsonb({"secret": "never-deliver"})),
        )
        conn.execute(
            "INSERT INTO agent_action_artifact_links(action_id,attempt_id,artifact_id,role,"
            "content_hash,materialize_revision,materialize_status,sensitivity) "
            "VALUES(%s,%s,%s,'materialized',%s,2,'partial','restricted')",
            (action_id, attempt_id, artifact_id, "e" * 64),
        )
        conn.commit()
    return artifact_id


def test_completed_content_freezes_ordered_safe_artifact_manifest(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    result_hash = _install_final_result(database, facts)
    first = _link_artifact(database, facts)
    second_id = _add_second_artifact(database, facts)
    assert _rpc(database, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    claim = _claim(database)
    applied = _apply_v2(
        database, claim, request_id=str(uuid4()),
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert applied["outcome"] == "applied"
    before = _projection_read(database, facts)
    manifest = before["content"]["artifact_manifest"]
    assert [item["artifact_id"] for item in manifest] == [first["artifact_id"], second_id]
    assert manifest[0]["materialize_revision"] == 1
    assert manifest[1]["materialize_revision"] == 2
    assert manifest[1]["materialize_status"] == "partial"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET last_result=%s WHERE id=%s",
            (Jsonb({"secret": "mutable-last-result"}), facts["task_id"]),
        )
        conn.commit()
    assert _projection_read(database, facts) == before
    serialized = json.dumps(before, sort_keys=True).lower()
    for forbidden in ("never-deliver", "secret.invalid", "/private/", "storage_ref"):
        assert forbidden not in serialized


def test_artifact_identity_mismatch_blocks_atomic_intent(database: str) -> None:
    _setup(database)
    facts = _bound_run(database)
    result_hash = _install_final_result(database, facts)
    _link_artifact(database, facts, invalid="hash_mismatch")
    assert _rpc(database, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    claim = _claim(database)
    with pytest.raises(Exception, match="ARTIFACT_FENCED|CONTENT_FENCED"):
        _apply_v2(
            database, claim, request_id=str(uuid4()),
            next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    assert _projection_read(database, facts)["content"] is None
