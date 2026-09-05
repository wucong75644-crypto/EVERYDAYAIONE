"""ModelGateway 的轻量采样事件。

事件只写入现有日志与 Langfuse，不创建新的持久化存储，也不承载 prompt、
API key 或响应内容。Gateway 使用该模块记录一次 Provider stream 的生命周期。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol

from loguru import logger


class SamplingEventType(str, Enum):
    """一次模型请求允许发布的生命周期事件。"""

    STARTED = "started"
    FIRST_CHUNK = "first_chunk"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_STARTED = "retry_started"


_TERMINAL_EVENTS = frozenset({
    SamplingEventType.COMPLETED,
    SamplingEventType.FAILED,
    SamplingEventType.CANCELLED,
})


@dataclass(frozen=True)
class ModelSamplingEvent:
    """不含输入或输出正文的模型调用采样记录。"""

    event: SamplingEventType
    request_id: str
    attempt_id: str | None
    model_id: str
    provider: str | None
    task_id: str | None = None
    trace_id: str | None = None
    request_index: int = 0
    turn_index: int | None = None
    usage: Mapping[str, int | float] = field(default_factory=dict)
    error_type: str | None = None
    previous_attempt_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.event in _TERMINAL_EVENTS

    def log_fields(self) -> dict[str, Any]:
        """返回可安全写入结构化日志和 Langfuse metadata 的字段。"""
        fields: dict[str, Any] = {
            "event": self.event.value,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "model_id": self.model_id,
            "provider": self.provider,
            "request_index": self.request_index,
            "turn_index": self.turn_index,
        }
        if self.usage:
            fields["usage"] = dict(self.usage)
        if self.error_type:
            fields["error_type"] = self.error_type
        if self.previous_attempt_id:
            fields["previous_attempt_id"] = self.previous_attempt_id
        return fields


class SamplingEventPublisher(Protocol):
    """允许测试或其他现有观测后端接收同一份事件。"""

    def publish(self, event: ModelSamplingEvent) -> None:
        ...


class ObservabilitySamplingEventPublisher:
    """复用 loguru 与 Langfuse 的默认事件发布器。"""

    def publish(self, event: ModelSamplingEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环时不存在模型流可被阻塞（例如同步 factory
            # 失败）；保留既有日志/追踪的 best-effort 语义。
            _publish_sampling_event(event)
            return
        # 日志文件和 Langfuse 都可能阻塞 I/O；让 Gateway 路径只创建后台任务，
        # 首 token 与后续 chunk 永不等待采样工作完成。
        loop.create_task(self._publish_in_background(event))

    @staticmethod
    async def _publish_in_background(event: ModelSamplingEvent) -> None:
        try:
            await asyncio.to_thread(_publish_sampling_event, event)
        except Exception:
            # 不记录错误正文，避免异常消息意外带入敏感提示词。
            await asyncio.to_thread(_log_sampling_publish_failure, event)


def _publish_sampling_event(event: ModelSamplingEvent) -> None:
    """在后台线程写已有日志；终态再补充 Langfuse generation。"""
    logger.bind(**event.log_fields()).info("ModelGateway sampling event")
    if event.is_terminal:
        _record_terminal_generation(event)


def _log_sampling_publish_failure(event: ModelSamplingEvent) -> None:
    logger.debug(
        "ModelGateway Langfuse sampling publish failed | "
        "event={} | request_id={} | attempt_id={}",
        event.event.value,
        event.request_id,
        event.attempt_id,
    )


def _record_terminal_generation(event: ModelSamplingEvent) -> None:
    """将终态补充为现有 trace 下的一条 Langfuse generation。"""
    from services.agent.observability.langfuse_integration import (
        create_model_gateway_generation,
    )

    generation = create_model_gateway_generation(
        trace_id=event.trace_id,
        model=event.model_id,
        metadata=event.log_fields(),
    )
    try:
        generation.end(usage=dict(event.usage))
    except Exception:
        # Langfuse 降级路径不影响调用方，也不展开异常内容。
        pass
