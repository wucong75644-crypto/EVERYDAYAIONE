"""确定 Provider adapter 边界的请求投影与 hash。"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from services.agent.runtime.ports.model import (
    ModelRequestOptions,
    ModelStepRequest,
)


def resolve_model_revision(model_id: str) -> str:
    """从现有模型注册表生成稳定 revision，不建立第二套配置来源。"""
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id)
    if config is None:
        raise ValueError(f"unknown model_id: {model_id}")
    return _hash_value(_normalize(config))


def compute_request_hash(
    *,
    model_id: str,
    model_revision: str,
    prompt_revision: str,
    tool_catalog_revision: str,
    input_receipt_hash: str,
    context_plan_hash: str,
    options: ModelRequestOptions,
) -> str:
    """计算不包含正文和 secret 的规范 ModelStep 请求 hash。"""
    return _hash_value({
        "schema_version": 1,
        "model_id": model_id,
        "model_revision": model_revision,
        "prompt_revision": prompt_revision,
        "tool_catalog_revision": tool_catalog_revision,
        "input_receipt_hash": input_receipt_hash,
        "context_plan_hash": context_plan_hash,
        "options": _normalize(options),
    })


def validate_request_projection(request: ModelStepRequest) -> None:
    """在 Provider IO 前验证 revision、hash 与 ContextPlan 投影。"""
    messages, tools = request.context_plan.project()
    if not request.context_plan.matches(messages, tools):
        raise ValueError("CONTEXT_PLAN_PROJECTION_MISMATCH")
    actual_revision = resolve_model_revision(request.model_id)
    if not hmac.compare_digest(request.model_revision, actual_revision):
        raise ValueError("MODEL_REVISION_MISMATCH")
    expected_hash = compute_request_hash(
        model_id=request.model_id,
        model_revision=request.model_revision,
        prompt_revision=request.prompt_revision,
        tool_catalog_revision=request.tool_catalog_revision,
        input_receipt_hash=request.input_receipt.receipt_hash,
        context_plan_hash=request.context_plan.plan_hash,
        options=request.options,
    )
    if not hmac.compare_digest(request.request_hash, expected_hash):
        raise ValueError("MODEL_REQUEST_HASH_MISMATCH")


def provider_kwargs(options: ModelRequestOptions) -> dict[str, Any]:
    """只投影现有 BaseChatAdapter 支持的请求参数。"""
    result: dict[str, Any] = {}
    if options.temperature is not None:
        result["temperature"] = options.temperature
    if options.reasoning_effort is not None:
        result["reasoning_effort"] = options.reasoning_effort
    if options.thinking_mode is not None:
        result["thinking_mode"] = options.thinking_mode
    if options.structured_output:
        result["response_format"] = {"type": "json_object"}
    return result


def _hash_value(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value
