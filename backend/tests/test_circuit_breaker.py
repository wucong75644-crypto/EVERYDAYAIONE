"""Provider 级别熔断器单元测试"""

import time

import pytest

from services.adapters.types import ModelProvider
from services.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_breaker,
    is_provider_available,
    get_available_providers,
    reset_all,
)


# ============================================================
# CircuitBreaker 状态转换测试
# ============================================================


class TestCircuitBreakerStates:
    """状态机转换"""

    def setup_method(self):
        reset_all()

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        assert cb.state == CircuitState.CLOSED
        assert cb.is_available() is True

    def test_single_failure_stays_closed(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_threshold_failures_opens(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_available() is False

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # 重置后只有 1 次失败，应仍为 CLOSED
        assert cb.state == CircuitState.CLOSED

    def test_open_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # 模拟时间流逝
        cb._opened_at = time.monotonic() - cb._open_duration - 1
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.is_available() is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        for _ in range(3):
            cb.record_failure()
        cb._opened_at = time.monotonic() - cb._open_duration - 1
        _ = cb.state  # 触发 HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        for _ in range(3):
            cb.record_failure()
        cb._opened_at = time.monotonic() - cb._open_duration - 1
        _ = cb.state  # 触发 HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_failures_outside_window_not_counted(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        cb.record_failure()
        cb.record_failure()
        # 将旧失败时间戳置为窗口外
        cb._failure_timestamps = [
            time.monotonic() - cb._failure_window - 10
            for _ in cb._failure_timestamps
        ]
        cb.record_failure()
        # 窗口内仅 1 次失败
        assert cb.state == CircuitState.CLOSED

    def test_open_state_ignores_additional_failures(self):
        cb = CircuitBreaker(ModelProvider.DASHSCOPE)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # 额外失败不影响状态
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


# ============================================================
# 全局注册表测试
# ============================================================


class TestGlobalRegistry:

    def setup_method(self):
        reset_all()

    def test_same_provider_returns_same_instance(self):
        b1 = get_breaker(ModelProvider.KIE)
        b2 = get_breaker(ModelProvider.KIE)
        assert b1 is b2

    def test_different_providers_different_instances(self):
        b1 = get_breaker(ModelProvider.KIE)
        b2 = get_breaker(ModelProvider.DASHSCOPE)
        assert b1 is not b2

    def test_is_provider_available_default_true(self):
        assert is_provider_available(ModelProvider.OPENROUTER) is True

    def test_get_available_providers_all_healthy(self):
        providers = get_available_providers()
        # 至少包含 4 个活跃 Provider
        assert ModelProvider.KIE in providers
        assert ModelProvider.DASHSCOPE in providers

    def test_get_available_providers_one_broken(self):
        cb = get_breaker(ModelProvider.DASHSCOPE)
        for _ in range(3):
            cb.record_failure()
        providers = get_available_providers()
        assert ModelProvider.DASHSCOPE not in providers
        assert ModelProvider.KIE in providers

    def test_reset_all_clears_state(self):
        cb = get_breaker(ModelProvider.KIE)
        for _ in range(3):
            cb.record_failure()
        assert not is_provider_available(ModelProvider.KIE)

        reset_all()
        assert is_provider_available(ModelProvider.KIE)
