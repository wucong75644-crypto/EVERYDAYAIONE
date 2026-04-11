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


def _make_chain_mock(name: str = "chain") -> MagicMock:
    """创建支持链式调用的 mock（select/eq/is_/like/order/limit/insert/update 都返回自身）

    注意：execute 不在链中，需要在测试中单独设置 .execute.return_value
    """
    chain = MagicMock(name=name)
    for method in ("select", "eq", "is_", "like", "order", "limit", "insert", "update"):
        getattr(chain, method).return_value = chain
    # execute 默认返回空数据（测试中可覆盖）
    chain.execute.return_value = MagicMock(data=[])
    return chain


def _make_db_mock(*table_names: str):
    """按表名隔离的 DB mock。预创建常用表的链式 mock。"""
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}

    # 预创建常用表
    for name in ("wecom_user_mappings", "users", "credits_history",
                 "org_members", *table_names):
        table_mocks[name] = _make_chain_mock(f"table({name})")

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = _make_chain_mock(f"table({name})")
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
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(
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

        # 映射表查询返回空（_find_mapping 走 is_("org_id","null") 分支）
        mapping_mock = db._table_mocks["wecom_user_mappings"]
        mapping_mock.execute.return_value = MagicMock(data=[])

        # 用户表插入返回新 user_id
        users_mock = db._table_mocks["users"]
        users_mock.execute.return_value = MagicMock(data=[{"id": "new-uuid-456"}])

        # 积分表插入（不关心返回值）
        credits_mock = db._table_mocks["credits_history"]

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user(
                wecom_userid="lisi", corp_id="corp2", channel="app",
            )

        assert user_id == "new-uuid-456"

    @pytest.mark.asyncio
    async def test_custom_nickname(self):
        """传入 nickname → 使用自定义昵称"""
        db = _make_db_mock()

        # 映射查询返回空 → 走创建流程
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        # 用户创建返回 user_id
        db._table_mocks["users"].execute.return_value = MagicMock(data=[{"id": "u1"}])

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user(
                wecom_userid="ww001", corp_id="corp",
                nickname="自定义昵称",
            )

        users_mock = db._table_mocks["users"]
        user_data = users_mock.insert.call_args[0][0]
        assert user_data["nickname"] == "自定义昵称"

    @pytest.mark.asyncio
    async def test_default_nickname_when_none(self):
        """未传 nickname → 使用默认格式"""
        db = _make_db_mock()

        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._table_mocks["users"].execute.return_value = MagicMock(data=[{"id": "u2"}])

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user(
                wecom_userid="abcdefgh_long_id", corp_id="corp",
            )

        user_data = db._table_mocks["users"].insert.call_args[0][0]
        assert user_data["nickname"] == "企微用户_abcdefgh"

    @pytest.mark.asyncio
    async def test_uses_wecom_user_get_real_name_when_available(self):
        """未传 nickname 但企微 user/get 拿到真名 → 用真名而不是兜底

        Why: 修复 2026-04 之前所有企微用户名为 '企微用户_xxxxxxxx' 的根因。
        实现方式: 按需调 cgi-bin/user/get（不依赖全量通讯录同步）。
        """
        db = _make_db_mock()

        # 映射查询返回空 → 走创建流程
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        # 用户创建返回 user_id
        db._table_mocks["users"].execute.return_value = MagicMock(data=[{"id": "u3"}])

        async def fake_fetch(d, oid, uid, **kw):
            return "王五"

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()), \
             patch(
                 "services.wecom.wecom_contact_api.fetch_wecom_real_name",
                 new=fake_fetch,
             ):
            await svc.get_or_create_user(
                wecom_userid="wangwu_userid", corp_id="corp",
                org_id="org-1",
            )

        user_data = db._table_mocks["users"].insert.call_args[0][0]
        assert user_data["nickname"] == "王五"

        # 同时映射表也应写入真名
        mapping_data = db._table_mocks["wecom_user_mappings"].insert.call_args[0][0]
        assert mapping_data["wecom_nickname"] == "王五"

    @pytest.mark.asyncio
    async def test_falls_back_when_user_get_returns_none(self):
        """企微 user/get 拿不到名（如不在可见范围）→ 兜底到 '企微用户_xxx'"""
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._table_mocks["users"].execute.return_value = MagicMock(data=[{"id": "u4"}])

        async def fake_fetch(d, oid, uid, **kw):
            return None  # 模拟 API 失败 / 不可见

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()), \
             patch(
                 "services.wecom.wecom_contact_api.fetch_wecom_real_name",
                 new=fake_fetch,
             ):
            await svc.get_or_create_user(
                wecom_userid="abcdefgh_long", corp_id="corp", org_id="org-1",
            )

        user_data = db._table_mocks["users"].insert.call_args[0][0]
        assert user_data["nickname"] == "企微用户_abcdefgh"

    @pytest.mark.asyncio
    async def test_db_query_error_raises(self):
        """DB 查询异常 → _find_mapping 抛异常（不再静默创建重复用户）"""
        db = _make_db_mock()

        mapping_mock = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mapping_mock.select.side_effect = RuntimeError("DB error")

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            with pytest.raises(RuntimeError, match="DB error"):
                await svc.get_or_create_user(
                    wecom_userid="err_user", corp_id="corp",
                )

    @pytest.mark.asyncio
    async def test_create_user_failure_raises(self):
        """用户创建失败（insert 返回空）→ RuntimeError"""
        db = _make_db_mock()

        # 映射查询返回空 → 走创建流程
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        # 用户创建返回空 → 抛 RuntimeError
        db._table_mocks["users"].execute.return_value = MagicMock(data=[])

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
