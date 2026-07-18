"""数据产物的最小确定性校验策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.agent.runtime.artifact_ledger import (
    ArtifactEvidence,
    ArtifactKind,
    ArtifactSnapshot,
    ArtifactStatus,
)
from services.agent.runtime.runtime_contract import RunContract


@dataclass(frozen=True)
class PolicyResult:
    accepted: bool
    reason: str = ""


class DataAccuracyPolicy:
    policy_id = "data_accuracy"

    def validate_artifact(
        self,
        contract: RunContract,
        evidence: ArtifactEvidence,
        payload: Mapping[str, object],
    ) -> PolicyResult:
        del contract
        if evidence.kind != ArtifactKind.DATA_RESULT:
            return PolicyResult(True)
        if evidence.status != ArtifactStatus.READY:
            return PolicyResult(False, f"data artifact is {evidence.status.value}")
        rows = payload.get("data")
        file_ref = payload.get("file_ref")
        if rows is None and file_ref is None:
            return PolicyResult(False, "data artifact has no rows or file reference")
        if rows is not None and not isinstance(rows, list):
            return PolicyResult(False, "data rows must be a list")
        if isinstance(rows, list) and any(not isinstance(row, dict) for row in rows):
            return PolicyResult(False, "each data row must be an object")
        columns = payload.get("columns")
        if columns is not None and not isinstance(columns, list):
            return PolicyResult(False, "columns must be a list")
        return PolicyResult(True)

    def evaluate_completion(
        self,
        contract: RunContract,
        snapshot: ArtifactSnapshot,
    ) -> PolicyResult:
        if not contract.enabled:
            return PolicyResult(True)
        missing = contract.required_artifacts - snapshot.ready_kinds
        if missing:
            names = ",".join(sorted(kind.value for kind in missing))
            return PolicyResult(False, f"missing required artifacts: {names}")
        return PolicyResult(True)


def validate_data_evidence(
    evidence: ArtifactEvidence,
) -> PolicyResult:
    payload: Mapping[str, Any] = evidence.payload or {}
    return DataAccuracyPolicy().validate_artifact(
        RunContract.empty(),
        evidence,
        payload,
    )
