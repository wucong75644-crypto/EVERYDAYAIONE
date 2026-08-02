"""Pure subject-scoped rollout and channel capability rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, kw_only=True)
class RolloutSubject:
    kind: str
    subject_id: str
    channels: frozenset[str]
    capabilities: frozenset[str]
    enabled: bool

    def __post_init__(self) -> None:
        if self.kind not in {"organization", "user"}:
            raise ValueError("RUNTIME_ROLLOUT_SUBJECT_KIND_INVALID")
        if not self.subject_id.strip() or not self.channels:
            raise ValueError("RUNTIME_ROLLOUT_SUBJECT_INVALID")


def resolve_rollout(
    *, org_id: str | None, user_id: str, channel: str,
    subjects: Mapping[tuple[str, str], RolloutSubject],
    required_capability: str = "runtime_ingress",
) -> tuple[bool, str]:
    """Require an enabled org or user subject; never treat NULL org as global."""
    if channel not in {"web", "wecom"}:
        return False, "channel_invalid"
    candidates: list[RolloutSubject] = []
    if org_id is not None:
        subject = subjects.get(("organization", org_id))
        if subject is not None:
            candidates.append(subject)
    user = subjects.get(("user", user_id))
    if user is not None:
        candidates.append(user)
    if not candidates:
        return False, "subject_not_enabled"
    if not any(item.enabled for item in candidates):
        return False, "subject_disabled"
    if not any(
        item.enabled and channel in item.channels
        and required_capability in item.capabilities for item in candidates
    ):
        return False, "channel_capability_missing"
    return True, "enabled"


__all__ = ["RolloutSubject", "resolve_rollout"]
