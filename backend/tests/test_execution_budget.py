"""
ExecutionBudget 单元测试

覆盖：多维预算 (turns/tokens/wall_time) + fork + stop_reason + 向后兼容
"""

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
        budget = ExecutionBudget(max_tokens=100_000)
        budget.use_tokens(5000)
        assert budget.tokens_used == 5000
        assert budget.tokens_remaining == 95_000

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
