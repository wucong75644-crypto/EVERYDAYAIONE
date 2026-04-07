"""
ExecutionBudget 单元测试

覆盖：elapsed / remaining / is_expired / tool_timeout / check_or_log
"""

import time

import pytest

from services.agent.execution_budget import ExecutionBudget


class TestExecutionBudgetProperties:

    def test_initial_state(self):
        budget = ExecutionBudget(120.0)
        assert budget.elapsed < 1.0
        assert budget.remaining > 119.0
        assert not budget.is_expired

    def test_remaining_decreases(self):
        budget = ExecutionBudget(120.0)
        r1 = budget.remaining
        time.sleep(0.05)
        r2 = budget.remaining
        assert r2 < r1

    def test_remaining_never_negative(self):
        budget = ExecutionBudget(0.001)
        time.sleep(0.01)
        assert budget.remaining == 0.0

    def test_is_expired_after_deadline(self):
        budget = ExecutionBudget(0.001)
        time.sleep(0.01)
        assert budget.is_expired


class TestToolTimeout:

    def test_returns_min_of_limit_and_remaining(self):
        budget = ExecutionBudget(10.0)
        # remaining ~10s, max_per_tool=30 → should return ~10
        timeout = budget.tool_timeout(30.0)
        assert timeout <= 10.5
        assert timeout >= 9.0

    def test_returns_max_per_tool_when_plenty_remaining(self):
        budget = ExecutionBudget(300.0)
        timeout = budget.tool_timeout(30.0)
        assert timeout == 30.0

    def test_minimum_1_second(self):
        budget = ExecutionBudget(0.001)
        time.sleep(0.01)
        timeout = budget.tool_timeout(30.0)
        assert timeout == 1.0

    def test_default_max_per_tool(self):
        budget = ExecutionBudget(300.0)
        timeout = budget.tool_timeout()
        assert timeout == 30.0


class TestCheckOrLog:

    def test_returns_true_when_budget_available(self):
        budget = ExecutionBudget(120.0)
        assert budget.check_or_log("test") is True

    def test_returns_false_when_expired(self):
        budget = ExecutionBudget(0.001)
        time.sleep(0.01)
        assert budget.check_or_log("test") is False
