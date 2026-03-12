"""
Provider 级别熔断器

当某个 Provider 连续失败达到阈值时自动熔断，避免继续向已知不可用的服务发送请求。
状态转换：CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN
"""

import time
from enum import Enum
from typing import Dict, List

from loguru import logger

from services.adapters.types import ModelProvider


# ============================================================
# 常量（config.py 中可覆盖，这里仅作导入时的兜底默认）
# ============================================================

_DEFAULT_FAILURE_THRESHOLD: int = 3
_DEFAULT_FAILURE_WINDOW: float = 60.0
_DEFAULT_OPEN_DURATION: float = 30.0


def _get_config():
    """懒加载配置，避免循环导入"""
    try:
        from core.config import get_settings
        s = get_settings()
        return (
            s.circuit_breaker_failure_threshold,
            s.circuit_breaker_failure_window,
            s.circuit_breaker_open_duration,
        )
    except Exception:
        return _DEFAULT_FAILURE_THRESHOLD, _DEFAULT_FAILURE_WINDOW, _DEFAULT_OPEN_DURATION


class CircuitState(str, Enum):
    """熔断器状态"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """单个 Provider 的熔断器"""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self._state = CircuitState.CLOSED
        self._failure_timestamps: list = []
        self._opened_at: float = 0.0
        # 读取配置
        threshold, window, duration = _get_config()
        self._failure_threshold = threshold
        self._failure_window = window
        self._open_duration = duration

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    @property
    def state(self) -> CircuitState:
        """获取当前状态（自动检测 OPEN→HALF_OPEN 转换）"""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._open_duration:
                self._transition(CircuitState.HALF_OPEN)
        return self._state

    def is_available(self) -> bool:
        """该 provider 是否可以接收请求"""
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """记录一次成功调用"""
        if self._state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)
        self._failure_timestamps.clear()

    def record_failure(self) -> None:
        """记录一次失败调用"""
        now = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN)
            return

        if self._state == CircuitState.OPEN:
            return

        # CLOSED：滑动窗口计数
        self._failure_timestamps.append(now)
        cutoff = now - self._failure_window
        self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]
        if len(self._failure_timestamps) >= self._failure_threshold:
            self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        """状态转换 + 日志"""
        old_state = self._state
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
        logger.warning(
            f"Circuit breaker | provider={self._provider.value} | "
            f"{old_state.value} → {new_state.value}"
        )


# ============================================================
# 全局注册表（进程内单例）
# ============================================================

_breakers: Dict[ModelProvider, CircuitBreaker] = {}


def get_breaker(provider: ModelProvider) -> CircuitBreaker:
    """获取指定 provider 的熔断器（懒初始化）"""
    if provider not in _breakers:
        _breakers[provider] = CircuitBreaker(provider)
    return _breakers[provider]


def is_provider_available(provider: ModelProvider) -> bool:
    """快捷方法：检查 provider 是否可用"""
    return get_breaker(provider).is_available()


def get_available_providers() -> List[ModelProvider]:
    """返回当前所有可用的 provider 列表"""
    return [p for p in ModelProvider if is_provider_available(p)]


def reset_all() -> None:
    """重置所有熔断器（仅测试使用）"""
    _breakers.clear()
