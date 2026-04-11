"""L4 TemporalValidator 单元测试。

设计文档：docs/document/TECH_ERP时间准确性架构.md §14
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.agent.guardrails.temporal_validator import (
    TemporalDeviation,
    validate_and_patch,
)
from utils.time_context import RequestContext, TimePoint

CN = ZoneInfo("Asia/Shanghai")
FRI_4_10 = datetime(2026, 4, 10, 13, 5, tzinfo=CN)


def _make_ctx(now: datetime = FRI_4_10) -> RequestContext:
    return RequestContext(
        now=now,
        today=TimePoint.from_datetime(now, reference=now),
        user_id="test",
        org_id="test",
    )


# ────────────────────────────────────────────────────────────────────
# 正常场景 — 应该修复
# ────────────────────────────────────────────────────────────────────


class TestShouldPatch:
    """应该检测并修复的幻觉。"""

    def test_bug_4_10_original_text(self):
        """4-10 bug 的原始模型输出。"""
        ctx = _make_ctx()
        text = "4月3日（上周四）截止同一时间：订单量 2,955 笔"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert devs[0].date_str == "2026-04-03"
        assert devs[0].claimed_weekday == "周四"
        assert devs[0].actual_weekday == "周五"
        assert "上周五" in patched
        assert "周四" not in patched

    def test_iso_date_with_wrong_weekday(self):
        ctx = _make_ctx()
        text = "2026-04-10 周四 订单 1769 笔"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert "周五" in patched
        assert "周四" not in patched

    def test_slash_date_format(self):
        ctx = _make_ctx()
        text = "2026/04/10 周四"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert "周五" in patched

    def test_weekday_before_date(self):
        ctx = _make_ctx()
        text = "周四（2026-04-10）发货"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert "周五（2026-04-10）" in patched

    def test_xingqi_prefix_preserved(self):
        """星期X 保留前缀，不替换成周X。"""
        ctx = _make_ctx()
        text = "4月6日是星期二"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        # 4-6 是周一，应保留"星期"前缀
        assert patched == "4月6日是星期一"

    def test_libai_prefix_preserved(self):
        """礼拜X 保留前缀。"""
        ctx = _make_ctx()
        text = "4月10日（礼拜六）"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert patched == "4月10日（礼拜五）"

    def test_year_defaulted_from_ctx(self):
        """不含年份的日期用 ctx.now.year 补全。"""
        ctx = _make_ctx()
        text = "4月3日 周四"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert devs[0].parsed_date.year == 2026
        assert "周五" in patched

    def test_multiple_deviations_all_patched(self):
        """多处偏离全部修复。"""
        ctx = _make_ctx()
        text = "4月3日（周四）和 4月7日（周三）都有订单"
        patched, devs = validate_and_patch(text, ctx=ctx)
        # 4-3 周五, 4-7 周二
        assert len(devs) == 2
        assert "周四" not in patched
        assert "周三" not in patched
        assert "4月3日（周五）" in patched
        assert "4月7日（周二）" in patched

    def test_iso_datetime_with_T_separator(self):
        """ISO 8601 格式（T 分隔符）不被 connector 的禁数字规则阻断。"""
        ctx = _make_ctx()
        text = "2026-04-10T13:05:00 周四"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert patched == "2026-04-10T13:05:00 周五"

    def test_iso_datetime_with_space_separator(self):
        """空格分隔的日期时间。"""
        ctx = _make_ctx()
        text = "2026-04-10 13:05:00 周四"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert patched == "2026-04-10 13:05:00 周五"


# ────────────────────────────────────────────────────────────────────
# 不应该修复的场景
# ────────────────────────────────────────────────────────────────────


class TestShouldNotPatch:
    """正确/应该跳过的场景。"""

    def test_correct_weekday_unchanged(self):
        ctx = _make_ctx()
        text = "2026-04-10 周五 订单 1769 笔"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0
        assert patched == text

    def test_zhou_tian_equals_zhou_ri(self):
        """周天 和 周日 语义相同，4-5 是周日应该不改。"""
        ctx = _make_ctx()
        text = "4月5日（周天）"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0
        assert patched == text

    def test_code_block_skipped(self):
        """markdown 代码块内的日期不校验。"""
        ctx = _make_ctx()
        text = "```\n2026-04-10 周四\n```"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0
        assert patched == text

    def test_example_context_skipped(self):
        """'例如' / '假设' 等上下文跳过。"""
        ctx = _make_ctx()
        for marker in ["例如", "比如", "假设", "举例"]:
            text = f"{marker} 2026-04-10 周四 只是示意"
            patched, devs = validate_and_patch(text, ctx=ctx)
            assert len(devs) == 0, f"marker={marker} 应该跳过但没跳过"
            assert patched == text

    def test_cross_line_not_matched(self):
        """日期和星期跨行不匹配。"""
        ctx = _make_ctx()
        text = "4月3日\n周四"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0

    def test_cross_punctuation_not_matched(self):
        """日期和星期跨句号/问号不匹配。"""
        ctx = _make_ctx()
        text = "4月3日。周四的订单"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0

    def test_invalid_date_ignored(self):
        """无效日期（2月30日）忽略。"""
        ctx = _make_ctx()
        text = "2026-02-30 周五"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0

    def test_standalone_weekday_ignored(self):
        """单独的星期（无附近日期）不处理。"""
        ctx = _make_ctx()
        text = "今天周五发货，明天周六休息"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 0

    def test_empty_text(self):
        ctx = _make_ctx()
        patched, devs = validate_and_patch("", ctx=ctx)
        assert patched == ""
        assert devs == []

    def test_qi_character_not_misparsed(self):
        """防御：'期X' 不应被误认成星期（来自'下一期'等词）。"""
        ctx = _make_ctx()
        text = "4月10日是下一期二类产品的截止"
        patched, devs = validate_and_patch(text, ctx=ctx)
        # "期二" 不是合法的星期表达，不应触发
        assert len(devs) == 0


# ────────────────────────────────────────────────────────────────────
# 边界 / 鲁棒性
# ────────────────────────────────────────────────────────────────────


class TestRobustness:
    def test_default_year_from_ctx(self):
        """ctx 提供 now.year 作为日期默认年份。"""
        now_2027 = datetime(2027, 6, 15, tzinfo=CN)
        ctx = _make_ctx(now=now_2027)
        text = "6月12日 周一"  # 2027-06-12 是周六
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        assert devs[0].parsed_date.year == 2027

    def test_default_year_fallback_when_no_ctx(self):
        """没 ctx 时用 datetime.now() 的年份。"""
        # 不传 ctx，只要不崩就行
        text = "今年不做校验"
        patched, devs = validate_and_patch(text)
        assert patched == text

    def test_dev_snippet_recorded(self):
        ctx = _make_ctx()
        text = "4月3日（上周四）截止同一时间"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert len(devs) == 1
        # snippet 应包含原始片段
        assert "4月3日" in devs[0].snippet
        assert "周四" in devs[0].snippet

    def test_patch_preserves_surrounding_text(self):
        """patch 不影响周围文字。"""
        ctx = _make_ctx()
        text = "前文无关内容。4月10日 周四 后文也无关。"
        patched, devs = validate_and_patch(text, ctx=ctx)
        assert "前文无关内容。" in patched
        assert "后文也无关。" in patched
        assert "周五" in patched
