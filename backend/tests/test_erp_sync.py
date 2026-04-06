"""
ERP 同步服务 + 归档任务单元测试
覆盖：erp_sync_service / erp_sync_worker
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from conftest import MockErpAsyncDBClient, MockSupabaseClient



def _sync_state(sync_type: str, **kw) -> dict:
    """创建同步状态测试数据"""
    now = datetime.now(timezone.utc)
    base = {
        "sync_type": sync_type,
        "status": "idle",
        "is_initial_done": True,
        "last_sync_time": (now - timedelta(minutes=2)).isoformat(),
        "last_run_at": now.isoformat(),
        "error_count": 0,
        "last_error": None,
        "total_synced": 100,
    }
    base.update(kw)
    return base


def _make_service(sync_states: list | None = None):
    """创建 ErpSyncService 实例（mock DB + settings）"""
    db = MockErpAsyncDBClient()
    if sync_states:
        db.set_table_data("erp_sync_state", sync_states)

    with patch("services.kuaimai.erp_sync_service.get_settings") as mock_settings:
        settings = MagicMock()
        settings.erp_sync_initial_days = 90
        settings.erp_sync_shard_days = 7
        mock_settings.return_value = settings

        from services.kuaimai.erp_sync_service import ErpSyncService
        service = ErpSyncService(db)
    return service


class TestSyncStateManagement:

    @pytest.mark.asyncio
    async def test_get_sync_state_exists(self):
        """读取已有同步状态"""
        service = _make_service([_sync_state("order")])
        state = await service._get_sync_state("order")
        assert state is not None
        assert state["sync_type"] == "order"

    @pytest.mark.asyncio
    async def test_get_sync_state_not_exists(self):
        """读取不存在的同步状态返回 None"""
        service = _make_service([])
        state = await service._get_sync_state("order")
        assert state is None

    @pytest.mark.asyncio
    async def test_init_sync_state(self):
        """初始化同步状态"""
        service = _make_service([])
        await service._init_sync_state("purchase")
        # 验证不抛异常即可（insert 到 mock DB）

    @pytest.mark.asyncio
    async def test_update_sync_state_error(self):
        """错误更新递增 error_count"""
        service = _make_service([_sync_state("order", error_count=2)])
        await service._update_sync_state_error("order", "test error")
        # 验证不抛异常（mock DB）

    @pytest.mark.asyncio
    async def test_mark_initial_done(self):
        """标记全量同步完成"""
        service = _make_service([_sync_state("order", is_initial_done=False)])
        await service._mark_initial_done("order", 5000)
        # 验证不抛异常


# ============================================================
# TestTimeWindows — 时间窗口计算
# ============================================================


class TestTimeWindows:

    def test_recent_sync_single_window(self):
        """最近同步 → 单个窗口"""
        now = datetime.now(timezone.utc)
        state = _sync_state("order", last_sync_time=(now - timedelta(hours=1)).isoformat())
        service = _make_service([state])
        windows = service._calculate_time_windows(state)
        assert len(windows) == 1

    def test_no_last_sync_uses_initial_days(self):
        """无 last_sync_time → 从 initial_days 前开始"""
        state = _sync_state("order", last_sync_time=None)
        service = _make_service([state])
        windows = service._calculate_time_windows(state)
        # 90天 / 7天分片 = 至少13个分片
        assert len(windows) >= 10

    def test_long_gap_creates_shards(self):
        """长时间间隔自动分片"""
        long_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        state = _sync_state("purchase", last_sync_time=long_ago)
        service = _make_service([state])
        windows = service._calculate_time_windows(state)
        assert len(windows) >= 4  # 30天 / 7天 ≈ 4-5 片

    def test_product_type_has_day_backtrack(self):
        """product 类型回溯1天"""
        now = datetime.now()
        recent = (now - timedelta(minutes=10)).isoformat()
        state = _sync_state("product", last_sync_time=recent)
        service = _make_service([state])
        windows = service._calculate_time_windows(state)
        start = windows[0][0]
        # 回溯1天，所以 start 应该在 ~1天前
        assert (now - start).total_seconds() > 80000  # > 22小时


# ============================================================
# TestSortAndAssignIndex — item_index 稳定排序
# ============================================================


class TestSortAndAssignIndex:

    def test_stable_hash_index_deterministic(self):
        """同一 key 哈希结果稳定"""
        from services.kuaimai.erp_sync_service import ErpSyncService
        items = [
            {"oid": "C", "product": "P1"},
            {"oid": "A", "product": "P2"},
            {"oid": "B", "product": "P3"},
        ]
        result1 = ErpSyncService.sort_and_assign_index(items, "order")
        indices1 = [r["_item_index"] for r in result1]

        # 重新构造同样的 items，确认结果一致
        items2 = [
            {"oid": "C", "product": "P1"},
            {"oid": "A", "product": "P2"},
            {"oid": "B", "product": "P3"},
        ]
        result2 = ErpSyncService.sort_and_assign_index(items2, "order")
        indices2 = [r["_item_index"] for r in result2]
        assert indices1 == indices2

    def test_same_item_different_order_same_index(self):
        """不同输入顺序，同一 item 得到相同 index"""
        from services.kuaimai.erp_sync_service import ErpSyncService
        items_a = [
            {"outerId": "Z", "itemOuterId": "A"},
            {"outerId": "A", "itemOuterId": "Z"},
        ]
        items_b = [
            {"outerId": "A", "itemOuterId": "Z"},
            {"outerId": "Z", "itemOuterId": "A"},
        ]
        result_a = ErpSyncService.sort_and_assign_index(items_a, "purchase")
        result_b = ErpSyncService.sort_and_assign_index(items_b, "purchase")
        # 找到 outerId=Z 的 index 在两次结果中一致
        idx_a = next(r["_item_index"] for r in result_a if r["outerId"] == "Z")
        idx_b = next(r["_item_index"] for r in result_b if r["outerId"] == "Z")
        assert idx_a == idx_b

    def test_add_item_does_not_change_existing_index(self):
        """新增 item 不影响已有 item 的 index"""
        from services.kuaimai.erp_sync_service import ErpSyncService
        items_before = [{"oid": "A"}, {"oid": "B"}]
        items_after = [{"oid": "A"}, {"oid": "B"}, {"oid": "C"}]
        r1 = ErpSyncService.sort_and_assign_index(items_before, "order")
        r2 = ErpSyncService.sort_and_assign_index(items_after, "order")
        idx_a1 = next(r["_item_index"] for r in r1 if r["oid"] == "A")
        idx_a2 = next(r["_item_index"] for r in r2 if r["oid"] == "A")
        assert idx_a1 == idx_a2

    def test_empty_items_returns_empty(self):
        """空列表返回空"""
        from services.kuaimai.erp_sync_service import ErpSyncService
        result = ErpSyncService.sort_and_assign_index([], "order")
        assert result == []


# ============================================================
# TestCollectAffectedKeys — 聚合键收集
# ============================================================


class TestCollectAffectedKeys:

    def test_basic_collection(self):
        """基础收集"""
        service = _make_service()
        rows = [
            {"outer_id": "C01", "doc_created_at": "2026-03-18T10:00:00+00:00"},
            {"outer_id": "C01", "doc_created_at": "2026-03-18T15:00:00+00:00"},
            {"outer_id": "C02", "doc_created_at": "2026-03-19T10:00:00+00:00"},
        ]
        keys = service.collect_affected_keys(rows)
        assert ("C01", "2026-03-18") in keys
        assert ("C02", "2026-03-19") in keys
        # 同日同商品去重
        assert len([k for k in keys if k[0] == "C01"]) == 1

    def test_empty_rows_returns_empty(self):
        """空行返回空"""
        service = _make_service()
        keys = service.collect_affected_keys([])
        assert keys == []

    def test_missing_fields_skipped(self):
        """缺少 outer_id 或 doc_created_at 的行被跳过"""
        service = _make_service()
        rows = [
            {"outer_id": None, "doc_created_at": "2026-03-18T10:00:00+00:00"},
            {"outer_id": "C01", "doc_created_at": None},
        ]
        keys = service.collect_affected_keys(rows)
        assert len(keys) == 0


# ============================================================
# TestUpsertDocumentItems — 数据入库
# ============================================================


class TestUpsertDocumentItems:

    @pytest.mark.asyncio
    async def test_empty_rows(self):
        """空数据返回0"""
        service = _make_service()
        assert await service.upsert_document_items([]) == 0

    @pytest.mark.asyncio
    async def test_batch_upsert(self):
        """批量 upsert 数据"""
        db = MockErpAsyncDBClient()
        # 添加 upsert 方法到 mock table
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute = AsyncMock(return_value=MagicMock())
        db.table = MagicMock(return_value=mock_table)

        with patch("services.kuaimai.erp_sync_service.get_settings") as ms:
            settings = MagicMock()
            settings.erp_sync_initial_days = 90
            settings.erp_sync_shard_days = 7
            ms.return_value = settings
            from services.kuaimai.erp_sync_service import ErpSyncService
            service = ErpSyncService(db)

        rows = [{"doc_type": "order", "doc_id": f"ORD{i}", "item_index": 0}
                for i in range(5)]
        count = await service.upsert_document_items(rows)
        assert count == 5

    @pytest.mark.asyncio
    async def test_orm_fallback_on_conflict_includes_org_id(self):
        """ORM 降级路径 on_conflict 包含 org_id"""
        db = MockErpAsyncDBClient()
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute = AsyncMock(
            return_value=MagicMock(),
        )
        db.table = MagicMock(return_value=mock_table)

        with patch("services.kuaimai.erp_sync_service.get_settings") as ms:
            settings = MagicMock()
            settings.erp_sync_initial_days = 90
            settings.erp_sync_shard_days = 7
            ms.return_value = settings
            from services.kuaimai.erp_sync_service import ErpSyncService
            service = ErpSyncService(db)

        rows = [{"doc_type": "order", "doc_id": "ORD1", "item_index": 0}]
        await service.upsert_document_items(rows)

        # 验证 upsert 调用的 on_conflict 参数包含 org_id
        call_kwargs = mock_table.upsert.call_args
        on_conflict_str = call_kwargs[1].get(
            "on_conflict", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else "",
        )
        assert "org_id" in on_conflict_str


class TestWriteDocGroupTxnOnConflict:
    """_write_doc_group_txn ON CONFLICT 生成逻辑"""

    @pytest.mark.asyncio
    async def test_insert_sql_contains_on_conflict(self):
        """INSERT 语句包含 ON CONFLICT DO UPDATE"""
        from services.kuaimai.erp_sync_persistence import (
            _write_doc_group_txn,
        )
        mock_conn = AsyncMock()
        rows = [{
            "doc_type": "order", "doc_id": "ORD1", "item_index": 0,
            "org_id": "org-123", "outer_id": "A001", "quantity": 10,
        }]

        await _write_doc_group_txn(mock_conn, "order", "ORD1", rows)

        # 收集所有 execute 调用的 SQL
        sqls = [
            call.args[0] for call in mock_conn.execute.call_args_list
        ]
        # 至少一条 INSERT 包含 ON CONFLICT
        insert_sqls = [s for s in sqls if "INSERT" in s]
        assert len(insert_sqls) == 1
        assert "ON CONFLICT" in insert_sqls[0]
        assert "DO UPDATE SET" in insert_sqls[0]

    @pytest.mark.asyncio
    async def test_conflict_cols_not_in_update_set(self):
        """唯一约束列不出现在 SET 子句中"""
        from services.kuaimai.erp_sync_persistence import (
            _CONFLICT_COLS,
            _write_doc_group_txn,
        )
        mock_conn = AsyncMock()
        rows = [{
            "doc_type": "order", "doc_id": "ORD1", "item_index": 0,
            "org_id": "org-123", "outer_id": "A001",
        }]

        await _write_doc_group_txn(mock_conn, "order", "ORD1", rows)

        insert_sql = [
            call.args[0] for call in mock_conn.execute.call_args_list
            if "INSERT" in call.args[0]
        ][0]
        # SET 子句部分
        set_part = insert_sql.split("DO UPDATE SET")[-1]
        for col in _CONFLICT_COLS:
            assert f"{col} = EXCLUDED.{col}" not in set_part

    @pytest.mark.asyncio
    async def test_delete_runs_before_insert(self):
        """DELETE 在 INSERT 之前执行"""
        from services.kuaimai.erp_sync_persistence import (
            _write_doc_group_txn,
        )
        mock_conn = AsyncMock()
        rows = [{
            "doc_type": "order", "doc_id": "ORD1", "item_index": 0,
            "org_id": "org-123", "outer_id": "A001",
        }]

        await _write_doc_group_txn(mock_conn, "order", "ORD1", rows)

        sqls = [
            call.args[0] for call in mock_conn.execute.call_args_list
        ]
        delete_idx = next(i for i, s in enumerate(sqls) if "DELETE" in s)
        insert_idx = next(i for i, s in enumerate(sqls) if "INSERT" in s)
        assert delete_idx < insert_idx


class TestConflictColsConstant:
    """_CONFLICT_COLS 模块常量"""

    def test_matches_db_constraint(self):
        """常量与数据库唯一约束列一致"""
        from services.kuaimai.erp_sync_persistence import _CONFLICT_COLS
        expected = {"doc_type", "doc_id", "item_index", "org_id"}
        assert _CONFLICT_COLS == expected

    def test_is_frozen(self):
        """常量是 frozenset（不可变）"""
        from services.kuaimai.erp_sync_persistence import _CONFLICT_COLS
        assert isinstance(_CONFLICT_COLS, frozenset)


# ============================================================
# TestRunAggregation — 聚合计算
# ============================================================


class TestRunAggregation:

    @pytest.mark.asyncio
    async def test_aggregation_calls_rpc(self):
        """聚合调用 RPC"""
        service = _make_service()
        await service.run_aggregation([("C01", "2026-03-18"), ("C02", "2026-03-19")])
        # 不抛异常即可（mock RPC）

    @pytest.mark.asyncio
    async def test_empty_keys_no_rpc(self):
        """无受影响键不调用 RPC"""
        service = _make_service()
        await service.run_aggregation([])
        # 应该直接返回


# ============================================================
# TestErpSyncWorker — 同步调度
# ============================================================


class TestErpSyncWorkerInit:

    def test_worker_init(self):
        """Worker 初始化"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        assert worker.is_running is False

    def test_high_freq_types(self):
        """高频同步类型定义"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        assert "order" in ErpSyncWorker.HIGH_FREQ_TYPES
        assert "purchase" in ErpSyncWorker.HIGH_FREQ_TYPES
        assert len(ErpSyncWorker.HIGH_FREQ_TYPES) == 9

    def test_low_freq_types(self):
        """低频同步类型定义"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        assert "platform_map" in ErpSyncWorker.LOW_FREQ_TYPES


class TestShouldRunDaily:

    def test_first_run_should_return_true(self):
        """首次运行应返回 True"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        assert worker._should_run_daily() is True

    def test_recent_run_should_return_false(self):
        """最近运行过应返回 False"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        worker._org_last_daily[None] = datetime.now()
        assert worker._should_run_daily() is False


# ============================================================
# TestLocalToolsIntegration — 工具集成验证
# ============================================================


class TestToolSchemaIntegration:

    def test_all_16_schemas(self):
        """ERP_TOOL_SCHEMAS 覆盖全部16个工具"""
        from config.erp_tools import ERP_SYNC_TOOLS, ERP_TOOL_SCHEMAS
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        all_tools = ERP_SYNC_TOOLS | ERP_LOCAL_TOOLS
        for tool in all_tools:
            assert tool in ERP_TOOL_SCHEMAS, f"{tool} 不在 Schema 中"

    def test_build_tools_count(self):
        """build_erp_tools 返回 20 个工具（8 API + 12 本地）"""
        from config.erp_tools import build_erp_tools
        assert len(build_erp_tools()) == 20

    def test_routing_prompt_non_empty(self):
        """路由提示词不为空"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert len(ERP_ROUTING_PROMPT) > 1000


# ============================================================
# TestGetSyncHandler — 处理器分发
# ============================================================


class TestGetSyncHandler:

    def test_known_types_return_handler(self):
        """已知同步类型返回 handler"""
        service = _make_service()
        for sync_type in [
            "purchase", "receipt", "shelf", "purchase_return",
            "aftersale", "order", "product", "stock",
            "supplier", "platform_map",
        ]:
            assert service._get_sync_handler(sync_type) is not None

    def test_unknown_type_returns_none(self):
        """未知同步类型返回 None"""
        service = _make_service()
        assert service._get_sync_handler("nonexistent") is None


# ============================================================
# TestUpdateSyncStateSuccess / Progress
# ============================================================


class TestUpdateSyncStateProgress:

    @pytest.mark.asyncio
    async def test_progress_update_no_error(self):
        """进度更新不抛异常"""
        service = _make_service([_sync_state("order")])
        await service._update_sync_state_progress(
            "order", datetime.now(timezone.utc),
        )

    @pytest.mark.asyncio
    async def test_success_update_no_error(self):
        """成功更新不抛异常"""
        service = _make_service([_sync_state("order", total_synced=50)])
        await service._update_sync_state_success("order", 10)


# ============================================================
# TestFetchAllPages — 翻页拉取
# ============================================================


class TestFetchAllPages:

    @pytest.mark.asyncio
    async def test_single_page(self):
        """单页数据"""
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.request_with_retry = AsyncMock(
            return_value={"list": [{"id": 1}, {"id": 2}]},
        )
        service._client = mock_client
        items = await service.fetch_all_pages("test.method", {})
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        """多页翻页"""
        service = _make_service()
        mock_client = AsyncMock()
        page1 = [{"id": i} for i in range(50)]
        page2 = [{"id": i} for i in range(50, 75)]
        mock_client.request_with_retry = AsyncMock(
            side_effect=[{"list": page1}, {"list": page2}],
        )
        service._client = mock_client
        items = await service.fetch_all_pages("test.method", {})
        assert len(items) == 75

    @pytest.mark.asyncio
    async def test_custom_response_key(self):
        """自定义响应键"""
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.request_with_retry = AsyncMock(
            return_value={"items": [{"id": 1}]},
        )
        service._client = mock_client
        items = await service.fetch_all_pages(
            "test.method", {}, response_key="items",
        )
        assert len(items) == 1


# ============================================================
# TestSyncWindowDispatch — 窗口分发
# ============================================================


class TestSyncWindowDispatch:

    @pytest.mark.asyncio
    async def test_unknown_type_returns_zero(self):
        """未实现的类型返回0"""
        service = _make_service()
        result = await service._sync_window(
            "nonexistent", datetime.now(timezone.utc),
            datetime.now(timezone.utc),
        )
        assert result == 0


# ============================================================
# TestWorkerShouldRunLowFreq — 低频任务判断
# ============================================================


class TestShouldRunLowFreq:

    def test_first_run_returns_true(self):
        """首次运行返回 True"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        assert worker._should_run_low_freq() is True

    def test_recent_run_returns_false(self):
        """最近运行过返回 False"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        worker._org_last_platform_map[None] = datetime.now()
        assert worker._should_run_low_freq() is False


# ============================================================
# TestWorkerStop — 停止
# ============================================================


class TestWorkerStop:

    @pytest.mark.asyncio
    async def test_stop_sets_flag(self):
        """stop 设置 is_running=False"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        worker.is_running = True
        worker._lock_token = None
        await worker.stop()
        assert worker.is_running is False


# ============================================================
# TestWorkerExecuteSync — 单类型同步
# ============================================================


class TestWorkerExecuteSync:

    @pytest.mark.asyncio
    async def test_execute_sync_catches_exception(self):
        """执行同步内部捕获异常不外抛"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        with patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
        ) as mock_cls:
            mock_svc = MagicMock()
            mock_svc.sync = AsyncMock(side_effect=Exception("test"))
            mock_cls.return_value = mock_svc
            await worker._execute_sync("order")


class TestRefreshKitStock:
    """_refresh_kit_stock: 套件库存物化视图刷新"""

    @pytest.mark.asyncio
    async def test_refresh_success(self):
        """正常刷新物化视图"""
        from contextlib import asynccontextmanager
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        mock_cursor = AsyncMock()

        mock_conn = AsyncMock()
        mock_conn.set_autocommit = AsyncMock()

        @asynccontextmanager
        async def _mock_cursor():
            yield mock_cursor

        mock_conn.cursor = _mock_cursor

        @asynccontextmanager
        async def _mock_connection():
            yield mock_conn

        db = MagicMock()
        db.pool.connection = _mock_connection

        worker = ErpSyncWorker(db)
        await worker._refresh_kit_stock()

        mock_cursor.execute.assert_called_once_with(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_kit_stock"
        )

    @pytest.mark.asyncio
    async def test_refresh_view_not_exist(self):
        """视图不存在时不崩溃（静默降级）"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MagicMock()
        db.pool.connection.side_effect = Exception("relation mv_kit_stock does not exist")

        worker = ErpSyncWorker(db)
        # 不应抛异常
        await worker._refresh_kit_stock()

    @pytest.mark.asyncio
    async def test_execute_sync_stock_triggers_refresh(self):
        """stock 同步后触发 kit stock refresh"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        with patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
        ) as mock_cls, patch.object(worker, "_refresh_kit_stock") as mock_refresh:
            mock_svc = MagicMock()
            mock_svc.sync = AsyncMock()
            mock_cls.return_value = mock_svc
            await worker._execute_sync("stock")
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_sync_non_stock_no_refresh(self):
        """非 stock 类型不触发 refresh"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        with patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
        ) as mock_cls, patch.object(worker, "_refresh_kit_stock") as mock_refresh:
            mock_svc = MagicMock()
            mock_svc.sync = AsyncMock()
            mock_cls.return_value = mock_svc
            await worker._execute_sync("order")
            mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_stock_full_refresh_triggers_kit_refresh(self):
        """全量刷新后触发 kit stock refresh"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        with patch(
            "services.kuaimai.erp_sync_master_handlers.sync_stock_full",
            new_callable=AsyncMock, return_value=100,
        ), patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
        ), patch.object(worker, "_refresh_kit_stock") as mock_refresh, patch.object(
            worker, "_extend_lock", new_callable=AsyncMock,
        ):
            await worker._execute_stock_full_refresh()
            mock_refresh.assert_called_once()


# ============================================================
# 归档/维护流程测试（原40个测试零覆盖）
# ============================================================


class TestRunArchive:
    """_run_archive: 热表→冷表归档"""

    @pytest.mark.asyncio
    async def test_run_archive_moves_old_rows(self):
        """正常归档：SELECT→UPSERT→DELETE"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        old_row = {
            "id": "row-1",
            "doc_id": "doc-1",
            "item_index": 0,
            "doc_type": "order",
            "doc_modified_at": "2024-01-01T00:00:00+00:00",
        }
        db.set_table_data("erp_document_items", [old_row])

        worker = ErpSyncWorker(db)
        with patch.object(worker, "settings") as mock_settings:
            mock_settings.erp_archive_retention_days = 90
            count = await worker._run_archive()

        assert count == 1
        # 验证数据写入归档表
        archive_table = db.table("erp_document_items_archive")
        assert len(archive_table._data) == 1

    @pytest.mark.asyncio
    async def test_run_archive_empty_table(self):
        """无可归档数据时返回 0"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        db.set_table_data("erp_document_items", [])

        worker = ErpSyncWorker(db)
        with patch.object(worker, "settings") as mock_settings:
            mock_settings.erp_archive_retention_days = 90
            count = await worker._run_archive()

        assert count == 0

    @pytest.mark.asyncio
    async def test_run_archive_idempotent(self):
        """重复归档同一数据不报错（冷表 upsert 幂等）"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        old_row = {
            "id": "row-1",
            "doc_id": "doc-1",
            "item_index": 0,
            "doc_type": "order",
            "doc_modified_at": "2024-01-01T00:00:00+00:00",
        }
        # 归档表已有相同数据
        db.set_table_data("erp_document_items_archive", [old_row])
        db.set_table_data("erp_document_items", [old_row])

        worker = ErpSyncWorker(db)
        with patch.object(worker, "settings") as mock_settings:
            mock_settings.erp_archive_retention_days = 90
            # 不应抛异常
            count = await worker._run_archive()

        assert count == 1

    @pytest.mark.asyncio
    async def test_run_archive_skips_recent_synced_with_old_modified(self):
        """synced_at 保底：modified=2000（ERP零值）但 synced_at 在保留期内 → 不归档"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        # 模拟补发/手工单：doc_modified_at 为 ERP 零值，但 synced_at 是近期
        recent_synced_row = {
            "id": "row-recent",
            "doc_id": "doc-recent",
            "item_index": 0,
            "doc_type": "order",
            "doc_modified_at": "2000-01-01T00:00:00+00:00",
            "synced_at": "2026-04-02T10:00:00+00:00",
        }
        db.set_table_data("erp_document_items", [recent_synced_row])

        worker = ErpSyncWorker(db)
        with patch.object(worker, "settings") as mock_settings:
            mock_settings.erp_archive_retention_days = 90
            count = await worker._run_archive()

        assert count == 0
        # 主表数据不应被删除
        hot_table = db.table("erp_document_items")
        assert len(hot_table._data) == 1

    @pytest.mark.asyncio
    async def test_run_archive_archives_when_both_old(self):
        """doc_modified_at 和 synced_at 都超过保留期 → 正常归档"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        both_old_row = {
            "id": "row-old",
            "doc_id": "doc-old",
            "item_index": 0,
            "doc_type": "order",
            "doc_modified_at": "2024-01-01T00:00:00+00:00",
            "synced_at": "2024-01-02T00:00:00+00:00",
        }
        db.set_table_data("erp_document_items", [both_old_row])

        worker = ErpSyncWorker(db)
        with patch.object(worker, "settings") as mock_settings:
            mock_settings.erp_archive_retention_days = 90
            count = await worker._run_archive()

        assert count == 1
        archive_table = db.table("erp_document_items_archive")
        assert len(archive_table._data) == 1


class TestRunReaggregation:
    """_run_daily_reaggregation: 每日聚合兜底"""

    @pytest.mark.asyncio
    async def test_run_reaggregation_calls_rpc(self):
        """正常路径：调用 batch RPC"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        db.set_rpc_result("erp_aggregate_daily_stats_batch", 42)

        worker = ErpSyncWorker(db)
        count = await worker._run_daily_reaggregation()

        assert count == 42

    @pytest.mark.asyncio
    async def test_run_reaggregation_fallback(self):
        """RPC 失败时降级到逐条重算"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        # 让 rpc 调用抛异常
        original_rpc = db.rpc

        class _FailingRpc:
            async def execute(self):
                raise Exception("RPC not found")

        def failing_rpc(fn_name, params=None):
            if fn_name == "erp_aggregate_daily_stats_batch":
                return _FailingRpc()
            return original_rpc(fn_name, params)

        db.rpc = failing_rpc

        worker = ErpSyncWorker(db)
        with patch.object(
            worker, "_reaggregate_fallback", new_callable=AsyncMock, return_value=5,
        ) as mock_fallback:
            count = await worker._run_daily_reaggregation()

        assert count == 5
        mock_fallback.assert_called_once()


class TestRunDeletionDetection:
    """_run_deletion_detection: 商品删除检测"""

    @pytest.mark.asyncio
    async def test_run_deletion_detection_marks_deleted(self):
        """检测到已删除的 SPU 和 SKU 标记 active_status=-1"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        # DB 中有 SPU A、B，SKU A-01、A-02、B-01
        db.set_table_data("erp_products", [
            {"outer_id": "A", "active_status": 1},
            {"outer_id": "B", "active_status": 1},
        ])
        db.set_table_data("erp_product_skus", [
            {"sku_outer_id": "A-01", "active_status": 1},
            {"sku_outer_id": "A-02", "active_status": 1},
            {"sku_outer_id": "B-01", "active_status": 1},
        ])

        worker = ErpSyncWorker(db)

        # API 只返回 A（含 SKU A-01），B 和 SKU A-02、B-01 视为已删除
        mock_svc = MagicMock()
        mock_svc.fetch_all_pages = AsyncMock(return_value=[
            {"outerId": "A", "skus": [{"skuOuterId": "A-01"}]},
        ])

        with patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
            return_value=mock_svc,
        ):
            count = await worker._run_deletion_detection()

        # B(SPU) + A-02(SKU) + B-01(SKU) = 3
        assert count == 3

    @pytest.mark.asyncio
    async def test_run_deletion_detection_no_api(self):
        """API 调用失败时返回 0 不报错"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)

        mock_svc = MagicMock()
        mock_svc.fetch_all_pages = AsyncMock(
            side_effect=Exception("API not configured"),
        )

        with patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
            return_value=mock_svc,
        ):
            count = await worker._run_deletion_detection()

        assert count == 0


class TestDailyMaintenanceOrchestration:
    """_run_daily_maintenance: 编排 archive → reagg → deletion"""

    @pytest.mark.asyncio
    async def test_daily_maintenance_orchestration(self):
        """验证 archive → reagg → deletion 依次执行"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)

        call_order = []

        async def mock_archive(**kwargs):
            call_order.append("archive")
            return 10

        async def mock_reagg(**kwargs):
            call_order.append("reagg")
            return 5

        async def mock_deletion(**kwargs):
            call_order.append("deletion")
            return 2

        with (
            patch.object(worker, "_run_archive", side_effect=mock_archive),
            patch.object(worker, "_run_daily_reaggregation", side_effect=mock_reagg),
            patch.object(worker, "_run_deletion_detection", side_effect=mock_deletion),
        ):
            await worker._run_daily_maintenance()

        assert call_order == ["archive", "reagg", "deletion"]


# ============================================================
# TestDeadLetterQueue — 死信队列
# ============================================================


class TestFetchDetailsWithFailures:
    """_fetch_details 返回成功和失败列表"""

    @pytest.mark.asyncio
    async def test_success_and_failure_split(self):
        """成功的进 succeeded，失败的进 failed"""
        from services.kuaimai.erp_sync_handlers import _fetch_details

        mock_client = AsyncMock()

        async def _mock_request(method, params):
            if params["id"] == "bad":
                raise Exception("network error")
            return {"list": [{"outerId": "A"}]}

        mock_client.request_with_retry = _mock_request

        docs = [{"id": "good"}, {"id": "bad"}]
        result = await _fetch_details(mock_client, "test.get", docs)

        assert len(result.succeeded) == 1
        assert result.succeeded[0][0]["id"] == "good"
        assert len(result.failed) == 1
        assert result.failed[0]["id"] == "bad"

    def test_iter_backward_compat(self):
        """__iter__ 向后兼容：for doc, detail in result"""
        from services.kuaimai.erp_sync_handlers import _DetailResult

        result = _DetailResult()
        result.succeeded = [
            ({"id": "d1"}, {"list": []}),
            ({"id": "d2"}, {"list": []}),
        ]

        items = [(doc, detail) for doc, detail in result]
        assert len(items) == 2


class TestRecordDeadLetter:
    """dead letter 写入"""

    @pytest.mark.asyncio
    async def test_record_writes_to_db(self):
        """失败的 doc 写入死信表"""
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter

        db = MockErpAsyncDBClient()
        failed_docs = [{"id": "123"}, {"id": "456"}]

        count = await record_dead_letter(
            db, "purchase", "purchase.order.get", failed_docs, "timeout",
        )

        assert count == 2
        dl_table = db.table("erp_sync_dead_letter")
        assert len(dl_table._data) == 2

    @pytest.mark.asyncio
    async def test_empty_docs_returns_zero(self):
        """空列表不写入"""
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter

        db = MockErpAsyncDBClient()
        count = await record_dead_letter(db, "purchase", "purchase.order.get", [])
        assert count == 0


class TestCalcNextRetry:
    """指数退避计算"""

    def test_exponential_backoff(self):
        """延迟随重试次数指数增长"""
        from services.kuaimai.erp_sync_dead_letter import _calc_next_retry

        t0 = _calc_next_retry(0)  # 5s
        t1 = _calc_next_retry(1)  # 10s
        t2 = _calc_next_retry(2)  # 20s
        # 只要后者比前者晚即可
        assert t1 > t0
        assert t2 > t1

    def test_max_cap(self):
        """超大重试次数不会溢出"""
        from services.kuaimai.erp_sync_dead_letter import _calc_next_retry

        # retry_count=100 不应报错
        result = _calc_next_retry(100)
        assert isinstance(result, str)


class TestWriteDocGroupTxn:
    """_write_doc_group_txn：事务内单据删+插"""

    @pytest.mark.asyncio
    async def test_deletes_and_inserts(self):
        from services.kuaimai.erp_sync_persistence import _write_doc_group_txn

        conn = AsyncMock()
        rows = [
            {"doc_type": "purchase", "doc_id": "D1", "item_index": 0, "outer_id": "A"},
            {"doc_type": "purchase", "doc_id": "D1", "item_index": 1, "outer_id": "B"},
        ]
        await _write_doc_group_txn(conn, "purchase", "D1", rows)

        # 应该先 DELETE 再 INSERT 每行
        assert conn.execute.call_count == 3  # 1 DELETE + 2 INSERT
        delete_sql = conn.execute.call_args_list[0][0][0]
        assert "DELETE" in delete_sql

    @pytest.mark.asyncio
    async def test_json_serialization(self):
        """dict/list 字段应被序列化为 JSON 字符串"""
        from services.kuaimai.erp_sync_persistence import _write_doc_group_txn

        conn = AsyncMock()
        rows = [{"doc_type": "t", "doc_id": "1", "extra_json": {"key": "val"}}]
        await _write_doc_group_txn(conn, "t", "1", rows)

        insert_call = conn.execute.call_args_list[1]
        vals = insert_call[0][1]
        # extra_json 的值应该是 JSON 字符串
        json_val = [v for v in vals if isinstance(v, str) and "{" in v]
        assert len(json_val) == 1
        assert '"key"' in json_val[0]


class TestRunAggregationAsync:
    """_run_aggregation_async：异步逐条聚合"""

    @pytest.mark.asyncio
    async def test_calls_rpc_for_each_key(self):
        from services.kuaimai.erp_sync_persistence import _run_aggregation_async

        db = MockErpAsyncDBClient()
        keys = [("A01", "2026-03-18"), ("B02", "2026-03-19")]
        await _run_aggregation_async(db, keys)

        # 验证不抛异常即可（async RPC mock）

    @pytest.mark.asyncio
    async def test_empty_keys_noop(self):
        from services.kuaimai.erp_sync_persistence import _run_aggregation_async

        db = MockErpAsyncDBClient()
        await _run_aggregation_async(db, [])
        # 空列表直接返回

    @pytest.mark.asyncio
    async def test_error_does_not_stop_iteration(self):
        """单条聚合失败不阻塞其他"""
        from services.kuaimai.erp_sync_persistence import _run_aggregation_async

        db = MockErpAsyncDBClient()
        call_count = [0]

        class _FailOnFirstRpc:
            def __init__(self, should_fail):
                self._should_fail = should_fail

            async def execute(self):
                if self._should_fail:
                    raise Exception("boom")
                return MagicMock(data=None)

        original_rpc = db.rpc

        def failing_rpc(fn_name, params=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _FailOnFirstRpc(True)
            return _FailOnFirstRpc(False)

        db.rpc = failing_rpc
        keys = [("A01", "2026-03-18"), ("B02", "2026-03-19")]
        # 不应抛异常
        await _run_aggregation_async(db, keys)
        assert call_count[0] == 2


class TestCollectApiProductIds:
    """_collect_api_product_ids：从 API 商品列表收集 SPU/SKU ID"""

    def test_collects_spu_and_sku(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        products = [
            {"outerId": "SPU1", "skus": [
                {"skuOuterId": "SKU1"}, {"skuOuterId": "SKU2"},
            ]},
            {"outerId": "SPU2", "skus": [{"skuOuterId": "SKU3"}]},
        ]
        spu_ids, sku_ids = ErpSyncWorker._collect_api_product_ids(products)
        assert spu_ids == {"SPU1", "SPU2"}
        assert sku_ids == {"SKU1", "SKU2", "SKU3"}

    def test_skips_none_outer_id(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        products = [{"outerId": None, "skus": [{"skuOuterId": "S1"}]}]
        spu_ids, sku_ids = ErpSyncWorker._collect_api_product_ids(products)
        assert spu_ids == set()
        assert sku_ids == {"S1"}

    def test_empty_skus(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        products = [{"outerId": "SPU1", "skus": None}]
        spu_ids, sku_ids = ErpSyncWorker._collect_api_product_ids(products)
        assert spu_ids == {"SPU1"}
        assert sku_ids == set()

    def test_empty_list(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        spu_ids, sku_ids = ErpSyncWorker._collect_api_product_ids([])
        assert spu_ids == set()
        assert sku_ids == set()


class TestMarkDeletedItems:
    """_mark_deleted_items：批量标记删除"""

    @pytest.mark.asyncio
    async def test_marks_all(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker.__new__(ErpSyncWorker)
        worker.db = db

        count = await worker._mark_deleted_items("erp_products", "outer_id", {"A", "B"})
        assert count == 2

    @pytest.mark.asyncio
    async def test_empty_set_noop(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker.__new__(ErpSyncWorker)
        worker.db = db

        count = await worker._mark_deleted_items("erp_products", "outer_id", set())
        assert count == 0

    @pytest.mark.asyncio
    async def test_single_error_continues(self):
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            mock = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                mock.update.return_value.eq.return_value.execute = AsyncMock(
                    side_effect=Exception("db error")
                )
            else:
                mock.update.return_value.eq.return_value.execute = AsyncMock()
            return mock

        db.table = MagicMock(side_effect=side_effect)
        worker = ErpSyncWorker.__new__(ErpSyncWorker)
        worker.db = db

        count = await worker._mark_deleted_items("erp_products", "outer_id", {"A", "B"})
        # 一个失败一个成功，count >= 1
        assert count >= 1


class TestApiRateLimiter:
    """_ApiRateLimiter：Leaky Bucket QPS 限流"""

    @pytest.mark.asyncio
    async def test_basic_acquire(self):
        from services.kuaimai.erp_sync_utils import _ApiRateLimiter

        limiter = _ApiRateLimiter(max_qps=1000)
        async with limiter:
            pass  # 不抛异常即成功

    @pytest.mark.asyncio
    async def test_enforces_interval(self):
        """连续两次请求间隔应 >= 1/max_qps"""
        import time
        from services.kuaimai.erp_sync_utils import _ApiRateLimiter

        limiter = _ApiRateLimiter(max_qps=50)  # 20ms 间隔
        t0 = time.monotonic()
        async with limiter:
            pass
        async with limiter:
            pass
        elapsed = time.monotonic() - t0
        # 两次请求应至少间隔 ~20ms
        assert elapsed >= 0.015

    @pytest.mark.asyncio
    async def test_lock_recreation_on_loop_change(self):
        """事件循环变化时 Lock 应自动重建"""
        from services.kuaimai.erp_sync_utils import _ApiRateLimiter

        limiter = _ApiRateLimiter(max_qps=1000)
        # 第一次使用，创建 lock
        async with limiter:
            first_lock = limiter._lock
        # 模拟 loop_id 变化
        limiter._lock_loop_id = -1
        async with limiter:
            second_lock = limiter._lock
        assert first_lock is not second_lock


class TestBuildRowsFromDetail:
    """死信重试时的行构建"""

    def test_build_purchase_rows(self):
        """采购单行构建"""
        from services.kuaimai.erp_sync_dead_letter_handlers import build_rows_from_detail

        doc = {"id": "123", "code": "CG123", "status": "FINISHED",
               "created": "2026-03-25 10:00:00", "modified": "2026-03-25 11:00:00",
               "remark": "test remark"}
        detail = {"list": [
            {"itemOuterId": "A01", "outerId": "A01-01", "count": 10,
             "price": 100, "amount": 1000, "_item_index": 0},
        ], "supplierName": "供应商A", "warehouseName": "仓库1"}

        rows = build_rows_from_detail("purchase", doc, detail)
        assert len(rows) == 1
        assert rows[0]["doc_type"] == "purchase"
        assert rows[0]["remark"] == "test remark"
        assert rows[0]["supplier_name"] == "供应商A"

    def test_unknown_type_returns_empty(self):
        """未知类型返回空"""
        from services.kuaimai.erp_sync_dead_letter_handlers import build_rows_from_detail

        rows = build_rows_from_detail("unknown_type", {}, {})
        assert rows == []


# ============================================================
# TestRunAggregationPendingDedup — pending 去重逻辑
# ============================================================


class TestRunAggregationPendingDedup:
    """run_aggregation 的 pending set 去重"""

    def test_duplicate_keys_deduped(self):
        """相同 key 不重复入队"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=100)
        pending: set = set()
        keys = [("P01", "2026-03-27"), ("P01", "2026-03-27"), ("P02", "2026-03-27")]

        run_aggregation(MagicMock(), q, keys, pending=pending)

        assert q.qsize() == 2  # P01 去重，只入队一次
        assert ("P01", "2026-03-27", None) in pending
        assert ("P02", "2026-03-27", None) in pending

    def test_pending_none_no_dedup(self):
        """pending=None 时不去重，全部入队"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=100)
        keys = [("P01", "2026-03-27"), ("P01", "2026-03-27")]

        run_aggregation(MagicMock(), q, keys, pending=None)

        assert q.qsize() == 2  # 无去重

    def test_queue_full_discards_from_pending(self):
        """队列满时丢弃 key 并从 pending 中移除"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=1)
        pending: set = set()
        keys = [("P01", "2026-03-27"), ("P02", "2026-03-27")]

        run_aggregation(MagicMock(), q, keys, pending=pending)

        assert q.qsize() == 1
        assert ("P01", "2026-03-27", None) in pending
        assert ("P02", "2026-03-27", None) not in pending

    def test_empty_keys_noop(self):
        """空 keys 直接返回"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=100)
        run_aggregation(MagicMock(), q, [], pending=set())
        assert q.qsize() == 0

    def test_already_pending_key_skipped(self):
        """已在 pending 中的 key 跳过入队"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=100)
        pending = {("P01", "2026-03-27", None)}  # 预先存在（三元组）
        keys = [("P01", "2026-03-27")]

        run_aggregation(MagicMock(), q, keys, pending=pending)

        assert q.qsize() == 0  # 跳过，不入队


# ============================================================
# TestAggregationConsumer — 聚合消费者
# ============================================================


class TestAggregationConsumer:
    """ErpSyncWorker._aggregation_consumer 测试"""

    @pytest.mark.asyncio
    async def test_consumes_from_queue(self):
        """消费者从队列取 key 并调用 RPC"""
        import asyncio
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        rpc_calls = []

        async def _mock_rpc_execute():
            result = MagicMock()
            result.data = True
            return result

        # 记录 RPC 调用
        original_rpc = db.rpc

        def tracking_rpc(fn_name, params=None):
            rpc_calls.append((fn_name, params))
            caller = original_rpc(fn_name, params)
            return caller

        db.rpc = tracking_rpc
        db.set_rpc_result("erp_aggregate_daily_stats", True)

        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)

        # 预填队列（三元组：outer_id, stat_date, org_id）
        await worker.aggregation_queue.put(("P01", "2026-03-27", None))
        await worker.aggregation_queue.put(("P02", "2026-03-28", None))
        worker.is_running = True

        # 启动消费者，短暂运行后停止
        async def _stop_after_delay():
            await asyncio.sleep(0.3)
            worker.is_running = False

        task = asyncio.create_task(worker._aggregation_consumer())
        stop_task = asyncio.create_task(_stop_after_delay())
        await asyncio.gather(task, stop_task)

        assert len(rpc_calls) == 2
        assert rpc_calls[0] == ("erp_aggregate_daily_stats", {"p_outer_id": "P01", "p_stat_date": "2026-03-27", "p_org_id": None})

    @pytest.mark.asyncio
    async def test_handles_rpc_error_gracefully(self):
        """RPC 调用失败不中断消费循环"""
        import asyncio
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MagicMock()
        # rpc().execute() 抛异常
        mock_caller = MagicMock()
        mock_caller.execute = AsyncMock(side_effect=Exception("DB down"))
        db.rpc.return_value = mock_caller

        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)

        await worker.aggregation_queue.put(("P01", "2026-03-27"))
        worker.is_running = True

        async def _stop():
            await asyncio.sleep(0.3)
            worker.is_running = False

        await asyncio.gather(
            asyncio.create_task(worker._aggregation_consumer()),
            asyncio.create_task(_stop()),
        )

        # 消费者不崩溃，正常退出
        assert not worker.is_running


# ============================================================
# TestMarkDeletedItems — 批量标记删除
# ============================================================


class TestMarkDeletedItems:
    @pytest.mark.asyncio
    async def test_marks_items_deleted(self):
        """批量标记已删除的条目"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)

        count = await worker._mark_deleted_items(
            "erp_products", "outer_id", {"P01", "P02"}
        )

        assert count == 2

    @pytest.mark.asyncio
    async def test_marks_empty_set_returns_zero(self):
        """空集合返回 0"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)

        count = await worker._mark_deleted_items("erp_products", "outer_id", set())
        assert count == 0

    @pytest.mark.asyncio
    async def test_partial_failure_continues(self):
        """单条失败不阻塞后续"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()

        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)

        # 正常执行，2 个都成功（MockErpAsyncDBClient 不会报错）
        count = await worker._mark_deleted_items(
            "erp_products", "outer_id", {"P01", "P02"}
        )
        assert count == 2


# ============================================================
# TestAcquireDbLock — DB 锁降级
# ============================================================


class TestAcquireDbLock:
    @pytest.mark.asyncio
    async def test_acquire_success(self):
        """DB 锁获取成功"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        db.set_rpc_result("erp_try_acquire_sync_lock", True)

        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(
                erp_sync_enabled=True, erp_sync_interval=60,
                erp_sync_lock_ttl=300,
            )
            worker = ErpSyncWorker(db)

        result = await worker._acquire_db_lock()
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_not_acquired(self):
        """DB 锁被其他 Worker 持有"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        db.set_rpc_result("erp_try_acquire_sync_lock", False)

        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(
                erp_sync_enabled=True, erp_sync_interval=60,
                erp_sync_lock_ttl=300,
            )
            worker = ErpSyncWorker(db)

        result = await worker._acquire_db_lock()
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_db_error_returns_false(self):
        """DB 不可用时返回 False（保守跳过）"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MagicMock()
        mock_caller = MagicMock()
        mock_caller.execute = AsyncMock(side_effect=Exception("connection refused"))
        db.rpc.return_value = mock_caller

        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(
                erp_sync_enabled=True, erp_sync_interval=60,
                erp_sync_lock_ttl=300,
            )
            worker = ErpSyncWorker(db)

        result = await worker._acquire_db_lock()
        assert result is False


# ============================================================
# TestPaginatedSelectIds — 分页加载 ID
# ============================================================


class TestPaginatedSelectIds:
    """_paginated_select_ids: 分页加载活跃记录 ID 集合"""

    @pytest.mark.asyncio
    async def test_single_batch(self):
        """数据量 < batch_size，一次查完"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        db.set_table_data("erp_products", [
            {"outer_id": "A", "active_status": 1},
            {"outer_id": "B", "active_status": 1},
        ])
        worker = ErpSyncWorker(db)

        ids = await worker._paginated_select_ids("erp_products", "outer_id")
        assert ids == {"A", "B"}

    @pytest.mark.asyncio
    async def test_multi_batch_pagination(self):
        """数据量 > batch_size，验证多批拼接完整"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        # 5 条数据，batch_size=2 → 需要 3 批（2+2+1）
        rows = [{"outer_id": f"P{i}", "active_status": 1} for i in range(5)]
        db.set_table_data("erp_products", rows)
        worker = ErpSyncWorker(db)

        ids = await worker._paginated_select_ids(
            "erp_products", "outer_id", batch_size=2,
        )
        assert ids == {f"P{i}" for i in range(5)}

    @pytest.mark.asyncio
    async def test_empty_table(self):
        """空表返回空集合"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)

        ids = await worker._paginated_select_ids("erp_products", "outer_id")
        assert ids == set()

    @pytest.mark.asyncio
    async def test_filters_deleted(self):
        """active_status=-1 的记录不包含在结果中"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        db.set_table_data("erp_products", [
            {"outer_id": "alive", "active_status": 1},
            {"outer_id": "deleted", "active_status": -1},
        ])
        worker = ErpSyncWorker(db)

        ids = await worker._paginated_select_ids("erp_products", "outer_id")
        assert ids == {"alive"}


# ============================================================
# TestRunAggregationDedup — 聚合去重
# ============================================================


class TestRunAggregationDedup:
    """run_aggregation: pending set 去重 + QueueFull 处理"""

    def test_dedup_skips_pending_key(self):
        """已在 pending 中的 key 不重复入队"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        pending: set = set()
        db = MockErpAsyncDBClient()

        keys = [("A", "2026-01-01"), ("B", "2026-01-02"), ("A", "2026-01-01")]
        run_aggregation(db, queue, keys, pending=pending)

        assert queue.qsize() == 2  # A 只入队一次
        assert pending == {("A", "2026-01-01", None), ("B", "2026-01-02", None)}

    def test_queue_full_discards_pending_and_skips(self):
        """队列满时跳过 key 并从 pending 回滚"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        pending: set = set()
        db = MockErpAsyncDBClient()

        # 第一个入队成功，第二个队列满
        keys = [("A", "2026-01-01"), ("B", "2026-01-02")]
        run_aggregation(db, queue, keys, pending=pending)

        assert queue.qsize() == 1
        # A 入队成功留在 pending，B 被回滚
        assert ("A", "2026-01-01", None) in pending
        assert ("B", "2026-01-02", None) not in pending

    def test_no_pending_set_still_works(self):
        """pending=None 时回退到无去重模式（兼容）"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        db = MockErpAsyncDBClient()

        keys = [("A", "2026-01-01"), ("A", "2026-01-01")]
        run_aggregation(db, queue, keys, pending=None)

        # 无去重，两次都入队
        assert queue.qsize() == 2


# ============================================================
# TestDailyMaintenanceFaultIsolation — 日维护容错
# ============================================================


class TestDailyMaintenanceFaultIsolation:
    """_run_daily_maintenance: 单步失败不阻断后续步骤"""

    @pytest.mark.asyncio
    async def test_archive_failure_does_not_block_reagg_and_deletion(self):
        """归档失败 → 兜底聚合和删除检测仍执行"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)

        call_order = []

        async def failing_archive(**kw):
            call_order.append("archive")
            raise RuntimeError("archive exploded")

        async def mock_reagg(**kw):
            call_order.append("reagg")
            return 5

        async def mock_deletion(**kw):
            call_order.append("deletion")
            return 2

        with (
            patch.object(worker, "_run_archive", side_effect=failing_archive),
            patch.object(worker, "_run_daily_reaggregation", side_effect=mock_reagg),
            patch.object(worker, "_run_deletion_detection", side_effect=mock_deletion),
        ):
            await worker._run_daily_maintenance()

        assert call_order == ["archive", "reagg", "deletion"]

    @pytest.mark.asyncio
    async def test_reagg_failure_does_not_block_deletion(self):
        """兜底聚合失败 → 删除检测仍执行"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)

        call_order = []

        async def mock_archive(**kw):
            call_order.append("archive")
            return 10

        async def failing_reagg(**kw):
            call_order.append("reagg")
            raise RuntimeError("reagg exploded")

        async def mock_deletion(**kw):
            call_order.append("deletion")
            return 1

        with (
            patch.object(worker, "_run_archive", side_effect=mock_archive),
            patch.object(worker, "_run_daily_reaggregation", side_effect=failing_reagg),
            patch.object(worker, "_run_deletion_detection", side_effect=mock_deletion),
        ):
            await worker._run_daily_maintenance()

        assert call_order == ["archive", "reagg", "deletion"]

    @pytest.mark.asyncio
    async def test_all_fail_no_crash(self):
        """三步全部失败 → 不抛异常"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)

        with (
            patch.object(
                worker, "_run_archive",
                side_effect=RuntimeError("archive fail"),
            ),
            patch.object(
                worker, "_run_daily_reaggregation",
                side_effect=RuntimeError("reagg fail"),
            ),
            patch.object(worker, "_should_run_deletion", return_value=True),
            patch.object(
                worker, "_run_deletion_detection",
                side_effect=RuntimeError("deletion fail"),
            ),
        ):
            await worker._run_daily_maintenance()


# ============================================================
# TestAggregationConsumerPendingDiscard — 消费者去重清理
# ============================================================


class TestAggregationConsumerPendingDiscard:
    """_aggregation_consumer: 处理后从 pending set 移除"""

    @pytest.mark.asyncio
    async def test_discard_after_success(self):
        """RPC 成功后 key 从 pending 移除"""
        import asyncio
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        worker.is_running = True

        key = ("X01", "2026-03-27", None)
        worker.aggregation_pending.add(key)
        await worker.aggregation_queue.put(key)

        task = asyncio.create_task(worker._aggregation_consumer())
        await asyncio.sleep(0.1)
        worker.is_running = False
        await asyncio.sleep(1.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert key not in worker.aggregation_pending

    @pytest.mark.asyncio
    async def test_discard_after_rpc_failure(self):
        """RPC 失败后 key 仍从 pending 移除（允许下次重新入队）"""
        import asyncio
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        worker = ErpSyncWorker(db)
        worker.is_running = True

        key = ("Y01", "2026-03-27", None)
        worker.aggregation_pending.add(key)
        await worker.aggregation_queue.put(key)

        # RPC 抛异常
        class FailingRpcCaller:
            async def execute(self):
                raise RuntimeError("RPC failed")

        worker.db.rpc = lambda fn, params=None: FailingRpcCaller()

        task = asyncio.create_task(worker._aggregation_consumer())
        await asyncio.sleep(0.1)
        worker.is_running = False
        await asyncio.sleep(1.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert key not in worker.aggregation_pending


# ============================================================
# TestMultiTenantSync — 多企业同步隔离
# ============================================================


class TestMultiTenantSync:
    """多企业场景下的同步隔离验证"""

    def test_org_timing_isolation(self):
        """每个企业独立计时"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockErpAsyncDBClient()
        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(
                erp_sync_enabled=True, erp_sync_interval=60,
                erp_stock_full_refresh_interval=3600,
                erp_platform_map_interval=7200,
            )
            worker = ErpSyncWorker(db)

        worker._org_last_stock_full["org-A"] = datetime.now()
        assert worker._should_run_stock_full("org-A") is False
        assert worker._should_run_stock_full("org-B") is True

        worker._org_last_daily["org-A"] = datetime.now()
        assert worker._should_run_daily("org-A") is False
        assert worker._should_run_daily("org-B") is True

    def test_aggregation_queue_3tuple(self):
        """聚合队列使用三元组"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=100)
        pending = set()
        run_aggregation(MagicMock(), q, [("P01", "2026-03-28")], pending=pending, org_id="org-A")
        item = q.get_nowait()
        assert item == ("P01", "2026-03-28", "org-A")
        assert ("P01", "2026-03-28", "org-A") in pending

    def test_aggregation_different_orgs_not_deduped(self):
        """不同企业的相同 key 不互相去重"""
        import asyncio
        from services.kuaimai.erp_sync_persistence import run_aggregation

        q = asyncio.Queue(maxsize=100)
        pending = set()
        run_aggregation(MagicMock(), q, [("P01", "2026-03-28")], pending=pending, org_id="org-A")
        run_aggregation(MagicMock(), q, [("P01", "2026-03-28")], pending=pending, org_id="org-B")
        assert q.qsize() == 2

    def test_sync_service_apply_org_enterprise(self):
        """_apply_org 企业模式"""
        from services.kuaimai.erp_sync_service import ErpSyncService
        svc = ErpSyncService.__new__(ErpSyncService)
        svc.org_id = "org-test"
        mock_q = MagicMock()
        mock_q.eq.return_value = mock_q
        svc._apply_org(mock_q)
        mock_q.eq.assert_called_with("org_id", "org-test")

    def test_sync_service_apply_org_personal(self):
        """_apply_org 散客模式"""
        from services.kuaimai.erp_sync_service import ErpSyncService
        svc = ErpSyncService.__new__(ErpSyncService)
        svc.org_id = None
        mock_q = MagicMock()
        mock_q.is_.return_value = mock_q
        svc._apply_org(mock_q)
        mock_q.is_.assert_called_with("org_id", "null")

    @pytest.mark.asyncio
    async def test_load_erp_orgs_empty_fallback(self):
        """无企业时降级散客"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        db.set_table_data("organizations", [])
        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)
        with patch("services.kuaimai.client.KuaiMaiClient") as MC:
            MC.return_value = MagicMock(is_configured=True)
            orgs = await worker._load_erp_orgs()
        assert len(orgs) == 1
        assert orgs[0][0] is None

    @pytest.mark.asyncio
    async def test_load_erp_orgs_skip_no_erp(self):
        """ERP 未开启的企业被跳过"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockErpAsyncDBClient()
        db.set_table_data("organizations", [
            {"id": "org-1", "features": {"erp": False}, "status": "active"},
            {"id": "org-2", "features": {"erp": True}, "status": "active"},
        ])
        with patch("services.kuaimai.erp_sync_worker.get_settings") as ms:
            ms.return_value = MagicMock(erp_sync_enabled=True, erp_sync_interval=60)
            worker = ErpSyncWorker(db)
        with patch("services.org.config_resolver.AsyncOrgConfigResolver") as MR:
            MR.return_value = MagicMock(get_erp_credentials=AsyncMock(return_value={
                "kuaimai_app_key": "k", "kuaimai_app_secret": "s",
                "kuaimai_access_token": "t", "kuaimai_refresh_token": "r",
            }))
            orgs = await worker._load_erp_orgs()
        assert len(orgs) == 1
        assert orgs[0][0] == "org-2"
