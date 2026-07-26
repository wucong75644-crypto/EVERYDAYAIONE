"""
get_scoped_db 依赖注入测试

验证：
- 已验证 OrgContext → DatabaseScope + OrgScopedDB
- request ID 进入事务身份
- 散客保持 org_id=None
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"
USER_ID = "f566f6cc-3e7a-4383-befe-42c05fbfbff8"


class TestGetScopedDb:
    """get_scoped_db 复用已校验的 OrgContext"""

    @pytest.mark.asyncio
    async def test_with_valid_org_id(self):
        """有效 X-Org-Id → 返回 OrgScopedDB(db, org_id)"""
        from api.deps import OrgContext, get_scoped_db
        from core.org_scoped_db import OrgScopedDB

        request = MagicMock()
        request.headers.get.return_value = "request-1"
        db = MagicMock()

        result = await get_scoped_db(
            request,
            org_ctx=OrgContext(user_id=USER_ID, org_id=ORG_ID),
            db=db,
        )
        assert isinstance(result, OrgScopedDB)
        assert result.org_id == ORG_ID
        assert result._db.scope.settings == (
            USER_ID, ORG_ID, "runtime", "request-1",
        )

    @pytest.mark.asyncio
    async def test_without_header(self):
        """无 X-Org-Id → 返回 OrgScopedDB(db, None)"""
        from api.deps import OrgContext, get_scoped_db
        from core.org_scoped_db import OrgScopedDB

        request = MagicMock()
        request.headers.get.return_value = ""
        db = MagicMock()

        result = await get_scoped_db(
            request,
            org_ctx=OrgContext(user_id=USER_ID),
            db=db,
        )
        assert isinstance(result, OrgScopedDB)
        assert result.org_id is None
        assert result._db.scope.settings == (USER_ID, "", "runtime", "")

    @pytest.mark.asyncio
    async def test_reuses_base_client_pool(self):
        """事务包装器继续复用原始数据库连接池。"""
        from api.deps import OrgContext, get_scoped_db

        request = MagicMock()
        request.headers.get.return_value = ""
        db = MagicMock()

        result = await get_scoped_db(
            request,
            org_ctx=OrgContext(user_id=USER_ID),
            db=db,
        )

        assert result.pool is db.pool

    @pytest.mark.asyncio
    async def test_invalid_actor_id_fails_closed(self):
        """无效可信身份不会降级为无 Scope 查询。"""
        from api.deps import OrgContext, get_scoped_db

        request = MagicMock()
        request.headers.get.return_value = ""

        with pytest.raises(ValueError):
            await get_scoped_db(
                request,
                org_ctx=OrgContext(user_id="invalid"),
                db=MagicMock(),
            )


class TestGetAuthService:
    """公开认证入口固定使用无 actor/org 的 runtime Scope。"""

    def test_builds_authentication_scope(self):
        from api.routes.auth import get_auth_service

        request = MagicMock()
        request.headers.get.return_value = "auth-request-1"
        db = MagicMock()

        service = get_auth_service(request, db)

        assert service.db.scope.settings == (
            "", "", "runtime", "auth-request-1",
        )
        assert service.db.pool is db.pool

    def test_rejects_unbounded_request_id(self):
        from api.routes.auth import get_auth_service

        request = MagicMock()
        request.headers.get.return_value = "x" * 129

        with pytest.raises(ValueError, match="request_id"):
            get_auth_service(request, MagicMock())


class TestRequestDatabase:
    def test_webhook_uses_actorless_worker_scope(self):
        from api.deps import get_webhook_worker_db

        db = MagicMock()
        result = get_webhook_worker_db(db)

        assert result.scope.settings == (
            "", "", "worker", "provider-webhook",
        )
        assert result.pool is db.pool

    @pytest.mark.asyncio
    async def test_current_user_identity_ignores_untrusted_org_header(self):
        from api.deps import get_current_user

        request = MagicMock()
        scoped = MagicMock()
        scoped.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "id": USER_ID,
        }
        with patch(
            "api.deps._runtime_scoped_db", return_value=scoped,
        ) as build_scope:
            result = await get_current_user(request, USER_ID, MagicMock())

        assert result == {"id": USER_ID}
        build_scope.assert_called_once()
        assert build_scope.call_args.kwargs["include_org"] is False

    @pytest.mark.asyncio
    async def test_binds_authenticated_actor_and_org(self):
        from api.deps import get_request_db

        request = MagicMock()
        request.headers.get.side_effect = lambda name, default="": {
            "X-Org-Id": ORG_ID,
            "X-Request-Id": "request-2",
        }.get(name, default)
        db = MagicMock()

        result = await get_request_db(request, USER_ID, db)

        assert result.scope.settings == (
            USER_ID, ORG_ID, "runtime", "request-2",
        )
        assert result.pool is db.pool

    @pytest.mark.asyncio
    async def test_public_request_uses_actorless_runtime_scope(self):
        from api.deps import get_request_db

        request = MagicMock()
        request.headers.get.side_effect = lambda _name, default="": default
        result = await get_request_db(request, None, MagicMock())

        assert result.scope.settings == ("", "", "runtime", "")

    @pytest.mark.asyncio
    async def test_invalid_org_header_fails_before_database_access(self):
        from fastapi import HTTPException
        from api.deps import get_request_db

        request = MagicMock()
        request.headers.get.side_effect = lambda name, default="": (
            "invalid" if name == "X-Org-Id" else default
        )

        with pytest.raises(HTTPException) as error:
            await get_request_db(request, USER_ID, MagicMock())

        assert error.value.status_code == 400
