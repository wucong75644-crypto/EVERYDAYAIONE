"""
erp_classified_summary 单元测试

覆盖：services/kuaimai/erp_classified_summary.py
- classified_summary: RPC 调用 + 分类引擎 → ToolOutput
- _build_flat: 无分组统计构建
- _build_grouped: 分组统计构建
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_classified_summary import (
    _build_flat,
    _build_grouped,
    classified_summary,
    CLASSIFIED_FLAT_COLUMNS,
    CLASSIFIED_GROUPED_COLUMNS,
)
from services.kuaimai.erp_unified_schema import TimeRange


# ── 测试工具 ──

def _make_tr(**overrides) -> TimeRange:
    defaults = {
        "start_iso": "2026-04-01T00:00:00+08:00",
        "end_iso": "2026-04-30T23:59:59+08:00",
        "time_col": "pay_time",
        "date_range": MagicMock(),  # DateRange dataclass，测试中 mock 即可
        "label": "本月",
    }
    defaults.update(overrides)
    return TimeRange(**defaults)


def _make_classifier_result(total_docs=100, total_amount=50000,
                            valid_docs=80, valid_amount=40000,
                            categories=None):
    """构造 mock ClassificationResult。"""
    cr = MagicMock()
    cr.total = {"doc_count": total_docs, "total_amount": total_amount}
    cr.valid = {"doc_count": valid_docs, "total_amount": valid_amount}
    cr.categories_list = categories or [
        {"name": "正常", "doc_count": 80},
        {"name": "刷单", "doc_count": 20},
    ]
    cr.to_display_text = MagicMock(return_value="订单分类统计文本")
    return cr


def _make_db_mock(rpc_data=None, rpc_error=None):
    """构造 mock db，支持 RPC 和 table 诊断查询。"""
    db = MagicMock()

    # table 诊断查询链
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.lt.return_value = chain
    chain.execute.return_value = MagicMock(count=100, data=[])
    db.table.return_value = chain

    # RPC 调用
    if rpc_error:
        db.rpc.return_value.execute.side_effect = rpc_error
    else:
        db.rpc.return_value.execute.return_value = MagicMock(
            data=rpc_data or []
        )
    return db


# ── _build_flat 测试 ──

class TestBuildFlat:

    def test_returns_tooloutput_with_flat_data(self):
        """正常分类返回扁平化 ToolOutput。"""
        cr = _make_classifier_result()
        classifier = MagicMock()
        classifier.classify.return_value = cr
        tr = _make_tr()

        result = _build_flat(classifier, [{"doc_count": 100}], "统计区间", tr, False)

        assert result is not None
        assert result.format.value == "table"
        assert result.data[0]["total_orders"] == 100
        assert result.data[0]["valid_orders"] == 80
        assert result.data[0]["正常单数"] == 80
        assert result.data[0]["刷单单数"] == 20
        assert result.columns == CLASSIFIED_FLAT_COLUMNS

    def test_classifier_exception_returns_none(self):
        """分类引擎抛异常时返回 None（回退到普通统计）。"""
        classifier = MagicMock()
        classifier.classify.side_effect = RuntimeError("OOM")
        tr = _make_tr()

        result = _build_flat(classifier, [], "", tr, False)
        assert result is None

    def test_empty_time_header(self):
        """time_header 为空时 summary 不加前缀。"""
        cr = _make_classifier_result()
        classifier = MagicMock()
        classifier.classify.return_value = cr
        tr = _make_tr()

        result = _build_flat(classifier, [], "", tr, False)
        assert result.summary == "订单分类统计文本"

    def test_metadata_has_doc_type_order(self):
        """metadata 固定包含 doc_type=order。"""
        cr = _make_classifier_result()
        classifier = MagicMock()
        classifier.classify.return_value = cr
        tr = _make_tr()

        result = _build_flat(classifier, [], "header", tr, False)
        assert result.metadata["doc_type"] == "order"
        assert result.metadata["time_range"] == "本月"


# ── _build_grouped 测试 ──

class TestBuildGrouped:

    def test_returns_grouped_data(self):
        """分组分类返回每组一行数据。"""
        cr1 = _make_classifier_result(total_docs=60, valid_docs=50)
        cr2 = _make_classifier_result(total_docs=40, valid_docs=30)

        classifier = MagicMock()
        classifier.classify_grouped.return_value = {"淘宝": cr1, "京东": cr2}
        tr = _make_tr()

        with patch(
            "services.kuaimai.erp_classified_summary.fmt_classified_grouped",
            return_value="分组统计文本",
        ):
            result = _build_grouped(
                classifier, [], "platform", "header", tr, False,
            )

        assert result is not None
        assert len(result.data) == 2
        assert result.data[0]["group_key"] == "淘宝"
        assert result.data[0]["total_orders"] == 60
        assert result.data[1]["group_key"] == "京东"
        assert result.columns == CLASSIFIED_GROUPED_COLUMNS
        assert result.metadata["group_by"] == "platform"

    def test_grouped_classifier_exception_returns_none(self):
        """分组分类引擎抛异常时返回 None。"""
        classifier = MagicMock()
        classifier.classify_grouped.side_effect = ValueError("bad data")
        tr = _make_tr()

        result = _build_grouped(classifier, [], "shop", "", tr, False)
        assert result is None


# ── classified_summary 集成测试 ──

class TestClassifiedSummary:

    @pytest.mark.asyncio
    async def test_rpc_failure_returns_none(self):
        """RPC 失败时返回 None（回退到普通统计）。"""
        db = _make_db_mock(rpc_error=RuntimeError("connection timeout"))
        tr = _make_tr()

        result = await classified_summary(db, "org1", [], tr, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_rpc_data_returns_none(self):
        """RPC 返回空数据时返回 None。"""
        db = _make_db_mock(rpc_data=[])
        tr = _make_tr()

        result = await classified_summary(db, "org1", [], tr, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_classifier_load_failure_returns_none(self):
        """分类引擎加载失败时返回 None。"""
        rpc_data = [{"doc_count": 100, "total_qty": 200, "total_amount": 50000}]
        db = _make_db_mock(rpc_data=rpc_data)
        tr = _make_tr()

        with patch(
            "services.kuaimai.order_classifier.OrderClassifier"
        ) as MockCls:
            MockCls.for_org.side_effect = RuntimeError("no rules")
            result = await classified_summary(db, "org1", [], tr, None)

        assert result is None

    @pytest.mark.asyncio
    async def test_success_flat_returns_tooloutput(self):
        """无分组：成功返回扁平化 ToolOutput。"""
        rpc_data = [{"doc_count": 100, "total_qty": 200, "total_amount": 50000}]
        db = _make_db_mock(rpc_data=rpc_data)
        tr = _make_tr()

        cr = _make_classifier_result()
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = cr

        with patch(
            "services.kuaimai.order_classifier.OrderClassifier"
        ) as MockCls:
            MockCls.for_org.return_value = mock_classifier
            result = await classified_summary(db, "org1", [], tr, None)

        assert result is not None
        assert result.data[0]["total_orders"] == 100
        assert result.metadata["doc_type"] == "order"

    @pytest.mark.asyncio
    async def test_success_grouped_returns_tooloutput(self):
        """有分组：成功返回分组 ToolOutput。"""
        rpc_data = [
            {"doc_count": 60, "total_qty": 100, "total_amount": 30000,
             "group_key": "tb", "platform": "tb"},
            {"doc_count": 40, "total_qty": 80, "total_amount": 20000,
             "group_key": "jd", "platform": "jd"},
        ]
        db = _make_db_mock(rpc_data=rpc_data)
        tr = _make_tr()

        cr1 = _make_classifier_result(total_docs=60, valid_docs=50)
        cr2 = _make_classifier_result(total_docs=40, valid_docs=30)
        mock_classifier = MagicMock()
        mock_classifier.classify_grouped.return_value = {"淘宝": cr1, "京东": cr2}

        with patch(
            "services.kuaimai.order_classifier.OrderClassifier"
        ) as MockCls, patch(
            "services.kuaimai.erp_classified_summary.fmt_classified_grouped",
            return_value="分组统计",
        ):
            MockCls.for_org.return_value = mock_classifier
            result = await classified_summary(
                db, "org1", [], tr, None, group_by=["platform"],
            )

        assert result is not None
        assert len(result.data) == 2
        assert result.metadata["group_by"] == "platform"

    @pytest.mark.asyncio
    async def test_non_list_rpc_data_returns_none(self):
        """RPC 返回非 list 数据时返回 None。"""
        db = _make_db_mock(rpc_data={"error": "bad query"})
        tr = _make_tr()

        result = await classified_summary(db, "org1", [], tr, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_diag_query_failure_does_not_block(self):
        """诊断直查失败不影响主流程。"""
        rpc_data = [{"doc_count": 50, "total_qty": 100, "total_amount": 25000}]
        db = _make_db_mock(rpc_data=rpc_data)
        # 让诊断直查抛异常
        db.table.return_value.select.side_effect = RuntimeError("diag fail")
        tr = _make_tr()

        cr = _make_classifier_result(total_docs=50, valid_docs=40)
        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = cr

        with patch(
            "services.kuaimai.order_classifier.OrderClassifier"
        ) as MockCls:
            MockCls.for_org.return_value = mock_classifier
            result = await classified_summary(db, "org1", [], tr, None)

        # 诊断失败不影响结果
        assert result is not None
        assert result.data[0]["total_orders"] == 50
