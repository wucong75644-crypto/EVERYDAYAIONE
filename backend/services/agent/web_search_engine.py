"""
Web Search Engine — Gemini Google Search Grounding

通过 KIE API 调用 Gemini + googleSearch 工具，获取带来源引用的搜索结果。
降级链：gemini-3-flash(15s) → 千问 enable_search(10s) → None
"""

from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

# Gemini 搜索配置
_SEARCH_MODEL = "gemini-3-flash"
_SEARCH_TIMEOUT = 30.0  # Google Search grounding 需要更长时间
_FALLBACK_TIMEOUT = 10.0
_KIE_BASE_URL = "https://api.kie.ai"
_CHAT_ENDPOINT = f"/{_SEARCH_MODEL}/v1/chat/completions"
# Google 搜索卡片 URL 前缀（KIE 响应中会附带，需要清理）
_GOOGLE_CARD_PREFIX = "http://googleusercontent.com/card_content/"


async def search_with_grounding(query: str) -> Optional[Dict[str, Any]]:
    """
    执行 Google Search Grounding 搜索。

    Returns:
        {
            "content": "Gemini 整合后的回答文本",
            "sources": [{"title": "...", "url": "..."}],
            "search_queries": ["实际搜索词1", ...]
        }
        失败返回 None
    """
    result = await _gemini_grounding_search(query)
    if result:
        return result

    # 降级：千问 enable_search
    logger.info(f"Gemini grounding failed, fallback to DashScope | query={query}")
    return await _dashscope_fallback_search(query)


async def _gemini_grounding_search(query: str) -> Optional[Dict[str, Any]]:
    """调用 Gemini + googleSearch 工具"""
    from core.config import get_settings

    settings = get_settings()
    if not settings.kie_api_key:
        logger.warning("web_search: kie_api_key not configured")
        return None

    request_body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个搜索助手。根据用户的查询搜索互联网，"
                    "返回准确、最新的信息。用中文回答，保持简洁。"
                    "在回答中明确标注信息来源。"
                ),
            },
            {"role": "user", "content": query},
        ],
        "stream": False,
        "tools": [{"type": "function", "function": {"name": "googleSearch"}}],
        "include_thoughts": False,
        "reasoning_effort": "low",
    }

    try:
        async with httpx.AsyncClient(
            base_url=_KIE_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.kie_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=_SEARCH_TIMEOUT, write=10.0, pool=5.0),
        ) as client:
            response = await client.post(_CHAT_ENDPOINT, json=request_body)

            if response.status_code != 200:
                logger.warning(
                    f"Gemini search HTTP error | status={response.status_code} | query={query}"
                )
                return None

            data = response.json()

            # KIE 可能返回 HTTP 200 但 body 包含错误码
            if "code" in data and data.get("code") != 200:
                logger.warning(f"Gemini search API error | code={data.get('code')} | query={query}")
                return None

            return _parse_gemini_response(data, query)

    except httpx.TimeoutException:
        logger.warning(f"Gemini search timeout | query={query}")
        return None
    except Exception as e:
        logger.warning(f"Gemini search failed | query={query} | error={type(e).__name__}: {e}")
        return None


def _parse_gemini_response(data: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    """解析 Gemini 响应，提取内容和 grounding 来源。

    KIE API 的 grounding 数据格式：
    - content: 搜索回答正文（可能以 googleusercontent.com/card_content/ 开头）
    - reasoning_content: JSON 数组，每项含 site_title / snippet / source_url
    """
    import json as _json

    choices = data.get("choices", [])
    if not choices:
        return None

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not content:
        return None

    # 清理 Google 搜索卡片 URL 前缀
    lines = content.split("\n", 1)
    if lines[0].startswith(_GOOGLE_CARD_PREFIX):
        content = lines[1] if len(lines) > 1 else ""
    if not content.strip():
        return None

    # 从 reasoning_content 提取搜索来源（KIE 把 grounding chunks 放在这里）
    sources: List[Dict[str, str]] = []
    reasoning = message.get("reasoning_content", "")
    if reasoning:
        try:
            chunks = _json.loads(reasoning) if reasoning.startswith("[") else []
            for chunk in chunks:
                if isinstance(chunk, dict):
                    url = chunk.get("source_url", "")
                    title = chunk.get("site_title", "")
                    if url:
                        sources.append({"title": title, "url": url})
        except (_json.JSONDecodeError, TypeError):
            # reasoning_content 不是 JSON 格式，可能是普通思考文本
            pass

    # 去重来源（同一 URL 可能出现多次）
    seen_urls: set = set()
    unique_sources: List[Dict[str, str]] = []
    for src in sources:
        if src["url"] not in seen_urls:
            seen_urls.add(src["url"])
            unique_sources.append(src)

    logger.info(
        f"Gemini search completed | query={query} | "
        f"content_len={len(content)} | sources={len(unique_sources)}"
    )
    return {
        "content": content.strip(),
        "sources": unique_sources,
        "search_queries": [query],
    }


async def _dashscope_fallback_search(query: str) -> Optional[Dict[str, Any]]:
    """降级：调用千问 enable_search"""
    from core.config import get_settings

    settings = get_settings()
    if not settings.dashscope_api_key:
        return None

    try:
        async with httpx.AsyncClient(
            base_url=settings.dashscope_base_url,
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=_FALLBACK_TIMEOUT, write=10.0, pool=5.0),
        ) as client:
            response = await client.post("/chat/completions", json={
                "model": settings.intent_router_model,
                "messages": [{"role": "user", "content": query}],
                "enable_search": True,
                "temperature": 0.3,
                "max_tokens": 2000,
            })
            response.raise_for_status()
            data = response.json()

            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content:
                    logger.info(f"DashScope fallback search completed | query={query}")
                    return {"content": content, "sources": [], "search_queries": [query]}

    except Exception as e:
        logger.warning(f"DashScope fallback search failed | query={query} | error={e}")

    return None
