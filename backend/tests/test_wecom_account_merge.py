"""
wecom_account_merge 单元测试

覆盖：merge_users 数据迁移、积分合并、映射更新、旧用户删除
"""

import sys
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom_account_merge import merge_users, _merge_credits, _add_login_method


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


class TestMergeUsers:
    """merge_users 完整流程测试"""

    @pytest.mark.asyncio
    async def test_migrates_all_tables(self):
        """迁移所有关联表数据"""
        db = _make_db_mock()

        # 设置 users 表返回积分
        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data={"credits": 0})
        )

        # 设置映射表查询返回空（无已有映射）
        mappings_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        (mappings_table.select.return_value
         .eq.return_value.eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[])

        await merge_users(
            db=db,
            keep_user_id="keep-uid",
            remove_user_id="remove-uid",
            wecom_userid="wecom_test",
            corp_id="ww_corp",
            nickname="测试用户",
        )

        # 验证迁移表的 update 被调用
        for table_name in ["conversations", "image_generations", "credits_history",
                           "tasks", "credit_transactions"]:
            table = db._table_mocks[table_name]
            table.update.assert_called()

        # 验证删除表被调用
        for table_name in ["user_subscriptions", "user_memory_settings"]:
            table = db._table_mocks[table_name]
            table.delete.assert_called()

        # 验证旧用户被删除
        users_table.delete.assert_called()

    @pytest.mark.asyncio
    async def test_creates_oauth_mapping_for_keep_user(self):
        """为保留用户创建 OAuth 映射"""
        db = _make_db_mock()
        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data={"credits": 0, "login_methods": ["phone"]})
        )

        mappings_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        # 查询已有映射返回空
        (mappings_table.select.return_value
         .eq.return_value.eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[])

        await merge_users(
            db=db, keep_user_id="keep", remove_user_id="remove",
            wecom_userid="wecom_1", corp_id="corp_1", nickname="nick",
        )

        # 验证 insert 被调用（创建新映射）
        mappings_table.insert.assert_called()


class TestMergeCredits:
    """_merge_credits 积分合并测试"""

    def test_transfers_credits(self):
        """转移积分到保留用户"""
        db = _make_db_mock()
        users_table = db._table_mocks.setdefault("users", MagicMock())
        credits_table = db._table_mocks.setdefault("credits_history", MagicMock())

        # 第一次调用：查询被删除用户积分
        # 第二次调用：查询保留用户积分
        users_table.select.return_value.eq.return_value.single.return_value.execute.side_effect = [
            MagicMock(data={"credits": 50}),   # remove_user
            MagicMock(data={"credits": 100}),  # keep_user
        ]

        _merge_credits(db, "keep-uid", "remove-uid")

        # 验证更新了保留用户积分
        users_table.update.assert_called()
        update_args = users_table.update.call_args[0][0]
        assert update_args["credits"] == 150  # 100 + 50

        # 验证记录了积分历史
        credits_table.insert.assert_called_once()
        insert_args = credits_table.insert.call_args[0][0]
        assert insert_args["change_amount"] == 50
        assert insert_args["balance_after"] == 150
        assert insert_args["change_type"] == "merge"

    def test_skips_zero_credits(self):
        """被删除用户积分为0时跳过"""
        db = _make_db_mock()
        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data={"credits": 0})
        )

        _merge_credits(db, "keep-uid", "remove-uid")

        users_table.update.assert_not_called()


class TestAddLoginMethod:
    """_add_login_method 测试"""

    def test_adds_new_method(self):
        """添加新方法到列表"""
        db = _make_db_mock()
        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data={"login_methods": ["phone"]})
        )

        _add_login_method(db, "uid-1", "wecom")

        users_table.update.assert_called_once()
        updated_methods = users_table.update.call_args[0][0]["login_methods"]
        assert "wecom" in updated_methods
        assert "phone" in updated_methods

    def test_skips_duplicate_method(self):
        """已存在的方法不重复添加"""
        db = _make_db_mock()
        users_table = db._table_mocks.setdefault("users", MagicMock())
        users_table.select.return_value.eq.return_value.single.return_value.execute.return_value = (
            MagicMock(data={"login_methods": ["phone", "wecom"]})
        )

        _add_login_method(db, "uid-1", "wecom")

        users_table.update.assert_not_called()
