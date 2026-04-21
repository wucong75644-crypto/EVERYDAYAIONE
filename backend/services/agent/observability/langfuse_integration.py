"""
Langfuse 可观测性集成（v6）。

功能：
- trace/span/generation 自动上报（异步，不阻塞主流程）
- 失败时静默降级（Langfuse 不可达不影响业务）
- 环境变量未配置时自动禁用

使用方式：
    from services.agent.observability.langfuse_integration import (
        get_langfuse, create_trace, create_span, create_generation,
    )

    trace = create_trace(name="chat_request", user_id=user_id)
    span = create_span(trace, name="erp_agent")
    gen = create_generation(span, name="llm_call", model="gemini-3-pro")
    gen.end(usage={"prompt_tokens": 100, "completion_tokens": 50})
    span.end()
    trace.update(output="done")

环境变量（.env）：
    LANGFUSE_PUBLIC_KEY=pk-lf-xxx
    LANGFUSE_SECRET_KEY=sk-lf-xxx
    LANGFUSE_HOST=https://cloud.langfuse.com
"""

from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

# Langfuse 客户端单例（懒初始化）
_langfuse_client: Any = None
_init_attempted = False


def get_langfuse() -> Any:
    """获取 Langfuse 客户端单例（懒初始化，未配置返回 None）。"""
    global _langfuse_client, _init_attempted
    if _init_attempted:
        return _langfuse_client

    _init_attempted = True
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        logger.debug("Langfuse disabled: LANGFUSE_PUBLIC_KEY/SECRET_KEY not set")
        return None

    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        logger.info("Langfuse initialized successfully")
    except Exception as e:
        logger.warning(f"Langfuse init failed (silent degrade): {e}")
        _langfuse_client = None

    return _langfuse_client


def create_trace(
    name: str,
    user_id: str = "",
    session_id: str = "",
    metadata: Optional[dict] = None,
) -> Any:
    """创建 Langfuse trace（顶级链路，对应一次用户请求）。"""
    from services.agent.observability import get_trace_id

    lf = get_langfuse()
    if lf is None:
        return _NullSpan()

    try:
        return lf.trace(
            id=get_trace_id() or None,
            name=name,
            user_id=user_id or None,
            session_id=session_id or None,
            metadata=metadata or {},
        )
    except Exception as e:
        logger.debug(f"Langfuse create_trace failed: {e}")
        return _NullSpan()


def create_span(parent: Any, name: str, metadata: Optional[dict] = None) -> Any:
    """创建 Langfuse span（对应一个 Agent 或工具执行）。"""
    if isinstance(parent, _NullSpan):
        return _NullSpan()
    try:
        return parent.span(name=name, metadata=metadata or {})
    except Exception as e:
        logger.debug(f"Langfuse create_span failed: {e}")
        return _NullSpan()


def create_generation(
    parent: Any,
    name: str,
    model: str = "",
    input_messages: Optional[list] = None,
) -> Any:
    """创建 Langfuse generation（对应一次 LLM 调用）。"""
    if isinstance(parent, _NullSpan):
        return _NullSpan()
    try:
        return parent.generation(
            name=name,
            model=model or None,
            input=input_messages,
        )
    except Exception as e:
        logger.debug(f"Langfuse create_generation failed: {e}")
        return _NullSpan()


class _NullSpan:
    """Langfuse 不可用时的空操作替身（Null Object 模式）。"""

    def span(self, **kwargs: Any) -> _NullSpan:
        return _NullSpan()

    def generation(self, **kwargs: Any) -> _NullSpan:
        return _NullSpan()

    def end(self, **kwargs: Any) -> None:
        pass

    def update(self, **kwargs: Any) -> None:
        pass
