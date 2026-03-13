"""
WecomUserMappingService 单元测试

覆盖：get_or_create_user（已有映射/首次创建/DB 异常）、
      _create_wecom_user（用户+映射+积分三步创建）、
      update_nickname
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from services.wecom.user_mapping_service import WecomUserMappingService


def _make_db_mock():
    """按表名隔离的 DB mock"""
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = MagicMock(name=f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)
    db._table_mocks = table_mocks
    return db


class TestGetOrCreateUser:
    """get_or_create_user 查找或创建"""

    @pytest.mark.asyncio
    async def test_returns_existing_user(self):
        """已有映射 → 直接返回 user_id"""
        db = _make_db_mock()
        mapping_mock = db._table_mocks.setdefault(
            "wecom_user_mappings", MagicMock()
        )
        # 模拟查询返回已有映射
        chain = mapping_mock.select.return_value.eq.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = MagicMock(
            data=[{"user_id": "existing-uuid-123", "wecom_nickname": "张三"}]
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user(
                wecom_userid="zhangsan", corp_id="corp1"
            )

        assert user_id == "existing-uuid-123"

    @pytest.mark.asyncio
    async def test_creates_new_user_on_first_message(self):
        """首次消息 → 创建系统用户+映射+积分"""
        db = _make_db_mock()

        # 映射表查询返回空
        mapping_mock = db._table_mocks.setdefault(
            "wecom_user_mappings", MagicMock()
        )
        chain = mapping_mock.select.return_value.eq.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=[])

        # 用户表插入返回新 user_id
        users_mock = db._table_mocks.setdefault("users", MagicMock())
        users_mock.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "new-uuid-456"}]
        )

        # 积分表和映射表插入（不关心返回值）
        credits_mock = db._table_mocks.setdefault("credits_history", MagicMock())
        credits_mock.insert.return_value.execute.return_value = MagicMock()
        mapping_mock.insert.return_value.execute.return_value = MagicMock()

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user(
                wecom_userid="lisi", corp_id="corp2", channel="app",
            )

        assert user_id == "new-uuid-456"

        # 验证用户创建参数
        user_data = users_mock.insert.call_args[0][0]
        assert user_data["created_by"] == "wecom"
        assert user_data["credits"] == 100
        assert user_data["status"] == "active"

        # 验证积分记录
        credits_data = credits_mock.insert.call_args[0][0]
        assert credits_data["change_amount"] == 100
        assert credits_data["change_type"] == "register_gift"

        # 验证映射创建
        mapping_data = mapping_mock.insert.call_args[0][0]
        assert mapping_data["wecom_userid"] == "lisi"
        assert mapping_data["corp_id"] == "corp2"
        assert mapping_data["channel"] == "app"

    @pytest.mark.asyncio
    async def test_custom_nickname(self):
        """传入 nickname → 使用自定义昵称"""
        db = _make_db_mock()

        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        chain = mapping_mock.select.return_value.eq.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=[])

        users_mock = db._table_mocks.setdefault("users", MagicMock())
        users_mock.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "u1"}]
        )
        db._table_mocks.setdefault("credits_history", MagicMock()).insert.return_value.execute.return_value = MagicMock()
        mapping_mock.insert.return_value.execute.return_value = MagicMock()

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user(
                wecom_userid="ww001", corp_id="corp",
                nickname="自定义昵称",
            )

        user_data = users_mock.insert.call_args[0][0]
        assert user_data["nickname"] == "自定义昵称"

    @pytest.mark.asyncio
    async def test_default_nickname_when_none(self):
        """未传 nickname → 使用默认格式"""
        db = _make_db_mock()

        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        chain = mapping_mock.select.return_value.eq.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=[])

        users_mock = db._table_mocks.setdefault("users", MagicMock())
        users_mock.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "u2"}]
        )
        db._table_mocks.setdefault("credits_history", MagicMock()).insert.return_value.execute.return_value = MagicMock()
        mapping_mock.insert.return_value.execute.return_value = MagicMock()

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user(
                wecom_userid="abcdefgh_long_id", corp_id="corp",
            )

        user_data = users_mock.insert.call_args[0][0]
        assert user_data["nickname"] == "企微用户_abcdefgh"

    @pytest.mark.asyncio
    async def test_db_query_error_returns_none_mapping(self):
        """DB 查询异常 → _find_mapping 返回 None → 走创建流程"""
        db = _make_db_mock()

        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        # 查询抛异常
        mapping_mock.select.side_effect = RuntimeError("DB connection lost")

        users_mock = db._table_mocks.setdefault("users", MagicMock())
        users_mock.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "u3"}]
        )
        db._table_mocks.setdefault("credits_history", MagicMock()).insert.return_value.execute.return_value = MagicMock()

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            # _find_mapping 异常返回 None，然后 _create_wecom_user 被调用
            # 但 _create_wecom_user 也需要 mapping_mock.insert，
            # 这里 select 有 side_effect 但 insert 没有
            mapping_mock.select.side_effect = RuntimeError("DB error")
            # 重置 insert 使其可用
            mapping_mock.insert = MagicMock()
            mapping_mock.insert.return_value.execute.return_value = MagicMock()

            user_id = await svc.get_or_create_user(
                wecom_userid="err_user", corp_id="corp",
            )

        assert user_id == "u3"

    @pytest.mark.asyncio
    async def test_create_user_failure_raises(self):
        """用户创建失败（insert 返回空）→ RuntimeError"""
        db = _make_db_mock()

        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        chain = mapping_mock.select.return_value.eq.return_value.eq.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=[])

        users_mock = db._table_mocks.setdefault("users", MagicMock())
        users_mock.insert.return_value.execute.return_value = MagicMock(data=[])

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            with pytest.raises(RuntimeError, match="Failed to create system user"):
                await svc.get_or_create_user(
                    wecom_userid="fail_user", corp_id="corp",
                )


class TestUpdateNickname:
    """update_nickname 昵称更新"""

    @pytest.mark.asyncio
    async def test_update_success(self):
        """正常更新昵称"""
        db = _make_db_mock()
        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.update_nickname("ww001", "corp1", "新昵称")

        mapping_mock.update.assert_called_once_with({"wecom_nickname": "新昵称"})

    @pytest.mark.asyncio
    async def test_update_error_no_raise(self):
        """更新失败 → 记录日志但不抛出"""
        db = _make_db_mock()
        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mapping_mock.update.side_effect = RuntimeError("DB error")

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            # 不应抛出异常
            await svc.update_nickname("ww001", "corp1", "新昵称")
