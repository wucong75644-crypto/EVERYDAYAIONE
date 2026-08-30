"""ChangeSet 状态机及状态变更边界。"""

from __future__ import annotations

from typing import FrozenSet

from services.changeset.contracts import ChangeSetStatus


class ChangeSetTransitionError(ValueError):
    """请求的状态迁移不符合 ChangeSet 契约。"""


TERMINAL_STATUSES: FrozenSet[ChangeSetStatus] = frozenset({
    ChangeSetStatus.APPLIED,
    ChangeSetStatus.CANCELLED,
    ChangeSetStatus.REJECTED,
    ChangeSetStatus.FAILED,
    ChangeSetStatus.EXPIRED,
    ChangeSetStatus.CONFLICTED,
})


ALLOWED_TRANSITIONS: dict[ChangeSetStatus, FrozenSet[ChangeSetStatus]] = {
    ChangeSetStatus.DRAFT: frozenset({
        ChangeSetStatus.RESOLVING, ChangeSetStatus.CANCELLED, ChangeSetStatus.EXPIRED,
    }),
    ChangeSetStatus.RESOLVING: frozenset({
        ChangeSetStatus.PROPOSED, ChangeSetStatus.FAILED,
        ChangeSetStatus.CANCELLED, ChangeSetStatus.EXPIRED,
    }),
    ChangeSetStatus.PROPOSED: frozenset({
        ChangeSetStatus.VALIDATING, ChangeSetStatus.REJECTED,
        ChangeSetStatus.FAILED, ChangeSetStatus.CANCELLED, ChangeSetStatus.EXPIRED,
    }),
    ChangeSetStatus.VALIDATING: frozenset({
        ChangeSetStatus.PREFLIGHTING, ChangeSetStatus.REJECTED,
        ChangeSetStatus.FAILED, ChangeSetStatus.CANCELLED,
        ChangeSetStatus.EXPIRED, ChangeSetStatus.CONFLICTED,
    }),
    ChangeSetStatus.PREFLIGHTING: frozenset({
        ChangeSetStatus.AWAITING_APPROVAL, ChangeSetStatus.REJECTED,
        ChangeSetStatus.FAILED, ChangeSetStatus.CANCELLED,
        ChangeSetStatus.EXPIRED, ChangeSetStatus.CONFLICTED,
    }),
    ChangeSetStatus.AWAITING_APPROVAL: frozenset({
        ChangeSetStatus.COMMITTING, ChangeSetStatus.REJECTED,
        ChangeSetStatus.FAILED, ChangeSetStatus.CANCELLED,
        ChangeSetStatus.EXPIRED, ChangeSetStatus.CONFLICTED,
    }),
    ChangeSetStatus.COMMITTING: frozenset({
        ChangeSetStatus.APPLIED, ChangeSetStatus.FAILED, ChangeSetStatus.CONFLICTED,
    }),
    ChangeSetStatus.APPLIED: frozenset(),
    ChangeSetStatus.CANCELLED: frozenset(),
    ChangeSetStatus.REJECTED: frozenset(),
    ChangeSetStatus.FAILED: frozenset(),
    ChangeSetStatus.EXPIRED: frozenset(),
    ChangeSetStatus.CONFLICTED: frozenset(),
}


def can_transition(current: str | ChangeSetStatus, target: str | ChangeSetStatus) -> bool:
    current_status = ChangeSetStatus(current)
    target_status = ChangeSetStatus(target)
    return target_status in ALLOWED_TRANSITIONS[current_status]


def require_transition(current: str | ChangeSetStatus, target: str | ChangeSetStatus) -> None:
    if not can_transition(current, target):
        raise ChangeSetTransitionError(
            f"invalid changeset transition: {ChangeSetStatus(current).value}"
            f" -> {ChangeSetStatus(target).value}"
        )


def is_terminal(status: str | ChangeSetStatus) -> bool:
    return ChangeSetStatus(status) in TERMINAL_STATUSES
