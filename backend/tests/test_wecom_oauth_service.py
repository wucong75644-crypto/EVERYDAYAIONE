"""
WecomOAuthService 单元测试

覆盖：state 管理（generate/validate）、exchange_code、login_or_create、build_qr_url
"""

import json
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.wecom_oauth_service import (
    OAUTH_STATE_PREFIX,
    OAUTH_STATE_TTL,
    WecomOAuthService,
)


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


def _make_settings(**overrides):
    """构造 settings mock"""
    defaults = {
        "wecom_corp_id": "ww_test_corp",
        "wecom_agent_id": 1000006,
        "wecom_agent_secret": "test_secret",
        "wecom_oauth_redirect_uri": "https://example.com/api/auth/wecom/callback",
        "frontend_url": "https://example.com",
        "jwt_access_token_expire_minutes": 1440,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ----------------------------------------------------------------
# State 管理
# ----------------------------------------------------------------


class TestGenerateState:
    """generate_state 测试"""

    @pytest.mark.asyncio
    async def test_generates_state_and_stores_in_redis(self):
        """生成 state 并存入 Redis"""
        db = _make_db_mock()
        redis_mock = AsyncMock()

        with patch("services.wecom_oauth_service.get_redis", return_value=redis_mock), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            state = await svc.generate_state("login")

        assert len(state) > 20
        redis_mock.set.assert_called_once()
        call_args = redis_mock.set.call_args
        assert call_args[0][0] == f"{OAUTH_STATE_PREFIX}{state}"
        assert call_args[1]["ex"] == OAUTH_STATE_TTL

        stored = json.loads(call_args[0][1])
        assert stored["type"] == "login"
        assert stored["user_id"] is None

    @pytest.mark.asyncio
    async def test_generates_bind_state_with_user_id(self):
        """bind 模式包含 user_id"""
        db = _make_db_mock()
        redis_mock = AsyncMock()

        with patch("services.wecom_oauth_service.get_redis", return_value=redis_mock), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            state = await svc.generate_state("bind", user_id="uuid-123")

        stored = json.loads(redis_mock.set.call_args[0][1])
        assert stored["type"] == "bind"
        assert stored["user_id"] == "uuid-123"

    @pytest.mark.asyncio
    async def test_raises_when_redis_unavailable(self):
        """Redis 不可用时抛出异常"""
        db = _make_db_mock()

        with patch("services.wecom_oauth_service.get_redis", return_value=None), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            with pytest.raises(RuntimeError, match="Redis 不可用"):
                await svc.generate_state("login")


class TestValidateState:
    """validate_state 测试"""

    @pytest.mark.asyncio
    async def test_validates_and_consumes_state(self):
        """校验成功后消费 state（GETDEL）"""
        db = _make_db_mock()
        redis_mock = AsyncMock()
        state_data = json.dumps({"type": "login", "user_id": None})
        redis_mock.getdel.return_value = state_data

        with patch("services.wecom_oauth_service.get_redis", return_value=redis_mock), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = await svc.validate_state("test_state")

        assert result["type"] == "login"
        redis_mock.getdel.assert_called_once_with(f"{OAUTH_STATE_PREFIX}test_state")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_state(self):
        """无效 state 抛出 ValueError"""
        db = _make_db_mock()
        redis_mock = AsyncMock()
        redis_mock.getdel.return_value = None

        with patch("services.wecom_oauth_service.get_redis", return_value=redis_mock), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="state 无效或已过期"):
                await svc.validate_state("bad_state")


# ----------------------------------------------------------------
# exchange_code
# ----------------------------------------------------------------


class TestExchangeCode:
    """exchange_code 测试"""

    @pytest.mark.asyncio
    async def test_returns_userid_on_success(self):
        """成功返回 userid"""
        db = _make_db_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "userid": "zhangsan",
            "user_ticket": "TICKET_123",
        }

        with patch("services.wecom_oauth_service.get_access_token", return_value="token_abc"), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = WecomOAuthService(db)
            result = await svc.exchange_code("auth_code_123")

        assert result["userid"] == "zhangsan"
        assert result["user_ticket"] == "TICKET_123"

    @pytest.mark.asyncio
    async def test_rejects_non_member(self):
        """非企业成员（返回 openid）→ 拒绝"""
        db = _make_db_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "errcode": 0,
            "errmsg": "ok",
            "openid": "external_user",
        }

        with patch("services.wecom_oauth_service.get_access_token", return_value="token_abc"), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="仅限企业成员"):
                await svc.exchange_code("code_for_non_member")

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self):
        """企微 API 错误 → 抛出异常"""
        db = _make_db_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 40029, "errmsg": "invalid code"}

        with patch("services.wecom_oauth_service.get_access_token", return_value="token_abc"), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="企微授权失败"):
                await svc.exchange_code("invalid_code")

    @pytest.mark.asyncio
    async def test_raises_when_no_access_token(self):
        """access_token 获取失败 → 抛出异常"""
        db = _make_db_mock()

        with patch("services.wecom_oauth_service.get_access_token", return_value=None), \
             patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="access_token"):
                await svc.exchange_code("any_code")


# ----------------------------------------------------------------
# login_or_create
# ----------------------------------------------------------------


class TestLoginOrCreate:
    """login_or_create 测试"""

    @pytest.mark.asyncio
    async def test_existing_user_login(self):
        """已有映射 → 直接登录"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        users_table = db._table_mocks.setdefault("users", MagicMock())

        # 映射查询返回已有用户
        (mapping_table.select.return_value
         .eq.return_value.eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[{"user_id": "uid-exist"}])

        # 用户查询
        (users_table.select.return_value
         .eq.return_value.single.return_value
         .execute.return_value) = MagicMock(data={
            "id": "uid-exist",
            "nickname": "张三",
            "avatar_url": None,
            "phone": "13800138000",
            "role": "user",
            "credits": 50,
            "status": "active",
            "created_at": "2026-01-01T00:00:00",
        })

        # update 返回
        users_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = await svc.login_or_create("wecom_zhangsan")

        assert result["user"]["id"] == "uid-exist"
        assert "access_token" in result["token"]

    @pytest.mark.asyncio
    async def test_new_user_creation(self):
        """无映射 → 创建用户 + 映射"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        users_table = db._table_mocks.setdefault("users", MagicMock())
        credits_table = db._table_mocks.setdefault("credits_history", MagicMock())

        # 映射查询返回空
        (mapping_table.select.return_value
         .eq.return_value.eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[])

        # 创建用户
        new_user = {
            "id": "uid-new",
            "nickname": "企微用户_wecom_zh",
            "avatar_url": None,
            "phone": None,
            "role": "user",
            "credits": 100,
            "status": "active",
            "created_at": "2026-03-22T00:00:00",
        }
        users_table.insert.return_value.execute.return_value = MagicMock(data=[new_user])

        # 积分记录
        credits_table.insert.return_value.execute.return_value = MagicMock()

        # 创建映射
        mapping_table.insert.return_value.execute.return_value = MagicMock()

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = await svc.login_or_create("wecom_zhangsan", nickname="张三")

        assert result["user"]["id"] == "uid-new"
        assert "access_token" in result["token"]

        # 验证调用了 insert
        users_table.insert.assert_called_once()
        mapping_table.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_disabled_user_rejected(self):
        """已禁用用户 → 拒绝登录"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        users_table = db._table_mocks.setdefault("users", MagicMock())

        (mapping_table.select.return_value
         .eq.return_value.eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[{"user_id": "uid-disabled"}])

        (users_table.select.return_value
         .eq.return_value.single.return_value
         .execute.return_value) = MagicMock(data={
            "id": "uid-disabled",
            "status": "disabled",
        })

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="禁用"):
                await svc.login_or_create("wecom_disabled")


# ----------------------------------------------------------------
# build_qr_url
# ----------------------------------------------------------------


# ----------------------------------------------------------------
# unbind_account
# ----------------------------------------------------------------


class TestUnbindAccount:
    """unbind_account 测试"""

    @pytest.mark.asyncio
    async def test_unbind_success(self):
        """正常解绑"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        users_table = db._table_mocks.setdefault("users", MagicMock())

        # 有绑定
        mapping_table.select.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[{"id": "m1", "wecom_userid": "wc1"}])
        )

        # 用户有手机号
        (users_table.select.return_value
         .eq.return_value.single.return_value
         .execute.return_value) = MagicMock(data={
            "phone": "13800138000",
            "login_methods": ["phone", "wecom"],
        })

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = await svc.unbind_account("uid-1")

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_unbind_no_binding(self):
        """未绑定 → 异常"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        mapping_table.select.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[])
        )

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="未绑定"):
                await svc.unbind_account("uid-1")

    @pytest.mark.asyncio
    async def test_unbind_only_wecom_no_phone(self):
        """仅企微创建无手机号 → 拒绝"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        users_table = db._table_mocks.setdefault("users", MagicMock())

        mapping_table.select.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[{"id": "m1", "wecom_userid": "wc1"}])
        )
        (users_table.select.return_value
         .eq.return_value.single.return_value
         .execute.return_value) = MagicMock(data={
            "phone": None,
            "login_methods": ["wecom"],
        })

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            with pytest.raises(ValueError, match="无法登录"):
                await svc.unbind_account("uid-1")


# ----------------------------------------------------------------
# get_binding_status
# ----------------------------------------------------------------


class TestGetBindingStatus:
    """get_binding_status 测试"""

    @pytest.mark.asyncio
    async def test_bound(self):
        """已绑定 → 返回详情"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        (mapping_table.select.return_value
         .eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[{
            "wecom_nickname": "张三",
            "bound_at": "2026-03-22T00:00:00+08:00",
        }])

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = await svc.get_binding_status("uid-1")

        assert result["bound"] is True
        assert result["wecom_nickname"] == "张三"

    @pytest.mark.asyncio
    async def test_not_bound(self):
        """未绑定 → 返回 False"""
        db = _make_db_mock()
        mapping_table = db._table_mocks.setdefault("wecom_user_mappings", MagicMock())
        (mapping_table.select.return_value
         .eq.return_value.limit.return_value
         .execute.return_value) = MagicMock(data=[])

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = await svc.get_binding_status("uid-1")

        assert result["bound"] is False


# ----------------------------------------------------------------
# build_qr_url
# ----------------------------------------------------------------


class TestEnsureOrgMember:
    """_ensure_org_member 测试"""

    def test_ensure_org_member_adds_when_not_exists(self):
        """用户不在 org_members 中时自动插入"""
        db = _make_db_mock()
        members_table = db._table_mocks.setdefault("org_members", MagicMock())

        # maybe_single 返回 None（不存在）
        (members_table.select.return_value
         .eq.return_value.eq.return_value.maybe_single.return_value
         .execute.return_value) = MagicMock(data=None)

        # insert 正常
        members_table.insert.return_value.execute.return_value = MagicMock()

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            svc._ensure_org_member("uid-1", "org-abc")

        members_table.insert.assert_called_once()
        insert_data = members_table.insert.call_args[0][0]
        assert insert_data["user_id"] == "uid-1"
        assert insert_data["org_id"] == "org-abc"
        assert insert_data["role"] == "member"

    def test_ensure_org_member_skips_when_exists(self):
        """用户已在 org_members 中时跳过插入"""
        db = _make_db_mock()
        members_table = db._table_mocks.setdefault("org_members", MagicMock())

        # maybe_single 返回已有数据
        (members_table.select.return_value
         .eq.return_value.eq.return_value.maybe_single.return_value
         .execute.return_value) = MagicMock(data={"user_id": "uid-1"})

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            svc._ensure_org_member("uid-1", "org-abc")

        members_table.insert.assert_not_called()

    def test_ensure_org_member_handles_insert_error(self):
        """insert 异常时不抛出（仅记录日志）"""
        db = _make_db_mock()
        members_table = db._table_mocks.setdefault("org_members", MagicMock())

        # maybe_single 返回 None
        (members_table.select.return_value
         .eq.return_value.eq.return_value.maybe_single.return_value
         .execute.return_value) = MagicMock(data=None)

        # insert 抛异常
        members_table.insert.return_value.execute.side_effect = RuntimeError("DB error")

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            # 不应抛异常
            svc._ensure_org_member("uid-1", "org-abc")


class TestFindUserOrg:
    """_find_user_org 测试"""

    def test_find_user_org_returns_org_info(self):
        """查到活跃企业成员时返回企业信息（两步查询）"""
        db = _make_db_mock()
        members_table = db._table_mocks.setdefault("org_members", MagicMock())
        orgs_table = db._table_mocks.setdefault("organizations", MagicMock())

        # 第一步：查 org_members
        (members_table.select.return_value
         .eq.return_value.eq.return_value.eq.return_value
         .limit.return_value.execute.return_value) = MagicMock(data=[{
            "org_id": "org-abc",
            "role": "admin",
        }])

        # 第二步：查 organizations
        (orgs_table.select.return_value
         .eq.return_value.maybe_single.return_value
         .execute.return_value) = MagicMock(data={
            "id": "org-abc", "name": "蓝创科技", "status": "active",
        })

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = svc._find_user_org("uid-1", preferred_org_id="org-abc")

        assert result is not None
        assert result["org_id"] == "org-abc"
        assert result["name"] == "蓝创科技"
        assert result["role"] == "admin"

    def test_find_user_org_returns_none_when_not_member(self):
        """用户不是任何企业成员时返回 None"""
        db = _make_db_mock()
        members_table = db._table_mocks.setdefault("org_members", MagicMock())

        (members_table.select.return_value
         .eq.return_value.eq.return_value
         .limit.return_value.execute.return_value) = MagicMock(data=[])

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = svc._find_user_org("uid-1")

        assert result is None

    def test_find_user_org_returns_none_when_org_inactive(self):
        """企业 status != active 时返回 None"""
        db = _make_db_mock()
        members_table = db._table_mocks.setdefault("org_members", MagicMock())

        (members_table.select.return_value
         .eq.return_value.eq.return_value.eq.return_value
         .limit.return_value.execute.return_value) = MagicMock(data=[{
            "org_id": "org-abc",
            "role": "member",
            "organizations": {"id": "org-abc", "name": "已停用公司", "status": "suspended"},
        }])

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = svc._find_user_org("uid-1", preferred_org_id="org-abc")

        assert result is None


class TestBuildQrUrl:
    """build_qr_url 测试"""

    def test_builds_correct_url(self):
        """生成正确的扫码 URL"""
        db = _make_db_mock()

        with patch("services.wecom_oauth_service.get_settings", return_value=_make_settings()):
            svc = WecomOAuthService(db)
            result = svc.build_qr_url("state_abc")

        assert "login_type=CorpApp" in result["qr_url"]
        assert "appid=ww_test_corp" in result["qr_url"]
        assert "agentid=1000006" in result["qr_url"]
        assert "state=state_abc" in result["qr_url"]
        assert result["state"] == "state_abc"
        assert result["appid"] == "ww_test_corp"
        assert result["agentid"] == "1000006"
