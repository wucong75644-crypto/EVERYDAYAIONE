from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import _connect, database
from tests.test_agent_runtime_owner_transition_postgres_external import (
    _apply,
    _prepared_task,
    _rollback,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = UUID("22222222-2222-2222-2222-222222222222")
USER = UUID("44444444-4444-4444-4444-444444444444")


def test_runtime_required_wecom_task_is_not_actor_claimable(database: str) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_02_agent_runtime_production_catalog_seed.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_13_agent_runtime_additive_ingress_compatibility.sql",
        "227_14_agent_runtime_owner_transition.sql",
        "163_conversation_actor_worker_discovery.sql",
        "227_64_agent_runtime_wecom_owner_closure.sql",
    ):
        _apply(database, name)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
            "context_through_message_id UUID"
        )
        conn.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS queue_sequence BIGINT")
        conn.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_mode TEXT")
        conn.execute("UPDATE tasks SET queue_sequence=1, execution_mode='serial'")
        conn.commit()

    task_id, input_id, output_id, turn_id = _prepared_task(
        database, key="wecom-runtime-required"
    )
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE tasks SET delivery_context='{" 
            "\"actor\":true,\"runtime\":true,\"runtime_required\":true"
            "}'::jsonb WHERE id=%s",
            (task_id,),
        )
        conn.commit()

    with _connect(database, "everydayai_worker") as conn:
        conn.execute("SELECT set_config('app.actor_user_id','',false)")
        conn.execute("SELECT set_config('app.org_id','',false)")
        conn.execute("SELECT set_config('app.access_kind','worker',false)")
        conn.execute("SELECT set_config('app.request_id','wecom-closure',false)")
        discovered = conn.execute(
            "SELECT discover_generation_turn_candidates(100)"
        ).fetchone()[0]
        assert discovered == []

    with _connect(database, "everydayai_runtime") as conn:
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (str(USER),))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (str(ORG),))
        conn.execute("SELECT set_config('app.access_kind','runtime',false)")
        with pytest.raises(psycopg.Error, match="WECOM_RUNTIME_LEGACY_OWNER_DISABLED"):
            conn.execute(
                "SELECT restore_prepared_task_to_legacy_actor(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (task_id, "55555555-5555-5555-5555-555555555555", USER, ORG,
                 input_id, output_id, turn_id, input_id, f"message:{input_id}",
                 "wecom-runtime-required", "wecom-runtime-required"),
            )

    _rollback(database, "227_64_agent_runtime_wecom_owner_closure_rollback.sql")
    _apply(database, "227_64_agent_runtime_wecom_owner_closure.sql")
