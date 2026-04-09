"""
get_scoped_db 依赖注入测试

验证：
- 有 X-Org-Id header → OrgScopedDB(db, org_id)
- 无 header → OrgScopedDB(db, None)
- 无效 UUID → OrgScopedDB(db, None)（当散客处理）
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"


class TestGetScopedDb:
    """get_scoped_db 从 X-Org-Id header 提取 org_id"""

    @pytest.mark.asyncio
    async def test_with_valid_org_id(self):
        """有效 X-Org-Id → 返回 OrgScopedDB(db, org_id)"""
        from api.deps import get_scoped_db
        from core.org_scoped_db import OrgScopedDB

        request = MagicMock()
        request.headers.get.return_value = ORG_ID
        db = MagicMock()

        result = await get_scoped_db(request, user_id="u1", db=db)
        assert isinstance(result, OrgScopedDB)
        assert result.org_id == ORG_ID

    @pytest.mark.asyncio
    async def test_without_header(self):
        """无 X-Org-Id → 返回 OrgScopedDB(db, None)"""
        from api.deps import get_scoped_db
        from core.org_scoped_db import OrgScopedDB

        request = MagicMock()
        request.headers.get.return_value = None
        db = MagicMock()

        result = await get_scoped_db(request, user_id="u1", db=db)
        assert isinstance(result, OrgScopedDB)
        assert result.org_id is None

    @pytest.mark.asyncio
    async def test_with_invalid_uuid(self):
        """无效 UUID → 当散客处理，org_id=None"""
        from api.deps import get_scoped_db
        from core.org_scoped_db import OrgScopedDB

        request = MagicMock()
        request.headers.get.return_value = "not-a-uuid"
        db = MagicMock()

        result = await get_scoped_db(request, user_id="u1", db=db)
        assert isinstance(result, OrgScopedDB)
        assert result.org_id is None

    @pytest.mark.asyncio
    async def test_with_empty_string(self):
        """空字符串 → org_id=None"""
        from api.deps import get_scoped_db
        from core.org_scoped_db import OrgScopedDB

        request = MagicMock()
        request.headers.get.return_value = ""
        db = MagicMock()

        result = await get_scoped_db(request, user_id="u1", db=db)
        assert isinstance(result, OrgScopedDB)
        assert result.org_id is None
