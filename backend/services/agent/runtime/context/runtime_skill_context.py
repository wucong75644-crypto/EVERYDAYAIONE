"""Resolve and freeze Skill context for Runtime model steps."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.context.skill_activation import (
    SkillContext,
    build_skill_context,
    restore_skill_context,
)
from services.agent.runtime.ports.coordinator_recovery import (
    RunAggregateSnapshot,
)

_RUNTIME_WORKSPACE_ROOT = "/mnt/nas-workspace"


def resolve_runtime_skill_context(
    *, snapshot: RunAggregateSnapshot, context: dict,
    params: Mapping[str, object], input_message_id: str | None,
    workspace_root: str = _RUNTIME_WORKSPACE_ROOT,
) -> SkillContext:
    """Load Skills once, then reuse the persisted snapshot for later steps."""
    previous_step = snapshot.latest_model_step
    if isinstance(previous_step, dict):
        receipt = previous_step.get("request_receipt")
        if isinstance(receipt, dict) and "skill_context" in receipt:
            return restore_skill_context(receipt["skill_context"])
        # A Run created before Skill integration keeps its old context shape.
        return _empty_skill_context()

    if _context_flag_disabled(params.get("personal_context_allowed")):
        return _empty_skill_context()
    session_value = context.get("session")
    if not isinstance(session_value, dict):
        return _empty_skill_context()
    user_id = _optional_text(session_value.get("user_id"))
    if not user_id:
        return _empty_skill_context()
    org_id = _optional_text(session_value.get("org_id"))
    query = _current_user_query(context.get("messages"), input_message_id)

    from core.workspace import resolve_workspace_dir

    workspace_dir = resolve_workspace_dir(
        workspace_root, user_id, org_id,
    )
    try:
        return build_skill_context(workspace_dir, query)
    except OSError:
        return _empty_skill_context()


def _empty_skill_context() -> SkillContext:
    return SkillContext(catalog=None, instructions=None, issue_count=0)


def _context_flag_disabled(value: object) -> bool:
    return value is False or value == "false"


def _current_user_query(value: object, input_message_id: str | None) -> str:
    if not isinstance(value, list):
        return ""
    fallback = ""
    for item in value:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = _message_text(item.get("content"))
        fallback = text or fallback
        if input_message_id and str(item.get("id") or "") == input_message_id:
            return text
    return fallback


def _message_text(value: object) -> str:
    if isinstance(value, str):
        return value[:20_000]
    if not isinstance(value, list):
        return ""
    parts = [
        str(item.get("text") or "")
        for item in value
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return " ".join(part for part in parts if part)[:20_000]


def _optional_text(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None
