"""模型最终草稿提交前的通用证据闸门。"""

from __future__ import annotations

from services.agent.runtime.artifact_ledger import ArtifactSnapshot
from services.agent.runtime.evidence_guard.claim_extractor import (
    extract_numeric_claims,
)
from services.agent.runtime.evidence_guard.evidence_values import (
    collect_evidence_values,
)
from services.agent.runtime.evidence_guard.models import (
    ClaimIssue,
    GuardDecision,
    GuardReceipt,
)


class EvidenceGuard:
    """只证明结构化证据能支持的声明，不参与意图识别或工具选择。"""

    def verify(
        self,
        draft: str,
        evidence: ArtifactSnapshot,
        *,
        question: str = "",
    ) -> GuardReceipt:
        values = collect_evidence_values(evidence)
        if values.evidence_count == 0:
            return GuardReceipt(
                decision=GuardDecision.SKIP,
                reason="no_structured_evidence",
            )
        claims = extract_numeric_claims(draft)
        if not claims:
            return GuardReceipt(
                decision=GuardDecision.PASS,
                evidence_count=values.evidence_count,
                reason="no_numeric_claims",
            )
        issues = tuple(
            ClaimIssue(claim=claim, reason="unsupported_by_evidence")
            for claim in claims
            if not values.supports(
                claim.value,
                claim.unit,
                f"{question}\n{claim.context}",
            )
        )
        return GuardReceipt(
            decision=GuardDecision.RETRY if issues else GuardDecision.PASS,
            claims=claims,
            issues=issues,
            evidence_count=values.evidence_count,
            reason="unsupported_claims" if issues else "all_claims_supported",
        )
