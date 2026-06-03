"""对话摘要读写。

- get_context_summary: 注入时读取已存的摘要
- update_summary_if_needed: 检查并更新摘要（fire-and-forget）
"""

from typing import Any, Optional

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

        # 查询对话信息（含已有摘要，一次查完）
        conv_result = (
            db.table("conversations")
            .select("message_count, summary_message_count, context_summary")
            .eq("id", conversation_id)
            .single()
            .execute()
        )

        if not conv_result.data:
            return

        message_count = conv_result.data.get("message_count", 0)
        summary_count = conv_result.data.get("summary_message_count", 0)
        existing_summary: Optional[str] = conv_result.data.get("context_summary")
        context_limit = settings.chat_context_limit

        # 不需要摘要（≤20 条消息）
        if message_count <= context_limit:
            return

        # 已有摘要且不需要更新（新增消息 < update_interval）
        if summary_count > 0 and (message_count - summary_count) < settings.context_summary_update_interval:
            return

        # 获取所有已完成的 user/assistant 消息（按时间正序）
        all_result = (
            db.table("messages")
            .select("role, content")
            .eq("conversation_id", conversation_id)
            .eq("status", "completed")
            .in_("role", ["user", "assistant"])
            .order("created_at", desc=False)
            .execute()
        )

        if not all_result.data:
            return

        all_msgs = all_result.data

        # 取除最近 N 条之外的消息进行压缩
        if len(all_msgs) <= context_limit:
            return

        msgs_to_summarize = all_msgs[:-context_limit]

        # 提取纯文本
        text_messages = []
        for msg in msgs_to_summarize:
            text = extract_text_from_content(msg.get("content"))
            if text:
                text_messages.append(
                    {"role": msg["role"], "content": text}
                )

        if not text_messages:
            return

        # 增量路径：有旧摘要时只传新增消息（对标 Claude PARTIAL_COMPACT_PROMPT）
        summary = None
        if existing_summary and summary_count > 0:
            from services.context_summarizer import update_summary

            # new_total = 新增消息数（所有角色），用作 msgs_to_summarize 尾部切片上界
            # 偏大（含 tool/system）无害——LLM 增量 prompt 会自动去重
            new_total = message_count - summary_count
            if new_total > 0:
                new_slice = msgs_to_summarize[-new_total:] if new_total < len(msgs_to_summarize) else msgs_to_summarize
                new_text_messages = []
                for msg in new_slice:
                    text = extract_text_from_content(msg.get("content"))
                    if text:
                        new_text_messages.append({"role": msg["role"], "content": text})
                if new_text_messages:
                    summary = await update_summary(existing_summary, new_text_messages)
                    if summary:
                        logger.info(
                            f"Context summary incremental update | "
                            f"conversation_id={conversation_id} | "
                            f"new_msgs={len(new_text_messages)}"
                        )

        # 全量降级：增量失败或无旧摘要
        if not summary:
            from services.context_summarizer import summarize_messages
            summary = await summarize_messages(text_messages)

        if summary:
            db.table("conversations").update({
                "context_summary": summary,
                "summary_message_count": message_count,
            }).eq("id", conversation_id).execute()

            logger.info(
                f"Context summary updated | "
                f"conversation_id={conversation_id} | "
                f"message_count={message_count} | "
                f"compressed={len(msgs_to_summarize)} msgs | "
                f"summary_len={len(summary)}"
            )

    except Exception as e:
        logger.warning(
            f"Context summary update failed | "
            f"conversation_id={conversation_id} | error={e}"
        )
