"""
Agent 可观测性模块。

提供全链路 trace_id 传播，后续接入 Langfuse。

使用方式：
    from services.agent.observability import set_trace_id, get_trace_id

    # 请求入口
    set_trace_id(task_id)
    logger.bind(trace_id=get_trace_id())

    # 任何层级读取
    trace_id = get_trace_id()
"""

from contextvars import ContextVar

_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(trace_id: str) -> None:
    """在请求入口设置 trace_id（chat_handler / scheduled_task_agent）。"""
    _trace_id_ctx.set(trace_id or "")


def get_trace_id() -> str:
    """读取当前上下文的 trace_id（任意层级可调用）。"""
    return _trace_id_ctx.get()
