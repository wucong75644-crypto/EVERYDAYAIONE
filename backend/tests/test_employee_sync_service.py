"""
EmployeeSyncService 单元测试

覆盖：部门同步、员工同步、离职标记、API 错误处理
"""

import sys
from pathlib import Path
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom.employee_sync_service import EmployeeSyncService


def _make_db_mock():
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)
    db._table_mocks = table_mocks
    return db


def _make_settings():
    mock = MagicMock()
    mock.wecom_corp_id = "ww_test_corp"
    return mock


class TestFetchDepartments:
    """_fetch_departments 测试"""

    @pytest.mark.asyncio
    async def test_success(self):
        db = _make_db_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "errcode": 0,
            "department": [
                {"id": 1, "name": "根部门", "parentid": 0},
                {"id": 2, "name": "技术部", "parentid": 1},
            ],
        }

        with patch("services.wecom.employee_sync_service.get_settings", return_value=_make_settings()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = EmployeeSyncService(db)
            result = await svc._fetch_departments("token_abc")

        assert len(result) == 2
        assert result[0]["name"] == "根部门"

    @pytest.mark.asyncio
    async def test_api_error_retries(self):
        db = _make_db_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 60011, "errmsg": "no permission"}

        with patch("services.wecom.employee_sync_service.get_settings", return_value=_make_settings()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = EmployeeSyncService(db)
            result = await svc._fetch_departments("token_abc")

        assert result is None


class TestSyncAll:
    """sync_all 完整流程测试"""

    @pytest.mark.asyncio
    async def test_full_sync(self):
        db = _make_db_mock()
        dept_table = db._table_mocks.setdefault("wecom_departments", MagicMock())
        emp_table = db._table_mocks.setdefault("wecom_employees", MagicMock())

        # 部门查询返回空（新建）
        dept_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        dept_table.insert.return_value.execute.return_value = MagicMock()

        # 员工查询返回空（新建）
        emp_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        emp_table.insert.return_value.execute.return_value = MagicMock()

        # 离职检查：无在职员工
        emp_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[])
        )

        with patch("services.wecom.employee_sync_service.get_settings", return_value=_make_settings()), \
             patch("services.wecom.employee_sync_service.get_access_token", return_value="token_ok"):

            svc = EmployeeSyncService(db)
            svc._fetch_departments = AsyncMock(return_value=[
                {"id": 1, "name": "总部", "parentid": 0},
            ])
            svc._fetch_department_users = AsyncMock(return_value=[
                {"userid": "zhangsan", "name": "张三", "department": [1]},
                {"userid": "lisi", "name": "李四", "department": [1]},
            ])

            result = await svc.sync_all()

        assert result["departments"] == 1
        assert result["employees"] == 2
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_no_access_token(self):
        db = _make_db_mock()

        with patch("services.wecom.employee_sync_service.get_settings", return_value=_make_settings()), \
             patch("services.wecom.employee_sync_service.get_access_token", return_value=None):
            svc = EmployeeSyncService(db)
            result = await svc.sync_all()

        assert "access_token" in result["errors"][0]


class TestMarkDeparted:
    """_mark_departed 测试"""

    @pytest.mark.asyncio
    async def test_marks_absent_employees(self):
        db = _make_db_mock()
        emp_table = db._table_mocks.setdefault("wecom_employees", MagicMock())

        # DB 有 3 人在职
        emp_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[
                {"id": "id-1", "wecom_userid": "zhangsan"},
                {"id": "id-2", "wecom_userid": "lisi"},
                {"id": "id-3", "wecom_userid": "wangwu"},
            ])
        )
        emp_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("services.wecom.employee_sync_service.get_settings", return_value=_make_settings()):
            svc = EmployeeSyncService(db)
            # API 只返回 zhangsan 和 lisi，wangwu 应标记离职
            count = await svc._mark_departed({"zhangsan", "lisi"})

        assert count == 1
        emp_table.update.assert_called_once()
