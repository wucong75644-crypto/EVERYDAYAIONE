"""单次 Run 的合同、证据和策略状态。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from services.agent.runtime.artifact_ledger import ArtifactLedger
from services.agent.runtime.completion_gate import (
    CompletionDecision,
    CompletionResult,
    evaluate_completion,
)
from services.agent.runtime.runtime_contract import RunContract


@dataclass
class RuntimeState:
    """运行时唯一可变容器；观察模式不影响现有循环控制。"""

    contract: RunContract = field(default_factory=RunContract.empty)
    ledger: ArtifactLedger = field(default_factory=ArtifactLedger)
    observation_only: bool = True
    final_synthesis_pending: bool = False
    last_completion: CompletionResult | None = None
    user_text: str = ""
    requires_validation: bool = False
    verified_final_pending: bool = False
    validation_plan: dict[str, Any] | None = None
    validation_error: str | None = None

    @classmethod
    def observing(cls) -> "RuntimeState":
        return cls()

    def evaluate(self, *, budget_exhausted: bool = False) -> CompletionResult:
        result = evaluate_completion(
            self.contract,
            self.ledger.snapshot(),
            budget_exhausted=budget_exhausted,
        )
        self.last_completion = result
        return result

    def request_final_synthesis(self) -> None:
        if self.contract.enabled:
            self.final_synthesis_pending = True

    def should_continue_after_plain_text(self) -> bool:
        if self.requires_validation and not self.verified_final_pending:
            return True
        result = self.evaluate()
        return (
            self.contract.enabled
            and not self.final_synthesis_pending
            and result.decision == CompletionDecision.CONTINUE
        )

    def final_tools(self, tools: list[dict]) -> list[dict]:
        return (
            []
            if self.final_synthesis_pending or self.verified_final_pending
            else tools
        )

    def request_verified_final(self) -> None:
        self.verified_final_pending = True

    @property
    def validation_blocked(self) -> bool:
        return (
            self.requires_validation
            and not self.verified_final_pending
            and self.validation_error is not None
        )

    @property
    def should_buffer_output(self) -> bool:
        return self.requires_validation or self.verified_final_pending

    def persistence_projection(self) -> list[dict]:
        """投影可跨 Turn 复用的 ready 数据证据。"""
        from services.agent.runtime.artifact_ledger import (
            ArtifactKind,
            ArtifactStatus,
        )

        projected: list[dict] = []
        for evidence in self.ledger.snapshot().evidence:
            if (
                evidence.kind != ArtifactKind.DATA_RESULT
                or evidence.status != ArtifactStatus.READY
            ):
                continue
            payload = dict(evidence.payload or {})
            metadata = payload.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if metadata.get("persisted") is True:
                continue
            rows = payload.get("data")
            if isinstance(rows, list) and len(rows) > 200:
                rows = None
            item = {
                "artifact_id": evidence.fingerprint,
                "source": str(payload.get("source") or ""),
                "columns": payload.get("columns") or [],
                "rows": rows,
                "file_ref": payload.get("file_ref"),
                "query_scope": metadata.get("query_scope") or metadata,
                "metric_definitions": metadata.get("metric_definitions") or {},
                "lineage": {
                    "tool_call_id": evidence.tool_call_id,
                    "derived_from": metadata.get("derived_from") or [],
                    "operation": metadata.get("operation") or {},
                },
                "validation_status": "ready",
            }
            encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) <= 1_048_576:
                projected.append(item)
        return projected[:20]

    def restore(self, evidence_items: tuple) -> None:
        for evidence in evidence_items:
            self.ledger.record(evidence)
