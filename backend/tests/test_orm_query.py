"""
新表 ORM 查询测试（summary_orm / export_orm / execute 路由）。

覆盖: erp_orm_query.py / erp_unified_query.py 新表路由
设计文档: docs/document/TECH_ERP多表统一查询.md §4.2
"""
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_unified_schema import ValidatedFilter, TimeRange


# ── mock 工厂 ──────────────────────────────────────


def _mock_time_range() -> TimeRange:
    from utils.time_context import DateRange, now_cn
    now = now_cn()
    end = now + timedelta(hours=1)
    return TimeRange(
        start_iso=now.strftime("%Y-%m-%d %H:%M:%S%z"),
        end_iso=end.strftime("%Y-%m-%d %H:%M:%S%z"),
        time_col="stock_modified_time",
        date_range=DateRange.custom(now, end, reference=now),
        label="04-01 00:00 ~ 04-01 01:00",
    )


def _mock_db(rows: list[dict], count: int | None = None):
    """构造 mock db，table().select().eq()...execute() 链。"""
    resp = MagicMock()
    resp.data = rows
    resp.count = count if count is not None else len(rows)

    q = MagicMock()
    q.eq = MagicMock(return_value=q)
    q.is_ = MagicMock(return_value=q)
    q.gte = MagicMock(return_value=q)
    q.lt = MagicMock(return_value=q)
    q.neq = MagicMock(return_value=q)
    q.ilike = MagicMock(return_value=q)
    q.order = MagicMock(return_value=q)
    q.limit = MagicMock(return_value=q)
    q.execute = MagicMock(return_value=resp)

    db = MagicMock()
    db.table = MagicMock(return_value=MagicMock(select=MagicMock(return_value=q)))
    return db


# ============================================================
# summary_orm 测试
# ============================================================


class TestSummaryOrm:

    @pytest.mark.asyncio
    async def test_returns_count_on_success(self):
        from services.kuaimai.erp_orm_query import summary_orm
        rows = [{"outer_id": "A001", "item_name": "商品A", "available_stock": -5}]
        db = _mock_db(rows, count=159)
        result = await summary_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        assert result.status == "success"
        assert "159" in result.summary
        assert result.data == [{"count": 159}]

    @pytest.mark.asyncio
    async def test_empty_result(self):
        from services.kuaimai.erp_orm_query import summary_orm
        db = _mock_db([], count=0)
        result = await summary_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        assert result.status == "empty"
        assert "无匹配" in result.summary

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self):
        from services.kuaimai.erp_orm_query import summary_orm
        db = MagicMock()
        db.table = MagicMock(side_effect=Exception("connection timeout"))
        result = await summary_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        assert result.status == "error"
        assert "connection timeout" in result.summary

    @pytest.mark.asyncio
    async def test_with_time_range(self):
        from services.kuaimai.erp_orm_query import summary_orm
        rows = [{"outer_id": "A001"}]
        db = _mock_db(rows, count=10)
        tr = _mock_time_range()
        result = await summary_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=tr,
        )
        assert result.status == "success"
        assert "10" in result.summary

    @pytest.mark.asyncio
    async def test_with_filters(self):
        from services.kuaimai.erp_orm_query import summary_orm
        rows = [{"outer_id": "A001", "available_stock": -5}]
        db = _mock_db(rows, count=1)
        filters = [ValidatedFilter("available_stock", "lt", 0, "numeric")]
        result = await summary_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=filters, tr=None,
        )
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_null_org_id_uses_is_null(self):
        from services.kuaimai.erp_orm_query import summary_orm
        db = _mock_db([{"x": 1}], count=1)
        await summary_orm(
            db, org_id=None, table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        q_chain = db.table.return_value.select.return_value
        q_chain.is_.assert_called()

    @pytest.mark.asyncio
    async def test_preview_lines_limit_5(self):
        from services.kuaimai.erp_orm_query import summary_orm
        rows = [{"outer_id": f"A{i:03d}", "item_name": f"商品{i}"} for i in range(10)]
        db = _mock_db(rows, count=10)
        result = await summary_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        assert "共10条" in result.summary
        assert "前5条" in result.summary


# ============================================================
# export_orm 测试
# ============================================================


class TestExportOrm:

    @pytest.mark.asyncio
    async def test_empty_result(self):
        from services.kuaimai.erp_orm_query import export_orm
        db = _mock_db([])
        result = await export_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        assert result.status == "empty"

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self):
        from services.kuaimai.erp_orm_query import export_orm
        db = MagicMock()
        db.table = MagicMock(side_effect=Exception("timeout"))
        result = await export_orm(
            db, org_id="org1", table="erp_stock_status",
            doc_type="stock", filters=[], tr=None,
        )
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_returns_file_ref_on_success(self):
        from services.kuaimai.erp_orm_query import export_orm
        rows = [
            {"outer_id": "A001", "item_name": "商品A", "available_stock": 100},
            {"outer_id": "A002", "item_name": "商品B", "available_stock": 50},
        ]
        db = _mock_db(rows)

        tmp_path = Path("/tmp/test_orm_export.parquet")
        with patch(
            "services.kuaimai.erp_duckdb_helpers.resolve_export_path",
            return_value=(tmp_path.parent, "test", tmp_path, "test.parquet"),
        ), patch(
            "core.duckdb_engine.get_duckdb_engine",
        ) as mock_engine, patch(
            "services.agent.data_profile.build_profile_from_duckdb",
            return_value=("profile text", {"rows": 2}),
        ):
            mock_engine.return_value.profile_parquet = MagicMock(return_value={})
            result = await export_orm(
                db, org_id="org1", table="erp_stock_status",
                doc_type="stock", filters=[], tr=None,
            )
            assert result.format.value == "file_ref"
            assert result.file_ref is not None
            assert result.file_ref.row_count == 2
            tmp_path.unlink(missing_ok=True)


# ============================================================
# execute() 新表路由测试
# ============================================================


class TestExecuteNewTableRouting:

    @pytest.mark.asyncio
    async def test_stock_routes_to_summary_orm(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=MagicMock(), org_id="org1")
        mock_result = MagicMock(summary="库存查询：共 10 条")
        with patch.object(engine, "_summary_orm", new=AsyncMock(return_value=mock_result)):
            await engine.execute(doc_type="stock", mode="summary", filters=[])
            engine._summary_orm.assert_called_once()
            assert engine._summary_orm.call_args[0][0] == "erp_stock_status"

    @pytest.mark.asyncio
    async def test_product_routes_to_export_orm(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=MagicMock(), org_id="org1")
        mock_result = MagicMock(summary="商品导出")
        with patch.object(engine, "_export_orm", new=AsyncMock(return_value=mock_result)):
            await engine.execute(doc_type="product", mode="export", filters=[])
            engine._export_orm.assert_called_once()
            assert engine._export_orm.call_args[0][0] == "erp_products"

    @pytest.mark.asyncio
    async def test_order_does_not_route_to_orm(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=MagicMock(), org_id="org1")
        with patch.object(engine, "_summary_orm", new=AsyncMock()) as mock_orm, \
             patch.object(engine, "_summary", new=AsyncMock(return_value=MagicMock(summary="ok"))), \
             patch("services.kuaimai.erp_unified_query.preflight_check") as mock_pf:
            mock_pf.return_value = MagicMock(ok=True, reject_reason=None)
            await engine.execute(doc_type="order", mode="summary", filters=[])
            mock_orm.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_doc_type_returns_error(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        result = await UnifiedQueryEngine(db=MagicMock()).execute(
            doc_type="nonexistent", mode="summary", filters=[],
        )
        assert result.status == "error"
        assert "无效" in result.summary

    @pytest.mark.asyncio
    async def test_new_table_validate_filters_uses_doc_type(self):
        """stock 表不认识 order_no → 返回 error"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        result = await UnifiedQueryEngine(db=MagicMock(), org_id="org1").execute(
            doc_type="stock", mode="summary",
            filters=[{"field": "order_no", "op": "eq", "value": "123"}],
        )
        assert result.status == "error"
        assert "order_no" in result.summary

    @pytest.mark.asyncio
    async def test_sort_by_validated_against_new_table_whitelist(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=MagicMock(), org_id="org1")
        mock_result = MagicMock(summary="ok")

        # available_stock 在 stock 白名单 → 保留
        with patch.object(engine, "_summary_orm", new=AsyncMock(return_value=mock_result)):
            await engine.execute(
                doc_type="stock", mode="summary", filters=[],
                sort_by="available_stock",
            )
            assert engine._summary_orm.call_args[1].get("sort_by") == "available_stock"

        # order_no 不在 stock 白名单 → 置 None
        with patch.object(engine, "_summary_orm", new=AsyncMock(return_value=mock_result)):
            await engine.execute(
                doc_type="stock", mode="summary", filters=[],
                sort_by="order_no",
            )
            assert engine._summary_orm.call_args[1].get("sort_by") is None
