"""对话摘要读写。

- get_context_summary: 注入时读取已存的摘要
- update_summary_if_needed: 检查并更新摘要（fire-and-forget）
"""

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from services.handlers.chat_context.content_extractors import (
    extract_text_from_content,
)


async def get_context_summary(
    db: Any,
    conversation_id: str,
    prefetched: Optional[str] = None,
) -> Optional[str]:
    """获取已缓存的对话摘要（失败返回 None）

    Args:
        db: Supabase client
        conversation_id: 对话 ID
        prefetched: HTTP 阶段预取的 context_summary（有值时跳过 DB 查询）
    """
    try:
        from core.config import settings

        if not settings.context_summary_enabled:
            return None

        # 优先使用预取值（HTTP 阶段 get_conversation 已查过同一行）
        summary_updated = None
        if prefetched is not None:
            summary = prefetched
        else:
            result = (
                db.table("conversations")
                .select("context_summary, updated_at")
                .eq("id", conversation_id)
                .single()
                .execute()
            )

            if not result.data:
                return None

            summary = result.data.get("context_summary")
            summary_updated = result.data.get("updated_at")
        if not summary:
            return None

        # 标注摘要生成时间，防止模型误将旧摘要当最新数据
        from utils.time_context import _parse_iso_to_cn
        ts_label = ""
        if summary_updated:
            ts = _parse_iso_to_cn(summary_updated)
            if ts:
                ts_label = f"（生成于 {ts.strftime('%m-%d %H:%M')}，可能不是最新数据）"

        logger.debug(
            f"Context summary injected | "
            f"conversation_id={conversation_id} | len={len(summary)}"
        )
        return f"以下是之前对话的摘要{ts_label}：\n{summary}"

    except Exception as e:
        logger.warning(
            f"Context summary fetch failed, skipping | "
            f"conversation_id={conversation_id} | error={e}"
        )
        return None


async def update_summary_if_needed(
    db: Any,
    conversation_id: str,
) -> None:
    """检查并更新对话摘要（fire-and-forget，失败不影响主流程）"""
    try:
        from core.config import settings

        if not settings.context_summary_enabled:
            return
        conv_result = (
            db.table("conversations")
            .select(
                "message_count, summary_message_count, context_summary, "
                "context_revision, summary_revision"
            )
            .eq("id", conversation_id)
            .single()
            .execute()
        )

        if not conv_result.data:
            return

        message_count = conv_result.data.get("message_count", 0)
        summary_count = conv_result.data.get("summary_message_count", 0)
        existing_summary: Optional[str] = conv_result.data.get("context_summary")
        summary_revision = int(conv_result.data.get("summary_revision") or 0)
        context_limit = settings.chat_context_limit

        if message_count <= context_limit:
            return

        if (
            summary_count > 0
            and (message_count - summary_count)
            < settings.context_summary_update_interval
        ):
            return

        all_result = (
            db.table("messages")
            .select("id, role, content, context_revision, message_kind")
            .eq("conversation_id", conversation_id)
            .eq("status", "completed")
            .in_("role", ["user", "assistant"])
            .order("context_revision", desc=False)
            .order("created_at", desc=False)
            .execute()
        )

        if not all_result.data:
            return
        summary_rows, new_rows, through_revision, through_message_id = (
            _select_closed_summary_window(
                all_result.data,
                context_limit=context_limit,
                current_summary_revision=summary_revision,
            )
        )
        if not summary_rows or not through_message_id:
            return

        text_messages = _extract_text_messages(summary_rows)
        if not text_messages:
            return

        await _coordinate_summary_update(
            db=db,
            conversation_id=conversation_id,
            summary_revision=summary_revision,
            through_revision=through_revision,
            through_message_id=through_message_id,
            existing_summary=existing_summary,
            text_messages=text_messages,
            new_rows=new_rows,
            message_count=message_count,
            compressed_count=len(summary_rows),
        )

    except Exception as e:
        logger.warning(
            f"Context summary update failed | "
            f"conversation_id={conversation_id} | error={e}"
        )


async def _coordinate_summary_update(
    *,
    db: Any,
    conversation_id: str,
    summary_revision: int,
    through_revision: int,
    through_message_id: str,
    existing_summary: Optional[str],
    text_messages: List[Dict[str, str]],
    new_rows: List[Dict[str, Any]],
    message_count: int,
    compressed_count: int,
) -> None:
    """协调跨 Worker 摘要生成，并以数据库 CAS 提交。"""
    from services.agent.runtime.context import (
        acquire_summary_coordination,
        finish_summary_coordination,
    )

    coordination = await acquire_summary_coordination(
        conversation_id,
        summary_revision,
        through_revision,
    )
    if not coordination.should_run:
        logger.info(
            f"Context summary skipped | conversation_id={conversation_id} | "
            f"reason={coordination.outcome}"
        )
        return

    summary: Optional[str] = None
    try:
        summary = await _generate_summary(
            existing_summary=existing_summary,
            summary_revision=summary_revision,
            text_messages=text_messages,
            new_rows=new_rows,
            conversation_id=conversation_id,
        )
        if not summary:
            return
        applied = _apply_summary(
            db,
            conversation_id=conversation_id,
            expected_revision=summary_revision,
            through_revision=through_revision,
            through_message_id=through_message_id,
            summary=summary,
            message_count=message_count,
        )
        logger.info(
            f"Context summary updated | conversation_id={conversation_id} | "
            f"outcome={applied} | through_revision={through_revision} | "
            f"compressed={compressed_count} msgs | summary_len={len(summary)}"
        )
    finally:
        await finish_summary_coordination(
            coordination,
            failed=not summary,
        )


async def _generate_summary(
    *,
    existing_summary: Optional[str],
    summary_revision: int,
    text_messages: List[Dict[str, str]],
    new_rows: List[Dict[str, Any]],
    conversation_id: str,
) -> Optional[str]:
    """优先增量生成摘要，失败时回退全量摘要。"""
    if existing_summary and summary_revision > 0:
        from services.context_summarizer import update_summary

        new_text_messages = _extract_text_messages(new_rows)
        if new_text_messages:
            summary = await update_summary(
                existing_summary,
                new_text_messages,
            )
            if summary:
                logger.info(
                    f"Context summary incremental update | "
                    f"conversation_id={conversation_id} | "
                    f"new_msgs={len(new_text_messages)}"
                )
                return summary

    from services.context_summarizer import summarize_messages

    return await summarize_messages(text_messages)


def _select_closed_summary_window(
    rows: List[Dict[str, Any]],
    *,
    context_limit: int,
    current_summary_revision: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, Optional[str]]:
    """选择连续闭合 Turn，且不覆盖需要完整保留的最近消息。"""
    valid = [
        row for row in rows
        if row.get("message_kind", "conversation") == "conversation"
        and isinstance(row.get("context_revision"), int)
        and row["context_revision"] > 0
        and row.get("id")
    ]
    if len(valid) <= context_limit:
        return [], [], 0, None

    first_recent_revision = valid[-context_limit]["context_revision"]
    candidates = [
        row for row in valid
        if row["context_revision"] < first_recent_revision
    ]
    rows_by_revision: Dict[int, List[Dict[str, Any]]] = {}
    for row in candidates:
        rows_by_revision.setdefault(row["context_revision"], []).append(row)

    expected_revision = current_summary_revision + 1
    selected: List[Dict[str, Any]] = []
    boundary_id: Optional[str] = None
    through_revision = current_summary_revision
    for revision in sorted(rows_by_revision):
        turn_rows = rows_by_revision[revision]
        if revision <= current_summary_revision:
            selected.extend(turn_rows)
            continue
        if revision != expected_revision:
            break
        roles = {row.get("role") for row in turn_rows}
        assistant = next(
            (row for row in reversed(turn_rows) if row.get("role") == "assistant"),
            None,
        )
        if roles != {"user", "assistant"} or assistant is None:
            break
        selected.extend(turn_rows)
        boundary_id = str(assistant["id"])
        through_revision = revision
        expected_revision += 1

    if through_revision <= current_summary_revision:
        return [], [], 0, None
    new_rows = [
        row for row in selected
        if row["context_revision"] > current_summary_revision
    ]
    return selected, new_rows, through_revision, boundary_id


def _extract_text_messages(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """将闭合摘要窗口转换为纯文本消息。"""
    messages: List[Dict[str, str]] = []
    for row in rows:
        text = extract_text_from_content(row.get("content"))
        if text:
            messages.append({"role": str(row["role"]), "content": text})
    return messages


def _apply_summary(
    db: Any,
    *,
    conversation_id: str,
    expected_revision: int,
    through_revision: int,
    through_message_id: str,
    summary: str,
    message_count: int,
) -> str:
    """通过数据库 CAS RPC 原子提交摘要覆盖边界。"""
    result = db.rpc("apply_context_summary", {
        "p_conversation_id": conversation_id,
        "p_expected_summary_revision": expected_revision,
        "p_through_revision": through_revision,
        "p_through_message_id": through_message_id,
        "p_summary": summary,
        "p_summary_message_count": message_count,
    }).execute()
    data = result.data if result else None
    return str((data or {}).get("outcome") or "unknown")
