from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.agent.web_search.contracts import SearchProviderError, SearchResponse
from services.agent.web_search.doubao_provider import DoubaoSearchProvider
from services.agent.web_search.service import search_web


def _response(payload: dict, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _http_client(response):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post.return_value = response
    return client


@pytest.mark.asyncio
async def test_doubao_provider_requests_search_and_preserves_citation_evidence():
    answer = "规则从 2026 年 1 月起生效。"
    payload = {
        "status": "completed",
        "output": [
            {"type": "web_search_call", "action": {"queries": ["官方规则"], "query": "规则 生效"}},
            {"type": "message", "content": [{
                "type": "output_text", "text": answer,
                "annotations": [
                    {"type": "url_citation", "start_index": 0, "end_index": len(answer),
                     "title": "官方公告", "site_name": "政府网站",
                     "url": "https://example.gov.cn/rule",
                     "summary": "规则从 2026 年 1 月起生效。", "publish_time": "2026-01-02"},
                    {"type": "url_citation", "start_index": 0, "end_index": len(answer),
                     "title": "重复引用", "url": "https://example.gov.cn/rule"},
                    {"type": "url_citation", "title": "不允许", "url": "javascript:alert(1)"},
                ],
            }]},
        ],
        "usage": {"input_tokens": 50, "output_tokens": 80,
                  "tool_usage": {"web_search": 1, "mcp": 0}},
    }
    client = _http_client(_response(payload))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")

    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client) as factory:
        result = await provider.search("完整问题和条件", timeout=9.0)

    kwargs = factory.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["timeout"].read == 9.0
    request = client.post.await_args.kwargs["json"]
    assert request["tools"] == [{"type": "web_search", "sources": ["doubao"]}]
    assert request["store"] is False
    assert request["input"] == "完整问题和条件"
    assert request["model"] == "fixture"
    assert result.status == "success"
    assert result.search_requests == 1
    assert result.search_queries == ("官方规则", "规则 生效")
    assert len(result.sources) == 1
    assert result.sources[0].citation_spans == ((0, len(answer)),)
    assert result.sources[0].snippet == "规则从 2026 年 1 月起生效。"
    assert result.sources[0].published_at == "2026-01-02"
    assert result.usage["tool_usage"]["web_search"] == 1


@pytest.mark.asyncio
async def test_citation_offsets_are_adjusted_across_multiple_output_blocks():
    first, second = "第一句。", "第二句。"
    client = _http_client(_response({
        "status": "completed",
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": first, "annotations": [{
                "type": "url_citation", "start_index": 0, "end_index": len(first),
                "title": "来源一", "url": "https://example.com/one",
            }]},
            {"type": "output_text", "text": second, "annotations": [{
                "type": "url_citation", "start_index": 0, "end_index": len(second),
                "title": "来源二", "url": "https://example.com/two",
            }]},
        ]}],
    }))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        result = await provider.search("查询", timeout=9.0)
    assert result.answer == f"{first}\n\n{second}"
    assert result.sources[0].citation_spans == ((0, len(first)),)
    assert result.sources[1].citation_spans == ((len(first) + 2, len(first) + 2 + len(second)),)


@pytest.mark.asyncio
async def test_doubao_answer_without_source_mapping_is_partial():
    client = _http_client(_response({
        "status": "completed",
        "output": [{"type": "message", "content":[{
            "type": "output_text", "text": "一个没有网页引证的摘要。", "annotations": [],
        }]}],
    }))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        result = await provider.search("查询", timeout=9.0)
    assert result.status == "partial"
    assert result.status_reason == "no_citations"
    assert not result.sources


@pytest.mark.asyncio
async def test_successful_search_with_no_usable_result_is_empty():
    client = _http_client(_response({
        "status": "completed",
        "output": [{"type": "web_search_call", "action": {"queries": ["rare"]}}],
    }))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        result = await provider.search("rare", timeout=9.0)
    assert result.status == "empty"
    assert result.search_requests == 1


@pytest.mark.asyncio
async def test_failed_search_call_is_not_misreported_as_empty_even_if_usage_counts_it():
    client = _http_client(_response({
        "status": "completed",
        "output": [{"type": "web_search_call", "status": "failed",
                    "action": {"queries": ["rare"]}}],
        "usage": {"tool_usage": {"web_search": 1}},
    }))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(SearchProviderError, match="没有返回可用内容"):
            await provider.search("rare", timeout=9.0)


@pytest.mark.asyncio
async def test_source_limit_is_reported_as_partial_instead_of_silently_dropping_citations():
    answer = "多来源结论。"
    annotations = [
        {"type": "url_citation", "start_index": 0, "end_index": len(answer),
         "title": f"来源 {index}", "url": f"https://example.com/{index}"}
        for index in range(9)
    ]
    client = _http_client(_response({
        "status": "completed",
        "output": [{"type": "message", "content":[{
            "type": "output_text", "text": answer, "annotations": annotations,
        }]}],
    }))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        result = await provider.search("查询", timeout=9.0)
    assert len(result.sources) == 8
    assert result.status == "partial"
    assert result.status_reason == "source_limit"
    assert result.sources_truncated


@pytest.mark.asyncio
async def test_incomplete_response_keeps_deliverable_evidence_as_partial():
    answer = "已核实的部分结论。"
    client = _http_client(_response({
        "status": "incomplete",
        "output": [{"type": "message", "content":[{
            "type": "output_text", "text": answer, "annotations": [{
                "type": "url_citation", "start_index": 0, "end_index": len(answer),
                "title": "来源", "url": "https://example.com/source",
            }],
        }]}],
    }))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        result = await provider.search("查询", timeout=9.0)
    assert result.answer == answer
    assert result.sources
    assert result.status == "partial"
    assert result.status_reason == "response_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "expected"), [
    (401, "方舟凭证无效"),
    (403, "豆包搜索服务尚未开通"),
    (429, "额度不足"),
    (503, "暂时不可用"),
])
async def test_provider_http_failures_are_explicit_and_never_claim_empty(status_code, expected):
    client = _http_client(_response({}, status_code))
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(SearchProviderError, match=expected):
            await provider.search("query", timeout=9.0)


@pytest.mark.asyncio
async def test_provider_timeout_keeps_timeout_semantics():
    client = _http_client(None)
    client.post.side_effect = httpx.ReadTimeout("fixture")
    provider = DoubaoSearchProvider(api_key="secret", base_url="https://ark.example/api/v3", model="fixture")
    with patch("services.agent.web_search.doubao_provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(SearchProviderError) as caught:
            await provider.search("query", timeout=2.0)
    assert caught.value.status == "timeout"
    assert caught.value.retryable


@pytest.mark.asyncio
async def test_auto_provider_selects_doubao_when_configured_and_does_not_double_search_on_failure():
    settings = SimpleNamespace(
        web_search_provider="auto", web_search_ark_api_key="configured",
        web_search_ark_base_url="https://ark.example/api/v3", web_search_ark_model="fixture",
    )
    provider = MagicMock()
    provider.search = AsyncMock(side_effect=SearchProviderError("quota exhausted"))
    legacy = AsyncMock()
    with patch("core.config.get_settings", return_value=settings), \
         patch("services.agent.web_search.service.DoubaoSearchProvider", return_value=provider), \
         patch("services.agent.web_search_engine.search_with_grounding", legacy):
        with pytest.raises(SearchProviderError, match="quota exhausted"):
            await search_web("query", timeout=8.0)
    provider.search.assert_awaited_once()
    assert provider.search.await_args.args == ("query",)
    assert 0 < provider.search.await_args.kwargs["timeout"] <= 8.0
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_provider_keeps_legacy_path_when_no_ark_key_is_configured():
    settings = SimpleNamespace(
        web_search_provider="auto", web_search_ark_api_key=None,
        web_search_ark_base_url="https://ark.example/api/v3", web_search_ark_model="fixture",
        kie_api_key="configured", dashscope_api_key=None,
    )
    legacy = AsyncMock(return_value={"content": "summary", "sources": [], "search_queries": ["q"]})
    with patch("core.config.get_settings", return_value=settings), \
         patch("services.agent.web_search_engine.search_with_grounding", legacy):
        result = await search_web("query", timeout=8.0)
    assert result.status == "partial"
    assert result.provider == "legacy_grounding"
    legacy.assert_awaited_once_with("query")


@pytest.mark.asyncio
async def test_explicit_doubao_mode_without_key_fails_before_any_provider_call():
    settings = SimpleNamespace(
        web_search_provider="doubao", web_search_ark_api_key=None,
        web_search_ark_base_url="https://ark.example/api/v3", web_search_ark_model="fixture",
    )
    legacy = AsyncMock()
    with patch("core.config.get_settings", return_value=settings), \
         patch("services.agent.web_search_engine.search_with_grounding", legacy):
        with pytest.raises(SearchProviderError, match="WEB_SEARCH_ARK_API_KEY"):
            await search_web("query", timeout=8.0)
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_ambiguous_none_is_error_not_empty():
    settings = SimpleNamespace(
        web_search_provider="legacy", web_search_ark_api_key=None,
        web_search_ark_base_url="https://ark.example/api/v3", web_search_ark_model="fixture",
        kie_api_key="configured", dashscope_api_key=None,
    )
    legacy = AsyncMock(return_value=None)
    with patch("core.config.get_settings", return_value=settings), \
         patch("services.agent.web_search_engine.search_with_grounding", legacy):
        with pytest.raises(SearchProviderError, match="没有返回可用结果"):
            await search_web("query", timeout=8.0)


@pytest.mark.asyncio
async def test_legacy_without_credentials_reports_configuration_error_without_request():
    settings = SimpleNamespace(
        web_search_provider="legacy", web_search_ark_api_key=None,
        web_search_ark_base_url="https://ark.example/api/v3", web_search_ark_model="fixture",
        kie_api_key=None, dashscope_api_key=None,
    )
    with patch("core.config.get_settings", return_value=settings), \
         patch("services.agent.web_search_engine.search_with_grounding", new_callable=AsyncMock) as legacy:
        with pytest.raises(SearchProviderError, match="未配置可用的网页搜索凭证"):
            await search_web("query", timeout=8.0)
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_provider_concurrency_is_capped_per_process():
    settings = SimpleNamespace(
        web_search_provider="auto", web_search_ark_api_key="configured",
        web_search_ark_base_url="https://ark.example/api/v3", web_search_ark_model="fixture",
    )
    active = 0
    peak = 0

    class SlowProvider:
        async def search(self, query: str, *, timeout: float):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return SearchResponse(answer=query, status="partial")

    provider = SlowProvider()
    with patch("core.config.get_settings", return_value=settings), \
         patch("services.agent.web_search.service.DoubaoSearchProvider", return_value=provider):
        await asyncio.gather(*(search_web(f"query-{index}", timeout=2.0) for index in range(8)))
    assert peak == 3
