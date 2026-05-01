"""
ExecutionBudget 单元测试

覆盖：多维预算 (turns/tokens/wall_time) + fork + stop_reason + 向后兼容
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import time

import pytest

from services.agent.execution_budget import ExecutionBudget


class TestExecutionBudgetProperties:

    def test_initial_state(self):
        budget = ExecutionBudget(max_turns=15, max_tokens=100_000, max_wall_time=600.0)
        assert budget.elapsed < 1.0
        assert budget.remaining > 599.0
        assert budget.turns_used == 0
        assert budget.tokens_used == 0
        assert not budget.is_expired
        assert budget.stop_reason is None

    def test_remaining_decreases(self):
        budget = ExecutionBudget(max_wall_time=600.0)
        r1 = budget.remaining
        time.sleep(0.05)
        r2 = budget.remaining
        assert r2 < r1

    def test_remaining_never_negative(self):
        budget = ExecutionBudget(max_wall_time=0.001)
        time.sleep(0.01)
        assert budget.remaining == 0.0

    def test_is_expired_by_wall_time(self):
        budget = ExecutionBudget(max_wall_time=0.001)
        time.sleep(0.01)
        assert budget.is_expired
        assert budget.stop_reason == "wall_timeout"

    def test_is_expired_by_turns(self):
        budget = ExecutionBudget(max_turns=2)
        budget.use_turn()
        budget.use_turn()
        assert budget.is_expired
        assert budget.stop_reason == "max_turns"

    def test_is_expired_by_tokens(self):
        budget = ExecutionBudget(max_tokens=1000)
        budget.use_tokens(1000)
        assert budget.is_expired
        assert budget.stop_reason == "max_tokens"


class TestUseTurn:

    def test_use_turn_increments(self):
        budget = ExecutionBudget(max_turns=10)
        budget.use_turn()
        assert budget.turns_used == 1
        assert budget.turns_remaining == 9

    def test_turns_remaining_never_negative(self):
        budget = ExecutionBudget(max_turns=1)
        budget.use_turn()
        budget.use_turn()  # 超过上限
        assert budget.turns_remaining == 0


class TestUseTokens:

    def test_use_tokens_increments(self):
        budget = ExecutionBudget(max_tokens=100_000, reserved_for_response=4000)
        budget.use_tokens(5000)
        assert budget.tokens_used == 5000
        # v6: tokens_remaining = max_tokens - used - reserved = 100000 - 5000 - 4000
        assert budget.tokens_remaining == 91_000

    def test_per_tool_tokens(self):
        """v6: per-tool token 统计"""
        budget = ExecutionBudget()
        budget.use_tokens(1000, tool_name="erp_agent")
        budget.use_tokens(500, tool_name="code_execute")
        budget.use_tokens(200, tool_name="erp_agent")
        assert budget.get_tool_tokens() == {"erp_agent": 1200, "code_execute": 500}

    def test_inline_threshold_normal(self):
        """v6: 正常状态 inline_threshold = 200"""
        budget = ExecutionBudget(max_tokens=100_000, reserved_for_response=4000)
        assert budget.inline_threshold == 200
        assert not budget.is_tight

    def test_inline_threshold_tight(self):
        """v6: 紧张状态 inline_threshold = 50"""
        budget = ExecutionBudget(max_tokens=20_000, reserved_for_response=4000)
        budget.use_tokens(10_000)
        # remaining = 20000 - 10000 - 4000 = 6000 < 15000
        assert budget.is_tight
        assert budget.inline_threshold == 50

    def test_tokens_writeback_to_parent(self):
        parent = ExecutionBudget(max_tokens=100_000)
        child = parent.fork(max_turns=5)
        child.use_tokens(3000)
        assert child.tokens_used == 3000
        assert parent.tokens_used == 3000  # 回写


class TestToolTimeout:

    def test_returns_min_of_limit_and_remaining(self):
        budget = ExecutionBudget(max_wall_time=10.0)
        timeout = budget.tool_timeout(30.0)
        assert timeout <= 10.5
        assert timeout >= 9.0

    def test_returns_max_per_tool_when_plenty_remaining(self):
        budget = ExecutionBudget(max_wall_time=300.0)
        timeout = budget.tool_timeout(30.0)
        assert timeout == 30.0

    def test_minimum_1_second(self):
        budget = ExecutionBudget(max_wall_time=0.001)
        time.sleep(0.01)
        timeout = budget.tool_timeout(30.0)
        assert timeout == 1.0

    def test_default_max_per_tool(self):
        budget = ExecutionBudget(max_wall_time=300.0)
        timeout = budget.tool_timeout()
        assert timeout == 30.0


class TestStopReason:

    def test_none_when_budget_available(self):
        budget = ExecutionBudget(max_turns=15, max_tokens=100_000, max_wall_time=600.0)
        assert budget.stop_reason is None

    def test_max_turns_first(self):
        """轮次先耗尽时返回 max_turns"""
        budget = ExecutionBudget(max_turns=1, max_tokens=100_000, max_wall_time=600.0)
        budget.use_turn()
        assert budget.stop_reason == "max_turns"

    def test_max_tokens_first(self):
        """token 先耗尽时返回 max_tokens"""
        budget = ExecutionBudget(max_turns=15, max_tokens=100, max_wall_time=600.0)
        budget.use_tokens(100)
        assert budget.stop_reason == "max_tokens"

    def test_priority_turns_over_tokens(self):
        """同时耗尽时轮次优先"""
        budget = ExecutionBudget(max_turns=1, max_tokens=100)
        budget.use_turn()
        budget.use_tokens(100)
        assert budget.stop_reason == "max_turns"

    def test_wrap_up_budget_triggers_before_max_turns(self):
        """wrap_up_turns_reserved=1 → 在 max_turns-1 时触发 wrap_up_budget"""
        budget = ExecutionBudget(max_turns=5, wrap_up_turns_reserved=1)
        for _ in range(4):
            budget.use_turn()
        # 4 轮用完，距 max_turns=5 还差 1 轮 → 触发 wrap_up_budget
        assert budget.stop_reason == "wrap_up_budget"

    def test_max_turns_still_triggers_at_limit(self):
        """用满 max_turns → 返回 max_turns（优先于 wrap_up_budget）"""
        budget = ExecutionBudget(max_turns=5, wrap_up_turns_reserved=1)
        for _ in range(5):
            budget.use_turn()
        assert budget.stop_reason == "max_turns"

    def test_wrap_up_reserved_zero_disables(self):
        """wrap_up_turns_reserved=0 → 不触发 wrap_up_budget"""
        budget = ExecutionBudget(max_turns=5, wrap_up_turns_reserved=0)
        for _ in range(4):
            budget.use_turn()
        assert budget.stop_reason is None
        budget.use_turn()
        assert budget.stop_reason == "max_turns"

    def test_wrap_up_budget_not_triggered_early(self):
        """未达阈值 → stop_reason=None"""
        budget = ExecutionBudget(max_turns=10, wrap_up_turns_reserved=1)
        for _ in range(5):
            budget.use_turn()
        assert budget.stop_reason is None


class TestFork:

    def test_fork_creates_child(self):
        parent = ExecutionBudget(max_turns=15, max_tokens=100_000, max_wall_time=600.0)
        child = parent.fork(max_turns=10)
        assert child.turns_remaining <= 10
        assert child.tokens_remaining <= 100_000

    def test_fork_respects_parent_remaining_turns(self):
        parent = ExecutionBudget(max_turns=5)
        parent.use_turn()
        parent.use_turn()
        # 父剩余 3 轮，子请求 10 轮 → 实际只有 3 轮
        child = parent.fork(max_turns=10)
        assert child._max_turns == 3

    def test_fork_token_writeback(self):
        parent = ExecutionBudget(max_tokens=50_000)
        child = parent.fork(max_turns=5)
        child.use_tokens(10_000)
        assert parent.tokens_used == 10_000

    def test_fork_wall_time_shared(self):
        parent = ExecutionBudget(max_wall_time=10.0)
        child = parent.fork(max_turns=5)
        # 子的 wall_time 不会超过父的 remaining
        assert child._max_wall_time <= parent.remaining + 0.1

    def test_fork_inherits_wrap_up_reserved(self):
        """子 Agent 继承父的 wrap_up_turns_reserved"""
        parent = ExecutionBudget(max_turns=15, wrap_up_turns_reserved=2)
        child = parent.fork(max_turns=10)
        assert child._wrap_up_turns_reserved == 2


class TestCheckOrLog:

    def test_returns_true_when_budget_available(self):
        budget = ExecutionBudget(max_turns=15, max_tokens=100_000, max_wall_time=600.0)
        assert budget.check_or_log("test") is True

    def test_returns_false_when_turns_expired(self):
        budget = ExecutionBudget(max_turns=1)
        budget.use_turn()
        assert budget.check_or_log("test") is False

    def test_returns_false_when_wall_expired(self):
        budget = ExecutionBudget(max_wall_time=0.001)
        time.sleep(0.01)
        assert budget.check_or_log("test") is False
