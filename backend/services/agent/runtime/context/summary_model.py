"""Context Runtime 私有的摘要模型调用边界。"""
from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger

from services.dashscope_client import DashScopeClient


_client = DashScopeClient("context_summary_timeout")
_DEFAULT_PROMPT = "压缩给定上下文，只保留明确事实、约束、决定和未完成事项。"


async def call_summary_model(
    model: str,
    source: str,
    *,
    system_prompt: Optional[str] = None,
    max_chars: int = 2_000,
) -> Optional[str]:
    """调用单个摘要模型；超时或响应异常时返回 None。"""
    client = await _client.get()
    try:
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt or _DEFAULT_PROMPT},
                    {"role": "user", "content": source},
                ],
                "temperature": 0.1,
                "max_tokens": max_chars * 2,
                "enable_thinking": False,
            },
        )
        response.raise_for_status()
        summary = response.json()["choices"][0]["message"]["content"].strip()
        return summary[:max_chars]
    except httpx.TimeoutException:
        logger.warning(f"Context compaction timeout | model={model}")
    except Exception as error:
        logger.warning(
            "Context compaction failed | "
            f"model={model} | error_type={type(error).__name__}"
        )
    return None


async def close_summary_model() -> None:
    """关闭 Context Runtime 摘要 HTTP 客户端。"""
    await _client.close()
