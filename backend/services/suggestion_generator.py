"""
建议问题生成器

AI 回复完成后，异步调用千问小模型生成 2-3 条后续建议问题。
降级链：qwen3.5-flash(3s) → qwen3.5-plus(3s) → 放弃（不显示建议）

架构对齐：ChatGPT / Open WebUI 的 follow-up questions 均为独立异步调用，
不嵌入主 Agent 提示词，保证格式可控。
"""

import json
from typing import List, Optional

from loguru import logger

from core.config import settings
from services.dashscope_client import DashScopeClient

# 模块级 HTTP 客户端（复用 memory_filter 同款模式）
_ds_client = DashScopeClient("suggestion_generator_timeout", default_timeout=5.0)

SYSTEM_PROMPT = """你是一个对话建议生成器。根据用户的问题和 AI 的回复，生成 2-3 条用户可能想继续追问的问题。

要求：
1. 每条建议是一个完整的中文问句，15字以内
2. 建议要有实际价值，引导用户深入分析
3. 避免重复 AI 已经回答的内容
4. 输出纯 JSON 数组，不要其他文字

示例输出：
["按店铺分析销量", "和前天对比一下", "导出明细报表"]"""


async def generate_suggestions(
    user_query: str,
    ai_reply: str,
    max_items: int = 3,
) -> Optional[List[str]]:
    """生成后续建议问题。

    Args:
        user_query: 用户原始问题
        ai_reply: AI 回复文本（截取前 500 字）
        max_items: 最多返回几条建议

    Returns:
        建议列表，失败返回 None（静默）
    """
    # 截取 AI 回复避免 token 浪费
    reply_summary = ai_reply[:500] if len(ai_reply) > 500 else ai_reply

    user_prompt = (
        f"用户问题：{user_query}\n\n"
        f"AI 回复：{reply_summary}\n\n"
        f"请生成 {max_items} 条后续建议问题（JSON 数组）："
    )

    # 降级链：flash → plus → 放弃
    models = [
        settings.memory_filter_model,          # qwen3.5-flash
        settings.memory_filter_fallback_model,  # qwen3.5-plus
    ]

    for model in models:
        result = await _call_model(model, user_prompt, max_items)
        if result is not None:
            return result
        logger.warning(f"suggestion_generator | model={model} failed, trying next")

    logger.warning("suggestion_generator | all models failed, no suggestions")
    return None


async def _call_model(
    model: str,
    user_prompt: str,
    max_items: int,
) -> Optional[List[str]]:
    """调用单个模型生成建议，失败返回 None"""
    try:
        client = await _ds_client.get()
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 200,
            },
        )
        response.raise_for_status()
        data = response.json()

        text = data["choices"][0]["message"]["content"].strip()
        return _parse_suggestions(text, max_items)

    except Exception as e:
        logger.warning(f"suggestion_generator | model={model} error: {e}")
        return None


def _parse_suggestions(text: str, max_items: int) -> Optional[List[str]]:
    """解析 JSON 数组格式的建议列表"""
    # 提取 JSON 数组（LLM 可能包裹在 markdown code block 里）
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"suggestion_generator | JSON parse failed: {text[:100]}")
        return None

    if not isinstance(parsed, list):
        return None

    # 过滤：只保留非空字符串，截断到 max_items
    suggestions = [
        s.strip() for s in parsed
        if isinstance(s, str) and s.strip()
    ][:max_items]

    return suggestions if suggestions else None
