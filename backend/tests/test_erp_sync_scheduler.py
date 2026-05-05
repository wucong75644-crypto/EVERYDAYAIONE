"""
ErpSyncScheduler 单元测试
覆盖：调度器的到期判断、任务入队、去重、首轮保护、mark_completed
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

# PR3：scheduler 改用 utils.time_context.now_cn() (aware)
# 测试 fixture 也必须用 aware datetime（CN_TZ）以匹配新行为
#
# 注意：原想用 time-machine 冻结时间，但发现在 macOS + Python 3.12 环境下
# time-machine 与 pandas/pyarrow C 扩展联跑时会触发 segfault。
# 回退到 unittest.mock.patch 直接 patch now_cn()，避免 C 扩展冲突。
_CN = ZoneInfo("Asia/Shanghai")

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── build_task_id / parse_task_id ────────────────────

class TestTaskIdSerialization:

    def test_build_task_id_with_org(self):
        from services.kuaimai.erp_sync_scheduler import _build_task_id
        assert _build_task_id("org-123", "product") == "org-123:product"

    def test_build_task_id_none_org(self):
        from services.kuaimai.erp_sync_scheduler import _build_task_id
        assert _build_task_id(None, "stock") == "__default__:stock"

    def test_parse_task_id_with_org(self):
        from services.kuaimai.erp_sync_scheduler import parse_task_id
        org_id, sync_type = parse_task_id("org-123:product")
        assert org_id == "org-123"
        assert sync_type == "product"

    def test_parse_task_id_default(self):
        from services.kuaimai.erp_sync_scheduler import parse_task_id
        org_id, sync_type = parse_task_id("__default__:stock")
        assert org_id is None
        assert sync_type == "stock"

    def test_parse_task_id_invalid(self):
        from services.kuaimai.erp_sync_scheduler import parse_task_id
        with pytest.raises(ValueError, match="Invalid task_id"):
            parse_task_id("no-colon-here")

    def test_roundtrip(self):
        from services.kuaimai.erp_sync_scheduler import _build_task_id, parse_task_id
        for org_id in ["abc-def", None]:
            for sync_type in ["product", "daily_maintenance"]:
                task_id = _build_task_id(org_id, sync_type)
                parsed_org, parsed_type = parse_task_id(task_id)
                assert parsed_org == org_id
                assert parsed_type == sync_type


# ── _is_interval_due ─────────────────────────────────

class TestIsIntervalDue:

    def test_none_means_due(self):
        from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
        assert ErpSyncScheduler._is_interval_due({}, "org1", 3600) is True

    def test_not_due_yet(self):
        from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
        last_map = {"org1": datetime.now(_CN) - timedelta(seconds=10)}
        assert ErpSyncScheduler._is_interval_due(last_map, "org1", 3600) is False

    def test_is_due(self):
        from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
        last_map = {"org1": datetime.now(_CN) - timedelta(seconds=7200)}
        assert ErpSyncScheduler._is_interval_due(last_map, "org1", 3600) is True

    def test_different_org_isolated(self):
        from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
        last_map = {"org1": datetime.now(_CN)}
        # org2 not in map → due
        assert ErpSyncScheduler._is_interval_due(last_map, "org2", 3600) is True


# ── _get_due_types ───────────────────────────────────

class TestGetDueTypes:

    def _make_scheduler(self):
        with patch("services.kuaimai.erp_sync_scheduler.get_settings") as mock:
            settings = MagicMock()
            settings.erp_sync_interval = 60
            settings.erp_platform_map_interval = 21600
            settings.erp_stock_full_refresh_interval = 3600
            settings.erp_sync_queue_key = "erp_tasks"
            settings.erp_sync_worker_count = 10
            mock.return_value = settings
            from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
            s = ErpSyncScheduler(db=MagicMock())
        return s

    def test_first_round_only_high_freq(self):
        s = self._make_scheduler()
        assert s._first_round is True
        due = s._get_due_types("org1")
        from services.kuaimai.erp_sync_scheduler import HIGH_FREQ_TYPES
        assert due == list(HIGH_FREQ_TYPES)
        # No low-freq or special types
        assert "platform_map" not in due
        assert "stock_full" not in due
        assert "daily_maintenance" not in due

    def test_second_round_includes_low_freq(self):
        s = self._make_scheduler()
        s._first_round = False
        due = s._get_due_types("org1")
        # First time → all intervals are due
        assert "platform_map" in due
        assert "stock_full" in due
        assert "daily_maintenance" in due

    def test_second_round_low_freq_not_due(self):
        s = self._make_scheduler()
        s._first_round = False
        # Mark recent execution（aware datetime，匹配 PR3 后的 _is_interval_due）
        now = datetime.now(_CN)
        s._org_last_platform_map["org1"] = now
        s._org_last_stock_full["org1"] = now
        s._org_last_daily["org1"] = now
        due = s._get_due_types("org1")
        assert "platform_map" not in due
        assert "stock_full" not in due
        assert "daily_maintenance" not in due


# ── mark_completed ───────────────────────────────────

class TestMarkCompleted:

    def _make_scheduler(self):
        with patch("services.kuaimai.erp_sync_scheduler.get_settings") as mock:
            settings = MagicMock()
            mock.return_value = settings
            from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
            return ErpSyncScheduler(db=MagicMock())

    def test_mark_platform_map(self):
        s = self._make_scheduler()
        s.mark_completed("org1", "platform_map")
        assert "org1" in s._org_last_platform_map

    def test_mark_stock_full(self):
        s = self._make_scheduler()
        s.mark_completed("org1", "stock_full")
        assert "org1" in s._org_last_stock_full

    def test_mark_daily_maintenance(self):
        s = self._make_scheduler()
        s.mark_completed(None, "daily_maintenance")
        assert None in s._org_last_daily

    def test_mark_high_freq_no_effect(self):
        s = self._make_scheduler()
        s.mark_completed("org1", "product")
        # High-freq types don't update any timestamp
        assert "org1" not in s._org_last_platform_map
        assert "org1" not in s._org_last_stock_full
        assert "org1" not in s._org_last_daily


# ── _schedule_round ──────────────────────────────────

class TestScheduleRound:

    @pytest.mark.asyncio
    async def test_enqueues_tasks_for_orgs(self):
        with patch("services.kuaimai.erp_sync_scheduler.get_settings") as mock_s:
            settings = MagicMock()
            settings.erp_sync_interval = 60
            settings.erp_platform_map_interval = 21600
            settings.erp_stock_full_refresh_interval = 3600
            settings.erp_sync_queue_key = "erp_tasks"
            settings.erp_sync_worker_count = 10
            mock_s.return_value = settings

            from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
            s = ErpSyncScheduler(db=MagicMock())

        s._load_erp_org_ids = AsyncMock(return_value=["org-a", "org-b"])
        s._enqueue_task = AsyncMock(return_value=True)
        s._check_queue_depth = AsyncMock()

        await s._schedule_round()

        # 2 orgs × 9 high-freq types = 18 calls (first round)
        assert s._enqueue_task.call_count == 18
        assert s._first_round is False  # Marked after first round

    @pytest.mark.asyncio
    async def test_empty_orgs_no_enqueue(self):
        with patch("services.kuaimai.erp_sync_scheduler.get_settings") as mock_s:
            settings = MagicMock()
            settings.erp_sync_interval = 60
            mock_s.return_value = settings
            from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
            s = ErpSyncScheduler(db=MagicMock())

        s._load_erp_org_ids = AsyncMock(return_value=[])
        s._enqueue_task = AsyncMock()
        s._check_queue_depth = AsyncMock()

        await s._schedule_round()
        s._enqueue_task.assert_not_called()


# ── PRIORITY_WEIGHTS ─────────────────────────────────

class TestPriorityWeights:

    def test_order_highest_priority(self):
        from services.kuaimai.erp_sync_scheduler import PRIORITY_WEIGHTS
        assert PRIORITY_WEIGHTS["order"] >= PRIORITY_WEIGHTS["stock"]
        assert PRIORITY_WEIGHTS["stock"] >= PRIORITY_WEIGHTS["product"]
        assert PRIORITY_WEIGHTS["product"] >= PRIORITY_WEIGHTS["platform_map"]

    def test_all_sync_types_have_weight(self):
        from services.kuaimai.erp_sync_scheduler import (
            PRIORITY_WEIGHTS, HIGH_FREQ_TYPES, LOW_FREQ_TYPES, SPECIAL_TYPES,
        )
        all_types = HIGH_FREQ_TYPES + LOW_FREQ_TYPES + SPECIAL_TYPES
        for t in all_types:
            assert t in PRIORITY_WEIGHTS, f"Missing priority weight for {t}"

    def test_reconcile_types_registered(self):
        from services.kuaimai.erp_sync_scheduler import PRIORITY_WEIGHTS, SPECIAL_TYPES
        assert "order_reconcile" in PRIORITY_WEIGHTS
        assert "aftersale_reconcile" in PRIORITY_WEIGHTS
        assert "order_reconcile" in SPECIAL_TYPES
        assert "aftersale_reconcile" in SPECIAL_TYPES


# ── 对账调度 ────────────────────────────────────────────


class TestReconcileScheduling:

    def _make_scheduler(self, reconcile_hour=3):
        with patch("services.kuaimai.erp_sync_scheduler.get_settings") as mock:
            settings = MagicMock()
            settings.erp_sync_interval = 60
            settings.erp_platform_map_interval = 21600
            settings.erp_stock_full_refresh_interval = 3600
            settings.erp_reconcile_hour = reconcile_hour
            settings.erp_reconcile_interval = 86400
            settings.erp_sync_queue_key = "erp_tasks"
            settings.erp_sync_worker_count = 10
            mock.return_value = settings
            from services.kuaimai.erp_sync_scheduler import ErpSyncScheduler
            s = ErpSyncScheduler(db=MagicMock())
        return s

    # PR3：scheduler 用 now_cn() 取北京时间。
    # 用 unittest.mock.patch("services.kuaimai.erp_sync_scheduler.now_cn")
    # 替代 time-machine 以避免 C 扩展 segfault（见模块顶部说明）。

    @patch("services.kuaimai.erp_sync_scheduler.now_cn")
    def test_reconcile_due_at_correct_hour(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 2, 3, 15, 0, tzinfo=_CN)
        s = self._make_scheduler(reconcile_hour=3)
        s._first_round = False
        due = s._get_due_types("org1")
        assert "order_reconcile" in due
        assert "aftersale_reconcile" in due

    @patch("services.kuaimai.erp_sync_scheduler.now_cn")
    def test_reconcile_not_due_wrong_hour(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 2, 10, 0, 0, tzinfo=_CN)
        s = self._make_scheduler(reconcile_hour=3)
        s._first_round = False
        due = s._get_due_types("org1")
        assert "order_reconcile" not in due
        assert "aftersale_reconcile" not in due

    @patch("services.kuaimai.erp_sync_scheduler.now_cn")
    def test_reconcile_not_due_if_recently_completed(self, mock_now):
        mock_now.return_value = datetime(2026, 4, 2, 3, 30, 0, tzinfo=_CN)
        s = self._make_scheduler(reconcile_hour=3)
        s._first_round = False
        # 刚完成过（aware datetime，PR3 后必须带 tzinfo）
        s._org_last_order_reconcile["org1"] = datetime(2026, 4, 2, 3, 5, 0, tzinfo=_CN)
        s._org_last_aftersale_reconcile["org1"] = datetime(2026, 4, 2, 3, 5, 0, tzinfo=_CN)
        due = s._get_due_types("org1")
        assert "order_reconcile" not in due
        assert "aftersale_reconcile" not in due

    def test_mark_completed_independent_tracking(self):
        """订单和售后完成时间戳独立追踪"""
        s = self._make_scheduler()
        s.mark_completed("org1", "order_reconcile")
        assert "org1" in s._org_last_order_reconcile
        assert "org1" not in s._org_last_aftersale_reconcile

        s.mark_completed("org1", "aftersale_reconcile")
        assert "org1" in s._org_last_aftersale_reconcile
