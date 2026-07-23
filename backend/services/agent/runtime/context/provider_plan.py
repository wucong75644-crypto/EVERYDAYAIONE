"""完整 Provider 请求的不可变 ContextPlan 与唯一 payload 投影。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderContextPlan:
    """单个 ModelStep 将投影为 Provider payload 的完整计划。"""

    schema_version: int
    context_epoch_id: str
    model_step: int
    stable_prefix_blocks: int
    message_count: int
    tool_count: int
    plan_hash: str
    _messages_json: str = field(repr=False)
    _tools_json: str = field(repr=False)

    @classmethod
    def build(
        cls,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        context_epoch_id: str,
        model_step: int,
        stable_prefix_blocks: int,
    ) -> "ProviderContextPlan":
        """严格序列化当前有效请求；不可序列化内容直接拒绝发送。"""
        messages_json = _canonical_json(messages)
        tools_json = _canonical_json(tools)
        plan_hash = _hash_json(
            f'{{"messages":{messages_json},"tools":{tools_json}}}'
        )
        return cls(
            schema_version=1,
            context_epoch_id=context_epoch_id,
            model_step=model_step,
            stable_prefix_blocks=stable_prefix_blocks,
            message_count=len(messages),
            tool_count=len(tools),
            plan_hash=plan_hash,
            _messages_json=messages_json,
            _tools_json=tools_json,
        )

    def project(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """生成与 Plan 隔离的 Provider messages/tools 投影。"""
        return json.loads(self._messages_json), json.loads(self._tools_json)

    def matches(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> bool:
        """比较当前旧 payload 与 Plan 规范投影是否完全一致。"""
        projected_messages, projected_tools = self.project()
        return projected_messages == messages and projected_tools == tools


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_json(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
