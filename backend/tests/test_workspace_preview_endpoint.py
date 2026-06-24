"""
/files/workspace/preview 端点测试

覆盖:
- inline (默认) - iframe 预览,Content-Disposition: inline
- attachment - 强制下载, Content-Disposition: attachment(用于 PDF 等)
- url 参数 - 从 OSS CDN URL 反推 NAS 路径,resolve_safe_path 校验用户归属
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from urllib.parse import quote

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest


def _build_app(workspace: Path):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from api.deps import OrgContext, get_org_context
    from api.routes.file_browse import router
    from core.exceptions import AppException

    app = FastAPI()
    app.include_router(router, prefix="/files")

    # 注册 AppException → HTTP status_code 处理(模拟 main.py 的全局 handler)
    @app.exception_handler(AppException)
    async def _app_exc_handler(_request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    async def _fake_ctx() -> OrgContext:
        return OrgContext(user_id="u-test", org_id=None)

    app.dependency_overrides[get_org_context] = _fake_ctx
    return app


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """构造散客 workspace,内置 PDF + image 文件"""
    user_hash = hashlib.md5("u-test".encode()).hexdigest()[:8]
    user_root = tmp_path / "personal" / user_hash
    (user_root / "下载" / "AI图片").mkdir(parents=True)
    (user_root / "下载" / "AI图片" / "IMG_001.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png" * 10)
    (user_root / "上传").mkdir(parents=True)
    (user_root / "上传" / "report.pdf").write_bytes(b"%PDF-1.4 fake-pdf-content" * 20)
    return tmp_path


class TestPreviewEndpoint:
    def test_default_inline_for_iframe_preview(self, workspace: Path, monkeypatch) -> None:
        """默认 disposition=inline,Content-Disposition: inline(用于 iframe PDF 预览)"""
        from fastapi.testclient import TestClient
        from core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "file_workspace_enabled", True)
        monkeypatch.setattr(settings, "file_workspace_root", str(workspace))

        client = TestClient(_build_app(workspace))
        resp = client.get("/files/workspace/preview?path=上传/report.pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        cd = resp.headers["content-disposition"]
        assert cd.startswith("inline"), f"expected inline, got: {cd}"

    def test_attachment_forces_download(self, workspace: Path, monkeypatch) -> None:
        """disposition=attachment 让浏览器强制下载(PDF 下载场景)"""
        from fastapi.testclient import TestClient
        from core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "file_workspace_enabled", True)
        monkeypatch.setattr(settings, "file_workspace_root", str(workspace))

        client = TestClient(_build_app(workspace))
        resp = client.get(
            "/files/workspace/preview?path=上传/report.pdf&disposition=attachment"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        cd = resp.headers["content-disposition"]
        assert cd.startswith("attachment"), f"expected attachment, got: {cd}"
        assert "report.pdf" in cd

    def test_url_parameter_accepts_cdn_url(self, workspace: Path, monkeypatch) -> None:
        """url 参数:OSS CDN URL → 解析 object_key → resolve_safe_path 校验"""
        from fastapi.testclient import TestClient
        from core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "file_workspace_enabled", True)
        monkeypatch.setattr(settings, "file_workspace_root", str(workspace))

        user_hash = hashlib.md5("u-test".encode()).hexdigest()[:8]
        object_key = f"personal/{user_hash}/下载/AI图片/IMG_001.png"
        cdn_url = f"https://cdn.example.com/workspace/{quote(object_key, safe='/')}"

        client = TestClient(_build_app(workspace))
        resp = client.get(
            f"/files/workspace/preview?url={quote(cdn_url, safe='')}&disposition=attachment"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.headers["content-disposition"].startswith("attachment")
        assert resp.content.startswith(b"\x89PNG")

    def test_url_outside_workspace_prefix_rejected(self, workspace: Path, monkeypatch) -> None:
        """非 workspace/ 前缀的 OSS URL 拒绝"""
        from fastapi.testclient import TestClient
        from core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "file_workspace_enabled", True)
        monkeypatch.setattr(settings, "file_workspace_root", str(workspace))

        client = TestClient(_build_app(workspace))
        evil_url = "https://cdn.example.com/images/foo.png"
        resp = client.get(
            f"/files/workspace/preview?url={quote(evil_url, safe='')}"
        )
        assert resp.status_code == 400

    def test_missing_both_params_rejected(self, workspace: Path, monkeypatch) -> None:
        """path 和 url 都不传 → 400"""
        from fastapi.testclient import TestClient
        from core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "file_workspace_enabled", True)
        monkeypatch.setattr(settings, "file_workspace_root", str(workspace))

        client = TestClient(_build_app(workspace))
        resp = client.get("/files/workspace/preview")
        assert resp.status_code == 400
