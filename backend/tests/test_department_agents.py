"""
采购/订单/售后 部门Agent 单元测试。

覆盖: purchase_agent.py / trade_agent.py / aftersale_agent.py
设计文档: docs/document/TECH_多Agent单一职责重构.md §8
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.tool_output import OutputFormat, OutputStatus, ToolOutput


# ============================================================
# PurchaseAgent
# ============================================================


def _make_purchase(db=None, org_id=None):
    from services.agent.departments.purchase_agent import PurchaseAgent
    return PurchaseAgent(db=db or MagicMock(), org_id=org_id)


class TestPurchaseProperties:

    def test_domain(self):
        assert _make_purchase().domain == "purchase"

    def test_tools(self):
        tools = _make_purchase().tools
        assert "local_data" in tools
        assert "erp_purchase_query" in tools

    def test_allowed_doc_types(self):
        agent = _make_purchase()
        assert "purchase" in agent.allowed_doc_types
        assert "purchase_return" in agent.allowed_doc_types
        assert "order" not in agent.allowed_doc_types

    def test_field_map(self):
        agent = _make_purchase()
        assert agent.FIELD_MAP["outer_id"] == "product_code"

    def test_system_prompt(self):
        prompt = _make_purchase().system_prompt
        assert "采购" in prompt
        assert "不负责" in prompt


class TestPurchaseValidation:

    def test_arrival_progress_ok(self):
        r = _make_purchase().validate_params("arrival_progress", {"po_no": "PO-001"})
        assert r.is_ok

    def test_arrival_progress_sku_ok(self):
        r = _make_purchase().validate_params("arrival_progress", {"sku_list": ["A01"]})
        assert r.is_ok

    def test_arrival_progress_missing(self):
        r = _make_purchase().validate_params("arrival_progress", {})
        assert r.is_missing

    def test_supplier_query_ok(self):
        r = _make_purchase().validate_params("supplier_query", {"supplier_name": "供应商A"})
        assert r.is_ok

    def test_supplier_query_missing(self):
        r = _make_purchase().validate_params("supplier_query", {})
        assert r.is_missing

    def test_purchase_list_time_ok(self):
        r = _make_purchase().validate_params("purchase_list", {
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_ok

    def test_purchase_list_po_no_ok(self):
        r = _make_purchase().validate_params("purchase_list", {"po_no": "PO-001"})
        assert r.is_ok

    def test_purchase_list_missing(self):
        r = _make_purchase().validate_params("purchase_list", {})
        assert r.is_missing

    def test_purchase_list_bad_time(self):
        r = _make_purchase().validate_params("purchase_list", {
            "time_range": "bad",
        })
        assert r.is_conflict

    def test_purchase_return_ok(self):
        r = _make_purchase().validate_params("purchase_return", {
            "product_code": "A001",
        })
        assert r.is_ok

    def test_purchase_return_missing(self):
        r = _make_purchase().validate_params("purchase_return", {})
        assert r.is_missing

    def test_unknown_action_ok(self):
        r = _make_purchase().validate_params("unknown", {})
        assert r.is_ok


class TestPurchaseQueries:

    @pytest.mark.asyncio
    async def test_query_purchase(self):
        agent = _make_purchase()
        mock = ToolOutput(summary="采购数据", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock)
            result = await agent.query_purchase(mode="detail", filters=[])
            assert result.summary == "采购数据"

    @pytest.mark.asyncio
    async def test_query_purchase_return(self):
        agent = _make_purchase()
        mock = ToolOutput(summary="采退数据", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock)
            result = await agent.query_purchase_return(mode="summary", filters=[])
            assert result.summary == "采退数据"

    @pytest.mark.asyncio
    async def test_query_order_blocked(self):
        """采购Agent不能查订单"""
        agent = _make_purchase()
        result = await agent._query_local_data("order")
        assert result.status == OutputStatus.ERROR


# ============================================================
# TradeAgent
# ============================================================


def _make_trade(db=None, org_id=None):
    from services.agent.departments.trade_agent import TradeAgent
    return TradeAgent(db=db or MagicMock(), org_id=org_id)


class TestTradeProperties:

    def test_domain(self):
        assert _make_trade().domain == "trade"

    def test_tools(self):
        tools = _make_trade().tools
        assert "local_data" in tools
        assert "erp_trade_query" in tools
        assert "erp_taobao_query" in tools

    def test_allowed_doc_types(self):
        agent = _make_trade()
        assert "order" in agent.allowed_doc_types
        assert "purchase" not in agent.allowed_doc_types

    def test_system_prompt(self):
        prompt = _make_trade().system_prompt
        assert "订单" in prompt
        assert "不负责" in prompt


class TestTradeValidation:

    def test_order_list_by_no(self):
        r = _make_trade().validate_params("order_list", {"order_no": "T001"})
        assert r.is_ok

    def test_order_list_by_time(self):
        r = _make_trade().validate_params("order_list", {
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_ok

    def test_order_list_by_platform_no(self):
        r = _make_trade().validate_params("order_list", {
            "platform_order_no": "123456789012345678",
        })
        assert r.is_ok

    def test_order_list_missing(self):
        r = _make_trade().validate_params("order_list", {})
        assert r.is_missing

    def test_order_list_bad_time(self):
        r = _make_trade().validate_params("order_list", {
            "time_range": "2026-01-01 ~ 2026-06-01",
        })
        assert r.is_conflict
        assert "90天" in r.message

    def test_logistics_query_ok(self):
        r = _make_trade().validate_params("logistics_query", {"order_no": "T001"})
        assert r.is_ok

    def test_logistics_query_missing(self):
        r = _make_trade().validate_params("logistics_query", {})
        assert r.is_missing

    def test_unknown_action_ok(self):
        r = _make_trade().validate_params("unknown", {})
        assert r.is_ok


class TestTradeQueries:

    @pytest.mark.asyncio
    async def test_query_orders(self):
        agent = _make_trade()
        mock = ToolOutput(summary="订单数据", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock)
            result = await agent.query_orders(mode="detail", filters=[])
            assert result.summary == "订单数据"

    @pytest.mark.asyncio
    async def test_query_purchase_blocked(self):
        """订单Agent不能查采购"""
        agent = _make_trade()
        result = await agent._query_local_data("purchase")
        assert result.status == OutputStatus.ERROR


# ============================================================
# AftersaleAgent
# ============================================================


def _make_aftersale(db=None, org_id=None):
    from services.agent.departments.aftersale_agent import AftersaleAgent
    return AftersaleAgent(db=db or MagicMock(), org_id=org_id)


class TestAftersaleProperties:

    def test_domain(self):
        assert _make_aftersale().domain == "aftersale"

    def test_tools(self):
        tools = _make_aftersale().tools
        assert "local_data" in tools
        assert "erp_aftersales_query" in tools

    def test_allowed_doc_types(self):
        agent = _make_aftersale()
        assert "aftersale" in agent.allowed_doc_types
        assert "order" not in agent.allowed_doc_types

    def test_system_prompt(self):
        prompt = _make_aftersale().system_prompt
        assert "售后" in prompt
        assert "不负责" in prompt


class TestAftersaleValidation:

    def test_aftersale_list_by_time(self):
        r = _make_aftersale().validate_params("aftersale_list", {
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_ok

    def test_aftersale_list_by_code(self):
        r = _make_aftersale().validate_params("aftersale_list", {
            "product_code": "A001",
        })
        assert r.is_ok

    def test_aftersale_list_by_no(self):
        r = _make_aftersale().validate_params("aftersale_list", {
            "aftersale_no": "AS001",
        })
        assert r.is_ok

    def test_aftersale_list_missing(self):
        r = _make_aftersale().validate_params("aftersale_list", {})
        assert r.is_missing

    def test_aftersale_list_bad_time(self):
        r = _make_aftersale().validate_params("aftersale_list", {
            "time_range": "bad",
        })
        assert r.is_conflict

    def test_return_rate_ok(self):
        r = _make_aftersale().validate_params("return_rate", {
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_ok

    def test_return_rate_missing_time(self):
        r = _make_aftersale().validate_params("return_rate", {})
        assert r.is_missing

    def test_return_rate_over_90_days(self):
        r = _make_aftersale().validate_params("return_rate", {
            "time_range": "2026-01-01 ~ 2026-06-01",
        })
        assert r.is_conflict

    def test_unknown_action_ok(self):
        r = _make_aftersale().validate_params("unknown", {})
        assert r.is_ok


class TestAftersaleQueries:

    @pytest.mark.asyncio
    async def test_query_aftersale(self):
        agent = _make_aftersale()
        mock = ToolOutput(summary="售后数据", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock)
            result = await agent.query_aftersale(mode="detail", filters=[])
            assert result.summary == "售后数据"

    @pytest.mark.asyncio
    async def test_query_order_blocked(self):
        """售后Agent不能查订单"""
        agent = _make_aftersale()
        result = await agent._query_local_data("order")
        assert result.status == OutputStatus.ERROR


# ============================================================
# _dispatch include_invalid 透传测试
# ============================================================


class TestDispatchIncludeInvalid:
    """所有 agent 的 _dispatch 应透传 include_invalid 到 _query_local_data。"""

    @pytest.mark.asyncio
    async def test_trade_dispatch_include_invalid(self):
        agent = _make_trade()
        mock_out = ToolOutput(summary="ok", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock_out)
            await agent._dispatch("order_list", {
                "mode": "summary",
                "filters": [],
                "include_invalid": True,
            }, {})
            call_kwargs = M.return_value.execute.call_args
            assert call_kwargs.kwargs.get("include_invalid") is True

    @pytest.mark.asyncio
    async def test_trade_dispatch_include_invalid_default_false(self):
        agent = _make_trade()
        mock_out = ToolOutput(summary="ok", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock_out)
            await agent._dispatch("order_list", {
                "mode": "summary", "filters": [],
            }, {})
            call_kwargs = M.return_value.execute.call_args
            assert call_kwargs.kwargs.get("include_invalid") is False

    @pytest.mark.asyncio
    async def test_aftersale_dispatch_include_invalid(self):
        agent = _make_aftersale()
        mock_out = ToolOutput(summary="ok", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock_out)
            await agent._dispatch("aftersale_list", {
                "mode": "summary",
                "filters": [],
                "include_invalid": True,
            }, {})
            call_kwargs = M.return_value.execute.call_args
            assert call_kwargs.kwargs.get("include_invalid") is True

    @pytest.mark.asyncio
    async def test_purchase_dispatch_include_invalid(self):
        agent = _make_purchase()
        mock_out = ToolOutput(summary="ok", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=mock_out)
            await agent._dispatch("purchase_list", {
                "mode": "summary",
                "filters": [],
                "include_invalid": True,
            }, {})
            call_kwargs = M.return_value.execute.call_args
            assert call_kwargs.kwargs.get("include_invalid") is True


# ============================================================
# L3 空结果诊断集成测试
# ============================================================


class TestL3EmptyDiagnosis:
    """_query_local_data 返回 EMPTY 时应追加诊断建议。"""

    @pytest.mark.asyncio
    async def test_empty_with_platform_gets_diagnosis(self):
        agent = _make_trade()
        empty_out = ToolOutput(
            summary="订单 无记录", source="erp",
            status=OutputStatus.EMPTY,
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=empty_out)
            result = await agent._query_local_data(
                "order", mode="summary",
                filters=[{"field": "platform", "op": "eq", "value": "tb"}],
            )
            assert "诊断建议" in result.summary
            assert "淘宝" in result.summary

    @pytest.mark.asyncio
    async def test_empty_without_filters_no_diagnosis(self):
        agent = _make_trade()
        empty_out = ToolOutput(
            summary="订单 无记录", source="erp",
            status=OutputStatus.EMPTY,
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=empty_out)
            result = await agent._query_local_data(
                "order", mode="summary", filters=[],
            )
            assert "诊断建议" not in result.summary

    @pytest.mark.asyncio
    async def test_non_empty_no_diagnosis(self):
        agent = _make_trade()
        ok_out = ToolOutput(summary="订单数据", source="erp")
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=ok_out)
            result = await agent._query_local_data(
                "order", mode="summary",
                filters=[{"field": "platform", "op": "eq", "value": "tb"}],
            )
            assert "诊断建议" not in result.summary

    @pytest.mark.asyncio
    async def test_error_gets_retry_hint(self):
        """ERROR 结果追加重试建议"""
        agent = _make_trade()
        err_out = ToolOutput(
            summary="统计查询失败: timeout",
            source="erp",
            status=OutputStatus.ERROR,
            error_message="query timeout after 30s",
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=err_out)
            result = await agent._query_local_data(
                "order", mode="summary", filters=[],
            )
            assert "重试建议" in result.summary
            assert "超时" in result.summary

    @pytest.mark.asyncio
    async def test_error_unknown_no_hint(self):
        """未知错误类型不追加建议"""
        agent = _make_trade()
        err_out = ToolOutput(
            summary="奇怪的错误",
            source="erp",
            status=OutputStatus.ERROR,
            error_message="something weird happened",
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=err_out)
            result = await agent._query_local_data(
                "order", mode="summary", filters=[],
            )
            assert "重试建议" not in result.summary


# ============================================================
# Staging + Data Profile 集成
# ============================================================


class TestStagingDir:
    """DepartmentAgent staging_dir 传递"""

    def test_init_accepts_staging_dir(self):
        """__init__ 接受 staging_dir 参数"""
        from services.agent.departments.trade_agent import TradeAgent
        agent = TradeAgent(db=MagicMock(), staging_dir="/tmp/test_staging")
        assert agent._staging_dir == "/tmp/test_staging"

    def test_init_staging_dir_default_none(self):
        """staging_dir 默认 None（向后兼容）"""
        from services.agent.departments.trade_agent import TradeAgent
        agent = TradeAgent(db=MagicMock())
        assert agent._staging_dir is None


class TestWriteToStaging:
    """_write_to_staging 返回 tuple + profile"""

    def test_returns_tuple(self, tmp_path):
        """返回 (FileRef, profile_text) 元组"""
        from services.agent.departments.trade_agent import TradeAgent
        from services.agent.tool_output import ColumnMeta

        agent = TradeAgent(db=MagicMock())
        rows = [{"order_no": "A001", "amount": 99.9}]
        columns = [
            ColumnMeta("order_no", "text", "订单号"),
            ColumnMeta("amount", "numeric", "金额"),
        ]
        file_ref, profile = agent._write_to_staging(rows, columns, str(tmp_path))

        assert isinstance(profile, str)
        assert "[数据已暂存]" in profile
        assert "[字段]" in profile
        assert file_ref.row_count == 1
        assert file_ref.filename.startswith("trade_")
        assert file_ref.preview == profile

    def test_parquet_file_created(self, tmp_path):
        """staging 目录下生成 parquet 文件"""
        from services.agent.departments.trade_agent import TradeAgent
        from services.agent.tool_output import ColumnMeta

        agent = TradeAgent(db=MagicMock())
        rows = [{"a": 1}, {"a": 2}]
        columns = [ColumnMeta("a", "integer")]
        file_ref, _ = agent._write_to_staging(rows, columns, str(tmp_path))

        assert Path(file_ref.path).exists()
        assert file_ref.format == "parquet"


class TestQueryLocalDataDetailStaging:
    """_query_local_data detail 模式走 staging"""

    @pytest.mark.asyncio
    async def test_detail_mode_with_staging_returns_file_ref(self, tmp_path):
        """detail 模式 + staging_dir → FILE_REF 格式"""
        from services.agent.departments.trade_agent import TradeAgent

        agent = TradeAgent(
            db=MagicMock(), staging_dir=str(tmp_path),
        )
        detail_out = ToolOutput(
            summary="订单明细",
            format=OutputFormat.TABLE,
            source="trade",
            status=OutputStatus.OK,
            data=[{"order_no": "A001", "amount": 99.9}],
            columns=None,
            metadata={"doc_type": "order"},
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=detail_out)
            result = await agent._query_local_data(
                "order", mode="detail", filters=[],
            )
        assert result.format == OutputFormat.FILE_REF
        assert result.file_ref is not None
        assert "[数据已暂存]" in result.summary

    @pytest.mark.asyncio
    async def test_detail_mode_without_staging_stays_table(self):
        """detail 模式无 staging_dir → 保持 TABLE 格式（降级）"""
        from services.agent.departments.trade_agent import TradeAgent

        agent = TradeAgent(db=MagicMock())  # 无 staging_dir
        detail_out = ToolOutput(
            summary="订单明细",
            format=OutputFormat.TABLE,
            source="trade",
            status=OutputStatus.OK,
            data=[{"order_no": "A001"}],
            metadata={"doc_type": "order"},
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=detail_out)
            result = await agent._query_local_data(
                "order", mode="detail", filters=[],
            )
        assert result.format == OutputFormat.TABLE  # 降级，没走 staging

    @pytest.mark.asyncio
    async def test_summary_mode_not_affected(self, tmp_path):
        """summary 模式不受 staging 影响"""
        from services.agent.departments.trade_agent import TradeAgent

        agent = TradeAgent(
            db=MagicMock(), staging_dir=str(tmp_path),
        )
        summary_out = ToolOutput(
            summary="统计结果",
            format=OutputFormat.TABLE,
            source="trade",
            status=OutputStatus.OK,
            data=[{"count": 100}],
            metadata={},
        )
        with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as M:
            M.return_value.execute = AsyncMock(return_value=summary_out)
            result = await agent._query_local_data(
                "order", mode="summary", filters=[],
            )
        assert result.format == OutputFormat.TABLE  # summary 保持不变
