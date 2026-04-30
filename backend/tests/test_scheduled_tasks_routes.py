"""定时任务 REST API 路由测试

覆盖：
- 创建（含 cron 校验、权限检查）
- 列表（按权限自动过滤）
- 详情/暂停/恢复/删除/立即执行
- 自然语言解析
"""
from __future__ import annotations
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ════════════════════════════════════════════════════════
# Fake DB
# ════════════════════════════════════════════════════════

class FakeQueryBuilder:
    def __init__(self, data=None):
        self._data = data if isinstance(data, list) else ([data] if data else [])
        self._is_single = False
        self._limit = None
        self._is_delete = False
        self._is_update = False

    def select(self, *a, **kw): return self
    def insert(self, data, **kw): return self
    def update(self, data, **kw):
        self._is_update = True
        return self
    def delete(self):
        self._is_delete = True
        return self
    def eq(self, *a): return self
    def in_(self, *a): return self
    def order(self, *a, **kw): return self
    def limit(self, n):
        self._limit = n
        return self
    def single(self):
        self._is_single = True
        return self

    def execute(self):
        r = MagicMock()
        if self._is_single:
            r.data = self._data[0] if self._data else None
        elif self._is_delete or self._is_update:
            r.data = []
        else:
            r.data = self._data[: self._limit] if self._limit else self._data
        return r


class FakeDB:
    def __init__(self):
        self._tables: dict = {}
        self._rpc_responses: dict = {}

    def add(self, name, data):
        self._tables.setdefault(name, []).append(FakeQueryBuilder(data))

    def add_rpc(self, name, data):
        self._rpc_responses[name] = data

    def table(self, name):
        items = self._tables.get(name, [])
        if items:
            return items.pop(0)
        return FakeQueryBuilder([])

    def rpc(self, name, params=None):
        result = MagicMock()
        result.execute.return_value = MagicMock(
            data=self._rpc_responses.get(name, [])
        )
        return result


def _build_app(db, user_id="user_1", org_id="org_1", with_perm=True):
    """构建 mock app"""
    from api.routes.scheduled_tasks import router
    from api.deps import get_current_user_id, get_org_context, get_scoped_db, OrgContext
    from core.database import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api")

    from main import register_exception_handlers
    register_exception_handlers(app)

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_org_context] = lambda: OrgContext(
        user_id=user_id, org_id=org_id, org_role="member"
    )
    app.dependency_overrides[get_scoped_db] = lambda: db
    app.dependency_overrides[get_db] = lambda: db

    return app


# ════════════════════════════════════════════════════════
# 1. POST /scheduled-tasks 创建
# ════════════════════════════════════════════════════════

class TestCreateTask:

    def test_create_success(self):
        db = FakeDB()
        # 创建后没有查询，只是 insert
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "每日销售日报",
                "prompt": "查询昨日销售",
                "cron_expr": "0 9 * * *",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "每日销售日报"
        assert data["status"] == "active"
        assert "cron_readable" in data
        assert data["cron_readable"] == "每天 09:00"

    def test_create_no_permission(self):
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "cron_expr": "0 9 * * *",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })
        assert resp.status_code == 403

    def test_create_invalid_cron(self):
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "cron_expr": "invalid",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })
        assert resp.status_code == 400

    def test_create_member_cannot_push_to_group(self):
        """member 职位推送到群聊 → 403（缺 task.push_to_others）"""
        db = FakeDB()
        app = _build_app(db)

        async def fake_check(db_, user_id_, org_id_, code, *a, **kw):
            return code != "task.push_to_others"

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=fake_check,
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "cron_expr": "0 9 * * *",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })
        assert resp.status_code == 403
        assert "管理职位" in resp.json()["detail"]

    def test_create_member_can_push_to_self_via_web(self):
        """member 推送到 web 自己 → 200（不需要 push_to_others）"""
        db = FakeDB()
        app = _build_app(db, user_id="user_1")

        async def fake_check(db_, user_id_, org_id_, code, *a, **kw):
            # member 没有 task.push_to_others
            return code != "task.push_to_others"

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=fake_check,
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "cron_expr": "0 9 * * *",
                "push_target": {"type": "web", "user_id": "user_1"},
            })
        assert resp.status_code == 200

    def test_create_daily_via_schedule_type(self):
        """schedule_type=daily + time_str → 自动组装 cron"""
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "每天 9 点",
                "prompt": "test",
                "schedule_type": "daily",
                "time_str": "09:00",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["schedule_type"] == "daily"
        assert data["cron_expr"] == "0 9 * * *"
        assert data["cron_readable"] == "每天 09:00"

    def test_create_weekly_multi_days(self):
        """schedule_type=weekly + 多个 weekdays"""
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "周一三五日报",
                "prompt": "test",
                "schedule_type": "weekly",
                "time_str": "09:00",
                "weekdays": [1, 3, 5],
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["schedule_type"] == "weekly"
        assert data["cron_expr"] == "0 9 * * 1,3,5"
        assert data["weekdays"] == [1, 3, 5]

    def test_create_monthly(self):
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "每月 15 日",
                "prompt": "test",
                "schedule_type": "monthly",
                "time_str": "09:00",
                "day_of_month": 15,
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["schedule_type"] == "monthly"
        assert data["cron_expr"] == "0 9 15 * *"
        assert data["day_of_month"] == 15

    def test_create_once(self):
        """schedule_type=once + run_at → 单次任务，cron_expr 为 None"""
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "今晚 22:00",
                "prompt": "test",
                "schedule_type": "once",
                "run_at": "2099-04-15T22:00:00+08:00",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["schedule_type"] == "once"
        assert data["cron_expr"] is None
        assert data["run_at"] is not None

    def test_create_once_in_past_rejected(self):
        """单次任务的 run_at 是过去时间 → 400"""
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "schedule_type": "once",
                "run_at": "2000-01-01T09:00:00+08:00",  # 远古时间
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })
        assert resp.status_code == 400
        assert "执行时间" in resp.json()["detail"]

    def test_create_once_missing_run_at(self):
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "schedule_type": "once",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })
        assert resp.status_code == 400
        assert "run_at" in resp.json()["detail"]

    def test_create_weekly_missing_weekdays(self):
        db = FakeDB()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks", json={
                "name": "test",
                "prompt": "test",
                "schedule_type": "weekly",
                "time_str": "09:00",
                "push_target": {"type": "wecom_group", "chatid": "x"},
            })
        assert resp.status_code == 400


# ════════════════════════════════════════════════════════
# 1.5 _is_push_to_self 辅助函数单测（覆盖 4 个分支）
# ════════════════════════════════════════════════════════

class TestIsPushToSelf:
    """直接单测 _is_push_to_self，避免只靠 HTTP 集成测试覆盖"""

    def test_web_target_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "web", "user_id": "user_zhangsan"}
        assert _is_push_to_self(db, "user_zhangsan", target) is True

    def test_web_target_other(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "web", "user_id": "user_lisi"}
        assert _is_push_to_self(db, "user_zhangsan", target) is False

    def test_wecom_user_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        # 当前用户的 wecom_user_mappings 中存在该 wecom_userid
        db.add("wecom_user_mappings", [{"wecom_userid": "ww_zhangsan"}])
        target = {"type": "wecom_user", "wecom_userid": "ww_zhangsan"}
        assert _is_push_to_self(db, "user_zhangsan", target) is True

    def test_wecom_user_other(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        # 当前用户的 wecom_user_mappings 中没有该 wecom_userid
        db.add("wecom_user_mappings", [])
        target = {"type": "wecom_user", "wecom_userid": "ww_other"}
        assert _is_push_to_self(db, "user_zhangsan", target) is False

    def test_wecom_user_missing_userid(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "wecom_user"}  # 缺 wecom_userid
        assert _is_push_to_self(db, "user_zhangsan", target) is False

    def test_wecom_group_never_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "wecom_group", "chatid": "group_xxx"}
        assert _is_push_to_self(db, "user_zhangsan", target) is False

    def test_multi_target_never_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "multi", "targets": []}
        assert _is_push_to_self(db, "user_zhangsan", target) is False

    def test_invalid_target_type(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        # 不是 dict
        assert _is_push_to_self(db, "user_zhangsan", None) is False
        assert _is_push_to_self(db, "user_zhangsan", "invalid") is False


# ════════════════════════════════════════════════════════
# 2. GET /scheduled-tasks 列表
# ════════════════════════════════════════════════════════

class TestListTasks:

    def test_list_returns_tasks(self):
        db = FakeDB()
        tasks = [
            {
                "id": "t1", "user_id": "user_1", "org_id": "org_1",
                "name": "任务1", "cron_expr": "0 9 * * *",
                "status": "active", "push_target": {},
                "next_run_at": None, "run_count": 0,
            },
        ]
        db.add("scheduled_tasks", tasks)
        # creator enrichment 需要的查询
        db.add("users", [])
        db.add("org_member_assignments", [])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routes.scheduled_tasks.apply_data_scope",
            side_effect=lambda db, q, *a, **kw: q,
        ):
            client = TestClient(app)
            resp = client.get("/api/scheduled-tasks")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["data"][0]["name"] == "任务1"
        assert body["data"][0]["cron_readable"] == "每天 09:00"

    def test_list_view_mine(self):
        db = FakeDB()
        db.add("scheduled_tasks", [])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.get("/api/scheduled-tasks?view=mine")
        assert resp.status_code == 200


# ════════════════════════════════════════════════════════
# 3. 任务操作（pause/resume/delete/run）
# ════════════════════════════════════════════════════════

class TestTaskOperations:

    def _make_task_db(self):
        db = FakeDB()
        task = {
            "id": "t1", "user_id": "user_1", "org_id": "org_1",
            "name": "测试", "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "status": "active", "push_target": {},
            "next_run_at": "2026-04-12T01:00:00Z", "run_count": 0,
        }
        return db, task

    def test_pause_task(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        # 第二次查询返回任务（用于 update 链）
        db.add("scheduled_tasks", [task])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/t1/pause")
        assert resp.status_code == 200

    def test_pause_no_permission(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/t1/pause")
        assert resp.status_code == 403

    def test_pause_not_found(self):
        db = FakeDB()
        db.add("scheduled_tasks", [])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/nonexistent/pause")
        assert resp.status_code == 404

    def test_resume_task(self):
        db, task = self._make_task_db()
        task["status"] = "paused"
        db.add("scheduled_tasks", [task])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/t1/resume")
        assert resp.status_code == 200

    def test_delete_task(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.delete("/api/scheduled-tasks/t1")
        assert resp.status_code == 200

    def test_run_now_no_execute_permission(self):
        db, task = self._make_task_db()
        db.add("scheduled_tasks", [task])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/t1/run")
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════
# 4. 自然语言解析
# ════════════════════════════════════════════════════════

class TestRunsAndChatTargets:
    """新增端点测试：/runs 和 /chat-targets"""

    def _make_task_db_with_runs(self):
        db = FakeDB()
        task = {
            "id": "t1", "user_id": "user_1", "org_id": "org_1",
            "name": "测试", "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "status": "active", "push_target": {},
            "next_run_at": None, "run_count": 0,
        }
        db.add("scheduled_tasks", [task])
        return db, task

    def test_list_runs_returns_history(self):
        db, _task = self._make_task_db_with_runs()
        # 任务存在性查询会返回 task
        runs = [
            {
                "id": "r1", "task_id": "t1", "org_id": "org_1",
                "status": "success", "started_at": "2026-04-11T01:00:00Z",
                "finished_at": "2026-04-11T01:00:12Z",
                "duration_ms": 12000,
                "result_summary": "销售额 10w",
                "credits_used": 3, "tokens_used": 1500,
            },
        ]
        db.add("scheduled_task_runs", runs)
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.get("/api/scheduled-tasks/t1/runs")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["status"] == "success"
        assert body["data"][0]["credits_used"] == 3

    def test_list_runs_no_permission_returns_403(self):
        db, _task = self._make_task_db_with_runs()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            resp = client.get("/api/scheduled-tasks/t1/runs")
        assert resp.status_code == 403

    def test_list_runs_task_not_found(self):
        db = FakeDB()
        db.add("scheduled_tasks", [])
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ):
            client = TestClient(app)
            resp = client.get("/api/scheduled-tasks/nonexistent/runs")
        assert resp.status_code == 404

    def test_list_chat_targets_returns_active_targets(self):
        db = FakeDB()
        targets = [
            {
                "chatid": "chat_a", "chat_type": "group",
                "chat_name": "运营群", "last_active": "2026-04-11T10:00:00Z",
            },
            {
                "chatid": "user_b", "chat_type": "single",
                "chat_name": "张三", "last_active": "2026-04-10T15:30:00Z",
            },
        ]
        db.add("wecom_chat_targets", targets)
        app = _build_app(db)

        client = TestClient(app)
        resp = client.get("/api/scheduled-tasks/chat-targets")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["chat_name"] == "运营群"

    def test_list_chat_targets_empty(self):
        db = FakeDB()
        db.add("wecom_chat_targets", [])
        app = _build_app(db)

        client = TestClient(app)
        resp = client.get("/api/scheduled-tasks/chat-targets")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_run_now_executes_immediately(self):
        db, _task = self._make_task_db_with_runs()
        app = _build_app(db)

        with patch(
            "api.routes.scheduled_tasks.check_permission",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.scheduler.task_executor.ScheduledTaskExecutor"
        ) as mock_exec_cls:
            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value=None)
            mock_exec_cls.return_value = mock_executor

            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/t1/run")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "已开始执行" in body["message"]


class TestParseNL:
    """parse_nl_task 路由 — 走 LLM 解析或兜底，返回结构化字段"""

    def test_daily_inferred(self):
        db = FakeDB()
        app = _build_app(db)

        # mock LLM 返回每日类型
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
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/parse", json={
                "text": "每天9点推送销售日报"
            })
        body = resp.json()["data"]
        assert body["schedule_type"] == "daily"
        assert body["time_str"] == "09:00"
        assert body["cron_readable"] == "每天 09:00"

    def test_weekly_inferred(self):
        db = FakeDB()
        app = _build_app(db)

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
            client = TestClient(app)
            resp = client.post("/api/scheduled-tasks/parse", json={
                "text": "每周一推经营周报"
            })
        body = resp.json()["data"]
        assert body["schedule_type"] == "weekly"
        assert body["weekdays"] == [1]
        assert body["cron_readable"] == "每周一 09:00"
