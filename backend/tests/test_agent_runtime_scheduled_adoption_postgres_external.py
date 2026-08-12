from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import (
    ORG,
    USER,
    _prepare,
)
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def _apply_adoption_lane(url: str) -> None:
    for number in range(29, 59):
        path = next(ROOT.glob(f"migrations/227_{number:02d}_*.sql"))
        _apply(url, path.name)
    _apply(url, "227_59_agent_runtime_scheduled_adoption_preflight.sql")
    _apply(url, "227_60_agent_runtime_scheduled_adoption.sql")
    _apply(url, "227_62_agent_runtime_scheduled_owner_convergence.sql")


def _hash_task(task: dict[str, object]) -> str:
    fields = {
        key: task.get(key)
        for key in (
            "id", "org_id", "user_id", "name", "prompt", "timezone",
            "push_target", "template_file", "max_credits", "retry_count",
            "timeout_sec", "schedule_type", "cron_expr", "run_at",
            "weekdays", "day_of_month", "next_run_at", "last_summary",
        )
    }
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _seed_tasks(url: str) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        for status in ("active", "paused", "error"):
            task = {
                "id": str(uuid4()),
                "org_id": ORG,
                "user_id": USER,
                "name": f"adoption-{status}",
                "prompt": "read-only adoption fixture",
                "timezone": "UTC",
                "push_target": {"type": "web", "user_id": USER},
                "template_file": None,
                "max_credits": 10,
                "retry_count": 1,
                "timeout_sec": 180,
                "schedule_type": "cron",
                "cron_expr": "0 9 * * *",
                "run_at": None,
                "weekdays": None,
                "day_of_month": None,
                "next_run_at": "2026-08-13T01:00:00+00:00",
                "last_summary": None,
                "status": status,
            }
            conn.execute(
                "INSERT INTO scheduled_tasks("
                "id,org_id,user_id,name,prompt,cron_expr,timezone,push_target,"
                "template_file,status,max_credits,retry_count,timeout_sec,next_run_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz)",
                (
                    task["id"], task["org_id"], task["user_id"], task["name"],
                    task["prompt"], task["cron_expr"], task["timezone"],
                    Jsonb(task["push_target"]), task["template_file"], task["status"],
                    task["max_credits"], task["retry_count"], task["timeout_sec"],
                    task["next_run_at"],
                ),
            )
            tasks.append(task)
        conn.commit()
    return tasks


def _facts(tasks: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        str(task["id"]): {
            "task_semantics_hash": _hash_task(task),
            "delivery_target_hash": hashlib.sha256(
                json.dumps(task["push_target"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "agent_definition_id": "everydayai-default",
            "agent_definition_revision": "v3",
            "agent_definition_hash": "a" * 64,
            "catalog_revision": "b" * 64,
            "source_effective_toolset_hash": "c" * 64,
            "effective_toolset_hash": "d" * 64,
            "model_snapshot": {"model_id": "qwen3.5-plus", "provider": "dashscope", "revision": "fixture"},
            "toolset_snapshot": {"tool_names": ["get_conversation_context"]},
            "scope_snapshot": {"scope_kind": "user", "scope_id": USER},
            "channel": "web",
            "budget_snapshot": {"max_credits": 10, "retry_count": 1, "timeout_sec": 180},
            "provider_key": "scheduler",
            "capability_key": "runtime.scheduler.adoption",
            "provider_revision": "fixture-v1",
            "capability_revision": "fixture-v1",
            "request_hash": "e" * 64,
        }
        for task in tasks
    }


def test_apply_readback_rollback_reapply_and_completion_gate(database: str) -> None:
    _prepare(database)
    _apply_adoption_lane(database)
    tasks = _seed_tasks(database)
    facts = _facts(tasks)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        applied = conn.execute(
            "SELECT adopt_agent_runtime_scheduled_tasks_v1(%s::jsonb,%s)",
            (Jsonb(facts), str(uuid4())),
        ).fetchone()[0]
        assert applied["applied_count"] == 3
        readback = conn.execute(
            "SELECT read_agent_runtime_scheduled_adoption_v1(NULL)"
        ).fetchone()[0]
        assert len(readback["profiles"]) == 3
        for task in tasks:
            rolled_back = conn.execute(
                "SELECT rollback_agent_runtime_scheduled_adoption_v1(%s)",
                (task["id"],),
            ).fetchone()[0]
            assert rolled_back["outcome"] == "rolled_back"
        conn.commit()

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        reapplied = conn.execute(
            "SELECT adopt_agent_runtime_scheduled_tasks_v1(%s::jsonb,%s)",
            (Jsonb(facts), str(uuid4())),
        ).fetchone()[0]
        assert reapplied["applied_count"] == 3
        for task in tasks:
            conn.execute(
                "SELECT rollback_agent_runtime_scheduled_adoption_v1(%s)",
                (task["id"],),
            )
        conn.commit()

    _rollback(database, "227_62_agent_runtime_scheduled_owner_convergence_rollback.sql")
    _rollback(database, "227_60_agent_runtime_scheduled_adoption_rollback.sql")
    _apply(database, "227_60_agent_runtime_scheduled_adoption.sql")
    _apply(database, "227_62_agent_runtime_scheduled_owner_convergence.sql")

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        completed = conn.execute(
            "SELECT adopt_agent_runtime_scheduled_tasks_v1(%s::jsonb,%s)",
            (Jsonb(facts), str(uuid4())),
        ).fetchone()[0]
        assert completed["applied_count"] == 3
        outcome = conn.execute(
            "SELECT complete_agent_runtime_scheduled_adoption_v1(%s)", (str(uuid4()),)
        ).fetchone()[0]
        assert outcome["outcome"] == "completed"
