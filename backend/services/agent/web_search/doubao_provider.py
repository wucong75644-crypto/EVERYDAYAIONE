"""Volcano Ark Responses API provider using the Doubao Search source."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from .contracts import SearchProviderError, SearchResponse, SearchSource

_INSTRUCTIONS = """你是一个严谨的网页资料搜索助手。围绕用户的完整任务检索公开网页：
先识别用户要求查清的事项、对象、地区和时间；必要时拆分搜索并核对直接来源。
用用户使用的语言作简洁回答。只把网页证据支持的事实写成结论；来源不足、页面冲突或无法确认的事项要明确标出，不要猜测，也不要把搜索不到说成事实不存在。"""
_MAX_SOURCES = 8
_MAX_TEXT_CHARS = 8_000
_MAX_URL_CHARS = 1_024


def _valid_public_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > _MAX_URL_CHARS:
        return ""
    normalized = value.strip()
    if any(ord(char) < 0x20 for char in normalized):
        return ""
    try:
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return ""
    except ValueError:
        return ""
    return normalized


def _response_parts(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str], int]:
    answer_parts: list[str] = []
    annotations: list[dict[str, Any]] = []
    queries: list[str] = []
    search_calls = 0
    answer_length = 0

    output = payload.get("output")
    if not isinstance(output, list):
        output = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            call_status = item.get("status")
            if call_status is not None and call_status not in {"completed", "succeeded"}:
                continue
            search_calls += 1
            action = item.get("action")
            if isinstance(action, dict):
                candidates = action.get("queries", [])
                if isinstance(candidates, list):
                    queries.extend(_clean(value, 500) for value in candidates if isinstance(value, str))
                query = action.get("query")
                if isinstance(query, str):
                    queries.append(_clean(query, 500))
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "output_text":
                continue
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            clean_text = text.strip()
            offset = answer_length + (2 if answer_parts else 0)
            citation_offset = offset - (len(text) - len(text.lstrip()))
            answer_parts.append(clean_text)
            answer_length += len(clean_text) + (2 if len(answer_parts) > 1 else 0)
            values = block.get("annotations")
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    adjusted = dict(value)
                    if type(adjusted.get("start_index")) is int:
                        adjusted["start_index"] += citation_offset
                    if type(adjusted.get("end_index")) is int:
                        adjusted["end_index"] += citation_offset
                    annotations.append(adjusted)

    # Some compatible gateways expose output_text at the response root.
    if not answer_parts and isinstance(payload.get("output_text"), str):
        answer_parts.append(payload["output_text"].strip())
    answer = "\n\n".join(answer_parts)[:_MAX_TEXT_CHARS]
    return answer, annotations, list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:8], search_calls


def _sources_from_annotations(
    annotations: list[dict[str, Any]], answer: str,
) -> tuple[tuple[SearchSource, ...], bool]:
    sources: list[SearchSource] = []
    by_url: dict[str, int] = {}
    truncated = False
    for annotation in annotations:
        if annotation.get("type") != "url_citation":
            continue
        url = _valid_public_url(annotation.get("url"))
        if not url:
            continue
        start = annotation.get("start_index")
        end = annotation.get("end_index")
        span = None
        if (type(start) is int and type(end) is int and
                0 <= start < end <= len(answer)):
            span = (start, end)

        existing = by_url.get(url)
        if existing is not None:
            old = sources[existing]
            spans = old.citation_spans + ((span,) if span and span not in old.citation_spans else ())
            sources[existing] = SearchSource(
                title=old.title or _clean(annotation.get("title"), 200),
                url=old.url,
                site_name=old.site_name or _clean(annotation.get("site_name"), 80),
                snippet=old.snippet or _clean(annotation.get("summary"), 300),
                published_at=old.published_at or _clean(annotation.get("publish_time"), 60),
                citation_spans=spans,
            )
            continue

        if len(sources) >= _MAX_SOURCES:
            truncated = True
            continue
        by_url[url] = len(sources)
        sources.append(SearchSource(
            title=_clean(annotation.get("title"), 200),
            url=url,
            site_name=_clean(annotation.get("site_name"), 80),
            snippet=_clean(annotation.get("summary"), 300),
            published_at=_clean(annotation.get("publish_time"), 60),
            citation_spans=(span,) if span else (),
        ))
    return tuple(sources), truncated


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _provider_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    error = error if isinstance(error, dict) else payload
    code = error.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", code):
        return None
    return code


def _http_error_message(response: httpx.Response) -> str:
    code = _provider_error_code(response)
    detail = f"HTTP {response.status_code}" + (f"，方舟错误码：{code}" if code else "")
    if response.status_code == 401:
        return f"方舟 API Key 鉴权失败，请核对 WEB_SEARCH_ARK_API_KEY（{detail}）"
    if response.status_code == 403:
        return (
            "方舟拒绝联网搜索访问，请在控制台的“开通管理 → 应用组件库 → 豆包搜索”"
            f"核对搜索服务开通状态及账号权限（{detail}）"
        )
    if response.status_code == 404:
        known_errors = {
            "PathNotFound": "方舟 Responses API 路径不存在，请核对 API 地址与版本",
            "ModelNotOpen": "方舟账号尚未开通当前模型，请在控制台开通模型服务",
            "InvalidEndpointOrModel.ModelIDAccessDisabled": (
                "当前方舟账号不能通过模型 ID 调用，请改用有权限的推理接入点 ID"
            ),
            "InvalidEndpointOrModel.NotFound": (
                "方舟模型或推理接入点不存在，或当前账号无权访问"
            ),
        }
        if code in known_errors:
            return f"{known_errors[code]}（{code}）"
        if code:
            return f"豆包搜索请求被拒绝（HTTP 404，方舟错误码：{code}）"
    return f"豆包搜索请求被拒绝（HTTP {response.status_code}）"


class DoubaoSearchProvider:
    """One bounded Responses API request; Ark owns the internal web-search loop."""

    name = "volcano_ark_doubao"

    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def search(self, query: str, *, timeout: float) -> SearchResponse:
        if not self.api_key:
            raise SearchProviderError("豆包搜索未配置方舟搜索 API Key")
        if not query.strip() or len(query) > 4000:
            raise SearchProviderError("搜索内容无效或过长")

        request = {
            "model": self.model,
            "instructions": _INSTRUCTIONS,
            "input": query,
            "tools": [{"type": "web_search", "sources": ["doubao"]}],
            "store": False,
            "max_output_tokens": 2500,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            ) as client:
                response = await client.post("/responses", json=request)
        except httpx.TimeoutException as error:
            raise SearchProviderError("豆包搜索请求超时", retryable=True, status="timeout") from error
        except httpx.RequestError as error:
            raise SearchProviderError("豆包搜索服务暂时无法连接", retryable=True) from error

        if response.status_code == 429:
            raise SearchProviderError("豆包搜索请求过于频繁或额度不足", retryable=True)
        if response.status_code >= 500:
            raise SearchProviderError("豆包搜索服务暂时不可用", retryable=True)
        if response.status_code >= 400:
            raise SearchProviderError(_http_error_message(response))
        try:
            payload = response.json()
        except ValueError as error:
            raise SearchProviderError("豆包搜索返回了无法识别的响应") from error
        if not isinstance(payload, dict):
            raise SearchProviderError("豆包搜索返回了无法识别的响应")

        if payload.get("error"):
            raise SearchProviderError("豆包搜索服务返回错误")
        response_incomplete = payload.get("status") == "incomplete"
        if payload.get("status") in {"failed", "cancelled"}:
            raise SearchProviderError("豆包搜索未能完成本次请求", retryable=True)

        answer, annotations, queries, search_calls = _response_parts(payload)
        sources, sources_truncated = _sources_from_annotations(annotations, answer)
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        tool_usage = usage.get("tool_usage")
        reported_calls = tool_usage.get("web_search") if isinstance(tool_usage, dict) else None
        metered_calls = max(search_calls, reported_calls) if type(reported_calls) is int else search_calls
        if not answer and not sources:
            if response_incomplete:
                raise SearchProviderError("豆包搜索请求未完成，且没有可交付的部分结果", retryable=True)
            if search_calls:
                return SearchResponse(
                    answer="", sources=(), search_queries=tuple(queries), provider=self.name,
                    search_requests=metered_calls, status="empty", status_reason="no_results",
                    usage=usage,
                )
            raise SearchProviderError("豆包搜索没有返回可用内容", retryable=True)

        spans_supported = sources and all(source.citation_spans for source in sources)
        status = "success" if spans_supported and not sources_truncated and not response_incomplete else "partial"
        reason = (
            "response_incomplete" if response_incomplete else
            "source_limit" if sources_truncated else
            "" if status == "success" else
            "sources_unmapped" if sources else "no_citations"
        )
        return SearchResponse(
            answer=answer,
            sources=sources,
            search_queries=tuple(queries),
            provider=self.name,
            search_requests=metered_calls,
            status=status,
            status_reason=reason,
            sources_truncated=sources_truncated,
            usage=usage,
        )
