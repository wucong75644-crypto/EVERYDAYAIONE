"""
多表统一查询 E2E 测试。

模拟用户查询 → PlanBuilder 参数提取 → Agent 路由 → UnifiedQueryEngine ORM 执行。
每个测试走完从 agent.execute() 到 ORM response 的全链路，只 mock 数据库返回。

设计文档: docs/document/TECH_ERP多表统一查询.md §6
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── mock 工厂 ──────────────────────────────────────


def _mock_db(rows: list[dict], count: int | None = None):
    """构造 Supabase ORM 响应链 mock。

    支持 table().select().eq().is_().gte().lt().order().limit().execute()。
    """
    resp = MagicMock()
    resp.data = rows
    resp.count = count if count is not None else len(rows)

    q = MagicMock()
    for method in ("eq", "is_", "gte", "gt", "lt", "lte", "neq",
                    "ilike", "in_", "order", "limit"):
        setattr(q, method, MagicMock(return_value=q))
    q.not_ = MagicMock()
    q.not_.in_ = MagicMock(return_value=q)
    q.execute = MagicMock(return_value=resp)

    db = MagicMock()
    db.table = MagicMock(return_value=MagicMock(select=MagicMock(return_value=q)))
    # 保存 q 引用供断言用
    db._q = q
    return db


def _make_warehouse(db=None, org_id="test_org"):
    from services.agent.departments.warehouse_agent import WarehouseAgent
    return WarehouseAgent(db=db or _mock_db([]), org_id=org_id)


# ============================================================
# E2E 1: 库存负数的商品有多少（stock summary）
# ============================================================


class TestE2EStockNegativeCount:
    """
    链路: WarehouseAgent.execute(dag_mode=True, params={doc_type=stock, numeric_filters})
      → _dispatch("stock_data_query")
      → _query_local_data(doc_type="stock")
      → UnifiedQueryEngine.execute(doc_type="stock")
      → _summary_orm(table="erp_stock_status")
      → ORM: select(*).eq("org_id").lt("available_stock", 0).execute()
    """

    @pytest.mark.asyncio
    async def test_full_chain(self):
        rows = [
            {"outer_id": "A001", "item_name": "商品A", "available_stock": -5,
             "total_stock": 10, "lock_stock": 15, "sellable_num": 0},
            {"outer_id": "A002", "item_name": "商品B", "available_stock": -3,
             "total_stock": 5, "lock_stock": 8, "sellable_num": 0},
        ]
        db = _mock_db(rows, count=159)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "库存负数的商品有多少",
            dag_mode=True,
            params={
                "doc_type": "stock",
                "mode": "summary",
                "numeric_filters": [
                    {"field": "available_stock", "op": "lt", "value": 0},
                ],
            },
        )

        # 验证结果
        assert result.status == "success", f"期望 success，实际 {result.status}: {result.summary}"
        assert "159" in result.summary, "应包含记录数 159"
        assert "可用库存" in result.summary, "过滤条件应包含中文标签"

        # 验证 ORM 调用链
        db.table.assert_called_with("erp_stock_status")

    @pytest.mark.asyncio
    async def test_empty_result(self):
        db = _mock_db([], count=0)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "库存负数的商品",
            dag_mode=True,
            params={
                "doc_type": "stock",
                "mode": "summary",
                "numeric_filters": [
                    {"field": "available_stock", "op": "lt", "value": 0},
                ],
            },
        )
        assert result.status == "empty"

    @pytest.mark.asyncio
    async def test_no_time_range_needed(self):
        """stock 快照表不强制时间范围——不传 time_range 也不报错"""
        db = _mock_db([{"outer_id": "X"}], count=1)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "全部库存",
            dag_mode=True,
            params={"doc_type": "stock", "mode": "summary"},
        )
        assert result.status == "success"


# ============================================================
# E2E 2: 停售商品列表（product export）
# ============================================================


class TestE2EProductStopSale:
    """
    链路: params={doc_type=product, mode=export, numeric_filters=[active_status=2]}
      → _dispatch("product_query")
      → _query_local_data(doc_type="product")
      → UnifiedQueryEngine.execute → _export_orm(table="erp_products")
    """

    @pytest.mark.asyncio
    async def test_full_chain(self):
        rows = [
            {"outer_id": "P001", "title": "停售商品A", "active_status": 2,
             "brand": "品牌X", "item_type": 0},
            {"outer_id": "P002", "title": "停售商品B", "active_status": 2,
             "brand": "品牌Y", "item_type": 0},
        ]
        db = _mock_db(rows, count=2)
        agent = _make_warehouse(db=db)

        with patch(
            "services.kuaimai.erp_duckdb_helpers.resolve_export_path",
        ) as mock_path, patch(
            "core.duckdb_engine.get_duckdb_engine",
        ) as mock_engine, patch(
            "services.agent.data_profile.build_profile_from_duckdb",
            return_value=("2行数据 | 文件大小 1KB", {"rows": 2}),
        ):
            tmp = Path("/tmp/test_e2e_product.parquet")
            mock_path.return_value = (tmp.parent, "test", tmp, "product_export.parquet")
            mock_engine.return_value.profile_parquet = MagicMock(return_value={})

            result = await agent.execute(
                "停售商品列表",
                dag_mode=True,
                params={
                    "doc_type": "product",
                    "mode": "export",
                    "numeric_filters": [
                        {"field": "active_status", "op": "eq", "value": 2},
                    ],
                },
            )

            assert result.status == "success", f"实际: {result.status}: {result.summary}"
            assert result.file_ref is not None, "export 模式应返回 file_ref"
            db.table.assert_called_with("erp_products")
            tmp.unlink(missing_ok=True)


# ============================================================
# E2E 3: 本月商品销量 Top10（daily_stats export + sort）
# ============================================================


class TestE2EDailyStatsTop:
    """
    链路: params={doc_type=daily_stats, time_range, sort_by=order_qty, limit=10}
      → _dispatch("daily_stats_query")
      → UnifiedQueryEngine.execute → _export_orm(table="erp_product_daily_stats")
    """

    @pytest.mark.asyncio
    async def test_full_chain(self):
        rows = [{"outer_id": f"D{i}", "item_name": f"商品{i}",
                 "stat_date": "2026-04-01", "order_count": 100 - i,
                 "order_qty": 500 - i * 10, "order_amount": 10000 - i * 100}
                for i in range(10)]
        db = _mock_db(rows, count=10)
        agent = _make_warehouse(db=db)

        with patch(
            "services.kuaimai.erp_duckdb_helpers.resolve_export_path",
        ) as mock_path, patch(
            "core.duckdb_engine.get_duckdb_engine",
        ) as mock_engine, patch(
            "services.agent.data_profile.build_profile_from_duckdb",
            return_value=("10行数据", {"rows": 10}),
        ):
            tmp = Path("/tmp/test_e2e_daily.parquet")
            mock_path.return_value = (tmp.parent, "test", tmp, "daily_stats.parquet")
            mock_engine.return_value.profile_parquet = MagicMock(return_value={})

            result = await agent.execute(
                "本月商品销量Top10",
                dag_mode=True,
                params={
                    "doc_type": "daily_stats",
                    "mode": "export",
                    "time_range": "2026-04-01 ~ 2026-04-26",
                    "sort_by": "order_qty",
                    "sort_dir": "desc",
                    "limit": 10,
                },
            )

            assert result.status == "success", f"实际: {result.status}: {result.summary}"
            db.table.assert_called_with("erp_product_daily_stats")
            tmp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_requires_time_range(self):
        """daily_stats 强制时间范围——不传时自动加默认范围（不报错）"""
        db = _mock_db([{"outer_id": "X", "stat_date": "2026-04-01",
                        "item_name": "test", "order_count": 1,
                        "order_qty": 1, "order_amount": 100}], count=1)
        agent = _make_warehouse(db=db)
        result = await agent.execute(
            "日统计",
            dag_mode=True,
            params={"doc_type": "daily_stats", "mode": "summary"},
        )
        # 不报错——自动加默认时间范围
        assert result.status == "success"


# ============================================================
# E2E 4: 某订单的操作日志（order_log + system_id 过滤）
# ============================================================


class TestE2EOrderLog:
    """
    链路：order_log 归属 trade 域 → 但 ERPAgent 层处理。
    此处直接测 UnifiedQueryEngine 层验证 system_id 过滤。
    """

    @pytest.mark.asyncio
    async def test_system_id_filter_passes_to_orm(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        rows = [
            {"system_id": "123456", "operator": "张三",
             "action": "审核", "content": "通过审核", "operate_time": "2026-04-01 10:00:00"},
        ]
        db = _mock_db(rows, count=1)
        engine = UnifiedQueryEngine(db=db, org_id="test_org")

        result = await engine.execute(
            doc_type="order_log",
            mode="summary",
            filters=[{"field": "system_id", "op": "eq", "value": "123456"}],
        )

        assert result.status == "success", f"实际: {result.status}: {result.summary}"
        assert "1" in result.summary
        # system_id 是 text 字段，应通过 order_log 白名单校验
        db.table.assert_called_with("erp_order_logs")


# ============================================================
# E2E 5: 某商品在哪些平台售卖（platform_map）
# ============================================================


class TestE2EPlatformMap:

    @pytest.mark.asyncio
    async def test_product_code_filter(self):
        rows = [
            {"outer_id": "HZ001", "num_iid": "6789", "title": "淘宝链接", "user_id": "u1"},
            {"outer_id": "HZ001", "num_iid": "9876", "title": "拼多多链接", "user_id": "u2"},
        ]
        db = _mock_db(rows, count=2)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "HZ001在哪些平台",
            dag_mode=True,
            params={
                "doc_type": "platform_map",
                "mode": "summary",
                "product_code": "HZ001",
            },
        )
        assert result.status == "success"
        db.table.assert_called_with("erp_product_platform_map")


# ============================================================
# E2E 6: 批次库存查询（batch_stock）
# ============================================================


class TestE2EBatchStock:

    @pytest.mark.asyncio
    async def test_batch_stock_summary(self):
        rows = [
            {"outer_id": "B001", "item_name": "食品A", "batch_no": "BAT-001",
             "stock_qty": 100, "expiry_date": "2026-06-01"},
        ]
        db = _mock_db(rows, count=1)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "快过期的批次库存",
            dag_mode=True,
            params={"doc_type": "batch_stock", "mode": "summary"},
        )
        assert result.status == "success"
        db.table.assert_called_with("erp_batch_stock")


# ============================================================
# E2E 7: 白名单拒绝——stock 表不认识 order_no
# ============================================================


class TestE2EWhitelistRejection:

    @pytest.mark.asyncio
    async def test_stock_rejects_order_field_via_direct_filter(self):
        """直接传 filters 中的非法字段 → 白名单拒绝"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        db = _mock_db([])
        engine = UnifiedQueryEngine(db=db, org_id="test_org")

        result = await engine.execute(
            doc_type="stock",
            mode="summary",
            filters=[{"field": "order_no", "op": "eq", "value": "123"}],
        )
        assert result.status == "error"
        assert "order_no" in result.summary

    @pytest.mark.asyncio
    async def test_numeric_filters_drops_unknown_field_silently(self):
        """numeric_filters 中的非数值字段被 NUMERIC_FILTER_FIELDS 静默丢弃"""
        from services.agent.param_converter import params_to_filters
        filters, _ = params_to_filters({
            "numeric_filters": [
                {"field": "order_no", "op": "eq", "value": "123"},  # text 字段
            ],
        })
        order_no_filters = [f for f in filters if f["field"] == "order_no"]
        assert len(order_no_filters) == 0, "order_no 是 text 字段，不应通过 numeric_filters"


# ============================================================
# E2E 8: numeric_filters 链路完整性验证
# ============================================================


class TestE2ENumericFilterChain:
    """验证 _params_to_filters → validate_filters → ORM 的完整链路。"""

    @pytest.mark.asyncio
    async def test_available_stock_filter_reaches_orm(self):
        """available_stock < 0 必须到达 ORM 查询，不被静默丢弃"""
        db = _mock_db([{"outer_id": "X", "available_stock": -1}], count=1)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "库存负数",
            dag_mode=True,
            params={
                "doc_type": "stock",
                "mode": "summary",
                "numeric_filters": [
                    {"field": "available_stock", "op": "lt", "value": 0},
                ],
            },
        )
        assert result.status == "success"

        # 关键断言：ORM 链上 lt("available_stock", 0) 被调用
        q = db._q
        q.lt.assert_called()
        lt_calls = [c for c in q.lt.call_args_list
                     if c[0] == ("available_stock", 0)]
        assert len(lt_calls) >= 1, (
            f"ORM 未调用 lt('available_stock', 0)，"
            f"实际 lt 调用: {q.lt.call_args_list}"
        )

    @pytest.mark.asyncio
    async def test_order_qty_filter_for_daily_stats(self):
        """order_qty > 100 在 daily_stats 表中正确传递"""
        db = _mock_db([{"outer_id": "X", "stat_date": "2026-04-01",
                        "item_name": "t", "order_count": 5,
                        "order_qty": 150, "order_amount": 3000}], count=1)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "订单量超过100的商品",
            dag_mode=True,
            params={
                "doc_type": "daily_stats",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-26",
                "numeric_filters": [
                    {"field": "order_qty", "op": "gt", "value": 100},
                ],
            },
        )
        assert result.status == "success"

        q = db._q
        q.gt.assert_called()
        gt_calls = [c for c in q.gt.call_args_list
                     if len(c[0]) >= 2 and c[0][0] == "order_qty"]
        assert len(gt_calls) >= 1, (
            f"ORM 未调用 gt('order_qty', 100)，"
            f"实际 gt 调用: {q.gt.call_args_list}"
        )


# ============================================================
# E2E 9: _classify_action 降级路径（不传 doc_type 走关键词匹配）
# ============================================================


class TestE2EClassifyActionFallback:

    @pytest.mark.asyncio
    async def test_keyword_routes_to_stock_data(self):
        """用户说"库存负数"但 LLM 没提取 doc_type → 关键词匹配到 stock_data_query"""
        db = _mock_db([{"outer_id": "X"}], count=1)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "库存负数的商品",
            dag_mode=True,
            params={"mode": "summary"},  # 无 doc_type
        )
        # _classify_action("库存负数的商品") → "stock_data_query"
        # → _dispatch → _query_local_data(doc_type="stock")
        assert result.status == "success"
        db.table.assert_called_with("erp_stock_status")

    @pytest.mark.asyncio
    async def test_keyword_routes_to_product(self):
        db = _mock_db([{"outer_id": "X", "title": "t", "active_status": 1, "brand": "b"}], count=1)
        agent = _make_warehouse(db=db)

        result = await agent.execute(
            "停售商品有哪些",
            dag_mode=True,
            params={"mode": "summary"},
        )
        assert result.status == "success"
        db.table.assert_called_with("erp_products")
