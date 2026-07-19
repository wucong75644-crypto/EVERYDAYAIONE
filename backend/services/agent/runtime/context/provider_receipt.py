"""真实 Provider 请求前的 ContextReceipt 采集。"""

from __future__ import annotations

from typing import Any


def record_provider_context_receipt(
    runtime_state: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> None:
    """生成并登记本次请求回执；观测失败沿用既有 best-effort 语义。"""
    from services.handlers.chat.stream_setup import _record_context_receipt

    receipt = _record_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id=runtime_state.conversation_id or "",
        task_id=runtime_state.task_id,
        model_id=runtime_state.model_id,
        model_step=len(runtime_state.context_receipts),
        base_revision=runtime_state.base_revision or 0,
    )
    if receipt is not None:
        runtime_state.context_receipts.append(receipt)
