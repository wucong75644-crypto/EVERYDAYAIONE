"""error_monitor API 路由测试

使用 TestClient 真 HTTP 调用，验证：
- 数据库能力权限拒绝（非 super_admin 返回 403）
- list: 分页、筛选、搜索
- stats: 统计数据
- resolve: 标记已处理
- clear: 批量清除
- _serialize_row: 序列化辅助函数
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from api.routes.error_monitor import _serialize_row


# ── Fake DB ──────────────────────────────────────────────


class FakeDB:
    """可预设数据库能力返回值的 Fake DB。"""

    def __init__(self):
        self._queue: list[object] = []
        self.calls: list[tuple[str, dict]] = []

    def enqueue(self, data=None):
        self._queue.append(data)

    def rpc(self, name, params):
        self.calls.append((name, params))
        value = self._queue.pop(0) if self._queue else None
        caller = MagicMock()
        if isinstance(value, Exception):
            caller.execute.side_effect = value
        else:
            caller.execute.return_value.data = value
        return caller


# ── Test App Builder ─────────────────────────────────────


SUPER_ADMIN_ID = "admin-001"
NORMAL_USER_ID = "user-001"

FAKE_ERROR_ROW = {
    "id": 1,
    "fingerprint": "abc123def456",
    "level": "ERROR",
    "module": "services.kuaimai",
    "function": "sync_stock",
    "line": 42,
    "message": "ReadTimeout connecting to API",
    "traceback": None,
    "occurrence_count": 3,
    "first_seen_at": "2026-04-12T10:00:00+08:00",
    "last_seen_at": "2026-04-12T12:00:00+08:00",
    "org_id": None,
    "is_critical": False,
    "is_resolved": False,
    "resolved_at": None,
    "resolved_by": None,
}


def _build_app(db, user_id: str = SUPER_ADMIN_ID) -> FastAPI:
    from api.routes.error_monitor import router
    from api.deps import get_current_user_id, get_request_db
    from core.database import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api")

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_request_db] = lambda: db

    return app


# ── _serialize_row ───────────────────────────────────────


class TestSerializeRow:
    def test_datetime_to_string(self):
        from datetime import datetime
        row = {"first_seen_at": datetime(2026, 4, 12), "last_seen_at": "already-string", "resolved_at": None}
        result = _serialize_row(row)
        assert isinstance(result["first_seen_at"], str)
        assert result["last_seen_at"] == "already-string"
        assert result["resolved_at"] is None

    def test_org_id_to_string(self):
        from uuid import UUID
        row = {"first_seen_at": None, "last_seen_at": None, "resolved_at": None,
               "org_id": UUID("eadc4c11-7e83-4279-a849-cfe0cbf6982b")}
        result = _serialize_row(row)
        assert result["org_id"] == "eadc4c11-7e83-4279-a849-cfe0cbf6982b"

    def test_none_org_id_stays_none(self):
        row = {"first_seen_at": None, "last_seen_at": None, "resolved_at": None, "org_id": None}
        result = _serialize_row(row)
        assert result["org_id"] is None


# ── 权限测试 ─────────────────────────────────────────────


class TestPermission:
    def test_non_admin_gets_403(self):
        db = FakeDB()
        db.enqueue(PermissionError("PLATFORM_ADMIN_REQUIRED"))
        app = _build_app(db, user_id=NORMAL_USER_ID)
        client = TestClient(app)

        resp = client.get("/api/error-monitor/list", params={"days": 7})
        assert resp.status_code == 403


# ── list 端点 ────────────────────────────────────────────


class TestListErrors:
    def test_list_returns_items(self):
        db = FakeDB()
        db.enqueue({"items": [FAKE_ERROR_ROW], "total": 1})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/error-monitor/list", params={"days": 7})

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["fingerprint"] == "abc123def456"
        assert db.calls == [("list_platform_error_logs", {
            "p_page": 1, "p_page_size": 20, "p_level": None,
            "p_is_critical": None, "p_is_resolved": None,
            "p_search": None, "p_days": 7,
        })]

    def test_list_empty(self):
        db = FakeDB()
        db.enqueue({"items": [], "total": 0})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/error-monitor/list", params={"days": 7})

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_list_pagination(self):
        db = FakeDB()
        db.enqueue({"items": [FAKE_ERROR_ROW], "total": 11})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/error-monitor/list", params={"page": 2, "page_size": 10, "days": 7})

        assert resp.status_code == 200
        assert resp.json()["page"] == 2


# ── stats 端点 ───────────────────────────────────────────


class TestGetStats:
    def test_stats_returns_counts(self):
        db = FakeDB()
        db.enqueue({
            "today_total": 1,
            "today_critical": 0,
            "week_total": 2,
            "unresolved": 1,
            "top_modules": [{"module": "services.kuaimai", "count": 5}],
        })

        app = _build_app(db)
        client = TestClient(app)
        resp = client.get("/api/error-monitor/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["today_total"] == 1
        assert body["today_critical"] == 0
        assert body["week_total"] == 2
        assert body["unresolved"] == 1
        assert body["top_modules"][0]["module"] == "services.kuaimai"


class TestSummarizeErrors:
    @patch("api.routes.error_monitor._call_ai_summary", new_callable=AsyncMock)
    def test_summary_uses_sanitized_capability_rows(self, summarize):
        summarize.return_value = "趋势稳定"
        db = FakeDB()
        db.enqueue([FAKE_ERROR_ROW])

        app = _build_app(db)
        client = TestClient(app)
        resp = client.post("/api/error-monitor/summarize", params={"days": 7})

        assert resp.status_code == 200
        assert resp.json() == {"summary": "趋势稳定"}
        assert db.calls == [
            ("list_platform_error_summary", {"p_days": 7}),
        ]


# ── resolve 端点 ─────────────────────────────────────────


class TestResolveError:
    def test_resolve_success(self):
        db = FakeDB()
        db.enqueue({"updated": 1})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.post("/api/error-monitor/1/resolve")

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_resolve_not_found(self):
        db = FakeDB()
        db.enqueue({"updated": 0})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.post("/api/error-monitor/999/resolve")

        assert resp.status_code == 404


# ── clear 端点 ───────────────────────────────────────────


class TestClearErrors:
    def test_clear_resolved(self):
        db = FakeDB()
        db.enqueue({"deleted": 2})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.delete("/api/error-monitor/clear", params={"resolved_only": True})

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2

    def test_clear_with_date(self):
        db = FakeDB()
        db.enqueue({"deleted": 0})

        app = _build_app(db)
        client = TestClient(app)
        resp = client.delete("/api/error-monitor/clear", params={
            "resolved_only": False,
            "before_date": "2026-04-01",
        })

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
        assert db.calls == [("clear_platform_errors", {
            "p_before_date": "2026-04-01",
            "p_resolved_only": False,
        })]
