"""管理员统一资产路由与旧端点删除测试。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.test_admin_users_route import (
    TARGET_USER_ID,
    FakeDB,
    _build_app,
)


class TestRemovedAssetEndpoints:
    @pytest.mark.parametrize("suffix", ["uploads", "generations"])
    def test_removed_asset_endpoint_returns_404(self, suffix):
        app = _build_app(FakeDB())
        resp = TestClient(app).get(
            f"/api/admin/users/{TARGET_USER_ID}/{suffix}",
        )
        assert resp.status_code == 404


class TestDownloadZip:
    def test_asset_ids_must_be_uuid(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": ["not-a-uuid"]},
        )
        assert resp.status_code == 422

    def test_user_not_found_404(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data=None)
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
            ]},
        )
        assert resp.status_code == 404

    def test_empty_asset_ids_validation_error(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": []},
        )
        assert resp.status_code == 422

    def test_cross_user_asset_is_rejected(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
            ]},
        )
        assert resp.status_code == 403

    def test_partial_asset_ownership_is_rejected(self):
        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue(data=[{
            "asset_id": "00000000-0000-0000-0000-000000000011",
        }])
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
                "00000000-0000-0000-0000-000000000012",
            ]},
        )
        assert resp.status_code == 403

    @patch(
        "api.routes.admin_users_zip._is_allowed_asset_url",
        return_value=True,
    )
    @patch("api.routes.admin_users_zip.httpx.AsyncClient")
    def test_zip_success_with_mock_http(
        self, mock_client_class, _allow_url,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"fake-image-bytes"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        db = FakeDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue(data=[
            {"asset_id": "00000000-0000-0000-0000-000000000011"},
            {"asset_id": "00000000-0000-0000-0000-000000000012"},
        ])
        db.enqueue(data=[
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": "https://cdn.example.com/photo1.jpg",
                "name": "photo1.jpg",
            },
            {
                "id": "00000000-0000-0000-0000-000000000012",
                "download_url": "https://cdn.example.com/photo2.jpg",
                "name": "photo2.jpg",
            },
        ])
        db.enqueue(data=[])
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
                "00000000-0000-0000-0000-000000000012",
            ]},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
