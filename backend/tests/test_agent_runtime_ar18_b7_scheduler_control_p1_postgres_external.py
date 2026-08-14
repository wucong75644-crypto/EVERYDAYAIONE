from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.agent.runtime.scheduler_cas import SchedulerCasError
from services.agent.runtime.scheduler_control_payload import (
    normalize_scheduler_control_payload,
)
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import (
    ORG, USER, _create_payload, _mutate, _prepare, _resume, _seed,
)


pytestmark = pytest.mark.external


def _schedule_payload(schedule_type: str) -> dict[str, object]:
    payload = _create_payload(f"Runtime {schedule_type}")
    payload["schedule_type"] = schedule_type
    if schedule_type == "once":
        payload["run_at"] = (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat()
        payload["cron_expr"] = None
    elif schedule_type == "daily":
        payload.update({"cron_expr": "15 9 * * *", "time_str": "09:15"})
    elif schedule_type == "weekly":
        payload.update({
            "cron_expr": "15 9 * * 1,3,5", "time_str": "09:15",
            "weekdays": [1, 3, 5],
        })
    elif schedule_type == "monthly":
        payload.update({
            "cron_expr": "15 9 20 * *", "time_str": "09:15",
            "day_of_month": 20,
        })
    return normalize_scheduler_control_payload("create", payload)


def test_pause_clears_due_time_and_resume_recomputes_all_schedule_types(
    database: str,
) -> None:
    _prepare(database)
    for schedule_type in ("daily", "weekly", "monthly", "cron", "once"):
        task_id = str(uuid4())
        created = _mutate(
            database, _seed(database), task_id, "create", 0,
            f"p1-create-{schedule_type}", _schedule_payload(schedule_type),
        )
        assert created["outcome"] == "committed"
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "UPDATE scheduled_tasks SET next_run_at=clock_timestamp()-interval '1 hour' "
                "WHERE id=%s", (task_id,),
            )
            conn.commit()
        paused = _mutate(
            database, _seed(database), task_id, "pause", 1,
            f"p1-pause-{schedule_type}", {},
        )
        assert paused["outcome"] == "committed"
        with psycopg.connect(database) as conn:
            row = conn.execute(
                "SELECT status,next_run_at FROM scheduled_tasks WHERE id=%s", (task_id,),
            ).fetchone()
            assert row == ("paused", None)
        resumed, calculated = _resume(
            database, _seed(database), task_id, 2,
            f"p1-resume-{schedule_type}",
        )
        assert resumed["outcome"] == "committed"
        with psycopg.connect(database) as conn:
            row = conn.execute(
                "SELECT status,next_run_at,consecutive_failures FROM scheduled_tasks "
                "WHERE id=%s", (task_id,),
            ).fetchone()
            assert row[0] == "active" and row[2] == 0
            assert row[1] == datetime.fromisoformat(calculated)
            assert row[1] > datetime.now(timezone.utc)


def test_once_resume_fails_closed_after_run_at_passes(database: str) -> None:
    _prepare(database)
    task_id = str(uuid4())
    _mutate(
        database, _seed(database), task_id, "create", 0,
        "p1-once-create", _schedule_payload("once"),
    )
    _mutate(database, _seed(database), task_id, "pause", 1, "p1-once-pause", {})
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET run_at=clock_timestamp()-interval '1 minute' "
            "WHERE id=%s", (task_id,),
        )
        conn.commit()
    with pytest.raises(SchedulerCasError, match="ONCE_RESUME_EXPIRED"):
        _resume(database, _seed(database), task_id, 2, "p1-once-expired")
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status,next_run_at FROM scheduled_tasks WHERE id=%s", (task_id,),
        ).fetchone() == ("paused", None)


def _insert_actor_fixtures(database: str) -> dict[str, str]:
    values = {name: str(uuid4()) for name in (
        "same_dept", "other_dept", "same_owner", "other_owner",
        "boss", "vp", "deputy", "member",
    )}
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        actor_dept = str(conn.execute(
            "SELECT department_id FROM org_member_assignments "
            "WHERE org_id=%s AND user_id=%s AND is_primary", (ORG, USER),
        ).fetchone()[0])
        values["same_dept"] = actor_dept
        conn.execute(
            "INSERT INTO org_departments(id,org_id,name,type,path) "
            "VALUES(%s,%s,'Runtime Finance','finance','root.runtime_finance')",
            (values["other_dept"], ORG),
        )
        for code in ("boss", "vp", "deputy", "member"):
            conn.execute(
                "INSERT INTO org_positions(id,org_id,code,name,level) "
                "VALUES(%s,%s,%s,%s,%s)",
                (values[code], ORG, code, f"Runtime {code}",
                 {"boss": 1, "vp": 2, "deputy": 4, "member": 5}[code]),
            )
        for owner, department in (
            (values["same_owner"], actor_dept),
            (values["other_owner"], values["other_dept"]),
        ):
            conn.execute("INSERT INTO users(id) VALUES(%s)", (owner,))
            conn.execute(
                "INSERT INTO org_members(org_id,user_id,status) VALUES(%s,%s,'active')",
                (ORG, owner),
            )
            conn.execute(
                "INSERT INTO org_member_assignments(org_id,user_id,department_id,"
                "position_id,data_scope) VALUES(%s,%s,%s,%s,'self')",
                (ORG, owner, department, values["member"]),
            )
        conn.commit()
    return values


def _set_actor(
    database: str, position_id: str, *, department_id: str | None,
    data_scope: str, managed: list[str] | None = None,
) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE org_member_assignments SET position_id=%s,department_id=%s,"
            "data_scope=%s,data_scope_dept_ids=%s WHERE org_id=%s AND user_id=%s",
            (position_id, department_id, data_scope, managed, ORG, USER),
        )
        conn.commit()


def _insert_task(database: str, owner: str, name: str) -> str:
    task_id = str(uuid4())
    payload = _schedule_payload("cron")
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,timezone,"
            "push_target,status,schedule_type,next_run_at,runtime_state_version) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'active','cron',%s,0)",
            (task_id, ORG, owner, name, payload["prompt"], payload["cron_expr"],
             payload["timezone"], Jsonb(payload["push_target"]), payload["next_run_at"]),
        )
        conn.commit()
    return task_id


def _update(database: str, task_id: str, key: str):
    return _mutate(
        database, _seed(database), task_id, "update", 0, key, {"name": key},
    )


def test_runtime_scheduler_resource_permissions_match_permission_checker(
    database: str,
) -> None:
    _prepare(database)
    values = _insert_actor_fixtures(database)
    with psycopg.connect(database) as conn:
        manager_position = str(conn.execute(
            "SELECT position_id FROM org_member_assignments "
            "WHERE org_id=%s AND user_id=%s", (ORG, USER),
        ).fetchone()[0])

    same = _insert_task(database, values["same_owner"], "same-manager")
    assert _update(database, same, "manager-same")["outcome"] == "committed"
    cross = _insert_task(database, values["other_owner"], "cross-manager")
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_OPERATION_PERMISSION_DENIED"):
        _update(database, cross, "manager-cross")

    _set_actor(
        database, values["vp"], department_id=None, data_scope="dept_subtree",
        managed=[values["same_dept"]],
    )
    assert _update(
        database, _insert_task(database, values["same_owner"], "vp-managed"),
        "vp-managed",
    )["outcome"] == "committed"
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_OPERATION_PERMISSION_DENIED"):
        _update(
            database, _insert_task(database, values["other_owner"], "vp-cross"),
            "vp-cross",
        )
    _set_actor(database, values["vp"], department_id=None, data_scope="all")
    assert _update(
        database, _insert_task(database, values["other_owner"], "vp-all"), "vp-all",
    )["outcome"] == "committed"

    for role in ("deputy", "member"):
        _set_actor(
            database, values[role], department_id=values["same_dept"],
            data_scope="self",
        )
        assert _update(
            database, _insert_task(database, USER, f"{role}-self"), f"{role}-self",
        )["outcome"] == "committed"
        with pytest.raises(Exception, match="RUNTIME_SCHEDULER_OPERATION_PERMISSION_DENIED"):
            _update(
                database, _insert_task(database, values["same_owner"], f"{role}-other"),
                f"{role}-other",
            )

    _set_actor(database, values["boss"], department_id=None, data_scope="all")
    boss_task = _insert_task(database, values["other_owner"], "boss-cross")
    assert _mutate(
        database, _seed(database), boss_task, "delete", 0, "boss-delete", {},
    )["outcome"] == "committed"

    _set_actor(
        database, manager_position, department_id=values["same_dept"],
        data_scope="dept_subtree",
    )
