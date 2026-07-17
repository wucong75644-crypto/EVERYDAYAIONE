"""对话历史加载（token 预算驱动）。

Phase 1 重写：替代旧的固定 10 条滑窗，改为 token 预算驱动。
- token 没满 → 尽可能多加载历史
- token 满了 → 才停止加载
- 分批查 DB（每批 20 条），短对话只查一次
设计文档：docs/document/TECH_上下文工程重构.md §四
"""

import re
from typing import Any, Dict, List, Optional

from loguru import logger

from services.handlers.chat_context.content_extractors import (
    extract_image_urls_from_content,
    extract_interrupt_marker,
    extract_oai_messages_from_content,
    extract_text_from_content,
)
from services.handlers.interrupt_anchor import (
    TASK_RESUMPTION_TEMPLATE,
    fix_orphan_tool_calls,
)


def _build_history_query(
    db: Any,
    conversation_id: str,
    base_revision: Optional[int],
) -> Any:
    """构造 legacy 或固定 revision 的历史查询。"""
    query = (
        db.table("messages")
        .select(
            "role, content, status, created_at, generation_params, "
            "context_revision, message_kind"
        )
        .eq("conversation_id", conversation_id)
        .in_("status", ["completed", "interrupted"])
        .in_("role", ["user", "assistant"])
    )
    if base_revision is not None:
        query = (
            query.eq("message_kind", "conversation")
            .lte("context_revision", base_revision)
        )
    return query


def _row_to_oai_messages(
    row: Dict[str, Any],
    remaining_images: int,
) -> tuple[List[Dict[str, Any]], int]:
    """把一条数据库消息转换为标准消息，并限制历史图片数量。"""
    raw_content = row.get("content")
    role = row["role"]
    messages = extract_oai_messages_from_content(
        raw_content, role=role, ts_prefix="",
    )
    images = extract_image_urls_from_content(raw_content)[:remaining_images]
    if not images:
        return messages, 0

    if role == "user":
        text_index = next(
            (
                index for index, message in enumerate(messages)
                if message.get("role") == "user"
                and isinstance(message.get("content"), str)
            ),
            None,
        )
        text_value = messages[text_index]["content"] if text_index is not None else ""
        parts: List[Dict[str, Any]] = []
        if text_value:
            parts.append({"type": "text", "text": text_value})
        parts.extend(
            {"type": "image_url", "image_url": {"url": url}}
            for url in images
        )
        user_message = {"role": "user", "content": parts}
        if text_index is None:
            messages.insert(0, user_message)
        else:
            messages[text_index] = user_message
    else:
        image_hint = "".join("\n📊 [已生成图表]" for _ in images)
        target_index = next(
            (
                index for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "assistant"
                and isinstance(messages[index].get("content"), str)
            ),
            None,
        )
        if target_index is None:
            messages.append({
                "role": "assistant",
                "content": image_hint.lstrip(),
            })
        else:
            messages[target_index]["content"] += image_hint
    return messages, len(images)


def _estimate_message_tokens(messages: List[Dict[str, Any]]) -> int:
    """保守估算一组标准消息的 token 数，仅用于加载日志和批次上限。"""
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            chars += sum(
                len(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        chars += sum(
            len(call.get("function", {}).get("arguments", ""))
            for call in (message.get("tool_calls") or [])
        )
    return int(chars / 2.5)


def _append_tool_digest(
    context: List[Dict[str, Any]],
    row: Dict[str, Any],
) -> None:
    """把 assistant 的工具摘要追加到最近的普通 assistant 内容。"""
    params = row.get("generation_params") or {}
    digest = params.get("tool_digest") if isinstance(params, dict) else None
    if not digest:
        return

    from services.handlers.tool_digest import format_tool_digest

    annotation = format_tool_digest(digest)
    if not annotation:
        return
    target = next(
        (
            message for message in reversed(context)
            if message.get("role") == "assistant"
            and not message.get("tool_calls")
            and isinstance(message.get("content"), (str, list))
        ),
        None,
    )
    if target is None:
        return
    if isinstance(target["content"], str):
        target["content"] += annotation
    else:
        target["content"].append({"type": "text", "text": annotation})


def _finalize_history(
    context: List[Dict[str, Any]],
    interrupt_marker: Optional[Dict[str, Any]],
    current_text: str,
    is_legacy: bool,
) -> List[Dict[str, Any]]:
    """补全工具配对、中断提示和 legacy 文本去重。"""
    context.reverse()
    context = fix_orphan_tool_calls(context)
    if interrupt_marker:
        from utils.time_context import format_relative_time

        ago_text = format_relative_time(interrupt_marker.get("interrupted_at", ""))
        context.append({
            "role": "system",
            "content": TASK_RESUMPTION_TEMPLATE.format(ago_text=ago_text),
        })
    if is_legacy and context and context[-1]["role"] == "user":
        tail_content = context[-1]["content"]
        tail = (
            extract_text_from_content(tail_content)
            if isinstance(tail_content, list) else tail_content
        )
        stripped = re.sub(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] ", "", tail).strip()
        if stripped == current_text.strip():
            context.pop()
    return context


async def build_context_messages(
    db: Any,
    conversation_id: str,
    current_text: str,
    base_revision: Optional[int] = None,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """加载 legacy 时间线或固定 revision 的闭合历史。"""
    try:
        from core.config import settings

        budget = settings.context_history_token_budget
        max_images = settings.chat_context_max_images
        context: List[Dict[str, Any]] = []
        total_tokens = 0
        total_images = 0
        first_assistant_seen = False
        latest_marker: Optional[Dict[str, Any]] = None

        for batch in range(5):
            query = _build_history_query(db, conversation_id, base_revision)
            query = query.order("created_at", desc=True)
            if base_revision is not None:
                query = (
                    query.order("context_revision", desc=True)
                    .order("role", desc=False)
                    .order("id", desc=True)
                )
            result = query.range(
                batch * 20, batch * 20 + 19,
            ).execute()
            rows = result.data if result else None
            if not rows:
                break
            for row in rows:
                if row["role"] == "assistant" and not first_assistant_seen:
                    first_assistant_seen = True
                    latest_marker = extract_interrupt_marker(row.get("content"))
                messages, image_count = _row_to_oai_messages(
                    row, max(0, max_images - total_images),
                )
                if not messages:
                    continue
                context.extend(messages)
                total_images += image_count
                total_tokens += _estimate_message_tokens(messages)
                if row["role"] == "assistant":
                    _append_tool_digest(context, row)
            if len(rows) < 20 or total_tokens >= budget:
                break

        context = _finalize_history(
            context, latest_marker, current_text, base_revision is None,
        )
        if context:
            logger.debug(
                f"Context injected | conversation_id={conversation_id} | "
                f"count={len(context)} | tokens={total_tokens} | "
                f"budget={budget} | images={total_images}"
            )
        return context
    except Exception as error:
        logger.warning(
            f"Context injection failed, skipping | "
            f"conversation_id={conversation_id} | "
            f"base_revision={base_revision} | error={error}"
        )
        if strict:
            raise
        return []
