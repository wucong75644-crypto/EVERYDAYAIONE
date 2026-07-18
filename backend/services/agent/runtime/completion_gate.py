"""基于合同和结构化证据的确定性完成门。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.agent.runtime.artifact_ledger import (
    ArtifactKind,
    ArtifactSnapshot,
    ArtifactStatus,
)
from services.agent.runtime.runtime_contract import RunContract


class CompletionDecision(StrEnum):
    CONTINUE = "continue"
    FINALIZE = "finalize"
    FALLBACK = "fallback"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CompletionResult:
    decision: CompletionDecision
    reason: str = ""


def evaluate_completion(
    contract: RunContract,
    snapshot: ArtifactSnapshot,
    *,
    budget_exhausted: bool = False,
) -> CompletionResult:
    """空合同保持原停止语义；非空合同只接受 ready 的必需产物。"""
    if not contract.enabled:
        return CompletionResult(CompletionDecision.FINALIZE, "empty contract")
    ready = snapshot.ready_kinds
    missing = contract.required_artifacts - ready
    if not missing:
        return CompletionResult(
            CompletionDecision.FINALIZE,
            "required artifacts are ready",
        )
    if budget_exhausted:
        failed = _failed_required(contract, snapshot)
        decision = (
            CompletionDecision.FALLBACK
            if failed else CompletionDecision.BLOCKED
        )
        return CompletionResult(decision, _missing_reason(missing))
    return CompletionResult(CompletionDecision.CONTINUE, _missing_reason(missing))


def _failed_required(
    contract: RunContract,
    snapshot: ArtifactSnapshot,
) -> bool:
    return any(
        item.kind in contract.required_artifacts
        and item.status in {ArtifactStatus.FAILED, ArtifactStatus.DEGRADED}
        for item in snapshot.evidence
    )


def _missing_reason(missing: frozenset[ArtifactKind]) -> str:
    names = ",".join(sorted(kind.value for kind in missing))
    return f"missing required artifacts: {names}"
