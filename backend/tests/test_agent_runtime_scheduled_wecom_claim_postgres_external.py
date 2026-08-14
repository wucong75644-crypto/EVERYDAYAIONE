from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_b1d2a_wecom_foundation_postgres_external import (
    _finalize,
    _setup as _foundation_setup,
)


pytestmark = pytest.mark.external
MIGRATION = "227_38_agent_runtime_scheduled_wecom_claim.sql"
ROLLBACK = "227_38_agent_runtime_scheduled_wecom_claim_rollback.sql"
PUBLIC_SIGNATURES = (
    "claim_agent_runtime_scheduled_wecom_delivery_v1(uuid,text,integer)",
    "renew_agent_runtime_scheduled_wecom_delivery_lease_v1(uuid,uuid,uuid,text,bigint,integer)",
    "read_agent_runtime_scheduled_wecom_claim_v1(uuid)",
    "read_agent_runtime_scheduled_wecom_dispatch_context_v1(uuid,uuid,uuid,text,bigint)",
)


def _setup(url: str) -> None:
    _foundation_setup(url)
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "ALTER TABLE wecom_user_mappings ADD COLUMN last_chatid VARCHAR(128),"
            "ADD COLUMN last_chat_type VARCHAR(20) DEFAULT 'single'",
        )
        conn.commit()
    _apply(url, MIGRATION)


def _owner(url: str, sql: str, params: tuple = ()) -> object:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        row = conn.execute(sql, params).fetchone()
        conn.commit()
        return row[0] if row else None


def _rpc(url: str, name: str, params: tuple, *, role: str = "everydayai_wecom_runtime",
         access_kind: str = "worker") -> dict:
    role_url = url.replace("postgres@", f"{role}@")
    with psycopg.connect(role_url) as conn:
        conn.execute("SELECT set_config('app.access_kind',%s,false)", (access_kind,))
        value = conn.execute(
            f"SELECT {name}({','.join(['%s'] * len(params))})", params,
        ).fetchone()[0]
        conn.commit()
        return value


def _set_user_target(url: str, channel: str) -> None:
    _owner(
        url,
        "UPDATE wecom_user_mappings SET channel=%s,last_chatid=%s,last_chat_type='single' "
        "WHERE org_id=%s AND wecom_userid='runtime-user' RETURNING id",
        (channel, "runtime-chat" if channel == "smart_robot" else None, ORG),
    )


def _claim(url: str, worker: str = "wecom-worker", request_id: str | None = None) -> dict:
    return _rpc(
        url,
        "claim_agent_runtime_scheduled_wecom_delivery_v1",
        (request_id or str(uuid4()), worker, 60),
    )


def _context(url: str, claim: dict) -> dict:
    return _rpc(
        url,
        "read_agent_runtime_scheduled_wecom_dispatch_context_v1",
        (
            claim["intent_id"], claim["claim_request_id"], claim["lease_token"],
            claim["worker_id"], claim["state_version"],
        ),
    )


@pytest.mark.parametrize(
    ("target", "channel", "expected_address"),
    (
        ({"type": "wecom_user", "wecom_userid": "runtime-user"}, "app", "runtime-user"),
        ({"type": "wecom_user", "wecom_userid": "runtime-user"}, "smart_robot", "runtime-chat"),
        ({"type": "wecom_group", "chatid": "runtime-group"}, "smart_robot", "runtime-group"),
    ),
)
def test_app_smart_robot_user_and_group_claim_safe_context(
    database: str, target: dict, channel: str, expected_address: str,
) -> None:
    _setup(database)
    if target["type"] == "wecom_user":
        _set_user_target(database, channel)
    _finalize(database, target)
    claim = _claim(database)
    assert claim["outcome"] == "claimed" and claim["state_version"] == 1
    context = _context(database, claim)
    assert context["outcome"] == "context"
    assert context["target"]["channel"] == channel
    assert expected_address in {context["target"].get("wecom_userid"), context["target"].get("chatid")}
    assert context["provider_revision"] == 1
    assert context["terminal_status"] == "completed"
    assert [item["ordinal"] for item in context["items"]] == [1]
    serialized = str(context).lower()
    for forbidden in ("secret", "authorization", "raw_body", "/private/", "http://", "https://"):
        assert forbidden not in serialized


def test_fifty_claimers_have_one_winner_and_request_readback_is_pure(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    requests = [str(uuid4()) for _ in range(50)]
    with ThreadPoolExecutor(max_workers=50) as pool:
        outcomes = list(pool.map(
            lambda pair: _claim(database, f"worker-{pair[0]}", pair[1]),
            enumerate(requests),
        ))
    winners = [item for item in outcomes if item["outcome"] == "claimed"]
    assert len(winners) == 1
    assert sum(item["outcome"] == "empty" for item in outcomes) == 49
    winner = winners[0]
    before = _owner(
        database,
        "SELECT to_jsonb(d) FROM agent_runtime_scheduled_wecom_deliveries d WHERE intent_id=%s",
        (winner["intent_id"],),
    )
    replay = _claim(database, winner["worker_id"], winner["claim_request_id"])
    readback = _rpc(
        database, "read_agent_runtime_scheduled_wecom_claim_v1",
        (winner["claim_request_id"],),
    )
    after = _owner(
        database,
        "SELECT to_jsonb(d) FROM agent_runtime_scheduled_wecom_deliveries d WHERE intent_id=%s",
        (winner["intent_id"],),
    )
    assert replay["outcome"] == "readback"
    assert readback["outcome"] == "readback" and readback["lease_active"] is True
    assert before == after


def test_renew_expiry_takeover_and_old_token_are_fenced(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    first = _claim(database, "old-worker")
    renewed = _rpc(
        database, "renew_agent_runtime_scheduled_wecom_delivery_lease_v1",
        (first["intent_id"], first["claim_request_id"], first["lease_token"],
         "old-worker", first["state_version"], 90),
    )
    assert renewed["outcome"] == "renewed" and renewed["state_version"] == 2
    stale = _rpc(
        database, "renew_agent_runtime_scheduled_wecom_delivery_lease_v1",
        (first["intent_id"], first["claim_request_id"], first["lease_token"],
         "old-worker", first["state_version"], 90),
    )
    assert stale["outcome"] == "fenced"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE intent_id=%s RETURNING intent_id",
        (first["intent_id"],),
    )
    second = _claim(database, "new-worker")
    assert second["outcome"] == "claimed" and second["lease_token"] != first["lease_token"]
    assert second["claim_request_id"] != first["claim_request_id"]
    old_renew = _rpc(
        database, "renew_agent_runtime_scheduled_wecom_delivery_lease_v1",
        (first["intent_id"], first["claim_request_id"], first["lease_token"],
         "old-worker", renewed["state_version"], 90),
    )
    assert old_renew["outcome"] == "fenced"
    old_context = _rpc(
        database, "read_agent_runtime_scheduled_wecom_dispatch_context_v1",
        (first["intent_id"], first["claim_request_id"], first["lease_token"],
         "old-worker", renewed["state_version"]),
    )
    assert old_context["outcome"] == "fenced"
    assert _context(database, second)["outcome"] == "context"


def test_live_target_mutation_matrix_fails_closed_without_secret_reads(database: str) -> None:
    _setup(database)
    _set_user_target(database, "smart_robot")
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    intent_id = _owner(database, "SELECT intent_id FROM agent_runtime_scheduled_wecom_deliveries")
    mutations = (
        "UPDATE organizations SET status='suspended' WHERE id=%s",
        "UPDATE org_members SET status='disabled' WHERE org_id=%s AND user_id='44444444-4444-4444-4444-444444444444'",
        "UPDATE wecom_user_mappings SET id=gen_random_uuid() WHERE org_id=%s AND wecom_userid='runtime-user'",
        "UPDATE wecom_user_mappings SET corp_id='rebound-corp' WHERE org_id=%s AND wecom_userid='runtime-user'",
        "UPDATE wecom_user_mappings SET wecom_userid='rebound-user' WHERE org_id=%s AND wecom_userid='runtime-user'",
        "UPDATE wecom_user_mappings SET channel='app' WHERE org_id=%s AND wecom_userid='runtime-user'",
        "UPDATE wecom_user_mappings SET last_chatid=NULL WHERE org_id=%s AND wecom_userid='runtime-user'",
        "UPDATE wecom_user_mappings SET last_chat_type='invalid' WHERE org_id=%s AND wecom_userid='runtime-user'",
    )
    for sql in mutations:
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(sql, (ORG,))
            live = conn.execute(
                "SELECT _agent_runtime_scheduled_wecom_live_context(%s)", (intent_id,),
            ).fetchone()[0]
            assert live["outcome"] == "unavailable"
            conn.rollback()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        other_org = str(uuid4())
        conn.execute(
            "INSERT INTO organizations(id,status) VALUES(%s,'active')", (other_org,),
        )
        conn.execute(
            "UPDATE wecom_user_mappings SET org_id=%s WHERE org_id=%s AND wecom_userid='runtime-user'",
            (other_org, ORG),
        )
        live = conn.execute(
            "SELECT _agent_runtime_scheduled_wecom_live_context(%s)", (intent_id,),
        ).fetchone()[0]
        assert live["outcome"] == "unavailable"
        conn.rollback()
    _owner(
        database,
        "UPDATE org_members SET status='disabled' WHERE org_id=%s AND user_id=%s RETURNING user_id",
        (ORG, USER),
    )
    assert _claim(database)["outcome"] == "empty"
    state = _owner(
        database,
        "SELECT jsonb_build_object('delivery',d.status,'delivery_reason',d.terminal_reason_code,"
        "'items',jsonb_agg(item.status),'item_reasons',jsonb_agg(item.terminal_reason_code)) "
        "FROM agent_runtime_scheduled_wecom_deliveries d JOIN agent_runtime_scheduled_wecom_delivery_items item "
        "ON item.intent_id=d.intent_id GROUP BY d.status,d.terminal_reason_code",
    )
    assert state == {
        "delivery": "unavailable", "delivery_reason": "wecom_member_unavailable",
        "items": ["cancelled"], "item_reasons": ["wecom_member_unavailable"],
    }


def test_group_matrix_and_multi_intent_isolation(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _finalize(database, {"type": "multi", "targets": [
        {"type": "wecom_group", "chatid": "runtime-group"},
        {"type": "wecom_user", "wecom_userid": "runtime-user"},
    ]})
    group_intent = _owner(
        database, "SELECT intent_id FROM agent_runtime_scheduled_wecom_deliveries WHERE target_type='wecom_group'",
    )
    mutations = (
        "UPDATE wecom_chat_targets SET id=gen_random_uuid() WHERE chatid='runtime-group'",
        "UPDATE wecom_chat_targets SET corp_id='rebound-corp' WHERE chatid='runtime-group'",
        "UPDATE wecom_chat_targets SET chatid='rebound-chat' WHERE chatid='runtime-group'",
        "UPDATE wecom_chat_targets SET chat_type='single' WHERE chatid='runtime-group'",
        "UPDATE wecom_chat_targets SET is_active=false WHERE chatid='runtime-group'",
    )
    for sql in mutations:
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(sql)
            live = conn.execute(
                "SELECT _agent_runtime_scheduled_wecom_live_context(%s)", (group_intent,),
            ).fetchone()[0]
            assert live["outcome"] == "unavailable"
            conn.rollback()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        other_org = str(uuid4())
        conn.execute(
            "INSERT INTO organizations(id,status) VALUES(%s,'active')", (other_org,),
        )
        conn.execute(
            "UPDATE wecom_chat_targets SET org_id=%s WHERE chatid='runtime-group'",
            (other_org,),
        )
        live = conn.execute(
            "SELECT _agent_runtime_scheduled_wecom_live_context(%s)", (group_intent,),
        ).fetchone()[0]
        assert live["outcome"] == "unavailable"
        conn.rollback()
    _owner(
        database,
        "UPDATE wecom_chat_targets SET is_active=false WHERE chatid='runtime-group' RETURNING id",
    )
    claimed = _claim(database)
    assert claimed["outcome"] == "claimed"
    statuses = _owner(
        database,
        "SELECT jsonb_object_agg(target_type,status) FROM agent_runtime_scheduled_wecom_deliveries",
    )
    assert statuses == {"wecom_group": "unavailable", "wecom_user": "claimed"}


def test_unknown_terminal_web_acl_rls_and_scope_contract(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    for state in (
        "dispatching", "accepted", "unknown", "reconcile_required", "partial",
        "completed", "failed", "cancelled", "unavailable",
    ):
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_deliveries SET status=%s RETURNING intent_id",
            (state,),
        )
        assert _claim(database)["outcome"] == "empty"
        _owner(
            database,
            "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='pending' RETURNING intent_id",
        )
    with psycopg.connect(database) as conn:
        for table in (
            "agent_runtime_scheduled_wecom_deliveries",
            "agent_runtime_scheduled_wecom_delivery_items",
            "agent_runtime_scheduled_wecom_dispatch_attempts",
        ):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", (table,),
            ).fetchone() == (True, True)
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_wecom_runtime',%s,'SELECT,INSERT,UPDATE,DELETE')",
                (table,),
            ).fetchone()[0] is False
        for signature in PUBLIC_SIGNATURES:
            assert conn.execute(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure", (signature,),
            ).fetchone() == (True, ["search_path=pg_catalog, public"])
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')", (signature,),
            ).fetchone()[0] is True
            assert conn.execute(
                "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
                "WHERE p.oid=%s::regprocedure AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
                (signature,),
            ).fetchone()[0] is True
            for role in ("everydayai_runtime", "everydayai_worker"):
                assert conn.execute(
                    "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, signature),
                ).fetchone()[0] is False
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _rpc(
            database, "read_agent_runtime_scheduled_wecom_claim_v1", (str(uuid4()),),
            access_kind="runtime",
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _rpc(
            database, "read_agent_runtime_scheduled_wecom_claim_v1", (str(uuid4()),),
            access_kind="agent_runtime",
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _rpc(
            database, "read_agent_runtime_scheduled_wecom_claim_v1", (str(uuid4()),),
            role="everydayai_runtime",
        )


def test_web_is_not_materialized_and_retry_wait_requires_due_item(database: str) -> None:
    _setup(database)
    _finalize(database, {"type": "web", "user_id": USER})
    assert _owner(
        database, "SELECT count(*) FROM agent_runtime_scheduled_wecom_deliveries",
    ) == 0
    assert _claim(database)["outcome"] == "empty"


def test_retry_wait_delivery_and_item_must_both_be_due(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='retry_wait',"
        "next_attempt_at=clock_timestamp()+interval '1 hour' RETURNING intent_id",
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='retry_wait',"
        "next_attempt_at=clock_timestamp()+interval '1 hour' RETURNING id",
    )
    assert _claim(database)["outcome"] == "empty"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries "
        "SET next_attempt_at=clock_timestamp()-interval '1 second' RETURNING intent_id",
    )
    assert _claim(database)["outcome"] == "empty"
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items "
        "SET next_attempt_at=clock_timestamp()-interval '1 second' RETURNING id",
    )
    assert _claim(database)["outcome"] == "claimed"


def test_rollback_allows_a1_facts_but_guards_a2a_state_and_cleanup(database: str) -> None:
    _setup(database)
    _set_user_target(database, "app")
    _finalize(database, {"type": "wecom_user", "wecom_userid": "runtime-user"})
    _rollback(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_wecom_deliveries",
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT to_regprocedure(%s)", (PUBLIC_SIGNATURES[0],),
        ).fetchone()[0] is None
    _apply(database, MIGRATION)
    claim = _claim(database)
    assert claim["outcome"] == "claimed"
    with pytest.raises(Exception, match="CLAIM_ROLLBACK_HAS_STATE"):
        _rollback(database, ROLLBACK)
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_deliveries SET status='pending',state_version=0,"
        "claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,lease_expires_at=NULL,"
        "next_attempt_at=NULL,terminal_reason_code=NULL RETURNING intent_id",
    )
    _owner(
        database,
        "UPDATE agent_runtime_scheduled_wecom_delivery_items SET status='pending',state_version=0,"
        "next_attempt_at=NULL,terminal_reason_code=NULL RETURNING id",
    )
    _rollback(database, ROLLBACK)
    _apply(database, MIGRATION)
    _rollback(database, ROLLBACK)
