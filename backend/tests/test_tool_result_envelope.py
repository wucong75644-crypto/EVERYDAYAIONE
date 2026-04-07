"""
tool_result_envelope 单元测试

覆盖：wrap / wrap_for_erp_agent / wrap_erp_agent_result
      _truncate_erp / _truncate_code / _truncate_search
      双重截断防护 / 边界值 / 并发隔离
"""

import asyncio

import pytest

from services.agent.tool_result_envelope import (
    wrap,
    wrap_for_erp_agent,
    wrap_erp_agent_result,
    MAIN_AGENT_BUDGET,
    ERP_AGENT_BUDGET,
    ERP_AGENT_RESULT_BUDGET,
)


# ============================================================
# wrap() 基础行为
# ============================================================

class TestWrapBasic:

    def test_none_returns_none(self):
        assert wrap("tool", None) is None

    def test_empty_returns_empty(self):
        assert wrap("tool", "") == ""

    def test_short_result_unchanged(self):
        result = "库存100件"
        assert wrap("local_stock_query", result) == result

    def test_exact_budget_unchanged(self):
        result = "x" * MAIN_AGENT_BUDGET
        assert wrap("some_tool", result) == result

    def test_over_budget_truncated_with_signal(self):
        result = "x" * (MAIN_AGENT_BUDGET + 500)
        wrapped = wrap("some_tool", result)
        assert len(wrapped) < len(result)
        assert "⚠ 输出已截断" in wrapped
        assert str(MAIN_AGENT_BUDGET + 500) in wrapped

    def test_no_truncate_tools_pass_through(self):
        result = "x" * 50000
        assert wrap("generate_image", result) == result
        assert wrap("generate_video", result) == result

    def test_double_wrap_skipped(self):
        """已截断的结果不再二次截断"""
        first = wrap("some_tool", "x" * 5000)
        assert "⚠ 输出已截断" in first
        second = wrap("erp_agent", first)
        # 不应有嵌套的截断信号
        assert second.count("⚠ 输出已截断") == 1
        assert second == first


# ============================================================
# 三种预算
# ============================================================

class TestBudgetLevels:

    def test_main_agent_budget(self):
        result = "x" * (MAIN_AGENT_BUDGET + 100)
        wrapped = wrap("some_tool", result)
        assert "⚠ 输出已截断" in wrapped

    def test_erp_agent_internal_budget(self):
        result = "x" * (ERP_AGENT_BUDGET + 100)
        wrapped = wrap_for_erp_agent("local_stock_query", result)
        assert "⚠ 输出已截断" in wrapped

    def test_erp_agent_result_budget(self):
        result = "x" * (ERP_AGENT_RESULT_BUDGET + 100)
        wrapped = wrap_erp_agent_result(result)
        assert "⚠ 输出已截断" in wrapped

    def test_erp_agent_budget_larger_than_main(self):
        assert ERP_AGENT_BUDGET > MAIN_AGENT_BUDGET

    def test_erp_agent_result_budget_largest(self):
        assert ERP_AGENT_RESULT_BUDGET >= ERP_AGENT_BUDGET


# ============================================================
# _truncate_erp — ERP 结果截断
# ============================================================

class TestTruncateErp:

    def test_preserves_first_line(self):
        lines = ["订单查询结果(共50单)"] + [f"行{i}" for i in range(200)]
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("local_order_query", result)
        assert wrapped.startswith("订单查询结果(共50单)")

    def test_preserves_summary_lines(self):
        lines = (
            ["库存汇总"] +
            [f"商品{i}: 100件" for i in range(200)] +
            ["合计：20000件", "统计：200个SKU"]
        )
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("local_stock_query", result)
        assert "合计：20000件" in wrapped
        assert "统计：200个SKU" in wrapped

    def test_separator_lines_excluded(self):
        lines = ["标题", "---", "数据行1", "---", "数据行2"]
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("erp_trade_query", result)
        # 短结果不截断，但分隔线不影响
        assert "标题" in wrapped

    def test_signal_appended_at_end(self):
        """截断信号在末尾，不在开头（保护正则匹配）"""
        lines = ["商品编码：ABC123"] + ["x" * 100 for _ in range(100)]
        result = "\n".join(lines)
        wrapped = wrap_for_erp_agent("local_product_identify", result)
        assert wrapped.startswith("商品编码：ABC123")
        assert "get_persisted_result" in wrapped
        assert "⚠ 输出已截断" in wrapped


# ============================================================
# _truncate_code — 代码执行结果截断
# ============================================================

class TestTruncateCode:

    def test_error_preserves_start(self):
        """错误信息优先保留开头"""
        result = "❌ SyntaxError: invalid syntax\n" + "x" * 5000
        wrapped = wrap("code_execute", result)
        assert wrapped.startswith("❌ SyntaxError")

    def test_traceback_preserves_start(self):
        result = "Traceback (most recent call last):\n" + "x" * 5000
        wrapped = wrap("code_execute", result)
        assert wrapped.startswith("Traceback")

    def test_normal_output_preserves_tail(self):
        """正常输出保留最后几行"""
        lines = [f"line {i}: processing..." for i in range(100)]
        lines.append("FINAL RESULT: 42")
        result = "\n".join(lines)
        wrapped = wrap("code_execute", result)
        assert "FINAL RESULT: 42" in wrapped

    def test_short_code_result_unchanged(self):
        result = "输出: 42\n完成"
        assert wrap("code_execute", result) == result

    def test_few_lines_but_long_chars_returns_full(self):
        """行数少(<=15)但总字符超预算时，应返回全文而非截断"""
        # 10 行，每行 300 字 → 总计 3000+ 字符，超 MAIN_AGENT_BUDGET=2000
        lines = [f"line{i}: " + "x" * 290 for i in range(10)]
        result = "\n".join(lines)
        assert len(result) > MAIN_AGENT_BUDGET
        wrapped = wrap("code_execute", result)
        # _truncate_code 返回全文，但 _smart_truncate 仍会追加截断信号
        assert "⚠ 输出已截断" in wrapped
        # 关键：不应丢失中间行
        assert "line5" in wrapped


# ============================================================
# _truncate_erp — reserve 边界保护
# ============================================================

class TestTruncateErpReserveBoundary:

    def test_huge_summary_lines_clamped(self):
        """汇总行极长时，reserve 被 clamp 到 budget//2，仍有数据行空间"""
        # 构造：首行短 + 大量数据行 + 一个超长汇总行 → 总长远超 ERP_AGENT_BUDGET(3000)
        huge_summary = "合计：" + "统计数据" * 500  # ~2000 字符
        lines = ["查询结果"] + [f"数据行{i}: 详细数据内容" * 5 for i in range(100)] + [huge_summary]
        result = "\n".join(lines)
        assert len(result) > ERP_AGENT_BUDGET  # 确保超预算
        wrapped = wrap_for_erp_agent("local_stock_query", result)
        # 应该包含首行
        assert "查询结果" in wrapped
        # reserve 被 clamp 到 budget//2，仍有空间放数据行
        assert "数据行0" in wrapped
        assert "⚠ 输出已截断" in wrapped


# ============================================================
# persist_and_get_key + get_persisted 基础链路
# ============================================================

class TestPersistedBasicFlow:

    def setup_method(self):
        from services.agent.tool_result_envelope import clear_persisted
        clear_persisted()

    def test_persist_and_retrieve(self):
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted,
        )
        key = persist_and_get_key("local_stock_query", "完整库存数据" * 100)
        assert key.startswith("local_stock_query_")
        assert get_persisted(key) == "完整库存数据" * 100

    def test_get_nonexistent_returns_none(self):
        from services.agent.tool_result_envelope import get_persisted
        assert get_persisted("nonexistent_key") is None

    def test_clear_removes_all(self):
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted, clear_persisted,
        )
        key = persist_and_get_key("tool", "data")
        clear_persisted()
        assert get_persisted(key) is None

    def test_truncation_auto_persists(self):
        """wrap() 截断时自动暂存完整结果"""
        from services.agent.tool_result_envelope import get_persisted
        long_result = "标题行\n" + "数据" * 2000
        wrapped = wrap("local_stock_query", long_result)
        # 截断信号中应有 key
        assert "key=" in wrapped
        # 提取 key 并验证可读取
        import re
        match = re.search(r'key=(\S+?)\)', wrapped)
        assert match
        key = match.group(1).rstrip(',')
        assert get_persisted(key) == long_result


# ============================================================
# _truncate_search — 搜索结果截断
# ============================================================

class TestTruncateSearch:

    def test_preserves_first_items(self):
        items = [f"- 搜索结果{i}: 详细描述" + "x" * 200 for i in range(20)]
        result = "\n".join(items)
        wrapped = wrap("web_search", result)
        assert "搜索结果0" in wrapped
        assert "⚠ 输出已截断" in wrapped

    def test_social_crawler_uses_search_strategy(self):
        items = [f"• 帖子{i}: 内容" + "x" * 200 for i in range(20)]
        result = "\n".join(items)
        wrapped = wrap("social_crawler", result)
        assert "帖子0" in wrapped

    def test_short_search_unchanged(self):
        result = "- 结果1: xxx\n- 结果2: yyy"
        assert wrap("web_search", result) == result

    def test_erp_api_search_uses_search_strategy(self):
        items = [f"- {i}. erp_tool_{i}: 描述" for i in range(50)]
        result = "\n".join(items)
        wrapped = wrap("erp_api_search", result)
        assert "erp_tool_0" in wrapped


# ============================================================
# 并发隔离（contextvars）
# ============================================================

class TestPersistedConcurrentIsolation:
    """验证 ContextVar 在多 task 场景下的隔离性"""

    def setup_method(self):
        """每个测试前清理 ContextVar 状态（前面的截断测试会写入暂存）"""
        from services.agent.tool_result_envelope import clear_persisted
        clear_persisted()

    @pytest.mark.asyncio
    async def test_concurrent_tasks_isolated(self):
        """两个并发 task 的 persisted_results 互不干扰"""
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted, clear_persisted,
        )

        results_a = []
        results_b = []

        async def task_a():
            key = persist_and_get_key("tool_a", "data_from_request_A")
            await asyncio.sleep(0.01)  # 让 task_b 有机会执行
            # task_b 的 clear 不应影响 task_a
            val = get_persisted(key)
            results_a.append(val)
            clear_persisted()

        async def task_b():
            key = persist_and_get_key("tool_b", "data_from_request_B")
            clear_persisted()  # task_b 先清理完毕
            results_b.append(get_persisted(key))

        await asyncio.gather(
            asyncio.create_task(task_a()),
            asyncio.create_task(task_b()),
        )

        # task_a 应该看到自己的数据（不被 task_b 的 clear 影响）
        assert results_a[0] == "data_from_request_A"
        # task_b clear 后自己看不到了
        assert results_b[0] is None

    @pytest.mark.asyncio
    async def test_clear_only_affects_own_context(self):
        """clear_persisted 只清自己的 context，不影响其他"""
        from services.agent.tool_result_envelope import (
            persist_and_get_key, get_persisted, clear_persisted,
        )

        seen_by_writer = []

        async def writer():
            key = persist_and_get_key("tool", "important_data")
            await asyncio.sleep(0.05)  # 等 cleaner 执行完
            # 自己的数据应该还在（cleaner 清的是自己的 context）
            seen_by_writer.append(get_persisted(key))
            clear_persisted()

        async def cleaner():
            await asyncio.sleep(0.01)
            clear_persisted()  # 清自己的（空的），不影响 writer

        await asyncio.gather(
            asyncio.create_task(writer()),
            asyncio.create_task(cleaner()),
        )

        assert seen_by_writer[0] == "important_data"
