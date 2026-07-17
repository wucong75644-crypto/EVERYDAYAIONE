"""不可变任务上下文快照。

新任务只读取 Turn 绑定时已经闭合的 conversation 消息。Redis、后续 Turn
以及任务私有工具循环都不能改变同一任务看到的历史。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

if TYPE_CHECKING:
    from services.handlers.resource_manifest import ResourceManifest


@dataclass(frozen=True)
class ContextAnchor:
    """任务绑定事务返回的不可变上下文边界。"""

    task_id: str
    conversation_id: str
    turn_id: str
    input_message_id: str
    base_revision: int
    through_message_id: Optional[str]
    org_id: Optional[str]


@dataclass(frozen=True)
class ContextSnapshot:
    """一次生成任务使用的闭合历史快照。"""

    anchor: ContextAnchor
    history_messages: List[Dict[str, Any]]
    summary_prompt: Optional[str]
    summary_revision: int
    conversation_source: str
    resource_manifest: "ResourceManifest | None" = None


def context_anchor_from_binding(
    task_data: Dict[str, Any],
    input_message_id: str,
    turn_id: str,
    binding_data: Dict[str, Any],
) -> ContextAnchor:
    """把 bind_generation_turn 返回值转换为类型化锚点。"""
    return ContextAnchor(
        task_id=str(task_data["id"]),
        conversation_id=str(task_data["conversation_id"]),
        turn_id=turn_id,
        input_message_id=input_message_id,
        base_revision=int(binding_data["base_context_revision"]),
        through_message_id=binding_data.get("context_through_message_id"),
        org_id=task_data.get("org_id"),
    )


async def build_context_snapshot(
    db: Any,
    anchor: ContextAnchor,
    current_text: str,
) -> ContextSnapshot:
    """严格构造固定 revision 的历史；读取失败时禁止静默使用不确定历史。"""
    input_result = (
        db.table("messages")
        .select("id, conversation_id, role, turn_id, content")
        .eq("id", anchor.input_message_id)
        .maybe_single()
        .execute()
    )
    input_message = input_result.data if input_result else None
    if (
        not input_message
        or str(input_message.get("conversation_id")) != anchor.conversation_id
        or input_message.get("role") != "user"
        or str(input_message.get("turn_id")) != anchor.turn_id
    ):
        raise ValueError("CONTEXT_INPUT_ANCHOR_MISMATCH")

    from services.handlers import conversation_cache
    from services.handlers.chat_context.history_loader import build_context_messages

    history = await conversation_cache.get_closed_messages(
        anchor.conversation_id,
        anchor.base_revision,
        anchor.through_message_id,
        anchor.org_id,
        task_id=anchor.task_id,
        turn_id=anchor.turn_id,
    )
    try:
        if history is None:
            history = await build_context_messages(
                db,
                anchor.conversation_id,
                current_text,
                base_revision=anchor.base_revision,
                strict=True,
            )
            await conversation_cache.set_closed_messages(
                anchor.conversation_id,
                anchor.base_revision,
                anchor.through_message_id,
                history,
                anchor.org_id,
                task_id=anchor.task_id,
                turn_id=anchor.turn_id,
            )
    except Exception as error:
        logger.error(
            "context_snapshot_failed | "
            f"org_id={anchor.org_id} | conversation_id={anchor.conversation_id} | "
            f"task_id={anchor.task_id} | turn_id={anchor.turn_id} | "
            f"input_message_id={anchor.input_message_id} | "
            f"base_revision={anchor.base_revision} | error={error}"
        )
        raise
    summary_prompt, summary_revision, conversation_source = (
        _load_snapshot_metadata(db, anchor)
    )
    from services.handlers.resource_manifest import build_resource_manifest

    resource_manifest = build_resource_manifest(
        db,
        task_id=anchor.task_id,
        input_message_id=anchor.input_message_id,
        conversation_id=anchor.conversation_id,
        turn_id=anchor.turn_id,
        org_id=anchor.org_id,
        input_content=input_message.get("content"),
    )

    logger.info(
        "context_snapshot_built | "
        f"org_id={anchor.org_id} | conversation_id={anchor.conversation_id} | "
        f"task_id={anchor.task_id} | turn_id={anchor.turn_id} | "
        f"input_message_id={anchor.input_message_id} | "
        f"base_revision={anchor.base_revision} | history_count={len(history)} | "
        f"summary_revision={summary_revision}"
    )
    return ContextSnapshot(
        anchor=anchor,
        history_messages=history,
        summary_prompt=summary_prompt,
        summary_revision=summary_revision,
        conversation_source=conversation_source,
        resource_manifest=resource_manifest,
    )


def _load_snapshot_metadata(
    db: Any,
    anchor: ContextAnchor,
) -> tuple[Optional[str], int, str]:
    """读取预算来源，并只接纳基线以内的版本化摘要。"""
    result = (
        db.table("conversations")
        .select("context_summary, summary_revision, source")
        .eq("id", anchor.conversation_id)
        .maybe_single()
        .execute()
    )
    conversation = result.data if result else None
    if not conversation:
        return None, 0, ""

    source = str(conversation.get("source") or "")
    summary = conversation.get("context_summary")
    revision = int(conversation.get("summary_revision") or 0)
    if not summary or revision <= 0 or revision > anchor.base_revision:
        return None, 0, source
    return (
        f"以下是之前对话的摘要（截至 revision {revision}）：\n{summary}",
        revision,
        source,
    )
