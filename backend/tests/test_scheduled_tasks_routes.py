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

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from tests.scheduled_tasks_routes_cases import (
    TestParseNL,
    TestRunsAndChatTargets,
    TestTaskOperations,
)
from tests.scheduled_tasks_routes_test_support import (
    FakeDB,
    build_app as _build_app,
)


# ════════════════════════════════════════════════════════
# Fake DB
# ════════════════════════════════════════════════════════

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
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is True

    def test_web_target_other(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "web", "user_id": "user_lisi"}
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is False

    def test_wecom_user_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        # 当前用户的 wecom_user_mappings 中存在该 wecom_userid
        db.add_rpc("is_runtime_wecom_self_target", True)
        target = {"type": "wecom_user", "wecom_userid": "ww_zhangsan"}
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is True

    def test_wecom_user_other(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        # 当前用户的 wecom_user_mappings 中没有该 wecom_userid
        db.add_rpc("is_runtime_wecom_self_target", False)
        target = {"type": "wecom_user", "wecom_userid": "ww_other"}
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is False

    def test_wecom_user_missing_userid(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "wecom_user"}  # 缺 wecom_userid
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is False

    def test_wecom_group_never_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "wecom_group", "chatid": "group_xxx"}
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is False

    def test_multi_target_never_self(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        target = {"type": "multi", "targets": []}
        assert _is_push_to_self(db, "user_zhangsan", "org_1", target) is False

    def test_invalid_target_type(self):
        from api.routes.scheduled_tasks import _is_push_to_self
        db = FakeDB()
        # 不是 dict
        assert _is_push_to_self(db, "user_zhangsan", "org_1", None) is False
        assert _is_push_to_self(db, "user_zhangsan", "org_1", "invalid") is False


# ════════════════════════════════════════════════════════
# 2. GET /scheduled-tasks 列表
# ════════════════════════════════════════════════════════

class TestListTasks:

    def test_list_returns_tasks(self):
        db = FakeDB()
        tasks = [
            {
                "id": "t1",
                "user_id": "11111111-1111-1111-1111-111111111111",
                "org_id": "org_1",
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
