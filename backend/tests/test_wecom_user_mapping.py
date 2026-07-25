"""
WecomUserMappingService 单元测试

覆盖（迁移 152 安全门面）：
- 所有身份解析统一调用 resolve_wecom_ingress_user
- RPC 并发输家：返回 is_new=False → 复用赢家的 user_id
- RPC 失败 → 抛 RuntimeError
- display_name 解析优先级（传入 nickname > 企微 user/get > 兜底）
- org/corp/member 处理完全位于数据库安全门面
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


class TestIdentityFacade:
    """已有和新用户统一通过数据库安全门面解析。"""

    @pytest.mark.asyncio
    async def test_returns_existing_user(self):
        db = _make_db_mock()
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "existing-uuid-123", "is_new": False}
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user("zhangsan", "corp1")

        assert user_id == "existing-uuid-123"
        identity_params = _rpc_params(db, "resolve_wecom_ingress_user")
        assert identity_params["p_wecom_userid"] == "zhangsan"
        assert identity_params["p_corp_id"] == "corp1"
        db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_user_does_not_touch_mapping_tables(self):
        db = _make_db_mock()
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "u-x", "is_new": False}
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            user_id = await svc.get_or_create_user("z", "c")

        assert user_id == "u-x"
        db.table.assert_not_called()


class TestSlowPathRPC:
    """安全门面负责并发创建与成员关系。"""

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

        # 验证身份门面参数正确（活跃事件会额外调用 record_user_activity）
        create_calls = [
            call for call in db.rpc.call_args_list
            if call[0][0] == "resolve_wecom_ingress_user"
        ]
        assert len(create_calls) == 1
        rpc_call = create_calls[0]
        assert rpc_call[0][0] == "resolve_wecom_ingress_user"
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
            with pytest.raises(
                RuntimeError,
                match="resolve_wecom_ingress_user RPC 失败",
            ):
                await svc.get_or_create_user("fail_user", "corp")

    @pytest.mark.asyncio
    async def test_org_membership_is_owned_by_facade(self):
        db = _make_db_mock()
        db._rpc_chain.execute.return_value = MagicMock(
            data={"user_id": "new-u", "is_new": True}
        )

        svc = WecomUserMappingService(db)
        with patch.object(svc, "settings", MagicMock()):
            await svc.get_or_create_user(
                "u",
                "c",
                org_id="org-1",
                nickname="成员",
            )

        params = _rpc_params(db, "resolve_wecom_ingress_user")
        assert params["p_org_id"] == "org-1"
        db.table.assert_not_called()


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

        params = _rpc_params(db, "resolve_wecom_ingress_user")
        assert params["p_display_name"] == "自定义昵称"

    @pytest.mark.asyncio
    async def test_identity_resolution_uses_safe_fallback_before_actor_scope(self):
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

        params = _rpc_params(db, "resolve_wecom_ingress_user")
        assert params["p_display_name"] == "企微用户_wangwu"
        assert db.rpc.call_count == 1

    @pytest.mark.asyncio
    async def test_refreshes_real_name_after_actor_scope_is_bound(self):
        db = _make_db_mock()
        db._rpc_chain.execute.return_value = MagicMock(
            data={"outcome": "updated"}
        )

        async def fake_fetch(d, oid, uid, **kw):
            return "王五"

        svc = WecomUserMappingService(db)
        with patch(
            "services.wecom.wecom_contact_api.fetch_wecom_real_name",
            new=fake_fetch,
        ):
            await svc.refresh_display_name(
                user_id="u3",
                wecom_userid="wangwu",
                corp_id="corp",
                org_id="org-1",
            )

        params = _rpc_params(db, "update_wecom_ingress_display_name")
        assert params == {
            "p_user_id": "u3",
            "p_wecom_userid": "wangwu",
            "p_corp_id": "corp",
            "p_org_id": "org-1",
            "p_display_name": "王五",
        }

    @pytest.mark.asyncio
    async def test_failed_real_name_lookup_does_not_overwrite_with_fallback(self):
        db = _make_db_mock()

        async def fake_fetch(d, oid, uid, **kw):
            return None

        svc = WecomUserMappingService(db)
        with patch(
            "services.wecom.wecom_contact_api.fetch_wecom_real_name",
            new=fake_fetch,
        ):
            await svc.refresh_display_name(
                user_id="u3",
                wecom_userid="wangwu",
                corp_id="corp",
                org_id="org-1",
            )

        assert not any(
            call.args[0] == "update_wecom_ingress_display_name"
            for call in db.rpc.call_args_list
        )

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

        params = _rpc_params(db, "resolve_wecom_ingress_user")
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


class TestChatTargetFacade:
    """聊天目标只通过安全门面登记。"""

    @pytest.mark.asyncio
    async def test_upsert_uses_scoped_facade(self):
        db = _make_db_mock()
        svc = WecomUserMappingService(db)

        await svc.upsert_chat_target(
            "chat-1",
            "group",
            "corp-1",
            org_id="org-1",
        )

        db.rpc.assert_called_once_with(
            "upsert_wecom_ingress_chat_target",
            {
                "p_chatid": "chat-1",
                "p_chattype": "group",
                "p_corp_id": "corp-1",
                "p_org_id": "org-1",
            },
        )
        db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_failure_remains_best_effort(self):
        db = _make_db_mock()
        db.rpc.side_effect = RuntimeError("DB unavailable")
        svc = WecomUserMappingService(db)

        await svc.upsert_chat_target(
            "chat-1",
            "group",
            "corp-1",
            org_id="org-1",
        )
