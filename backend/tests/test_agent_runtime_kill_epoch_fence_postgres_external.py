from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar173_postgres_external import _seed_specialist_action
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_tenant_kill_control_postgres_external import _set_gate


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = "22222222-2222-2222-2222-222222222222"
ACTOR = "44444444-4444-4444-4444-444444444444"


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))


def test_b_epoch_fences_dispatch_and_rejects_worker_legacy_bypass(database: str) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
    ):
        _apply(database, name)
    ids = _seed_specialist_action(database)
    try:
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "INSERT INTO agent_runtime_owner_fences "
                "(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,status) "
                "VALUES('attempt',%s,%s,%s,0,'active')",
                (ids["attempt"], ORG, ids["token"]),
            )
            assert conn.execute(
                "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
                "'agent_runtime_owner_fences','SELECT')"
            ).fetchone()[0] is False
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_worker',"
                "'gate_agent_action_dispatch_v2(uuid,uuid,bigint,text,uuid,text,integer,text,text)','EXECUTE')"
            ).fetchone()[0] is False
            conn.commit()
        _set_gate(database, str(uuid4()), "tenant", "tenant", True, 0)
        worker_url = database.replace("postgres@", "everydayai_agent_runtime_worker@")
        with psycopg.connect(worker_url) as conn:
            conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
            conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (ACTOR,))
            conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
            row = conn.execute(
                "SELECT gate_agent_action_dispatch_v2(%s,%s,0,%s,%s,%s,1,%s,%s)",
                (ids["attempt"],ids["token"],ids["request_hash"],
                 ids["policy"],"runtime_media_generation:generate_image", "v1", "reconcile_only"),
            ).fetchone()[0]
            assert row["error_code"] == "RUNTIME_KILL_EPOCH_FENCED"
    finally:
        # The disposable database fixture owns teardown; no production cleanup path is used.
        pass


def test_b_rollback_reapply_without_facts(database: str) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
    ):
        _apply(database, name)
    rollback = ROOT / "migrations/rollback/227_07_agent_runtime_kill_epoch_fence_rollback.sql"
    _apply(database, rollback.relative_to(ROOT / "migrations").as_posix())
    _apply(database, "227_07_agent_runtime_kill_epoch_fence.sql")
    _apply(database, rollback.relative_to(ROOT / "migrations").as_posix())
