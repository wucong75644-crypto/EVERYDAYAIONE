"""Build Runtime model messages with mode and selected Skill context."""

from __future__ import annotations

from services.agent.runtime.context.mode_prompt import (
    normalize_permission_mode,
    render_runtime_mode_prompt,
)
from services.agent.runtime.context.runtime_skill_context import (
    resolve_runtime_skill_context,
)
from services.agent.runtime.context.skill_activation import (
    SkillContext,
    skill_context_snapshot,
)
from services.agent.runtime.ports.coordinator_recovery import (
    RunAggregateSnapshot,
)


def _messages(
    value: object,
    system_prompt: str,
    *,
    current_input_message_id: str | None = None,
    supports_vision: bool = True,
) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("AGENT_RUNTIME_MESSAGES_INVALID")
    from services.handlers.chat_context.content_extractors import (
        extract_image_urls_from_content,
        extract_oai_messages_from_content,
        project_user_image_urls,
    )
    if not system_prompt:
        raise RuntimeError("RUNTIME_DEFINITION_PROMPT_MISSING")
    result = [{"role": "system", "content": system_prompt}]
    for item in value:
        row = _mapping(item, "message")
        role = str(row.get("role") or "")
        if role not in {"system", "user", "assistant"}:
            continue
        projected = extract_oai_messages_from_content(
            row.get("content"), role, safe_completed_tools_only=True,
        )
        is_current_input = (
            role == "user"
            and current_input_message_id is not None
            and str(row.get("id") or "") == current_input_message_id
        )
        if is_current_input:
            image_urls = extract_image_urls_from_content(row.get("content"))
            if image_urls and not supports_vision:
                raise RuntimeError("RUNTIME_MODEL_VISION_REQUIRED")
            projected = project_user_image_urls(
                projected, image_urls, include_reference_indexes=True,
            )
        result.extend(projected)
    if len(result) == 1:
        raise RuntimeError("AGENT_RUNTIME_MESSAGES_EMPTY")
    return result


def _runtime_messages(
    *, snapshot: RunAggregateSnapshot, context: dict, definition: object,
    payload: dict, model_id: str, input_message_id: str | None,
    supports_vision: bool = True,
) -> tuple[list[dict], str, SkillContext]:
    params = _mapping(payload.get("params") or {}, "params")
    permission_mode = normalize_permission_mode(params.get("permission_mode"))
    skill_context = resolve_runtime_skill_context(
        snapshot=snapshot, context=context, params=params,
        input_message_id=input_message_id,
    )
    prompt_blocks = [
        str(definition.system_prompt),
        render_runtime_mode_prompt(permission_mode),
    ]
    prompt_blocks.extend(
        block for block in (skill_context.catalog, skill_context.instructions)
        if block
    )
    return _messages(
        context.get("messages"), "\n\n".join(prompt_blocks),
        current_input_message_id=input_message_id,
        supports_vision=supports_vision,
    ), permission_mode, skill_context


def _runtime_prompt_inputs(
    *, snapshot: RunAggregateSnapshot, context: dict, definition: object,
    payload: dict, model_id: str, input_message_id: str | None,
    supports_vision: bool = True,
) -> tuple[list[dict], str, dict[str, object]]:
    messages, permission_mode, skill_context = _runtime_messages(
        snapshot=snapshot, context=context, definition=definition,
        payload=payload, model_id=model_id,
        input_message_id=input_message_id, supports_vision=supports_vision,
    )
    return messages, permission_mode, skill_context_snapshot(skill_context)


def _mapping(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"AGENT_RUNTIME_{name.upper()}_INVALID")
    return value
