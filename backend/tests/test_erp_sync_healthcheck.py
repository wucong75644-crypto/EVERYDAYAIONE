"""ERP 同步健康检查 — 单元测试

覆盖：
- 阈值过滤（error_count < ALERT_THRESHOLD 不告警）
- 按 org 聚合
- Redis dedupe（已告警过 1h 内不重复）
- dedupe 顺序修复（推送失败时不持锁 1h）
- 推送失败时主流程不抛异常
- 管理员查询走 org_members（不是 users.role）
- 指纹生成（按 error_count // ALERT_THRESHOLD 分档）
- best-effort 通道：缺 wecom 配置时跳过
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.kuaimai.erp_sync_healthcheck import (
    ALERT_THRESHOLD,
    _fingerprint,
    _maybe_alert_org,
    _push_to_org_admins,
    _scan_and_alert,
)


# ── 指纹生成 ─────────────────────────────────────────────


class TestFingerprint:
    def test_same_items_same_fingerprint(self):
        items = [
            {"sync_type": "order", "error_count": 50},
            {"sync_type": "aftersale", "error_count": 30},
        ]
        assert _fingerprint(items) == _fingerprint(items)

    def test_order_independent(self):
        """指纹与 sync_type 顺序无关"""
        items_a = [
            {"sync_type": "order", "error_count": 50},
            {"sync_type": "aftersale", "error_count": 30},
        ]
        items_b = [
            {"sync_type": "aftersale", "error_count": 30},
            {"sync_type": "order", "error_count": 50},
        ]
        assert _fingerprint(items_a) == _fingerprint(items_b)

    def test_error_count_bucketed_by_threshold(self):
        """error_count 按 ALERT_THRESHOLD 分档（同档去重，避免数字 +1 刷屏）

        2026-04-11: ALERT_THRESHOLD 10→3 后，分档粒度从 10 变 3。
        例如阈值=3：3-5 同档，6-8 同档，9-11 同档。
        """
        from services.kuaimai.erp_sync_healthcheck import ALERT_THRESHOLD
        # 同档：[ALERT_THRESHOLD * k, ALERT_THRESHOLD * (k+1) - 1]
        base = ALERT_THRESHOLD * 5  # 任取一档起点
        items_a = [{"sync_type": "order", "error_count": base}]
        items_b = [{"sync_type": "order", "error_count": base + 1}]
        items_c = [{"sync_type": "order", "error_count": base + ALERT_THRESHOLD - 1}]
        assert _fingerprint(items_a) == _fingerprint(items_b) == _fingerprint(items_c)

    def test_error_count_crosses_bucket(self):
        """跨档应生成不同指纹，重新触发告警"""
        from services.kuaimai.erp_sync_healthcheck import ALERT_THRESHOLD
        base = ALERT_THRESHOLD * 5
        items_low = [{"sync_type": "order", "error_count": base + ALERT_THRESHOLD - 1}]
        items_high = [{"sync_type": "order", "error_count": base + ALERT_THRESHOLD}]
        assert _fingerprint(items_low) != _fingerprint(items_high)


# ── _scan_and_alert（阈值过滤 + 按 org 聚合）──────────────


class _AsyncQueryStub:
    """链式 query stub，最后 execute 返回 mock data"""

    def __init__(self, data):
        self._data = data

    def select(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def maybe_single(self): return self

    async def execute(self):
        result = MagicMock()
        result.data = self._data
        return result


class _DBStub:
    def __init__(self, table_data: dict):
        self._tables = table_data

    def table(self, name):
        return _AsyncQueryStub(self._tables.get(name, []))


@pytest.fixture
def mock_redis_no_persist_failures():
    """fixture: mock get_redis 返回 None 让 persist_failure 扫描跳过

    _scan_and_alert 会扫两类异常源：
    1. erp_sync_state（同步任务失败）
    2. Redis kuaimai:persist_failure:* （token 持久化失败 — 隐性失败兜底）
    多数测试只关心第 1 类，需要把第 2 类禁用掉。
    """
    with patch(
        "core.redis.get_redis", new_callable=AsyncMock, return_value=None,
    ) as mock_get:
        yield mock_get


class TestScanAndAlert:
    @pytest.mark.asyncio
    async def test_no_errors_no_alert(self, mock_redis_no_persist_failures):
        """所有同步都正常时不触发告警"""
        db = _DBStub({"erp_sync_state": []})
        with patch(
            "services.kuaimai.erp_sync_healthcheck._maybe_alert_org",
            new_callable=AsyncMock,
        ) as mock_alert:
            await _scan_and_alert(db)
        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_groups_by_org(self, mock_redis_no_persist_failures):
        """多个 org 的告警按 org 聚合后分别推送"""
        db = _DBStub({"erp_sync_state": [
            {"org_id": "org-a", "sync_type": "order", "error_count": 50, "last_error": "x", "last_run_at": None},
            {"org_id": "org-a", "sync_type": "aftersale", "error_count": 30, "last_error": "y", "last_run_at": None},
            {"org_id": "org-b", "sync_type": "stock", "error_count": 20, "last_error": "z", "last_run_at": None},
        ]})
        with patch(
            "services.kuaimai.erp_sync_healthcheck._maybe_alert_org",
            new_callable=AsyncMock,
        ) as mock_alert:
            await _scan_and_alert(db)

        # 应该调 2 次（org-a 一次包含 2 项；org-b 一次包含 1 项）
        assert mock_alert.call_count == 2
        call_orgs = sorted([c.args[1] for c in mock_alert.call_args_list])
        assert call_orgs == ["org-a", "org-b"]

    @pytest.mark.asyncio
    async def test_null_org_treated_as_system(self, mock_redis_no_persist_failures):
        """org_id=None（散客全局）映射为 'system'"""
        db = _DBStub({"erp_sync_state": [
            {"org_id": None, "sync_type": "order", "error_count": 30, "last_error": "x", "last_run_at": None},
        ]})
        with patch(
            "services.kuaimai.erp_sync_healthcheck._maybe_alert_org",
            new_callable=AsyncMock,
        ) as mock_alert:
            await _scan_and_alert(db)
        mock_alert.assert_called_once()
        assert mock_alert.call_args.args[1] == "system"

    @pytest.mark.asyncio
    async def test_one_org_failure_does_not_block_other(
        self, mock_redis_no_persist_failures,
    ):
        """单个 org 推送异常不影响其他 org"""
        db = _DBStub({"erp_sync_state": [
            {"org_id": "org-a", "sync_type": "order", "error_count": 30, "last_error": "x", "last_run_at": None},
            {"org_id": "org-b", "sync_type": "order", "error_count": 30, "last_error": "x", "last_run_at": None},
        ]})

        async def selective_alert(db, org_id, items):
            if org_id == "org-a":
                raise RuntimeError("simulated failure on a")

        with patch(
            "services.kuaimai.erp_sync_healthcheck._maybe_alert_org",
            new=AsyncMock(side_effect=selective_alert),
        ) as mock_alert:
            # 必须不抛异常
            await _scan_and_alert(db)

        assert mock_alert.call_count == 2  # 两次都调到了

    @pytest.mark.asyncio
    async def test_persist_failure_marker_triggers_alert(self):
        """关键回归测试: Redis 里的 token DB 持久化失败状态位会触发告警

        这是 2026-04-10 雪崩根因的隐性失败兜底防线 —— 即使 erp_sync_state
        没异常（worker 用 Redis 缓存的新 token 工作正常），DB 写失败也必须被
        healthcheck 扫到，防止 Redis 失效后回到雪崩状态。
        """
        db = _DBStub({"erp_sync_state": []})  # 同步状态全部正常

        # mock Redis 扫描返回一个持久化失败的 org
        async def fake_scan_iter(match=None, count=None):
            yield "kuaimai:persist_failure:org-broken"

        mock_redis = AsyncMock()
        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value="connection refused")

        with patch(
            "core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis,
        ), patch(
            "services.kuaimai.erp_sync_healthcheck._maybe_alert_org",
            new_callable=AsyncMock,
        ) as mock_alert:
            await _scan_and_alert(db)

        # 必须触发告警，且 sync_type 是 token_db_persist
        mock_alert.assert_called_once()
        args = mock_alert.call_args.args
        assert args[1] == "org-broken"
        items = args[2]
        assert any(i["sync_type"] == "token_db_persist" for i in items)
        assert "Token DB 持久化失败" in items[0]["last_error"]


# ── _maybe_alert_org（去重 + 推送失败处理）─────────────────


class TestMaybeAlertOrg:
    @pytest.mark.asyncio
    async def test_dedupe_skip_when_recently_alerted(self):
        """相同指纹在 1h 内已告警过 → skip"""
        items = [{"sync_type": "order", "error_count": 50, "last_error": "x"}]

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)  # 已存在 dedupe key
        mock_redis.set = AsyncMock()

        with patch(
            "core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis,
        ), patch(
            "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
            new_callable=AsyncMock,
        ) as mock_push:
            await _maybe_alert_org(MagicMock(), "org-test", items)

        # 已告警过 → 不应该再推送
        mock_push.assert_not_called()
        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_success_then_set_dedupe(self):
        """正常路径：推送成功后才设置 dedupe key"""
        items = [{"sync_type": "order", "error_count": 50, "last_error": "x"}]

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.set = AsyncMock(return_value=True)

        with patch(
            "core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis,
        ), patch(
            "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
            new_callable=AsyncMock,
        ) as mock_push:
            await _maybe_alert_org(MagicMock(), "org-test", items)

        mock_push.assert_called_once()
        # set 调用 2 次：1 次状态位 + 1 次 dedupe key
        assert mock_redis.set.call_count >= 2
        # 检查 dedupe key 被设置
        set_keys = [c.args[0] for c in mock_redis.set.call_args_list]
        assert any("erp_sync_healthcheck:fired:" in k for k in set_keys)

    @pytest.mark.asyncio
    async def test_push_failure_does_not_lock_dedupe(self):
        """关键回归测试: 推送失败时 dedupe key 不被设置，下次扫描可重试

        历史 bug: 旧实现先 SETNX 再推送，推送失败也会持锁 1h，
        导致告警延迟最多 1 小时。
        """
        items = [{"sync_type": "order", "error_count": 50, "last_error": "x"}]

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.set = AsyncMock(return_value=True)

        with patch(
            "core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis,
        ), patch(
            "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
            new=AsyncMock(side_effect=RuntimeError("wecom api failed")),
        ):
            # 必须不抛
            await _maybe_alert_org(MagicMock(), "org-test", items)

        # 检查所有 set 调用 — dedupe key 必须不被设置
        set_keys = [c.args[0] for c in mock_redis.set.call_args_list]
        dedupe_keys = [k for k in set_keys if "erp_sync_healthcheck:fired:" in k]
        assert dedupe_keys == [], (
            "dedupe key 不应该在推送失败时被设置，否则下次扫描会被锁 1h"
        )

    @pytest.mark.asyncio
    async def test_system_org_skips_wecom_push(self):
        """org_id='system'（散客）不应触发企微推送"""
        items = [{"sync_type": "order", "error_count": 50, "last_error": "x"}]

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.set = AsyncMock()

        with patch(
            "core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis,
        ), patch(
            "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
            new_callable=AsyncMock,
        ) as mock_push:
            await _maybe_alert_org(MagicMock(), "system", items)

        mock_push.assert_not_called()


# ── _push_to_org_admins（管理员查询走 org_members）────────


class TestPushToOrgAdmins:
    @pytest.mark.asyncio
    async def test_queries_org_members_not_users_role(self):
        """关键回归测试: admin 查询必须用 org_members 表

        历史 bug: 旧实现用 users.role IN ('admin', 'super_admin') + current_org_id，
        但 users.role 实际只有 'super_admin' 和 'user'，企业管理员是
        org_members.role IN ('owner', 'admin')。普通管理员永远收不到告警。
        """
        # 记录所有 db.table 调用
        table_calls = []

        class SpyDB:
            def table(self, name):
                table_calls.append(name)
                return _AsyncQueryStub([])  # 空，触发 early return

        await _push_to_org_admins(SpyDB(), "org-test", "test msg")

        # 必须查 org_members（多租户成员关系）而不是 users
        assert "org_members" in table_calls
        # 不应该查 users 表来判断 role
        assert "users" not in table_calls

    @pytest.mark.asyncio
    async def test_no_admins_returns_silently(self):
        """没有管理员时静默返回，不报错"""
        db = _DBStub({"org_members": []})
        # 不应抛异常
        await _push_to_org_admins(db, "org-test", "test msg")

    @pytest.mark.asyncio
    async def test_no_wecom_mapping_returns_silently(self):
        """有管理员但没绑企微时静默返回"""
        db = _DBStub({
            "org_members": [{"user_id": "u1", "role": "owner"}],
            "wecom_user_mappings": [],
        })
        # 不应抛异常
        await _push_to_org_admins(db, "org-test", "test msg")

    @pytest.mark.asyncio
    async def test_no_wecom_agent_config_returns_silently(self):
        """有 wecom 映射但企业未配自建应用 → 静默返回"""
        db = _DBStub({
            "org_members": [{"user_id": "u1", "role": "owner"}],
            "wecom_user_mappings": [{"wecom_userid": "wu1", "user_id": "u1"}],
            "org_configs": None,  # _load_encrypted 返回 None
        })
        # mock resolver.get 返回 None（没有 agent 配置）
        with patch(
            "services.org.config_resolver.AsyncOrgConfigResolver.get",
            new_callable=AsyncMock, return_value=None,
        ):
            # 不应抛异常
            await _push_to_org_admins(db, "org-test", "test msg")


# ── push_token_refresh_alert（Bug 3 快档告警）─────────────


class TestPushTokenRefreshAlert:
    """token refresh 失败立即告警的快档路径测试"""

    @pytest.mark.asyncio
    async def test_system_org_only_logs(self):
        """org_id=None 时只打日志，不查 db / 不推送企微"""
        from services.kuaimai.erp_sync_healthcheck import push_token_refresh_alert

        with patch("core.redis.get_redis", new_callable=AsyncMock) as mock_redis, \
             patch("core.database.get_async_db", new_callable=AsyncMock) as mock_db, \
             patch(
                 "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
                 new_callable=AsyncMock,
             ) as mock_push:
            await push_token_refresh_alert(None, "code=invalid_session")

        mock_redis.assert_not_called()
        mock_db.assert_not_called()
        mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_org_pushes_to_admins(self):
        """正常 org 应推送给管理员"""
        from services.kuaimai.erp_sync_healthcheck import push_token_refresh_alert

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.set = AsyncMock()

        with patch("core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis), \
             patch("core.database.get_async_db", new_callable=AsyncMock, return_value=MagicMock()), \
             patch(
                 "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
                 new_callable=AsyncMock,
             ) as mock_push:
            await push_token_refresh_alert("org-x", "code=invalid_session msg=会话不存在")

        mock_push.assert_called_once()
        args, _ = mock_push.call_args
        assert args[1] == "org-x"
        # 消息里应包含 org_id 和原始错误
        assert "org-x" in args[2]
        assert "invalid_session" in args[2]
        # 推送成功后才设置 dedupe key
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedupe_skips_if_recent_alert(self):
        """1 小时内已告警过 → 跳过，不重发"""
        from services.kuaimai.erp_sync_healthcheck import push_token_refresh_alert

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)  # 已存在
        mock_redis.set = AsyncMock()

        with patch("core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis), \
             patch("core.database.get_async_db", new_callable=AsyncMock) as mock_db, \
             patch(
                 "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
                 new_callable=AsyncMock,
             ) as mock_push:
            await push_token_refresh_alert("org-x", "code=invalid_session")

        # 已告警过 → 不应再 db 查询 / 不应推送
        mock_db.assert_not_called()
        mock_push.assert_not_called()
        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_failure_does_not_set_dedupe(self):
        """推送失败时不设置 dedupe key，下次扫描可重试"""
        from services.kuaimai.erp_sync_healthcheck import push_token_refresh_alert

        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.set = AsyncMock()

        with patch("core.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis), \
             patch("core.database.get_async_db", new_callable=AsyncMock, return_value=MagicMock()), \
             patch(
                 "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError("wecom api down"),
             ):
            # 不应抛异常
            await push_token_refresh_alert("org-x", "err")

        # 推送失败 → dedupe key 不应被设置
        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_unavailable_still_attempts_push(self):
        """Redis 不可用时不应阻塞推送，best-effort 走完整链路"""
        from services.kuaimai.erp_sync_healthcheck import push_token_refresh_alert

        with patch(
            "core.redis.get_redis", new_callable=AsyncMock, return_value=None,
        ), patch(
            "core.database.get_async_db",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ), patch(
            "services.kuaimai.erp_sync_healthcheck._push_to_org_admins",
            new_callable=AsyncMock,
        ) as mock_push:
            await push_token_refresh_alert("org-x", "err")

        mock_push.assert_called_once()
