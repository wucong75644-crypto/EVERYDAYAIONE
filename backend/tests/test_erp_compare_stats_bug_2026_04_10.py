"""4-10 weekday bug 端到端回归测试。

复现：用户在企微中查询「4月10日 vs 同期对比」，模型回复中将 4 月 3 日错标为「上周四」
（实际是周五）。

修复：local_compare_stats 工具由后端确定地计算 weekday，模型只复述。

设计文档：docs/document/TECH_ERP时间准确性架构.md §1.1 / §8.3
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from services.kuaimai.erp_local_compare_stats import local_compare_stats
from utils.time_context import RequestContext, TimePoint

CN = ZoneInfo("Asia/Shanghai")
BUG_TIME = datetime(2026, 4, 10, 13, 5, tzinfo=CN)


class _MockExecuteResult:
    def __init__(self, data):
        self.data = data


class _MockRPC:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _MockExecuteResult(self._data)


class MockDB:
    """伪造 DB，记录 RPC 调用并返回固定数据。"""

    def __init__(self):
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, dict(params)))
        # 模拟两次 RPC 调用：当前期 vs 基线期
        # 当前期 = 2026-04-10 → 1769 笔
        # 基线期 = 2026-04-03 → 2955 笔
        start = str(params["p_start"])
        if "2026-04-10" in start:
            return _MockRPC({
                "doc_count": 1769,
                "total_qty": 2000,
                "total_amount": 100000.0,
            })
        return _MockRPC({
            "doc_count": 2955,
            "total_qty": 3500,
            "total_amount": 180000.0,
        })

    def table(self, name):
        # 用于 check_sync_health (返回空数据)
        class _T:
            def select(self, *a, **kw):
                return self

            def in_(self, *a, **kw):
                return self

            def execute(self):
                return _MockExecuteResult([])

        return _T()


@pytest.mark.asyncio
async def test_bug_2026_04_10_baseline_is_friday_not_thursday():
    """核心回归：基线期必须标注为「周五（上周五）」，绝对不能是「周四」。"""
    ctx = RequestContext(
        now=BUG_TIME,
        today=TimePoint.from_datetime(BUG_TIME, reference=BUG_TIME),
        user_id="test_user",
        org_id="test_org",
        request_id="bug_test",
    )

    db = MockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="wow",
        current_period="today",
        request_ctx=ctx,
    )

    # 当前期：2026-04-10 周五
    assert "2026-04-10" in result
    assert "周五" in result

    # 基线期：2026-04-03 周五（上周同期）
    assert "2026-04-03" in result
    # 4-3 必须是「周五」，不能是「周四」（这是 bug）
    assert "周四" not in result, f"出现了「周四」幻觉！\n{result}"
    # 必须明确标注为周五
    assert "上周五" in result or "周五" in result

    # 数据正确
    assert "1769" in result or "1,769" in result
    assert "2955" in result or "2,955" in result

    # 对比模式说明
    assert "上周" in result  # "环比上周同期" 或 "上周"

    # ISO 周语义说明
    assert "ISO" in result or "周一为始" in result


@pytest.mark.asyncio
async def test_bug_4_10_rpc_call_dates_are_correct():
    """RPC 双查的日期参数正确：当前=4-10，基线=4-3。"""
    ctx = RequestContext(
        now=BUG_TIME,
        today=TimePoint.from_datetime(BUG_TIME, reference=BUG_TIME),
        user_id="u",
        org_id="o",
    )
    db = MockDB()
    await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="wow",
        current_period="today",
        request_ctx=ctx,
    )

    assert len(db.rpc_calls) == 2

    # 第一次：当前期 4-10
    name1, params1 = db.rpc_calls[0]
    assert name1 == "erp_global_stats_query"
    assert "2026-04-10" in str(params1["p_start"])

    # 第二次：基线期 4-3
    name2, params2 = db.rpc_calls[1]
    assert name2 == "erp_global_stats_query"
    assert "2026-04-03" in str(params2["p_start"])


@pytest.mark.asyncio
async def test_compare_kind_mom_returns_last_month_same_day():
    """月环比：当前=4-10，基线=3-10。"""
    ctx = RequestContext(
        now=BUG_TIME,
        today=TimePoint.from_datetime(BUG_TIME, reference=BUG_TIME),
        user_id="u",
        org_id="o",
    )
    db = MockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="mom",
        current_period="today",
        request_ctx=ctx,
    )
    assert "2026-04-10" in result
    assert "2026-03-10" in result
    # 3-10 是周二
    assert "上月" in result or "周二" in result


@pytest.mark.asyncio
async def test_compare_kind_yoy_returns_last_year_same_day():
    """年同比：当前=2026-04-10，基线=2025-04-10。"""
    ctx = RequestContext(
        now=BUG_TIME,
        today=TimePoint.from_datetime(BUG_TIME, reference=BUG_TIME),
        user_id="u",
        org_id="o",
    )
    db = MockDB()
    result = await local_compare_stats(
        db=db,
        doc_type="order",
        compare_kind="yoy",
        current_period="today",
        request_ctx=ctx,
    )
    assert "2026-04-10" in result
    assert "2025-04-10" in result


# ────────────────────────────────────────────────────────────────────
# 双加号 bug 防护（不能出现 ++100）
# ────────────────────────────────────────────────────────────────────


class _PositiveGrowthMockDB:
    """模拟「当前期增长」场景：当前 3000 笔 vs 基线 2000 笔。"""

    def rpc(self, name, params):
        start = str(params["p_start"])
        if "2026-04-10" in start:
            data = {"doc_count": 3000, "total_qty": 5000, "total_amount": 200000.0}
        else:
            data = {"doc_count": 2000, "total_qty": 3000, "total_amount": 150000.0}
        class _R:
            def execute(_self):
                class _D: pass
                d = _D()
                d.data = data
                return d
        return _R()

    def table(self, name):
        class _T:
            def select(self, *a, **kw): return self
            def in_(self, *a, **kw): return self
            def execute(self):
                class _D: pass
                d = _D()
                d.data = []
                return d
        return _T()


@pytest.mark.asyncio
async def test_no_double_plus_sign_for_positive_growth():
    """正增长格式必须是 +1000 而不是 ++1000（双加号 bug 回归）。"""
    ctx = RequestContext(
        now=BUG_TIME,
        today=TimePoint.from_datetime(BUG_TIME, reference=BUG_TIME),
        user_id="u",
        org_id="o",
    )
    result = await local_compare_stats(
        db=_PositiveGrowthMockDB(),
        doc_type="order",
        compare_kind="wow",
        current_period="today",
        request_ctx=ctx,
    )
    # 核心：禁止双加号
    assert "++" not in result, f"出现了双加号：\n{result}"
    # 必须有正确的 +1000 格式
    assert "+1000" in result or "+1,000" in result
    assert "+50.0%" in result  # (3000-2000)/2000 = 50%

