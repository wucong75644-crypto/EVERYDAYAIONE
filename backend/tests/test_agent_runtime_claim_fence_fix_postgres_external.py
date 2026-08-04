from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar173_postgres_external import (
    _seed_specialist_action,
    _worker_rpc,
    database,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    f"227_{index:02d}_" for index in range(1, 10)
)


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _apply_file(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(path.read_text())


def _migration_name(prefix: str) -> str:
    return next((ROOT / "migrations").glob(f"{prefix}*.sql")).name


def _queue_action(database: str) -> dict[str, str]:
    conversation_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
            "VALUES(%s,%s,%s,'user',%s)",
            (conversation_id, "44444444-4444-4444-4444-444444444444",
             "22222222-2222-2222-2222-222222222222",
             "44444444-4444-4444-4444-444444444444"),
        )
        conn.commit()
    ids = _seed_specialist_action(database, conversation_id=conversation_id)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_actions SET status='queued' WHERE id=%s",
            (ids["action"],),
        )
        conn.execute(
            "UPDATE agent_action_attempts SET status='claimed',dispatch_phase='claimed' WHERE id=%s",
            (ids["attempt"],),
        )
        conn.commit()
    return ids


def test_claim_fix_apply_security_and_rollback(database: str) -> None:
    for prefix in MIGRATIONS:
        _apply(database, _migration_name(prefix))
    with psycopg.connect(database) as conn:
        for signature in (
            "claim_ready_agent_action_snapshots_v2(text,text,integer,integer)",
            "claim_ready_agent_actions_v2(text,text,integer,integer)",
        ):
            assert conn.execute(
                "SELECT proconfig FROM pg_proc WHERE oid=%s::regprocedure",
                (signature,),
            ).fetchone()[0] == ["search_path=pg_catalog, public"]
            assert conn.execute(
                "SELECT prosecdef FROM pg_proc WHERE oid=%s::regprocedure",
                (signature,),
            ).fetchone()[0] is True
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'claim_ready_agent_actions_v2(text,text,integer,integer)','EXECUTE')"
        ).fetchone()[0] is True
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'agent_runtime_tenant_gate_controls','SELECT')"
        ).fetchone()[0] is False

    _queue_action(database)
    claimed = _worker_rpc(database, "claim_ready_agent_actions_v2", (
        "fix-worker", "fix-claim", 10, 120,
    ))
    assert claimed["outcome"] == "claimed"

    rollback = next(
        (ROOT / "migrations/rollback").glob(
            "227_09_agent_runtime_claim_fence_ambiguity_fix_rollback.sql"
        )
    )
    _apply_file(database, rollback)
    _apply(database, _migration_name("227_09_"))
    _apply_file(database, rollback)


def test_claim_snapshot_fix_claims_with_fence(database: str) -> None:
    for prefix in MIGRATIONS:
        _apply(database, _migration_name(prefix))
    _queue_action(database)
    claimed = _worker_rpc(database, "claim_ready_agent_action_snapshots_v2", (
        "fix-snapshot-worker", "fix-snapshot-claim", 10, 120,
    ))
    assert claimed["outcome"] == "claimed"
