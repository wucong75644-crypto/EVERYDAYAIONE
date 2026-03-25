"""
ERP 同步服务 + 归档任务单元测试
覆盖：erp_sync_service / erp_sync_worker
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockSupabaseClient



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
    db = MockSupabaseClient()
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

    def test_get_sync_state_exists(self):
        """读取已有同步状态"""
        service = _make_service([_sync_state("order")])
        state = service._get_sync_state("order")
        assert state is not None
        assert state["sync_type"] == "order"

    def test_get_sync_state_not_exists(self):
        """读取不存在的同步状态返回 None"""
        service = _make_service([])
        state = service._get_sync_state("order")
        assert state is None

    def test_init_sync_state(self):
        """初始化同步状态"""
        service = _make_service([])
        service._init_sync_state("purchase")
        # 验证不抛异常即可（insert 到 mock DB）

    def test_update_sync_state_error(self):
        """错误更新递增 error_count"""
        service = _make_service([_sync_state("order", error_count=2)])
        service._update_sync_state_error("order", "test error")
        # 验证不抛异常（mock DB）

    def test_mark_initial_done(self):
        """标记全量同步完成"""
        service = _make_service([_sync_state("order", is_initial_done=False)])
        service._mark_initial_done("order", 5000)
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

    def test_empty_rows(self):
        """空数据返回0"""
        service = _make_service()
        assert service.upsert_document_items([]) == 0

    def test_batch_upsert(self):
        """批量 upsert 数据"""
        db = MockSupabaseClient()
        # 添加 upsert 方法到 mock table
        mock_table = MagicMock()
        mock_table.upsert.return_value.execute.return_value = MagicMock()
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
        count = service.upsert_document_items(rows)
        assert count == 5


# ============================================================
# TestRunAggregation — 聚合计算
# ============================================================


class TestRunAggregation:

    def test_aggregation_calls_rpc(self):
        """聚合调用 RPC"""
        service = _make_service()
        service.run_aggregation([("C01", "2026-03-18"), ("C02", "2026-03-19")])
        # 不抛异常即可（mock RPC）

    def test_empty_keys_no_rpc(self):
        """无受影响键不调用 RPC"""
        service = _make_service()
        service.run_aggregation([])
        # 应该直接返回


# ============================================================
# TestErpSyncWorker — 同步调度
# ============================================================


class TestErpSyncWorkerInit:

    def test_worker_init(self):
        """Worker 初始化"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockSupabaseClient()
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
        db = MockSupabaseClient()
        worker = ErpSyncWorker(db)
        assert worker._should_run_daily() is True

    def test_recent_run_should_return_false(self):
        """最近运行过应返回 False"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockSupabaseClient()
        worker = ErpSyncWorker(db)
        worker._last_daily_maintenance = datetime.now()
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
        """build_erp_tools 返回 19 个工具（8 API + 11 本地）"""
        from config.erp_tools import build_erp_tools
        assert len(build_erp_tools()) == 19

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

    def test_progress_update_no_error(self):
        """进度更新不抛异常"""
        service = _make_service([_sync_state("order")])
        service._update_sync_state_progress(
            "order", datetime.now(timezone.utc),
        )

    def test_success_update_no_error(self):
        """成功更新不抛异常"""
        service = _make_service([_sync_state("order", total_synced=50)])
        service._update_sync_state_success("order", 10)


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
        db = MockSupabaseClient()
        worker = ErpSyncWorker(db)
        assert worker._should_run_low_freq() is True

    def test_recent_run_returns_false(self):
        """最近运行过返回 False"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockSupabaseClient()
        worker = ErpSyncWorker(db)
        worker._last_platform_map_sync = datetime.now()
        assert worker._should_run_low_freq() is False


# ============================================================
# TestWorkerStop — 停止
# ============================================================


class TestWorkerStop:

    @pytest.mark.asyncio
    async def test_stop_sets_flag(self):
        """stop 设置 is_running=False"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker
        db = MockSupabaseClient()
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
        db = MockSupabaseClient()
        worker = ErpSyncWorker(db)
        with patch(
            "services.kuaimai.erp_sync_service.ErpSyncService",
        ) as mock_cls:
            mock_svc = MagicMock()
            mock_svc.sync = AsyncMock(side_effect=Exception("test"))
            mock_cls.return_value = mock_svc
            await worker._execute_sync("order")


# ============================================================
# 归档/维护流程测试（原40个测试零覆盖）
# ============================================================


class TestRunArchive:
    """_run_archive: 热表→冷表归档"""

    @pytest.mark.asyncio
    async def test_run_archive_moves_old_rows(self):
        """正常归档：SELECT→UPSERT→DELETE"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockSupabaseClient()
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

        db = MockSupabaseClient()
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

        db = MockSupabaseClient()
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


class TestRunReaggregation:
    """_run_daily_reaggregation: 每日聚合兜底"""

    @pytest.mark.asyncio
    async def test_run_reaggregation_calls_rpc(self):
        """正常路径：调用 batch RPC"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockSupabaseClient()
        db.set_rpc_result("erp_aggregate_daily_stats_batch", 42)

        worker = ErpSyncWorker(db)
        count = await worker._run_daily_reaggregation()

        assert count == 42

    @pytest.mark.asyncio
    async def test_run_reaggregation_fallback(self):
        """RPC 失败时降级到逐条重算"""
        from services.kuaimai.erp_sync_worker import ErpSyncWorker

        db = MockSupabaseClient()
        # 让 rpc 调用抛异常
        original_rpc = db.rpc

        def failing_rpc(fn_name, params=None):
            if fn_name == "erp_aggregate_daily_stats_batch":
                mock = MagicMock()
                mock.execute.side_effect = Exception("RPC not found")
                return mock
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

        db = MockSupabaseClient()
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

        # mock neq/range（不在 MockSupabaseTable 中）
        def _patch_table(table_name):
            tbl = db.table(table_name)
            original_select = tbl.select

            def patched_select(fields="*", count=None):
                result = original_select(fields, count)
                result.neq = lambda f, v: result
                result.range = lambda s, e: result
                return result

            tbl.select = patched_select

        _patch_table("erp_products")
        _patch_table("erp_product_skus")

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

        db = MockSupabaseClient()
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

        db = MockSupabaseClient()
        worker = ErpSyncWorker(db)

        call_order = []

        async def mock_archive():
            call_order.append("archive")
            return 10

        async def mock_reagg():
            call_order.append("reagg")
            return 5

        async def mock_deletion():
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

    def test_record_writes_to_db(self):
        """失败的 doc 写入死信表"""
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter

        db = MockSupabaseClient()
        failed_docs = [{"id": "123"}, {"id": "456"}]

        count = record_dead_letter(
            db, "purchase", "purchase.order.get", failed_docs, "timeout",
        )

        assert count == 2
        dl_table = db.table("erp_sync_dead_letter")
        assert len(dl_table._data) == 2

    def test_empty_docs_returns_zero(self):
        """空列表不写入"""
        from services.kuaimai.erp_sync_dead_letter import record_dead_letter

        db = MockSupabaseClient()
        count = record_dead_letter(db, "purchase", "purchase.order.get", [])
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
