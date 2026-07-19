"""单次Run内统一Validation门面；不执行工具且不写数据库。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.agent.runtime.validation.normalizer import normalize_tool_result
from services.agent.runtime.validation.recovery import decide_recovery
from services.agent.runtime.validation.tracker import ValidationTracker
from services.agent.runtime.validation.types import (
    RecoveryDecision,
    RecoveryPolicy,
    ToolEffect,
    ValidatedToolResult,
    ValidationReceipt,
)


@dataclass
class ValidationRuntime:
    task_id: str = ""
    policy: RecoveryPolicy = field(default_factory=RecoveryPolicy)
    tracker: ValidationTracker = field(default_factory=ValidationTracker)
    receipts: list[ValidationReceipt] = field(default_factory=list)

    def observe_result(
        self,
        result: Any,
        *,
        tool_call_id: str,
        tool_name: str,
        model_step: int,
        turns_remaining: int,
        audit_status: str = "",
        effect: ToolEffect = ToolEffect.READ_ONLY,
        duration_ms: int = 0,
    ) -> tuple[ValidatedToolResult, RecoveryDecision]:
        validated = normalize_tool_result(
            result,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            audit_status=audit_status,
            effect=effect,
        )
        attempt = self.tracker.observe(validated)
        decision = decide_recovery(
            validated,
            self.tracker,
            turns_remaining=turns_remaining,
            policy=self.policy,
        )
        self.receipts.append(
            ValidationReceipt(
                task_id=self.task_id,
                model_step=model_step,
                tool_call_id=tool_call_id,
                tool_name=validated.effective_tool_name,
                stage=validated.stage,
                result_class=validated.result_class,
                decision=decision,
                attempt=attempt,
                fingerprint=validated.fingerprint,
                reason_code=validated.error_code or "OK",
                duration_ms=max(duration_ms, 0),
            )
        )
        return validated, decision

    def receipt_projection(self) -> list[dict[str, Any]]:
        return [receipt.to_dict() for receipt in self.receipts]
