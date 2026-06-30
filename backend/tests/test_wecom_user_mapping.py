"""
WecomUserMappingService 单元测试

覆盖（commit 116 重构后）：
- 快速路径：已有 mapping → 直接复用 + 刷新 last_login_at
- 慢速路径：mapping 不存在 → 调原子 RPC wecom_get_or_create_user
- RPC 并发输家：返回 is_new=False → 复用赢家的 user_id
- RPC 失败 → 抛 RuntimeError
- display_name 解析优先级（传入 nickname > 企微 user/get > 兜底）
- is_new + org_id → 自动加入企业成员（_ensure_org_member_safe）
- update_nickname 不变
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
    chain = MagicMock(name=name)
    for method in ("select", "eq", "is_", "like", "order", "limit", "insert", "update", "maybe_single"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    return chain


def _make_db_mock(*table_names: str):
    db = MagicMock()
    table_mocks: Dict[str, MagicMock] = {}
    for name in ("wecom_user_mappings", "users", "credits_history", "org_members", *table_names):
        table_mocks[name] = _make_chain_mock(f"table({name})")

    def _table(name: str):
        if name not in table_mocks:
            table_mocks[name] = _make_chain_mock(f"table({name})")
        return table_mocks[name]

    db.table = MagicMock(side_effect=_table)

    # rpc mock：链式 .execute()
    rpc_chain = MagicMock(name="rpc()")
    rpc_chain.execute.return_value = MagicMock(data={})
    db.rpc = MagicMock(return_value=rpc_chain)
    db._rpc_chain = rpc_chain

    db._table_mocks = table_mocks
    return db


def _rpc_params(db, fn_name: str):
    matches = [call for call in db.rpc.call_args_list if call[0][0] == fn_name]
    assert matches
    return matches[0][0][1]


class TestFastPath:
    """快速路径：已有 mapping 直接复用"""

    @pytest.mark.asyncio
    async def test_returns_existing_user(self):
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(
            data=[{"user_id": "existing-uuid-123", "wecom_nickname": "张三"}]
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user("zhangsan", "corp1")

        assert user_id == "existing-uuid-123"
        # 刷新 last_login_at
        db._table_mocks["users"].update.assert_called_once()
        update_payload = db._table_mocks["users"].update.call_args[0][0]
        assert "last_login_at" in update_payload
        # 不应该调创建用户 RPC；只允许记录活跃事件
        db.rpc.assert_called_once()
        assert db.rpc.call_args[0][0] == "record_user_activity"

    @pytest.mark.asyncio
    async def test_refresh_last_login_failure_no_raise(self):
        """刷新失败不阻断业务，仍返回 user_id"""
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(
            data=[{"user_id": "u-x"}]
        )
        db._table_mocks["users"].update.side_effect = RuntimeError("DB write down")

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user("z", "c")

        assert user_id == "u-x"


class TestSlowPathRPC:
    """慢路径：mapping 不存在 → 调 wecom_get_or_create_user RPC"""

    @pytest.mark.asyncio
    async def test_creates_new_user_via_rpc(self):
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "new-uuid-456", "is_new": True}
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user(
                "lisi", "corp2", channel="app",
            )

        assert user_id == "new-uuid-456"

        # 验证创建用户 RPC 调用参数正确（活跃事件会额外调用 record_user_activity）
        create_calls = [
            call for call in db.rpc.call_args_list
            if call[0][0] == "wecom_get_or_create_user"
        ]
        assert len(create_calls) == 1
        rpc_call = create_calls[0]
        assert rpc_call[0][0] == "wecom_get_or_create_user"
        params = rpc_call[0][1]
        assert params["p_wecom_userid"] == "lisi"
        assert params["p_corp_id"] == "corp2"
        assert params["p_channel"] == "app"

    @pytest.mark.asyncio
    async def test_concurrent_loser_reuses_winner_user(self):
        """RPC 返回 is_new=False → 我们是并发输家，复用赢家的 user_id"""
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "winner-uuid", "is_new": False}
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user("liaojuan", "corp_X")

        assert user_id == "winner-uuid"
        # is_new=False 不应触发自动加入企业
        db._table_mocks["org_members"].insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_rpc_failure_raises(self):
        """RPC 返回空或没 user_id → 抛 RuntimeError"""
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(data={})

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            with pytest.raises(RuntimeError, match="wecom_get_or_create_user RPC 失败"):
                await svc.get_or_create_user("fail_user", "corp")

    @pytest.mark.asyncio
    async def test_is_new_auto_joins_org(self):
        """is_new=True + org_id → 调 _ensure_org_member_safe"""
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "new-u", "is_new": True}
        )
        # 模拟 org_members 查询：用户尚不在 org 中
        db._table_mocks["org_members"].execute.return_value = MagicMock(data=None)

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user("u", "c", org_id="org-1")

        # 应该 INSERT org_members
        db._table_mocks["org_members"].insert.assert_called_once()
        insert_payload = db._table_mocks["org_members"].insert.call_args[0][0]
        assert insert_payload["org_id"] == "org-1"
        assert insert_payload["user_id"] == "new-u"
        assert insert_payload["role"] == "member"


class TestDisplayNameResolution:
    """display_name 优先级：传入 > fetch_wecom_real_name > 兜底"""

    @pytest.mark.asyncio
    async def test_uses_provided_nickname(self):
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "u1", "is_new": True}
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user("ww001", "corp", nickname="自定义昵称")

        params = _rpc_params(db, "wecom_get_or_create_user")
        assert params["p_display_name"] == "自定义昵称"

    @pytest.mark.asyncio
    async def test_uses_wecom_real_name_when_no_nickname(self):
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "u3", "is_new": True}
        )

        async def fake_fetch(d, oid, uid, **kw):
            return "王五"

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()), \
             patch(
                 "services.wecom.wecom_contact_api.fetch_wecom_real_name",
                 new=fake_fetch,
             ):
            await svc.get_or_create_user("wangwu", "corp", org_id="org-1")

        params = _rpc_params(db, "wecom_get_or_create_user")
        assert params["p_display_name"] == "王五"

    @pytest.mark.asyncio
    async def test_fallback_when_no_real_name(self):
        db = _make_db_mock()
        db._table_mocks["wecom_user_mappings"].execute.return_value = MagicMock(data=[])
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "u4", "is_new": True}
        )

        async def fake_fetch(d, oid, uid, **kw):
            return None

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()), \
             patch(
                 "services.wecom.wecom_contact_api.fetch_wecom_real_name",
                 new=fake_fetch,
             ):
            await svc.get_or_create_user("abcdefgh_long", "corp", org_id="org-1")

        params = _rpc_params(db, "wecom_get_or_create_user")
        assert params["p_display_name"] == "企微用户_abcdefgh"


class TestUpdateNickname:
    """update_nickname 昵称更新（未改动，保留覆盖）"""

    @pytest.mark.asyncio
    async def test_update_success(self):
        db = _make_db_mock()
        mapping_mock = db._table_mocks["wecom_user_mappings"]

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.update_nickname("ww001", "corp1", "新昵称")

        mapping_mock.update.assert_called_once_with({"wecom_nickname": "新昵称"})

    @pytest.mark.asyncio
    async def test_update_error_no_raise(self):
        db = _make_db_mock()
        mapping_mock = db._table_mocks["wecom_user_mappings"]
        mapping_mock.update.side_effect = RuntimeError("DB error")

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.update_nickname("ww001", "corp1", "新昵称")
