"""对比工具 + 分类引擎集成测试。

修复：local_compare_stats 对 doc_type=order 走分类引擎过滤空包/刷单/补发/已关闭，
只用有效订单数据做同比/环比对比。
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from config.default_classification_rules import DEFAULT_ORDER_RULES
from services.kuaimai.erp_local_compare_stats import local_compare_stats
from services.kuaimai.order_classifier import OrderClassifier
from utils.time_context import RequestContext, TimePoint

CN = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 4, 22, 14, 0, tzinfo=CN)
ORG = "test_classified_org"


class _Exec:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _Exec(self._data)


class ClassifiedMockDB:
    """模拟 DB，erp_order_stats_grouped 返回含刷单/有效行的分组数据。"""

    def __init__(self):
        self.rpc_calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, dict(params)))
        start = str(params.get("p_start", ""))

        if name == "erp_order_stats_grouped":
            # 当前期：100笔有效 + 20笔刷单(order_type含10) + 5笔补发(order_type含14)
            if "2026-04-22" in start:
                return _Rpc([
                    {"order_type": "0,2", "order_status": "ACTIVE", "is_scalping": 0,
                     "doc_count": 100, "total_qty": 200, "total_amount": 50000.0},
                    {"order_type": "10", "order_status": "ACTIVE", "is_scalping": 0,
                     "doc_count": 20, "total_qty": 40, "total_amount": 8000.0},
                    {"order_type": "14", "order_status": "ACTIVE", "is_scalping": 0,
                     "doc_count": 5, "total_qty": 10, "total_amount": 2000.0},
                ])
            # 基线期（上周）：80笔有效 + 15笔刷单
            return _Rpc([
                {"order_type": "0,2", "order_status": "ACTIVE", "is_scalping": 0,
                 "doc_count": 80, "total_qty": 160, "total_amount": 40000.0},
                {"order_type": "10", "order_status": "ACTIVE", "is_scalping": 0,
                 "doc_count": 15, "total_qty": 30, "total_amount": 6000.0},
            ])

        # erp_global_stats_query 不应被调用（订单走分类引擎）
        raise AssertionError(f"订单对比不应调用 {name}")

    def table(self, name: str):
        class _T:
            def select(self, *a, **kw): return self
            def in_(self, *a, **kw): return self
            def execute(self):
                return _Exec([])
        return _T()


@pytest.fixture(autouse=True)
def _warm_classifier_cache():
    """预热分类引擎缓存，避免 mock DB 需要支持完整的规则加载链路。"""
    OrderClassifier._cache[ORG] = (DEFAULT_ORDER_RULES, time.time() + 600)
    yield
    OrderClassifier._cache.pop(ORG, None)


def _make_ctx() -> RequestContext:
    return RequestContext(
        now=NOW,
        today=TimePoint.from_datetime(NOW, reference=NOW),
        user_id="test",
        org_id=ORG,
    )


@pytest.mark.asyncio
async def test_order_compare_uses_classifier():
    """订单对比走分类引擎，只用有效订单数据。"""
    db = ClassifiedMockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="wow",
        current_period="today",
        org_id=ORG,
        request_ctx=_make_ctx(),
    )

    # 必须调用 erp_order_stats_grouped 而非 erp_global_stats_query
    rpc_names = [name for name, _ in db.rpc_calls]
    assert "erp_order_stats_grouped" in rpc_names
    assert "erp_global_stats_query" not in rpc_names

    text = result.summary
    # 当前期有效=100笔，基线期有效=80笔（刷单/补发被过滤）
    assert "100" in text
    assert "80" in text
    # 刷单数(20)和补发数(5)不应出现在最终汇总里
    # 总数 125 (100+20+5) 不应出现
    assert "125" not in text


@pytest.mark.asyncio
async def test_order_compare_valid_amounts():
    """分类引擎过滤后的金额正确。"""
    db = ClassifiedMockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="wow",
        current_period="today",
        org_id=ORG,
        request_ctx=_make_ctx(),
    )

    # 当前期有效金额 50000，基线期 40000
    assert "50,000" in result.summary or "50000" in result.summary
    assert "40,000" in result.summary or "40000" in result.summary


@pytest.mark.asyncio
async def test_non_order_skips_classifier():
    """非订单类型（采购）不走分类引擎，直接用 erp_global_stats_query。"""

    class PurchaseMockDB:
        def __init__(self):
            self.rpc_calls = []

        def rpc(self, name, params):
            self.rpc_calls.append((name, dict(params)))
            return _Rpc({"doc_count": 50, "total_qty": 100, "total_amount": 20000.0})

        def table(self, name):
            class _T:
                def select(self, *a, **kw): return self
                def in_(self, *a, **kw): return self
                def execute(self): return _Exec([])
            return _T()

    db = PurchaseMockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="purchase",
        compare_kind="wow",
        current_period="today",
        org_id=ORG,
        request_ctx=_make_ctx(),
    )

    rpc_names = [name for name, _ in db.rpc_calls]
    assert all(n == "erp_global_stats_query" for n in rpc_names)
    assert "erp_order_stats_grouped" not in rpc_names


@pytest.mark.asyncio
async def test_classifier_failure_falls_back():
    """分类引擎异常时回退到 erp_global_stats_query。"""
    # 清除缓存，让 for_org 实际执行（会因 mock DB 不支持而失败）
    OrderClassifier._cache.pop(ORG, None)

    class FallbackMockDB:
        def __init__(self):
            self.rpc_calls = []

        def rpc(self, name, params):
            self.rpc_calls.append((name, dict(params)))
            return _Rpc({"doc_count": 200, "total_qty": 400, "total_amount": 80000.0})

        def table(self, name):
            # 不支持 eq/is_/order → OrderClassifier.for_org 会抛异常
            class _T:
                def select(self, *a, **kw): return self
                def in_(self, *a, **kw): return self
                def execute(self): return _Exec([])
            return _T()

    db = FallbackMockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="wow",
        current_period="today",
        org_id=ORG,
        request_ctx=_make_ctx(),
    )

    # 回退到 erp_global_stats_query
    rpc_names = [name for name, _ in db.rpc_calls]
    assert "erp_global_stats_query" in rpc_names
    assert "200" in result.summary
