"""admin_users API 路由测试

验证：
- 权限校验（非 super_admin → 403）
- list_users：分页 / 搜索 / 散客过滤
- summary：404
- recharge：delta=0 → 422 / 用户不存在 → 404 / 余额不足 → 422 / 正常 → success
- conversation messages：跨用户访问 → 403
- uploads：从 messages.content JSONB 解析附件
- generations：聚合 image_generations + tasks
- download_zip：空数组 / filenames 长度不匹配
- 工具函数：_safe_parse_content / _extract_upload_parts / _mask_phone
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from api.routes.admin_users_helpers import (
    _extract_upload_parts,
    _filename_from_url,
    _mask_phone,
    _safe_parse_content,
)


SUPER_ADMIN_ID = "admin-001"
NORMAL_USER_ID = "user-001"
TARGET_USER_ID = "target-user-001"


# ── Fake DB ──────────────────────────────────────────────


class FakeQueryBuilder:
    def __init__(self, data=None, count=None):
        if isinstance(data, list):
            self._data = data
        elif data is None:
            self._data = []
        else:
            self._data = [data]
        self._count = count if count is not None else len(self._data)
        self._is_single = False

    def select(self, *a, count=None, **kw):
        if count == "exact":
            self._count = len(self._data)
        return self

    def eq(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lt(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def ilike(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def or_(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def range(self, *a, **kw): return self
    def limit(self, *a, **kw): return self

    def maybe_single(self):
        self._is_single = True
        return self

    def single(self):
        self._is_single = True
        return self

    def update(self, data):
        for item in self._data:
            if isinstance(item, dict):
                item.update(data)
        return self

    def insert(self, *a, **kw): return self
    def delete(self): return self

    def execute(self):
        r = MagicMock()
        if self._is_single:
            r.data = self._data[0] if self._data else None
        else:
            r.data = self._data
        r.count = self._count
        return r


class FakeRPCBuilder:
    def __init__(self, data):
        self._data = data

    def execute(self):
        r = MagicMock()
        r.data = self._data
        return r


class FakeDB:
    def __init__(self):
        self._queue: list[FakeQueryBuilder] = []
        self._rpc_queue: list[FakeRPCBuilder] = []

    def enqueue(self, data=None, count=None):
        self._queue.append(FakeQueryBuilder(data, count))

    def enqueue_rpc(self, data):
        self._rpc_queue.append(FakeRPCBuilder(data))

    def table(self, name):
        if self._queue:
            return self._queue.pop(0)
        return FakeQueryBuilder([])

    def rpc(self, fn_name, params):
        if self._rpc_queue:
            return self._rpc_queue.pop(0)
        return FakeRPCBuilder({})


def _build_app(db, user_id: str = SUPER_ADMIN_ID) -> FastAPI:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from api.routes.admin_users import router
    from api.deps import get_current_user_id
    from core.database import get_db
    from core.exceptions import AppException

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = lambda: db

    @app.exception_handler(AppException)
    async def _handle_app_exc(_req: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    return app


# ── 工具函数 ─────────────────────────────────────────────


class TestUtils:
    def test_safe_parse_content_json_list(self):
        raw = json.dumps([{"type": "text", "text": "hi"}, {"type": "image", "url": "u"}])
        result = _safe_parse_content(raw)
        assert isinstance(result, list) and len(result) == 2

    def test_safe_parse_content_plain_text(self):
        assert _safe_parse_content("hello") == "hello"

    def test_safe_parse_content_none(self):
        assert _safe_parse_content(None) is None

    def test_safe_parse_content_malformed_json(self):
        raw = "[not valid json"
        assert _safe_parse_content(raw) == raw

    def test_safe_parse_content_already_list(self):
        raw = [{"type": "text"}]
        assert _safe_parse_content(raw) is raw

    def test_extract_upload_parts_image_url_obj(self):
        parts = [{"type": "image_url", "image_url": {"url": "https://x.com/a.jpg"}}]
        result = _extract_upload_parts(parts)
        assert len(result) == 1
        assert result[0]["url"] == "https://x.com/a.jpg"
        assert result[0]["type"] == "image"

    def test_extract_upload_parts_file(self):
        parts = [
            {"type": "file", "url": "https://x.com/doc.pdf", "name": "doc.pdf", "size": 1024},
            {"type": "text", "text": "hello"},
        ]
        result = _extract_upload_parts(parts)
        assert len(result) == 1
        assert result[0]["name"] == "doc.pdf"
        assert result[0]["size"] == 1024

    def test_extract_upload_parts_skips_invalid(self):
        parts = [None, "string", {"type": "file"}, {"type": "image", "url": ""}]
        assert _extract_upload_parts(parts) == []

    def test_extract_upload_parts_non_list(self):
        assert _extract_upload_parts(None) == []
        assert _extract_upload_parts("string") == []

    def test_filename_from_url(self):
        assert _filename_from_url("https://x.com/path/photo.jpg") == "photo.jpg"
        assert _filename_from_url("https://x.com/photo.jpg?token=abc") == "photo.jpg"
        assert _filename_from_url("not a url") == "not a url"
        assert _filename_from_url("") == "file"

    def test_mask_phone(self):
        assert _mask_phone("13812345678") == "138****5678"
        assert _mask_phone(None) is None
        assert _mask_phone("short") == "short"


# ── 权限测试 ─────────────────────────────────────────────


class TestPermission:
    def test_non_admin_403_on_list(self):
        db = FakeDB()
        db.enqueue(data={"role": "user"})  # _require_super_admin
        app = _build_app(db, user_id=NORMAL_USER_ID)
        resp = TestClient(app).get("/api/admin/users")
        assert resp.status_code == 403

    def test_no_user_403(self):
        db = FakeDB()
        db.enqueue(data=None)
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users")
        assert resp.status_code == 403

    def test_normal_admin_role_403(self):
        """role='admin' 也不算 super_admin"""
        db = FakeDB()
        db.enqueue(data={"role": "admin"})
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users")
        assert resp.status_code == 403


# ── 用户列表 ─────────────────────────────────────────────


class TestListUsers:
    def test_list_returns_items(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})  # 权限
        db.enqueue(data=[{
            "id": TARGET_USER_ID,
            "nickname": "测试用户",
            "phone": "13812345678",
            "avatar_url": None,
            "role": "user",
            "credits": 500,
            "status": "active",
            "current_org_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }])
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        # phone 被脱敏
        assert body["items"][0]["phone"] == "138****5678"
        # 散客 org_name = None
        assert body["items"][0]["org_name"] is None

    def test_list_with_org_name(self):
        """有 current_org_id 的用户应 batch join 出 org_name"""
        org_id = "11111111-1111-1111-1111-111111111111"
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[{
            "id": TARGET_USER_ID, "nickname": "员工 A",
            "phone": None, "avatar_url": None, "role": "user",
            "credits": 100, "status": "active",
            "current_org_id": org_id,
            "created_at": "2026-01-01T00:00:00+00:00",
        }])
        # batch query organizations
        db.enqueue(data=[{"id": org_id, "name": "蓝创科技"}])
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["org_name"] == "蓝创科技"

    def test_list_empty(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_search_by_phone(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users", params={"search": "13812345678"})
        assert resp.status_code == 200

    def test_list_search_by_nickname(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users", params={"search": "张三"})
        assert resp.status_code == 200

    def test_list_filter_solo(self):
        """org_id=none 过滤散客"""
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).get("/api/admin/users", params={"org_id": "none"})
        assert resp.status_code == 200


# ── summary ──────────────────────────────────────────────


class TestSummary:
    def test_summary_user_not_found(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})  # 权限
        db.enqueue(data=None)                      # users 查询返回 None
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/summary")
        assert resp.status_code == 404

    def test_summary_returns_aggregates(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={
            "id": TARGET_USER_ID,
            "nickname": "test",
            "phone": "13800000000",
            "avatar_url": None,
            "role": "user",
            "credits": 100,
            "status": "active",
            "current_org_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        })
        db.enqueue(data=[{"change_amount": -10}, {"change_amount": -25}])  # consumed
        db.enqueue(data=[{"id": "c1"}, {"id": "c2"}], count=2)              # conversation_count
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_consumed"] == 35
        assert body["conversation_count"] == 2
        assert body["phone"] == "138****0000"


# ── recharge ─────────────────────────────────────────────


class TestRecharge:
    def test_delta_zero_422(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/credits/recharge",
            json={"delta": 0, "reason": "test"},
        )
        assert resp.status_code == 422

    def test_user_not_found_404(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})  # 权限
        db.enqueue(data=None)                      # 用户校验
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/credits/recharge",
            json={"delta": 100, "reason": "test"},
        )
        assert resp.status_code == 404

    def test_recharge_success(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc({"success": True, "new_balance": 600, "delta": 100})
        db.enqueue(data=[])  # admin_action_logs insert
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/credits/recharge",
            json={"delta": 100, "reason": "活动补偿"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["new_balance"] == 600

    def test_insufficient_balance_422(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc({"success": False, "reason": "insufficient_balance"})
        db.enqueue(data={"credits": 50})  # get_balance fallback
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/credits/recharge",
            json={"delta": -100, "reason": "扣减测试"},
        )
        assert resp.status_code == 422


# ── 流水 ─────────────────────────────────────────────────


class TestCreditsHistory:
    def test_history_pagination(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[{
            "id": "h1", "user_id": TARGET_USER_ID,
            "change_amount": 100, "balance_after": 600,
            "change_type": "admin_adjust",
            "description": "test", "operator_id": SUPER_ADMIN_ID,
            "created_at": "2026-06-28T10:00:00+00:00",
        }])
        db.enqueue(data=[{"id": SUPER_ADMIN_ID, "nickname": "管理员A"}])  # operators
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/credits/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["operator_name"] == "管理员A"


# ── 对话 / 消息 ──────────────────────────────────────────


class TestConversationMessages:
    def test_conversation_not_found(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=None)
        app = _build_app(db)
        resp = TestClient(app).get(
            f"/api/admin/users/{TARGET_USER_ID}/conversations/conv-1/messages"
        )
        assert resp.status_code == 404

    def test_conversation_wrong_user_403(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"user_id": "other-user", "title": "X"})
        app = _build_app(db)
        resp = TestClient(app).get(
            f"/api/admin/users/{TARGET_USER_ID}/conversations/conv-1/messages"
        )
        assert resp.status_code == 403

    def test_conversation_messages_with_attachments(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"user_id": TARGET_USER_ID, "title": "test"})
        db.enqueue(data=[{
            "id": "m1", "conversation_id": "conv-1", "role": "user",
            "content": json.dumps([
                {"type": "text", "text": "hi"},
                {"type": "image", "url": "https://x.com/a.jpg"},
            ]),
            "image_url": None, "video_url": None,
            "credits_cost": 0, "is_error": False,
            "generation_params": None,
            "created_at": "2026-06-28T10:00:00+00:00",
        }])
        app = _build_app(db)
        resp = TestClient(app).get(
            f"/api/admin/users/{TARGET_USER_ID}/conversations/conv-1/messages"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        # 附件提取
        assert len(body["items"][0]["attachments"]) == 1
        assert body["items"][0]["attachments"][0]["url"] == "https://x.com/a.jpg"

    def test_assistant_message_extracts_generated_media(self):
        """AI 助手消息的生成图存在 content JSONB（非 image_url 字段），需要提取到 attachments"""
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"user_id": TARGET_USER_ID, "title": "test"})
        db.enqueue(data=[{
            "id": "m2", "conversation_id": "conv-1", "role": "assistant",
            "content": json.dumps([
                {"type": "image", "url": "https://cdn.../gen1.png", "name": "gen1.png",
                 "kind": "image", "width": 1024, "height": 1024},
                {"type": "image", "url": "https://cdn.../gen2.png", "name": "gen2.png"},
            ]),
            "image_url": None, "video_url": None,
            "credits_cost": 6, "is_error": False,
            "generation_params": {"type": "image", "model": "gpt-image-2"},
            "created_at": "2026-06-28T10:00:00+00:00",
        }])
        app = _build_app(db)
        resp = TestClient(app).get(
            f"/api/admin/users/{TARGET_USER_ID}/conversations/conv-1/messages"
        )
        assert resp.status_code == 200
        body = resp.json()
        # 关键：AI 消息也提取媒体到 attachments
        msg = body["items"][0]
        assert msg["role"] == "assistant"
        assert len(msg["attachments"]) == 2
        assert msg["attachments"][0]["url"] == "https://cdn.../gen1.png"
        assert msg["attachments"][1]["url"] == "https://cdn.../gen2.png"


# ── uploads ──────────────────────────────────────────────


class TestUploads:
    def test_uploads_no_conversations(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[])  # conversations 空
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/uploads")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_uploads_extracts_from_content(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[{"id": "conv-1"}])
        db.enqueue(data=[{
            "id": "m1",
            "conversation_id": "conv-1",
            "content": json.dumps([
                {"type": "image", "url": "https://x.com/a.jpg", "name": "a.jpg"},
                {"type": "file", "url": "https://x.com/doc.pdf", "name": "doc.pdf"},
            ]),
            "created_at": "2026-06-28T10:00:00+00:00",
        }])
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/uploads")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2


# ── generations ──────────────────────────────────────────


class TestGenerations:
    def test_generations_image_only(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[{
            "id": "img-1", "conversation_id": "conv-1",
            "prompt": "a cat", "negative_prompt": None,
            "image_size": "1024x1024",
            "image_url": "https://x.com/cat.jpg",
            "credits_cost": 5,
            "created_at": "2026-06-28T10:00:00+00:00",
            "model_id": "sd3.5",
        }])
        db.enqueue(data=[])  # videos 空
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/generations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["kind"] == "image"
        assert body["items"][0]["prompt"] == "a cat"

    def test_generations_kind_filter_image(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[{
            "id": "img-1", "conversation_id": None,
            "prompt": "x", "negative_prompt": None,
            "image_size": None,
            "image_url": "https://x.com/a.jpg",
            "credits_cost": 5,
            "created_at": "2026-06-28T10:00:00+00:00",
            "model_id": None,
        }])
        # kind=image 时只走 image_generations 查询
        app = _build_app(db)
        resp = TestClient(app).get(
            f"/api/admin/users/{TARGET_USER_ID}/generations",
            params={"kind": "image"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_generations_skips_empty_urls(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=[
            {"id": "1", "conversation_id": None, "prompt": "x", "negative_prompt": None,
             "image_size": None, "image_url": "", "credits_cost": 0,
             "created_at": "2026-06-28T10:00:00+00:00", "model_id": None},
            {"id": "2", "conversation_id": None, "prompt": "y", "negative_prompt": None,
             "image_size": None, "image_url": "https://x.com/y.jpg", "credits_cost": 0,
             "created_at": "2026-06-28T11:00:00+00:00", "model_id": None},
        ])
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).get(f"/api/admin/users/{TARGET_USER_ID}/generations")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


# ── download_zip ─────────────────────────────────────────


class TestDownloadZip:
    def test_filenames_length_mismatch_400(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/download_zip",
            json={"urls": ["https://x.com/a.jpg"], "filenames": ["a.jpg", "b.jpg"]},
        )
        assert resp.status_code == 400

    def test_user_not_found_404(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=None)
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/download_zip",
            json={"urls": ["https://x.com/a.jpg"]},
        )
        assert resp.status_code == 404

    def test_empty_urls_validation_error(self):
        """pydantic min_length=1 校验拦截"""
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/download_zip",
            json={"urls": []},
        )
        assert resp.status_code == 422

    @patch("api.routes.admin_users_zip.httpx.AsyncClient")
    def test_zip_success_with_mock_http(self, mock_client_class):
        """模拟 httpx 下载成功，验证返回 ZIP 流"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake-image-bytes"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue(data=[])  # admin_action_logs insert
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/download_zip",
            json={"urls": ["https://x.com/photo1.jpg", "https://x.com/photo2.jpg"]},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
