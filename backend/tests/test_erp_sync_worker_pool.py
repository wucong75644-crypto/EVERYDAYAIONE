"""
ErpSyncWorkerPool 单元测试
覆盖：LockLostError、extend_fn、并发限制、requeue、client 创建、lock renew loop
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


def _make_pool(**overrides):
    """创建 WorkerPool 实例（全 mock 依赖）"""
    with patch("services.kuaimai.erp_sync_worker_pool.get_settings") as mock_s:
        settings = MagicMock()
        settings.erp_sync_worker_count = 2
        settings.erp_sync_max_org_concurrency = 3
        settings.erp_sync_task_lock_ttl = 60
        settings.erp_sync_kit_refresh_throttle = 30
        settings.erp_sync_queue_key = "erp_tasks"
        for k, v in overrides.items():
            setattr(settings, k, v)
        mock_s.return_value = settings

        from services.kuaimai.erp_sync_worker_pool import ErpSyncWorkerPool
        pool = ErpSyncWorkerPool(
            db=MagicMock(),
            scheduler=MagicMock(),
            aggregation_queue=asyncio.Queue(maxsize=100),
            aggregation_pending=set(),
        )
    return pool


# ── LockLostError ────────────────────────────────────

class TestLockLostError:

    def test_is_exception(self):
        from services.kuaimai.erp_sync_worker_pool import LockLostError
        assert issubclass(LockLostError, Exception)

    def test_message(self):
        from services.kuaimai.erp_sync_worker_pool import LockLostError
        e = LockLostError("test msg")
        assert "test msg" in str(e)


# ── _make_extend_fn ──────────────────────────────────

class TestMakeExtendFn:

    @pytest.mark.asyncio
    async def test_extend_success(self):
        pool = _make_pool()
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "token-abc"
        event = asyncio.Event()

        extend_fn = pool._make_extend_fn(lock_key, event)

        with patch("core.redis.RedisClient.extend_lock", new_callable=AsyncMock, return_value=True):
            await extend_fn()  # Should not raise

    @pytest.mark.asyncio
    async def test_extend_token_mismatch_raises_lock_lost(self):
        from services.kuaimai.erp_sync_worker_pool import LockLostError
        pool = _make_pool()
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "token-abc"
        event = asyncio.Event()

        extend_fn = pool._make_extend_fn(lock_key, event)

        with patch("core.redis.RedisClient.extend_lock", new_callable=AsyncMock, return_value=False):
            with pytest.raises(LockLostError):
                await extend_fn()

        # Event should be set
        assert event.is_set()
        # Token should be removed
        assert lock_key not in pool._held_locks

    @pytest.mark.asyncio
    async def test_extend_checks_event_first(self):
        from services.kuaimai.erp_sync_worker_pool import LockLostError
        pool = _make_pool()
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "token-abc"
        event = asyncio.Event()
        event.set()  # Pre-set by renew loop

        extend_fn = pool._make_extend_fn(lock_key, event)

        # Should raise immediately without calling Redis
        with pytest.raises(LockLostError, match="detected by renew loop"):
            await extend_fn()

    @pytest.mark.asyncio
    async def test_extend_db_lock_noop(self):
        pool = _make_pool()
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "__db_lock__"
        event = asyncio.Event()

        extend_fn = pool._make_extend_fn(lock_key, event)
        await extend_fn()  # Should not raise, noop for DB locks

    @pytest.mark.asyncio
    async def test_extend_redis_error_does_not_raise(self):
        pool = _make_pool()
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "token-abc"
        event = asyncio.Event()

        extend_fn = pool._make_extend_fn(lock_key, event)

        with patch("core.redis.RedisClient.extend_lock", new_callable=AsyncMock, side_effect=ConnectionError("redis down")):
            await extend_fn()  # Should not raise (transient error)
        assert not event.is_set()


# ── _lock_renew_loop ─────────────────────────────────

class TestLockRenewLoop:

    @pytest.mark.asyncio
    async def test_renew_success(self):
        pool = _make_pool(erp_sync_task_lock_ttl=0.1)  # Very short for test
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "token-abc"
        event = asyncio.Event()

        with patch("core.redis.RedisClient.extend_lock", new_callable=AsyncMock, return_value=True):
            task = asyncio.create_task(pool._lock_renew_loop(lock_key, event))
            await asyncio.sleep(0.15)  # Let it run one cycle
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert not event.is_set()

    @pytest.mark.asyncio
    async def test_renew_failure_sets_event(self):
        pool = _make_pool(erp_sync_task_lock_ttl=0.1)
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "token-abc"
        event = asyncio.Event()

        with patch("core.redis.RedisClient.extend_lock", new_callable=AsyncMock, return_value=False):
            await pool._lock_renew_loop(lock_key, event)

        assert event.is_set()
        assert lock_key not in pool._held_locks

    @pytest.mark.asyncio
    async def test_renew_db_lock_returns_immediately(self):
        pool = _make_pool(erp_sync_task_lock_ttl=0.1)
        lock_key = "erp_sync:org1:product"
        pool._held_locks[lock_key] = "__db_lock__"
        event = asyncio.Event()

        await pool._lock_renew_loop(lock_key, event)
        assert not event.is_set()


# ── _process_task (concurrency + requeue) ─────────────

class TestProcessTask:

    @pytest.mark.asyncio
    async def test_concurrency_limit_requeues(self):
        pool = _make_pool()
        pool._check_org_concurrency = AsyncMock(return_value=False)
        pool._requeue_task = AsyncMock()

        await pool._process_task(0, "org1:product")

        pool._requeue_task.assert_called_once_with("org1:product")

    @pytest.mark.asyncio
    async def test_lock_failure_requeues(self):
        pool = _make_pool()
        pool._check_org_concurrency = AsyncMock(return_value=True)
        pool._acquire_task_lock = AsyncMock(return_value=None)
        pool._decr_org_concurrency = AsyncMock()
        pool._requeue_task = AsyncMock()

        await pool._process_task(0, "org1:product")

        pool._requeue_task.assert_called_once_with("org1:product")
        pool._decr_org_concurrency.assert_called_once()

    @pytest.mark.asyncio
    async def test_lock_lost_does_not_mark_completed(self):
        from services.kuaimai.erp_sync_worker_pool import LockLostError
        pool = _make_pool()
        pool._check_org_concurrency = AsyncMock(return_value=True)
        pool._acquire_task_lock = AsyncMock(return_value="token-abc")
        pool._execute_task = AsyncMock(side_effect=LockLostError("test"))
        pool._release_task_lock = AsyncMock()
        pool._decr_org_concurrency = AsyncMock()

        await pool._process_task(0, "org1:product")

        pool.scheduler.mark_completed.assert_not_called()
        pool._release_task_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_marks_completed(self):
        pool = _make_pool()
        pool._check_org_concurrency = AsyncMock(return_value=True)
        pool._acquire_task_lock = AsyncMock(return_value="token-abc")
        pool._execute_task = AsyncMock()
        pool._release_task_lock = AsyncMock()
        pool._decr_org_concurrency = AsyncMock()

        await pool._process_task(0, "org1:platform_map")

        pool.scheduler.mark_completed.assert_called_once_with("org1", "platform_map")


# ── _create_client ───────────────────────────────────

class TestCreateClient:

    @pytest.mark.asyncio
    async def test_none_org_unconfigured_returns_none(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.is_configured = False

        with patch("services.kuaimai.client.KuaiMaiClient", return_value=mock_client):
            result = await pool._create_client(None)

        assert result is None

    @pytest.mark.asyncio
    async def test_none_org_configured_returns_client(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.is_configured = True

        with patch("services.kuaimai.client.KuaiMaiClient", return_value=mock_client):
            result = await pool._create_client(None)

        assert result is mock_client

    @pytest.mark.asyncio
    async def test_org_resolver_failure_returns_none(self):
        pool = _make_pool()

        with patch(
            "services.org.config_resolver.AsyncOrgConfigResolver"
        ) as MockResolver:
            MockResolver.return_value.get_erp_credentials = AsyncMock(side_effect=ValueError("no config"))
            result = await pool._create_client("org-123")

        assert result is None


# ── _requeue_task ────────────────────────────────────

class TestRequeueTask:

    @pytest.mark.asyncio
    async def test_requeue_calls_enqueue_with_delay(self):
        pool = _make_pool()

        with patch("core.redis.RedisClient.enqueue_task", new_callable=AsyncMock) as mock_enq:
            await pool._requeue_task("org1:product")

        mock_enq.assert_called_once()
        args = mock_enq.call_args
        assert args[0][1] == "org1:product"
        # Score should be in the future (delayed)
        import time
        assert args[0][2] > time.time()

    @pytest.mark.asyncio
    async def test_requeue_redis_error_silent(self):
        pool = _make_pool()

        with patch("core.redis.RedisClient.enqueue_task", new_callable=AsyncMock, side_effect=Exception("redis")):
            await pool._requeue_task("org1:product")  # Should not raise


# ── _release_all_locks ───────────────────────────────

class TestReleaseAllLocks:

    @pytest.mark.asyncio
    async def test_releases_all(self):
        pool = _make_pool()
        pool._held_locks = {"key1": "token1", "key2": "token2"}
        pool._release_task_lock = AsyncMock()

        await pool._release_all_locks()

        assert pool._release_task_lock.call_count == 2
        assert len(pool._held_locks) == 0


# ── 套件库存视图刷新 advisory lock ─────────────────


class TestThrottledKitRefresh:
    """_throttled_kit_refresh advisory lock 逻辑"""

    @pytest.mark.asyncio
    async def test_lock_acquired_executes_refresh(self):
        """获取 advisory lock 成功 → 执行 REFRESH"""
        pool = _make_pool()

        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=(True,))
        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_cur),
            __aexit__=AsyncMock(return_value=False),
        ))
        mock_conn.set_autocommit = AsyncMock()
        pool.db.pool.connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        mock_redis = MagicMock()
        mock_redis.try_throttle = AsyncMock(return_value=True)
        with patch("core.redis.RedisClient", mock_redis):
            await pool._throttled_kit_refresh()

        executed_sqls = [
            call.args[0] for call in mock_cur.execute.call_args_list
        ]
        assert any("REFRESH" in sql for sql in executed_sqls)
        assert any("advisory_unlock" in sql for sql in executed_sqls)

    @pytest.mark.asyncio
    async def test_lock_not_acquired_skips_refresh(self):
        """获取 advisory lock 失败 → 跳过 REFRESH"""
        pool = _make_pool()

        mock_cur = AsyncMock()
        mock_cur.fetchone = AsyncMock(return_value=(False,))
        mock_conn = AsyncMock()
        mock_conn.cursor = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_cur),
            __aexit__=AsyncMock(return_value=False),
        ))
        mock_conn.set_autocommit = AsyncMock()
        pool.db.pool.connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        ))

        mock_redis = MagicMock()
        mock_redis.try_throttle = AsyncMock(return_value=True)
        with patch("core.redis.RedisClient", mock_redis):
            await pool._throttled_kit_refresh()

        executed_sqls = [
            call.args[0] for call in mock_cur.execute.call_args_list
        ]
        assert not any("REFRESH" in sql for sql in executed_sqls)
        assert not any("advisory_unlock" in sql for sql in executed_sqls)

    @pytest.mark.asyncio
    async def test_throttle_blocked_skips_all(self):
        """Redis 节流未通过 → 完全跳过"""
        pool = _make_pool()

        mock_redis = MagicMock()
        mock_redis.try_throttle = AsyncMock(return_value=False)
        with patch("core.redis.RedisClient", mock_redis):
            await pool._throttled_kit_refresh()

        pool.db.pool.connection.assert_not_called()
