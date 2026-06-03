"""对话历史加载（token 预算驱动）。

Phase 1 重写：替代旧的固定 10 条滑窗，改为 token 预算驱动。
- token 没满 → 尽可能多加载历史
- token 满了 → 才停止加载
- 分批查 DB（每批 20 条），短对话只查一次
设计文档：docs/document/TECH_上下文工程重构.md §四
"""

import re
from typing import Any, Dict, List

from loguru import logger

from services.handlers.chat_context.content_extractors import (
    extract_image_urls_from_content,
    extract_oai_messages_from_content,
    extract_text_from_content,
)


async def build_context_messages(
    db: Any,
    conversation_id: str,
    current_text: str,
) -> List[Dict[str, Any]]:
    """基于 token 预算加载对话历史（含图片，失败时降级为空）"""
    try:
        from core.config import settings
        from utils.time_context import _parse_iso_to_cn

        budget = settings.context_history_token_budget  # 8000
        max_images = settings.chat_context_max_images   # 5
        BATCH_SIZE = 20
        MAX_BATCHES = 5  # 安全上限 5×20=100 条

        context: List[Dict[str, Any]] = []
        total_tokens = 0
        total_images = 0
        offset = 0
        has_more = True
        batch_count = 0

        while has_more and total_tokens < budget and batch_count < MAX_BATCHES:
            batch_count += 1
            result = (
                db.table("messages")
                # NOTE: 加载 generation_params 用于提取 tool_digest（跨轮上下文补全）
                .select("role, content, status, created_at, generation_params")
                .eq("conversation_id", conversation_id)
                .eq("status", "completed")
                .in_("role", ["user", "assistant"])
                .order("created_at", desc=True)
                .range(offset, offset + BATCH_SIZE - 1)
                .execute()
            )
            if not result.data or len(result.data) == 0:
                break
            has_more = len(result.data) == BATCH_SIZE
            offset += BATCH_SIZE

            budget_exhausted = False
            for row in result.data:  # DESC 排序，最新在前
                raw_content = row.get("content")
                role = row["role"]
                images = (
                    extract_image_urls_from_content(raw_content)
                    if total_images < max_images
                    else []
                )

                # 时间戳前缀 — 让模型区分历史消息日期，防止旧"今天"污染当前请求
                ts_prefix = ""
                if row.get("created_at"):
                    msg_time = _parse_iso_to_cn(row["created_at"])
                    if msg_time:
                        ts_prefix = f"[{msg_time.strftime('%m-%d %H:%M')}] "

                # Step 4 结构化：把 block list 拆成多条 OpenAI 标准消息
                # （tool_step → assistant.tool_calls + role=tool 配对）
                oai_msgs = extract_oai_messages_from_content(
                    raw_content, role=role, ts_prefix=ts_prefix,
                )

                # 图片处理：只有 user 消息可以发 image_url，assistant 转占位符
                remaining = max_images - total_images
                if images and remaining > 0:
                    images = images[:remaining]
                    if role == "user":
                        # 把第一条 user 文本消息升级为多模态 parts
                        text_msg_idx = next(
                            (i for i, m in enumerate(oai_msgs)
                             if m.get("role") == "user" and isinstance(m.get("content"), str)),
                            None,
                        )
                        text_value = oai_msgs[text_msg_idx]["content"] if text_msg_idx is not None else ts_prefix
                        parts: List[Dict[str, Any]] = []
                        if text_value:
                            parts.append({"type": "text", "text": text_value})
                        for url in images:
                            parts.append({"type": "image_url", "image_url": {"url": url}})
                        if text_msg_idx is not None:
                            oai_msgs[text_msg_idx] = {"role": "user", "content": parts}
                        else:
                            oai_msgs.insert(0, {"role": "user", "content": parts})
                    else:
                        # assistant 图片 → 文本占位符（LLM API 不接受 assistant 的 image_url）
                        img_hint = "".join("\n📊 [已生成图表]" for _ in images)
                        target_idx = next(
                            (i for i in range(len(oai_msgs) - 1, -1, -1)
                             if oai_msgs[i].get("role") == "assistant"
                             and isinstance(oai_msgs[i].get("content"), str)),
                            None,
                        )
                        if target_idx is not None:
                            oai_msgs[target_idx]["content"] = (
                                oai_msgs[target_idx]["content"] + img_hint
                            )
                        else:
                            oai_msgs.append({
                                "role": "assistant",
                                "content": f"{ts_prefix}{img_hint.lstrip()}",
                            })
                    total_images += len(images)

                if not oai_msgs:
                    continue

                # 估算这批 OAI 消息的总 token 数
                msg_chars = 0
                for m in oai_msgs:
                    c = m.get("content")
                    if isinstance(c, str):
                        msg_chars += len(c)
                    elif isinstance(c, list):
                        msg_chars += sum(
                            len(p.get("text", "")) for p in c
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    for tc in (m.get("tool_calls") or []):
                        msg_chars += len(tc.get("function", {}).get("arguments", ""))
                msg_tokens = int(msg_chars / 2.5)
                if total_tokens + msg_tokens > budget:
                    budget_exhausted = True
                    break  # 预算用完

                context.extend(oai_msgs)
                total_tokens += msg_tokens

                # 注入工具执行摘要（让 LLM 知道上轮做了什么、数据在哪）
                # 追加到这一轮最后一条 assistant text 消息（不是 tool 消息）
                if role == "assistant" and oai_msgs:
                    gen_params = row.get("generation_params") or {}
                    digest = gen_params.get("tool_digest") if isinstance(gen_params, dict) else None
                    if digest:
                        from services.handlers.tool_digest import format_tool_digest
                        annotation = format_tool_digest(digest)
                        if annotation:
                            target = None
                            for m in reversed(context):
                                if m.get("role") != "assistant":
                                    continue
                                if m.get("tool_calls"):
                                    continue
                                if isinstance(m.get("content"), (str, list)):
                                    target = m
                                    break
                            if target is not None:
                                if isinstance(target["content"], str):
                                    target["content"] += annotation
                                elif isinstance(target["content"], list):
                                    target["content"].append({"type": "text", "text": annotation})

            if budget_exhausted:
                break  # 跳出外层 while

        # 反转为正序（旧→新），LLM 需要按时间顺序读取
        context.reverse()

        # 去除末尾与当前消息重复的 user 消息
        if context and context[-1]["role"] == "user":
            tail_content = context[-1]["content"]
            tail = (
                extract_text_from_content(tail_content)
                if isinstance(tail_content, list)
                else tail_content
            )
            # 剥掉时间戳前缀 [MM-DD HH:MM] 后再比较
            tail_stripped = re.sub(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] ", "", tail).strip()
            if tail_stripped == current_text.strip():
                context.pop()

        if context:
            logger.debug(
                f"Context injected | conversation_id={conversation_id} "
                f"| count={len(context)} | tokens={total_tokens} "
                f"| budget={budget} | images={total_images}"
            )

        return context

    except Exception as e:
        logger.warning(
            f"Context injection failed, skipping | "
            f"conversation_id={conversation_id} | error={e}"
        )
        return []
