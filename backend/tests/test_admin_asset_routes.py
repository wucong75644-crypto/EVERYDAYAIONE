"""管理员统一资产路由与旧端点删除测试。"""
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from core.db_scope import PostgresArray
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


class AssetZipDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.table_calls: list[str] = []
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name):
        self.table_calls.append(name)
        return super().table(name)

    def rpc(self, fn_name, params):
        self.rpc_calls.append((fn_name, params))
        return super().rpc(fn_name, params)


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

    def test_incomplete_rpc_result_is_rejected(self):
        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc([])
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
            ]},
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "资产下载授权结果无效"
        assert "user_asset_refs" not in db.table_calls
        assert "user_assets" not in db.table_calls

    @pytest.mark.parametrize("rpc_payload", [
        [
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": "https://cdn.example.com/photo1.jpg",
                "name": "photo1.jpg",
            },
        ],
        [
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": "https://cdn.example.com/photo1.jpg",
                "name": "photo1.jpg",
            },
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": "https://cdn.example.com/photo1.jpg",
                "name": "photo1.jpg",
            },
        ],
        [
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": "https://cdn.example.com/photo1.jpg",
                "name": "photo1.jpg",
            },
            {
                "id": "00000000-0000-0000-0000-000000000099",
                "download_url": "https://cdn.example.com/extra.jpg",
                "name": "extra.jpg",
            },
        ],
        [
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": 123,
                "name": "photo1.jpg",
            },
            {
                "id": "00000000-0000-0000-0000-000000000012",
                "download_url": "https://cdn.example.com/photo2.jpg",
                "name": "photo2.jpg",
            },
        ],
    ])
    def test_malformed_rpc_collection_fails_closed(self, rpc_payload):
        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc(rpc_payload)
        app = _build_app(db)
        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
                "00000000-0000-0000-0000-000000000012",
            ]},
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "资产下载授权结果无效"

    def test_duplicate_request_is_rejected_before_rpc(self):
        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        app = _build_app(db)
        asset_id = "00000000-0000-0000-0000-000000000011"

        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [asset_id, asset_id]},
        )

        assert resp.status_code == 422
        assert db.rpc_calls == []

    def test_rpc_error_is_sanitized(self):
        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.rpc = MagicMock(side_effect=RuntimeError("database internals"))
        app = _build_app(db)

        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
            ]},
        )

        assert resp.status_code == 500
        assert resp.json()["detail"] == "资产下载授权失败"
        assert "database internals" not in resp.text

    def test_invalid_download_url_is_rejected_after_rpc(self):
        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc([{
            "id": "00000000-0000-0000-0000-000000000011",
            "download_url": "http://127.0.0.1/private",
            "name": "private.jpg",
        }])
        app = _build_app(db)

        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
            ]},
        )

        assert resp.status_code == 422
        assert resp.json()["detail"] == "资产下载地址无效"

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

        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc([
            {
                "id": "00000000-0000-0000-0000-000000000012",
                "download_url": "https://cdn.example.com/photo2.jpg",
                "name": "photo2.jpg",
            },
            {
                "id": "00000000-0000-0000-0000-000000000011",
                "download_url": "https://cdn.example.com/photo1.jpg",
                "name": "photo1.jpg",
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
        assert db.rpc_calls == [(
            "resolve_platform_admin_user_assets_download",
            {
                "p_actor_user_id": TARGET_USER_ID,
                "p_asset_ids": PostgresArray([
                    UUID("00000000-0000-0000-0000-000000000011"),
                    UUID("00000000-0000-0000-0000-000000000012"),
                ]),
            },
        )]
        assert "user_asset_refs" not in db.table_calls
        assert "user_assets" not in db.table_calls
        assert mock_client.get.await_args_list == [
            call(
                "https://cdn.example.com/photo1.jpg",
                follow_redirects=False,
            ),
            call(
                "https://cdn.example.com/photo2.jpg",
                follow_redirects=False,
            ),
        ]

    @patch(
        "api.routes.admin_users_zip._is_allowed_asset_url",
        return_value=True,
    )
    @patch("api.routes.admin_users_zip.httpx.AsyncClient")
    def test_download_failure_remains_in_zip_error_manifest(
        self, mock_client_class, _allow_url,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.content = b""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_class.return_value.__aenter__.return_value = mock_client
        db = AssetZipDB()
        db.enqueue(data={"role": "super_admin"})
        db.enqueue(data={"id": TARGET_USER_ID})
        db.enqueue_rpc([{
            "id": "00000000-0000-0000-0000-000000000011",
            "download_url": "https://cdn.example.com/photo1.jpg",
            "name": "photo1.jpg",
        }])
        db.enqueue(data=[])
        app = _build_app(db)

        resp = TestClient(app).post(
            f"/api/admin/users/{TARGET_USER_ID}/assets/download-zip",
            json={"asset_ids": [
                "00000000-0000-0000-0000-000000000011",
            ]},
        )

        assert resp.status_code == 200
        assert b"_errors.txt" in resp.content
