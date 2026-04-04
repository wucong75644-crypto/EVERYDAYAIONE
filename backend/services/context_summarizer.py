"""
对话历史摘要压缩

将超过 20 条的早期对话消息压缩为 ≤500 字摘要，注入 system prompt 实现低成本"长记忆"。
降级链：qwen-turbo → qwen-plus → 跳过（无摘要）
"""

from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import settings
from services.dashscope_client import DashScopeClient

SUMMARY_SYSTEM_PROMPT = """你是一个对话摘要生成器。将给定的对话历史压缩为简洁摘要。

要求：
- 必须保留：ERP 查询结论中的关键数字（金额、数量、日期、商品编码）
- 必须保留：用户明确表达的意图和决策
- 可以压缩：寒暄、确认、重复问答
- 可以丢弃：工具调用的中间过程、API 参数细节
- 按时间顺序，分话题段落概括
- 最大{max_chars}字
- 直接输出摘要文本，不要加前缀或解释"""

# 模块级 HTTP 客户端（延迟初始化）
_ds_client = DashScopeClient("context_summary_timeout")


def _build_summary_prompt(messages: List[Dict[str, Any]]) -> str:
    """将消息列表格式化为压缩用 prompt"""
    lines = []
    for msg in messages:
        role = "用户" if msg["role"] == "user" else "AI"
        content = msg["content"]
        # 截断过长的单条消息（避免 prompt 过大）
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"{role}：{content}")
    return "\n".join(lines)


async def _call_summary_model(
    model: str, messages_text: str
) -> Optional[str]:
    """调用单个模型生成摘要，失败返回 None"""
    client = await _ds_client.get()
    max_chars = settings.context_summary_max_chars
    system_prompt = SUMMARY_SYSTEM_PROMPT.format(max_chars=max_chars)

    try:
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请压缩以下对话历史：\n\n{messages_text}"},
                ],
                "temperature": 0.1,
                "max_tokens": max_chars * 2,
            },
        )
        response.raise_for_status()
        data = response.json()
        summary = data["choices"][0]["message"]["content"].strip()

        # 截断超长摘要
        if len(summary) > max_chars:
            summary = summary[:max_chars]

        logger.info(
            f"Context summary generated | model={model} | "
            f"input_len={len(messages_text)} | summary_len={len(summary)}"
        )
        return summary

    except httpx.TimeoutException:
        logger.warning(f"Context summary timeout | model={model}")
        return None
    except Exception as e:
        logger.warning(f"Context summary failed | model={model} | error={e}")
        return None


async def summarize_messages(
    messages: List[Dict[str, Any]],
) -> Optional[str]:
    """
    对消息列表生成压缩摘要。

    降级链：qwen-turbo → qwen-plus → 返回 None
    """
    if not messages:
        return None

    if not settings.dashscope_api_key:
        logger.warning("Context summary skipped: no dashscope_api_key")
        return None

    messages_text = _build_summary_prompt(messages)

    # 第一级：主模型
    summary = await _call_summary_model(
        settings.context_summary_model, messages_text
    )
    if summary:
        return summary

    # 第二级：备用模型
    logger.info("Context summary: falling back to secondary model")
    summary = await _call_summary_model(
        settings.context_summary_fallback_model, messages_text
    )
    if summary:
        return summary

    # 第三级：跳过
    logger.warning("Context summary: all models failed, skipping")
    return None


async def close() -> None:
    """关闭 HTTP 客户端"""
    await _ds_client.close()
