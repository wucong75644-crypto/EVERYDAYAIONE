"""Pure, secret-free planning for historical scheduled-task adoption."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


class AdoptionCategory(StrEnum):
    RUNTIME_OWNED = "runtime_owned"
    CANDIDATE_RUNTIME_SOURCE_REQUIRED = "candidate_runtime_source_required"
    PRESERVE_PAUSED = "preserve_paused"
    PRESERVE_ERROR = "preserve_error"
    BLOCKED_RUNNING = "blocked_running"
    BLOCKED_PARTIAL_RUNTIME_FACTS = "blocked_partial_runtime_facts"
    BLOCKED_INVALID_TASK = "blocked_invalid_task"
    BLOCKED_UNKNOWN_STATUS = "blocked_unknown_status"


@dataclass(frozen=True)
class AdoptionDecision:
    task_id: str
    org_id: str | None
    user_id: str | None
    category: AdoptionCategory
    reason_codes: tuple[str, ...]
    task_semantics_hash: str
    delivery_target_hash: str
    adoption_candidate: bool

    @property
    def safe_to_adopt(self) -> bool:
        """Only a later, fact-complete migration may return true.

        The preflight deliberately never claims that a historical task is safe
        to cut over: its Runtime source action/attempt/run still needs to be
        reconstructed and validated by the Runtime owner.
        """

        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "org_id": self.org_id,
            "user_id": self.user_id,
            "category": self.category.value,
            "reason_codes": list(self.reason_codes),
            "task_semantics_hash": self.task_semantics_hash,
            "delivery_target_hash": self.delivery_target_hash,
            "adoption_candidate": self.adoption_candidate,
            "safe_to_adopt": self.safe_to_adopt,
        }


def classify_scheduled_task(
    task: Mapping[str, Any], *, profile_exists: bool = False,
) -> AdoptionDecision:
    """Classify one exported scheduled task without contacting any provider."""

    task_id = str(task.get("id") or "")
    category, reasons, candidate = _classify(task, profile_exists)
    return AdoptionDecision(
        task_id=task_id,
        org_id=_optional_text(task.get("org_id")),
        user_id=_optional_text(task.get("user_id")),
        category=category,
        reason_codes=tuple(reasons),
        task_semantics_hash=_hash_task_semantics(task),
        delivery_target_hash=_hash_value(task.get("push_target")),
        adoption_candidate=candidate,
    )


def build_adoption_report(
    tasks: Iterable[Mapping[str, Any]],
    *,
    profile_task_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a deterministic dry-run report safe to save or review."""

    profile_ids = {str(value) for value in profile_task_ids}
    decisions = sorted(
        (
            classify_scheduled_task(
                task,
                profile_exists=(
                    bool(task.get("runtime_profile_exists"))
                    or str(task.get("id") or "") in profile_ids
                ),
            )
            for task in tasks
        ),
        key=lambda item: item.task_id,
    )
    counts: dict[str, int] = {}
    for decision in decisions:
        counts[decision.category.value] = counts.get(decision.category.value, 0) + 1
    return {
        "outcome": "dry_run",
        "plan_version": "scheduled-runtime-adoption-v1",
        "total_tasks": len(decisions),
        "counts": dict(sorted(counts.items())),
        "adoption_candidate_count": sum(
            decision.adoption_candidate for decision in decisions
        ),
        "safe_to_adopt_count": 0,
        "tasks": [decision.as_dict() for decision in decisions],
    }


def _classify(
    task: Mapping[str, Any], profile_exists: bool,
) -> tuple[AdoptionCategory, list[str], bool]:
    if profile_exists:
        return AdoptionCategory.RUNTIME_OWNED, ["runtime_profile_exists"], False

    if any(
        task.get(field) not in (None, "")
        for field in (
            "runtime_action_id",
            "runtime_attempt_id",
            "runtime_request_hash",
            "runtime_idempotency_key",
        )
    ):
        return (
            AdoptionCategory.BLOCKED_PARTIAL_RUNTIME_FACTS,
            ["partial_runtime_identity_without_profile"],
            False,
        )

    status = str(task.get("status") or "")
    if status == "running":
        return AdoptionCategory.BLOCKED_RUNNING, ["task_is_in_flight"], False
    if status == "paused":
        return AdoptionCategory.PRESERVE_PAUSED, ["task_is_paused"], False
    if status == "error":
        return AdoptionCategory.PRESERVE_ERROR, ["task_is_error"], False
    if status != "active":
        return AdoptionCategory.BLOCKED_UNKNOWN_STATUS, ["unknown_task_status"], False

    invalid = _invalid_task_reasons(task)
    if invalid:
        return AdoptionCategory.BLOCKED_INVALID_TASK, invalid, False
    return (
        AdoptionCategory.CANDIDATE_RUNTIME_SOURCE_REQUIRED,
        [
            "runtime_source_action_attempt_run_missing",
            "delivery_target_requires_scope_recheck",
        ],
        True,
    )


def _invalid_task_reasons(task: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not _non_empty(task.get("name")):
        reasons.append("name_missing")
    if not _non_empty(task.get("prompt")):
        reasons.append("prompt_missing")
    timezone = str(task.get("timezone") or "")
    if not timezone:
        reasons.append("timezone_missing")
    else:
        try:
            ZoneInfo(timezone)
        except Exception:
            reasons.append("timezone_invalid")

    schedule_type = str(task.get("schedule_type") or "cron")
    if schedule_type not in {"once", "daily", "weekly", "monthly", "cron"}:
        reasons.append("schedule_type_invalid")
    elif schedule_type == "once" and not _non_empty(task.get("run_at")):
        reasons.append("run_at_missing")
    elif schedule_type != "once" and not _non_empty(task.get("cron_expr")):
        reasons.append("cron_expr_missing")
    if not _non_empty(task.get("next_run_at")):
        reasons.append("next_run_at_missing")
    if not _target_shape_is_valid(task.get("push_target")):
        reasons.append("push_target_shape_invalid")
    return reasons


def _target_shape_is_valid(value: Any, depth: int = 0) -> bool:
    if depth > 4 or not isinstance(value, Mapping):
        return False
    target_type = value.get("type")
    if target_type == "web":
        return _non_empty(value.get("user_id"))
    if target_type in {"wecom_group", "wecom_user"}:
        return _non_empty(value.get("chatid")) or _non_empty(value.get("wecom_userid"))
    if target_type == "multi":
        targets = value.get("targets")
        return (
            isinstance(targets, list)
            and 1 <= len(targets) <= 20
            and all(_target_shape_is_valid(item, depth + 1) for item in targets)
        )
    return False


def _hash_task_semantics(task: Mapping[str, Any]) -> str:
    fields = {
        key: task.get(key)
        for key in (
            "id", "org_id", "user_id", "name", "prompt", "timezone",
            "push_target", "template_file", "max_credits", "retry_count",
            "timeout_sec", "schedule_type", "cron_expr", "run_at",
            "weekdays", "day_of_month", "next_run_at", "last_summary",
        )
    }
    return _hash_value(fields)


def _hash_value(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None

