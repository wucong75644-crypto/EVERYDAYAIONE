"""
main.py 全局异常 handler 测试

覆盖：RowNotFoundError → 404、ConfigurationError → 500(AppException handler)、
      未知异常 → 500 INTERNAL_ERROR
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import ConfigurationError
from core.local_db import RowNotFoundError
from main import register_exception_handlers


def _build_app() -> FastAPI:
    """构建带全局 handler 的测试 app"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/trigger-row-not-found")
    async def _row_not_found():
        raise RowNotFoundError("messages")

    @app.get("/trigger-config-error")
    async def _config_error():
        raise ConfigurationError("KIE")

    @app.get("/trigger-unknown")
    async def _unknown():
        raise RuntimeError("unexpected bug")

    return app


class TestRowNotFoundHandler:
    """RowNotFoundError → 404"""

    def test_returns_404(self):
        client = TestClient(_build_app())
        resp = client.get("/trigger-row-not-found")
        assert resp.status_code == 404

    def test_error_code(self):
        client = TestClient(_build_app())
        resp = client.get("/trigger-row-not-found")
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"

    def test_message_is_generic(self):
        client = TestClient(_build_app())
        resp = client.get("/trigger-row-not-found")
        body = resp.json()
        assert body["error"]["message"] == "请求的资源不存在"

    def test_no_table_name_leaked(self):
        """响应中不暴露内部表名"""
        client = TestClient(_build_app())
        resp = client.get("/trigger-row-not-found")
        body = resp.json()
        assert "messages" not in str(body)
        assert body["error"]["details"] == {}


class TestConfigurationErrorHandler:
    """ConfigurationError → 500 (走 AppException handler)"""

    def test_returns_500(self):
        client = TestClient(_build_app())
        resp = client.get("/trigger-config-error")
        assert resp.status_code == 500

    def test_error_code(self):
        client = TestClient(_build_app())
        resp = client.get("/trigger-config-error")
        body = resp.json()
        assert body["error"]["code"] == "SERVICE_NOT_CONFIGURED"

    def test_user_friendly_message(self):
        client = TestClient(_build_app())
        resp = client.get("/trigger-config-error")
        body = resp.json()
        assert "暂未开通" in body["error"]["message"]

    def test_no_api_key_info_leaked(self):
        """不暴露 API Key 相关细节"""
        client = TestClient(_build_app())
        resp = client.get("/trigger-config-error")
        body = resp.json()
        assert "API Key" not in str(body)


class TestGenericExceptionHandler:
    """未知异常 → 500 INTERNAL_ERROR"""

    def test_returns_500(self):
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/trigger-unknown")
        assert resp.status_code == 500

    def test_generic_message(self):
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/trigger-unknown")
        body = resp.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "服务器内部错误" in body["error"]["message"]

    def test_no_stack_trace_leaked(self):
        """不暴露堆栈信息"""
        client = TestClient(_build_app(), raise_server_exceptions=False)
        resp = client.get("/trigger-unknown")
        body = resp.json()
        assert "unexpected bug" not in str(body)
        assert "Traceback" not in str(body)
