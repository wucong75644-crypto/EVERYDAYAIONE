"""
ErpSyncOrchestrator 单元测试
覆盖：队列模式启动、fallback 启动、Redis 恢复热切换、优雅停止
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


def _make_orchestrator(**overrides):
    """创建 Orchestrator 实例（mock 所有外部依赖）"""
    with patch("services.kuaimai.erp_sync_orchestrator.get_settings") as mock_s:
        settings = MagicMock()
        settings.erp_sync_enabled = True
        settings.erp_sync_worker_count = 2
        settings.erp_sync_max_org_concurrency = 3
        for k, v in overrides.items():
            setattr(settings, k, v)
        mock_s.return_value = settings

        from services.kuaimai.erp_sync_orchestrator import ErpSyncOrchestrator
        orch = ErpSyncOrchestrator(db=MagicMock())
    return orch


# ── start() 模式选择 ─────────────────────────────────

class TestStartModeSelection:

    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self):
        orch = _make_orchestrator(erp_sync_enabled=False)
        await orch.start()
        assert orch.is_running is False
        assert orch._mode == "idle"

    @pytest.mark.asyncio
    async def test_redis_available_starts_queue_mode(self):
        orch = _make_orchestrator()
        orch._is_redis_available = AsyncMock(return_value=True)
        orch._start_queue_mode = AsyncMock()

        await orch.start()

        assert orch.is_running is True
        orch._start_queue_mode.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_unavailable_starts_fallback(self):
        orch = _make_orchestrator()
        orch._is_redis_available = AsyncMock(return_value=False)
        orch._start_fallback_mode = AsyncMock()

        await orch.start()

        assert orch.is_running is True
        orch._start_fallback_mode.assert_called_once()


# ── _start_queue_mode ────────────────────────────────

class TestQueueMode:

    @pytest.mark.asyncio
    async def test_creates_scheduler_and_pool(self):
        orch = _make_orchestrator()

        with patch("services.kuaimai.erp_sync_scheduler.ErpSyncScheduler") as MockSched, \
             patch("services.kuaimai.erp_sync_worker_pool.ErpSyncWorkerPool") as MockPool:
            mock_sched = MagicMock()
            mock_sched.start = AsyncMock()
            MockSched.return_value = mock_sched

            mock_pool = MagicMock()
            mock_pool.start = AsyncMock()
            MockPool.return_value = mock_pool

            await orch._start_queue_mode()

        assert orch._mode == "queue"
        assert hasattr(orch, "_scheduler")
        assert hasattr(orch, "_worker_pool")
        # 4 tasks: scheduler + pool + aggregation + dead_letter
        assert len(orch._tasks) == 4


# ── _start_fallback_mode ─────────────────────────────

class TestFallbackMode:

    @pytest.mark.asyncio
    async def test_creates_legacy_worker(self):
        orch = _make_orchestrator()

        with patch("services.kuaimai.erp_sync_worker.ErpSyncWorker") as MockWorker:
            mock_worker = MagicMock()
            mock_worker.start = AsyncMock()
            MockWorker.return_value = mock_worker

            await orch._start_fallback_mode()

        assert orch._mode == "fallback"
        assert hasattr(orch, "_fallback_worker")
        # 2 tasks: fallback worker + redis recovery probe
        assert len(orch._tasks) == 2


# ── stop() ───────────────────────────────────────────

class TestStop:

    @pytest.mark.asyncio
    async def test_stop_clears_tasks(self):
        orch = _make_orchestrator()
        orch.is_running = True
        orch._mode = "queue"
        orch._scheduler = MagicMock()
        orch._scheduler.stop = AsyncMock()
        orch._worker_pool = MagicMock()
        orch._worker_pool.stop = AsyncMock()

        # Add a dummy completed task
        async def _noop():
            pass
        task = asyncio.create_task(_noop())
        await task  # Let it finish
        orch._tasks.append(task)

        await orch.stop()

        assert orch.is_running is False
        assert orch._mode == "idle"
        assert len(orch._tasks) == 0


# ── _redis_recovery_probe ────────────────────────────

class TestRedisRecoveryProbe:

    @pytest.mark.asyncio
    async def test_switches_to_queue_mode_on_recovery(self):
        orch = _make_orchestrator()
        orch.is_running = True
        orch._mode = "fallback"

        call_count = 0

        async def mock_redis_check():
            nonlocal call_count
            call_count += 1
            return call_count >= 2  # Available on second check

        orch._is_redis_available = mock_redis_check
        orch._stop_current_mode = AsyncMock()
        orch._start_queue_mode = AsyncMock()

        # Patch the sleep to speed up test
        with patch("services.kuaimai.erp_sync_orchestrator._REDIS_PROBE_INTERVAL", 0.05):
            await orch._redis_recovery_probe()

        orch._stop_current_mode.assert_called_once()
        orch._start_queue_mode.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_switch_failure(self):
        orch = _make_orchestrator()
        orch.is_running = True
        orch._mode = "fallback"

        orch._is_redis_available = AsyncMock(return_value=True)
        orch._stop_current_mode = AsyncMock()
        orch._start_queue_mode = AsyncMock(side_effect=Exception("boom"))
        orch._start_fallback_mode = AsyncMock()

        with patch("services.kuaimai.erp_sync_orchestrator._REDIS_PROBE_INTERVAL", 0.01):
            await orch._redis_recovery_probe()

        orch._start_fallback_mode.assert_called_once()


# ── _stop_current_mode ───────────────────────────────

class TestStopCurrentMode:

    @pytest.mark.asyncio
    async def test_cleans_up_scheduler_and_pool(self):
        orch = _make_orchestrator()
        mock_sched = MagicMock()
        mock_sched.stop = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.stop = AsyncMock()
        orch._scheduler = mock_sched
        orch._worker_pool = mock_pool
        orch._tasks = []

        await orch._stop_current_mode()

        mock_sched.stop.assert_called_once()
        mock_pool.stop.assert_called_once()
        assert orch._mode == "idle"
        assert not hasattr(orch, "_scheduler")
        assert not hasattr(orch, "_worker_pool")

    @pytest.mark.asyncio
    async def test_cleans_up_fallback_worker(self):
        orch = _make_orchestrator()
        mock_worker = MagicMock()
        mock_worker.stop = AsyncMock()
        orch._fallback_worker = mock_worker
        orch._tasks = []

        await orch._stop_current_mode()

        mock_worker.stop.assert_called_once()
        assert not hasattr(orch, "_fallback_worker")


# ── _is_redis_available ──────────────────────────────

class TestIsRedisAvailable:

    @pytest.mark.asyncio
    async def test_returns_true_when_healthy(self):
        from services.kuaimai.erp_sync_orchestrator import ErpSyncOrchestrator
        with patch("core.redis.RedisClient.health_check", new_callable=AsyncMock, return_value=True):
            assert await ErpSyncOrchestrator._is_redis_available() is True

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self):
        from services.kuaimai.erp_sync_orchestrator import ErpSyncOrchestrator
        with patch("core.redis.RedisClient.health_check", new_callable=AsyncMock, side_effect=Exception("down")):
            assert await ErpSyncOrchestrator._is_redis_available() is False
