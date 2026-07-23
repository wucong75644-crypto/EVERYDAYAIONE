"""真实 Provider 请求前的 ContextReceipt 采集。"""

from __future__ import annotations

from typing import Any

from services.agent.runtime.context.provider_plan import ProviderContextPlan

_PROVIDER_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_creation_tokens",
)


def prepare_provider_context_plan(
    runtime_state: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> ProviderContextPlan:
    """构建唯一 ProviderContextPlan；任何失败均在发送请求前终止。"""
    from services.handlers.chat.stream_setup import _record_context_receipt

    receipt = _record_context_receipt(
        messages=messages,
        tools=tools,
        conversation_id=runtime_state.conversation_id or "",
        task_id=runtime_state.task_id,
        model_id=runtime_state.model_id,
        model_step=len(runtime_state.context_receipts),
        base_revision=runtime_state.base_revision or 0,
        stable_prefix_blocks=runtime_state.stable_prefix_blocks,
    )
    if receipt is None:
        raise RuntimeError("CONTEXT_RECEIPT_BUILD_FAILED")
    from services.agent.runtime.context.telemetry import record_context_event

    plan = ProviderContextPlan.build(
        messages=messages,
        tools=tools,
        context_epoch_id=receipt["context_epoch_id"],
        model_step=receipt["model_step"],
        stable_prefix_blocks=receipt["stable_prefix_blocks"],
    )
    if not plan.matches(messages, tools):
        raise RuntimeError("CONTEXT_PLAN_PROJECTION_MISMATCH")
    runtime_state.current_context_plan = plan
    receipt["context_plan_hash"] = plan.plan_hash
    receipt["context_plan_projection_match"] = True
    pruning_receipt = next(
        (
            item
            for item in reversed(runtime_state.pruning_receipts)
            if item.get("model_step") == receipt["model_step"]
        ),
        None,
    )
    if pruning_receipt is not None:
        receipt["pruning_receipt"] = pruning_receipt
    compaction_receipt = next(
        (
            item
            for item in reversed(runtime_state.compaction_receipts)
            if item.get("model_step") == receipt["model_step"]
        ),
        None,
    )
    if compaction_receipt is not None:
        receipt["compaction_receipt"] = compaction_receipt
    runtime_state.context_receipts.append(receipt)
    record_context_event(
        "context_plan_projection",
        conversation_id=runtime_state.conversation_id,
        task_id=runtime_state.task_id,
        model_id=runtime_state.model_id,
        model_step=receipt["model_step"],
        plan_hash=plan.plan_hash,
        projection_match=True,
        outcome="match",
    )
    return plan


def accumulate_provider_context_usage(
    runtime_state: Any,
    chunk: Any,
) -> None:
    """将 Provider chunk 用量累计到当前 ModelStep Receipt。"""
    if not runtime_state.context_receipts:
        return
    receipt = runtime_state.context_receipts[-1]
    usage = receipt.get("provider_usage")
    if not isinstance(usage, dict):
        return
    for field in _PROVIDER_TOKEN_FIELDS:
        value = getattr(chunk, field, 0) or 0
        usage[field] = usage.get(field, 0) + max(0, int(value))
    receipt["provider_tokens"] = usage["prompt_tokens"]
