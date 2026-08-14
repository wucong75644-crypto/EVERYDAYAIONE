from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from tests.scheduled_tasks_routes_test_support import FakeDB, build_app


class TestTaskOperations:
    def _make_task_db(self):
        db = FakeDB()
        task = {
            "id": "t1",
            "user_id": "user_1",
            "org_id": "org_1",
            "name": "测试",
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "status": "active",
            "push_target": {},
            "next_run_at": "2026-04-12T01:00:00Z",
            "run_count": 0,
        }
        return db, task

    def test_pause_task(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        db.add("scheduled_tasks", [task])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            response = client.post("/api/scheduled-tasks/t1/pause")
        assert response.status_code == 200

    def test_pause_no_permission(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            response = client.post("/api/scheduled-tasks/t1/pause")
        assert response.status_code == 403

    def test_pause_not_found(self):
        db = FakeDB()
        db.add("scheduled_tasks", [])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            response = client.post("/api/scheduled-tasks/nonexistent/pause")
        assert response.status_code == 404

    def test_resume_task(self):
        db, task = self._make_task_db()
        task["status"] = "paused"
        db.add("scheduled_tasks", [task])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            response = client.post("/api/scheduled-tasks/t1/resume")
        assert response.status_code == 200

    def test_delete_task(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            response = client.delete("/api/scheduled-tasks/t1")
        assert response.status_code == 200

    def test_run_now_no_execute_permission(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            response = client.post("/api/scheduled-tasks/t1/run")
        assert response.status_code == 403


class TestRunsAndChatTargets:
    """Endpoint tests for run history and available chat targets."""

    def _make_task_db_with_runs(self):
        db = FakeDB()
        task = {
            "id": "t1",
            "user_id": "user_1",
            "org_id": "org_1",
            "name": "测试",
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "status": "active",
            "push_target": {},
            "next_run_at": None,
            "run_count": 0,
        }
        db.add("scheduled_tasks", [task])
        return db, task

    def test_list_runs_returns_history(self):
        db, _task = self._make_task_db_with_runs()
        runs = [
            {
                "id": "r1",
                "task_id": "t1",
                "org_id": "org_1",
                "status": "success",
                "started_at": "2026-04-11T01:00:00Z",
                "finished_at": "2026-04-11T01:00:12Z",
                "duration_ms": 12000,
                "result_summary": "销售额 10w",
                "credits_used": 3,
                "tokens_used": 1500,
            },
        ]
        db.add("scheduled_task_runs", runs)
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            response = client.get("/api/scheduled-tasks/t1/runs")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["status"] == "success"
        assert body["data"][0]["credits_used"] == 3

    def test_list_runs_no_permission_returns_403(self):
        db, _task = self._make_task_db_with_runs()
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            response = client.get("/api/scheduled-tasks/t1/runs")
        assert response.status_code == 403

    def test_list_runs_task_not_found(self):
        db = FakeDB()
        db.add("scheduled_tasks", [])
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            response = client.get("/api/scheduled-tasks/nonexistent/runs")
        assert response.status_code == 404

    def test_list_chat_targets_returns_active_targets(self):
        db = FakeDB()
        targets = [
            {
                "chatid": "chat_a",
                "chat_type": "group",
                "chat_name": "运营群",
                "last_active": "2026-04-11T10:00:00Z",
            },
            {
                "chatid": "user_b",
                "chat_type": "single",
                "chat_name": "张三",
                "last_active": "2026-04-10T15:30:00Z",
            },
        ]
        db.add_rpc("list_runtime_wecom_chat_targets", targets)
        app = build_app(db)

        response = TestClient(app).get("/api/scheduled-tasks/chat-targets")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["chat_name"] == "运营群"

    def test_list_chat_targets_empty(self):
        db = FakeDB()
        db.add_rpc("list_runtime_wecom_chat_targets", [])
        app = build_app(db)

        response = TestClient(app).get("/api/scheduled-tasks/chat-targets")

        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_run_now_submits_runtime_execution(self):
        db, task = self._make_task_db_with_runs()
        task.update({"runtime_action_id": "action-1", "runtime_state_version": 3})
        db.add_rpc(
            "request_agent_runtime_scheduled_execution_v1",
            {
                "owner_kind": "runtime",
                "outcome": "submitted",
                "command_id": "command-1",
            },
        )
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            response = TestClient(app).post(
                "/api/scheduled-tasks/t1/run",
                headers={"Idempotency-Key": "manual-1"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Runtime" in body["message"]
        assert body["command_id"] == "command-1"

    def test_run_now_without_runtime_profile_fails_closed(self):
        db, _task = self._make_task_db_with_runs()
        app = build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            response = TestClient(app).post(
                "/api/scheduled-tasks/t1/run",
                headers={"Idempotency-Key": "manual-1"},
            )

        assert response.status_code == 503
        assert "旧执行链路" in response.json()["detail"]


class TestParseNL:
    """Natural-language scheduling returns structured fields."""

    def test_daily_inferred(self):
        db = FakeDB()
        app = build_app(db)

        async def fake_parse(text, tz="Asia/Shanghai"):
            return {
                "name": "每日销售日报",
                "prompt": "汇总并推送销售日报",
                "schedule_type": "daily",
                "time_str": "09:00",
            }

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.scheduler.task_nl_parser.parse_task_nl",
            new=fake_parse,
        ):
            response = TestClient(app).post(
                "/api/scheduled-tasks/parse",
                json={"text": "每天9点推送销售日报"},
            )
        body = response.json()["data"]
        assert body["schedule_type"] == "daily"
        assert body["time_str"] == "09:00"
        assert body["cron_readable"] == "每天 09:00"

    def test_weekly_inferred(self):
        db = FakeDB()
        app = build_app(db)

        async def fake_parse(text, tz="Asia/Shanghai"):
            return {
                "name": "经营周报",
                "prompt": "汇总并推送经营周报",
                "schedule_type": "weekly",
                "time_str": "09:00",
                "weekdays": [1],
            }

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.scheduler.task_nl_parser.parse_task_nl",
            new=fake_parse,
        ):
            response = TestClient(app).post(
                "/api/scheduled-tasks/parse",
                json={"text": "每周一推经营周报"},
            )
        body = response.json()["data"]
        assert body["schedule_type"] == "weekly"
        assert body["weekdays"] == [1]
        assert body["cron_readable"] == "每周一 09:00"
