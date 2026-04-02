"""
ERP 分层对账单元测试

覆盖：erp_sync_reconcile（工具函数 + 订单对账 + 售后对账）
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── _time_slots ─────────────────────────────────────────


class TestTimeSlots:

    def test_full_day_2hour_slots(self):
        from services.kuaimai.erp_sync_reconcile import _time_slots
        start = datetime(2026, 4, 1, 0, 0, 0)
        end = datetime(2026, 4, 2, 0, 0, 0)
        slots = _time_slots(start, end)
        assert len(slots) == 12
        assert slots[0] == (datetime(2026, 4, 1, 0), datetime(2026, 4, 1, 2))
        assert slots[-1] == (datetime(2026, 4, 1, 22), datetime(2026, 4, 2, 0))

    def test_last_slot_does_not_exceed_end(self):
        """hour=22 + 2 = 24，确保不会 replace(hour=24) 崩溃"""
        from services.kuaimai.erp_sync_reconcile import _time_slots
        start = datetime(2026, 4, 1, 22, 0, 0)
        end = datetime(2026, 4, 2, 0, 0, 0)
        slots = _time_slots(start, end)
        assert len(slots) == 1
        assert slots[0] == (datetime(2026, 4, 1, 22), datetime(2026, 4, 2, 0))

    def test_custom_hours(self):
        from services.kuaimai.erp_sync_reconcile import _time_slots
        start = datetime(2026, 4, 1, 0, 0, 0)
        end = datetime(2026, 4, 2, 0, 0, 0)
        slots = _time_slots(start, end, hours=6)
        assert len(slots) == 4

    def test_empty_range(self):
        from services.kuaimai.erp_sync_reconcile import _time_slots
        t = datetime(2026, 4, 1, 0, 0, 0)
        assert _time_slots(t, t) == []


# ── _ALLOWED_TIME_COLS 白名单 ───────────────────────────


class TestAllowedTimeCols:

    @pytest.mark.asyncio
    async def test_invalid_time_col_raises(self):
        from services.kuaimai.erp_sync_reconcile import _db_count_distinct
        pool = MagicMock()
        with pytest.raises(ValueError, match="Invalid time_col"):
            await _db_count_distinct(pool, "order", "injected_col", datetime.now(), datetime.now(), None)

    @pytest.mark.asyncio
    async def test_invalid_time_col_in_existing_ids(self):
        from services.kuaimai.erp_sync_reconcile import _db_existing_ids
        pool = MagicMock()
        with pytest.raises(ValueError, match="Invalid time_col"):
            await _db_existing_ids(pool, "order", "bad_col", datetime.now(), datetime.now(), None)


# ── _yesterday_range ────────────────────────────────────


class TestYesterdayRange:

    def test_returns_yesterday_midnight(self):
        from services.kuaimai.erp_sync_reconcile import _yesterday_range
        y, t = _yesterday_range()
        assert y.hour == 0 and y.minute == 0 and y.second == 0
        assert t - y == timedelta(days=1)
        assert t.date() > y.date()


# ── reconcile_order ─────────────────────────────────────


def _mock_svc(org_id="org-1"):
    """创建 mock ErpSyncService"""
    svc = MagicMock()
    svc.org_id = org_id
    svc.settings = MagicMock()
    svc.settings.erp_reconcile_tolerance = 5
    client = MagicMock()
    client.request_with_retry = AsyncMock()
    svc._get_client.return_value = client
    svc.upsert_document_items = AsyncMock(side_effect=lambda rows: len(rows))
    svc.collect_affected_keys = MagicMock(return_value=[])
    svc.run_aggregation = AsyncMock()
    svc.sort_and_assign_index = MagicMock(side_effect=lambda items, t: [
        {**item, "_item_index": i} for i, item in enumerate(items)
    ])
    return svc, client


def _mock_pool(db_count=100, db_sids=None):
    """创建 mock async pool"""
    pool = MagicMock()
    conn = AsyncMock()
    cur = AsyncMock()

    # COUNT 查询返回
    count_row = MagicMock()
    count_row.__getitem__ = MagicMock(return_value=db_count)
    count_row.values = MagicMock(return_value=[db_count])
    # values() 返回 list → isinstance(row, dict) 判断为 False
    type(count_row).__iter__ = MagicMock(return_value=iter([db_count]))

    # DISTINCT 查询返回
    if db_sids is not None:
        id_rows = [{"doc_id": sid} for sid in db_sids]
    else:
        id_rows = []

    # 根据 SQL 区分 COUNT vs DISTINCT
    call_count = {"n": 0}

    async def execute_side_effect(sql, params=None):
        call_count["n"] += 1
        mock_cur = AsyncMock()
        if "COUNT" in sql:
            mock_cur.fetchone = AsyncMock(return_value=count_row)
        else:
            mock_cur.fetchall = AsyncMock(return_value=id_rows)
        return mock_cur

    conn.execute = execute_side_effect

    # connection() 返回异步上下文管理器
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection = MagicMock(return_value=cm)

    return pool


class TestReconcileOrder:

    @pytest.mark.asyncio
    async def test_all_slots_matched_returns_zero(self):
        """所有时段 COUNT 一致 → 返回 0，不拉明细"""
        from services.kuaimai.erp_sync_reconcile import reconcile_order

        svc, client = _mock_svc()
        pool = _mock_pool(db_count=100)
        svc.db = MagicMock()
        svc.db._pool = pool

        # API 返回 total=100，和 DB 一致
        client.request_with_retry = AsyncMock(return_value={"total": 100})

        with patch("services.kuaimai.erp_sync_reconcile._yesterday_range") as mock_yr:
            mock_yr.return_value = (datetime(2026, 4, 1), datetime(2026, 4, 2))
            result = await reconcile_order(svc)

        assert result == 0
        # 12 个时段 × 1 次 COUNT API
        assert client.request_with_retry.call_count == 12

    @pytest.mark.asyncio
    async def test_no_pool_returns_zero(self):
        """无 async pool → 返回 0"""
        from services.kuaimai.erp_sync_reconcile import reconcile_order

        svc, _ = _mock_svc()
        svc.db = MagicMock(spec=[])  # 无 _pool 属性

        result = await reconcile_order(svc)
        assert result == 0

    @pytest.mark.asyncio
    async def test_mismatch_triggers_backfill(self):
        """COUNT 不匹配 → 进入第二层拉明细补漏"""
        from services.kuaimai.erp_sync_reconcile import reconcile_order

        svc, client = _mock_svc()
        pool = _mock_pool(db_count=90, db_sids=["existing-1"])
        svc.db = MagicMock()
        svc.db._pool = pool

        # COUNT 返回 100，DB 返回 90 → 差 10 > tolerance(5)
        # 第二层拉明细返回含缺失订单
        missing_doc = {
            "sid": "new-1",
            "sysStatus": "SELLER_SEND_GOODS",
            "orders": [{"sysItemOuterId": "P001", "sysOuterId": "S001",
                        "title": "test", "num": 1, "price": 10,
                        "payment": 10, "cost": 5}],
            "discountFee": 0,
        }

        call_idx = {"n": 0}

        async def api_side_effect(method, params):
            call_idx["n"] += 1
            if params.get("pageSize") == 20:
                # COUNT 阶段
                return {"total": 100}
            else:
                # 明细拉取阶段：第一页返回数据，后续空
                if params.get("pageNo") == 1:
                    return {"list": [missing_doc]}
                return {"list": []}

        client.request_with_retry = AsyncMock(side_effect=api_side_effect)

        with patch("services.kuaimai.erp_sync_reconcile._yesterday_range") as mock_yr:
            mock_yr.return_value = (datetime(2026, 4, 1), datetime(2026, 4, 2))
            result = await reconcile_order(svc)

        assert result > 0
        svc.upsert_document_items.assert_called()

    @pytest.mark.asyncio
    async def test_api_error_marks_slot_as_mismatch(self):
        """API 异常 → 该时段标记为需补漏"""
        from services.kuaimai.erp_sync_reconcile import reconcile_order

        svc, client = _mock_svc()
        pool = _mock_pool(db_count=0, db_sids=[])
        svc.db = MagicMock()
        svc.db._pool = pool

        call_idx = {"n": 0}

        async def api_side_effect(method, params):
            call_idx["n"] += 1
            if params.get("pageSize") == 20:
                # 第1个时段 COUNT 抛异常，其余返回0
                if call_idx["n"] == 1:
                    raise ConnectionError("timeout")
                return {"total": 0}
            return {"list": []}

        client.request_with_retry = AsyncMock(side_effect=api_side_effect)

        with patch("services.kuaimai.erp_sync_reconcile._yesterday_range") as mock_yr:
            mock_yr.return_value = (datetime(2026, 4, 1), datetime(2026, 4, 2))
            result = await reconcile_order(svc)

        # 第1个时段 COUNT 失败 → 进入明细拉取 → 返回空 → 补0条
        assert result == 0


# ── reconcile_aftersale ─────────────────────────────────


class TestReconcileAftersale:

    @pytest.mark.asyncio
    async def test_all_matched_returns_zero(self):
        from services.kuaimai.erp_sync_reconcile import reconcile_aftersale

        svc, client = _mock_svc()
        pool = _mock_pool(db_count=50)
        svc.db = MagicMock()
        svc.db._pool = pool
        client.request_with_retry = AsyncMock(return_value={"total": 50})

        with patch("services.kuaimai.erp_sync_reconcile._yesterday_range") as mock_yr:
            mock_yr.return_value = (datetime(2026, 4, 1), datetime(2026, 4, 2))
            result = await reconcile_aftersale(svc)

        assert result == 0

    @pytest.mark.asyncio
    async def test_mismatch_backfills_aftersale(self):
        from services.kuaimai.erp_sync_reconcile import reconcile_aftersale

        svc, client = _mock_svc()
        pool = _mock_pool(db_count=40, db_sids=["existing-1"])
        svc.db = MagicMock()
        svc.db._pool = pool

        missing_doc = {
            "id": "as-new-1",
            "status": "FINISHED",
            "items": [{"mainOuterId": "P001", "outerId": "S001",
                       "title": "refund item", "receivableCount": 1,
                       "price": 20, "payment": 20}],
        }

        async def api_side_effect(method, params):
            if params.get("pageSize") == 20:
                return {"total": 50}
            if params.get("pageNo") == 1:
                return {"list": [missing_doc]}
            return {"list": []}

        client.request_with_retry = AsyncMock(side_effect=api_side_effect)

        with patch("services.kuaimai.erp_sync_reconcile._yesterday_range") as mock_yr:
            mock_yr.return_value = (datetime(2026, 4, 1), datetime(2026, 4, 2))
            result = await reconcile_aftersale(svc)

        assert result > 0
        svc.upsert_document_items.assert_called()
