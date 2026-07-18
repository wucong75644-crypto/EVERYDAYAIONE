"""在不同 Chat 循环之间共享最终草稿校验状态推进。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.agent.runtime.evidence_guard.guard import EvidenceGuard
from services.agent.runtime.evidence_guard.models import (
    GuardDecision,
    GuardReceipt,
)
from services.agent.runtime.evidence_guard.retry_observation import (
    build_retry_observation,
)


MAX_GUARD_RETRIES = 2
GUARD_BLOCKED_TEXT = (
    "当前回答中的数据结论未能通过证据一致性校验，"
    "因此没有输出未经确认的数字。请重试或重新查询数据。"
)


@dataclass(frozen=True)
class FinalDraftDecision:
    decision: GuardDecision
    text: str
    observation: dict[str, str] | None = None
    receipt: GuardReceipt | None = None


def review_final_draft(runtime_state: Any, draft: str) -> FinalDraftDecision:
    if not runtime_state.should_guard_output:
        return FinalDraftDecision(GuardDecision.SKIP, draft)
    receipt = EvidenceGuard().verify(
        draft,
        runtime_state.ledger.snapshot(),
        question=runtime_state.user_text,
    )
    runtime_state.last_guard_receipt = receipt
    if receipt.decision in {GuardDecision.PASS, GuardDecision.SKIP}:
        return FinalDraftDecision(receipt.decision, draft, receipt=receipt)
    runtime_state.guard_attempts += 1
    if runtime_state.guard_attempts <= MAX_GUARD_RETRIES:
        return FinalDraftDecision(
            GuardDecision.RETRY,
            "",
            observation=build_retry_observation(receipt),
            receipt=receipt,
        )
    runtime_state.guard_blocked = True
    return FinalDraftDecision(
        GuardDecision.BLOCK,
        GUARD_BLOCKED_TEXT,
        receipt=receipt,
    )


def append_retry_context(
    messages: list[dict[str, Any]],
    draft: str,
    decision: FinalDraftDecision,
) -> None:
    if decision.observation is None:
        return
    messages.append(
        {
            "role": "assistant",
            "content": draft,
        }
    )
    messages.append(decision.observation)
