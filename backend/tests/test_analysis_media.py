"""Actual workspace bytes, media HTTP boundaries and the unchanged chat path."""
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from PIL import Image

from core.config import get_settings
from schemas.ecom_requirement import RequirementSettings
from services.agent.image.analysis_media import AnalysisMediaError, _gate, prepare_analysis_media
from services.agent.image.input_adapters import DetailProjectRequirementAdapter
from services.agent.image.requirement_assist_service import RequirementAssistService
from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter, DashScopeAPIError
from services.adapters.kie.client import KieClient
from services.detail_page_generation import PageImageInputResolver, page_owner
from services.model_gateway import ModelGateway


@pytest.fixture
def images(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "file_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "detail_kimi_inline_images", True)
    monkeypatch.setattr(settings, "detail_kimi_json_output", True)
    def create(count=2, size=(32, 32), image_format="PNG"):
        resolver = PageImageInputResolver(page_owner(None, "user-1", "org-1", "project-1"))
        root = Path(resolver.files.files.workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        rows = []
        for position in range(count):
            path = root / f"{position}.{image_format.lower()}"
            Image.new("RGB", size, (position * 10 % 255, 50, 100)).save(path, format=image_format)
            rows.append({"id": f"image-{position}", "workspace_path": path.name,
                "original_url": f"https://cdn.invalid/{path.name}",
                "category": "reference" if position % 2 else "product"})
        return resolver, resolver.bind(rows), rows
    return create


def preparation(refs, resolver, **kwargs):
    return prepare_analysis_media(refs, resolver, model=kwargs.pop("model", "kimi-k3"),
        transport=kwargs.pop("transport", "inline"), deadline=kwargs.pop("deadline", time.monotonic() + 10), **kwargs)


@pytest.mark.parametrize("count", [1, 9])
async def test_inline_preserves_exact_original_bytes_roles_order_and_binding(images, count):
    resolver, refs, _ = images(count)
    original = json.dumps(refs, sort_keys=True)
    async with preparation(refs, resolver) as urls:
        assert len(urls) == count and _gate().used > 0
        for ref, url in zip(refs, urls):
            assert url.startswith("data:image/png;base64,")
            data = base64.b64decode(url.split(",", 1)[1], validate=True)
            assert hashlib.sha256(data).hexdigest() == ref["content_sha256"]
            assert data == resolver._target(ref).path.read_bytes()
    assert _gate().used == 0 and urls == []
    assert json.dumps(refs, sort_keys=True) == original


@pytest.mark.parametrize("size,fmt,code", [
    ((10, 32), "PNG", "ANALYSIS_IMAGE_DIMENSIONS"),
    ((2211, 11), "PNG", "ANALYSIS_IMAGE_DIMENSIONS"),
    ((3840, 32), "WEBP", "ANALYSIS_IMAGE_FORMAT"),
    ((32, 32), "BMP", "ANALYSIS_IMAGE_FORMAT"),
])
async def test_model_geometry_and_format_fail_before_provider_calls(images, size, fmt, code):
    resolver, refs, _ = images(1, size, fmt)
    with pytest.raises(AnalysisMediaError) as captured:
        async with preparation(refs, resolver):
            pytest.fail("invalid image must not reach a model")
    assert captured.value.code == code and _gate().used == 0


@pytest.mark.parametrize("size,code", [
    (7_500_001, "ANALYSIS_IMAGE_ENCODING_TOO_LARGE"),
    (10 * 1024 * 1024 + 1, "ANALYSIS_IMAGE_TOO_LARGE"),
])
async def test_encoded_size_is_checked_before_allocating_or_reading(images, size, code):
    resolver, refs, _ = images(1)
    refs[0]["file_version"][1] = size
    resolver._target = Mock(side_effect=AssertionError("preflight must reject first"))
    with pytest.raises(AnalysisMediaError) as captured:
        async with preparation(refs, resolver):
            pass
    assert captured.value.code == code
    resolver._target.assert_not_called()


@pytest.mark.parametrize("change", ["bytes", "digest", "path", "identity"])
async def test_changed_or_out_of_scope_source_is_not_replaced_by_cdn(images, change):
    resolver, refs, _ = images(1)
    if change == "bytes":
        resolver._target(refs[0]).path.write_bytes(b"changed")
    elif change == "digest":
        refs[0]["content_sha256"] = "0" * 64
    elif change == "path":
        refs[0]["workspace_path"] = "../other-user/product.png"
    else:
        refs[0]["file_id"] = "fid_other"
    with pytest.raises(AnalysisMediaError):
        async with preparation(refs, resolver):
            pass
    assert _gate().used == 0


async def test_frozen_reference_cannot_read_another_owner_workspace(images):
    resolver, refs, _ = images(1)
    other = PageImageInputResolver(page_owner(None, 'other-user', 'other-org', 'project-1'))
    with pytest.raises(AnalysisMediaError):
        async with preparation(refs, other):
            pytest.fail('references cannot grant access to another workspace')
    assert _gate().used == 0


def test_invalid_file_at_binding_returns_actionable_error(images):
    resolver, refs, rows = images(1)
    resolver._target(refs[0]).path.write_bytes(b'not an image')
    with pytest.raises(AnalysisMediaError) as captured:
        resolver.bind(rows)
    assert captured.value.code == 'ANALYSIS_IMAGE_INVALID'
    assert '图片1' in captured.value.message


def test_oversized_file_at_binding_is_rejected_before_decoding(images, monkeypatch):
    resolver, refs, rows = images(1)
    path = resolver._target(refs[0]).path
    with path.open('r+b') as stream:
        stream.truncate(10 * 1024 * 1024 + 1)
    decode = Mock(side_effect=AssertionError('oversized bytes must not be decoded'))
    monkeypatch.setattr('services.handlers.image_dimensions.read_image_dimensions',decode)
    with pytest.raises(AnalysisMediaError) as captured:
        resolver.bind(rows)
    assert captured.value.code == 'ANALYSIS_IMAGE_TOO_LARGE'
    decode.assert_not_called()


async def test_memory_wait_obeys_deadline_and_cancel_releases_allowance(images):
    resolver, refs, _ = images(1)
    gate = _gate()
    async with gate.hold(384 * 1024 * 1024, 384 * 1024 * 1024):
        with pytest.raises(AnalysisMediaError) as captured:
            async with preparation(refs, resolver, deadline=time.monotonic() + .02):
                pass
        assert captured.value.code == 'ANALYSIS_MEDIA_TIMEOUT'
    entered = asyncio.Event()
    async def pending():
        async with preparation(refs, resolver):
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(pending())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.used == 0


async def test_preparation_deadline_does_not_replace_model_stage_budget(images):
    resolver, refs, _ = images(1)
    async with preparation(refs, resolver, deadline=time.monotonic() + .2):
        await asyncio.sleep(.21)
    assert _gate().used == 0


async def test_cancelled_disk_read_keeps_allowance_until_thread_finishes(images, monkeypatch):
    import threading
    from services.agent.image import analysis_media
    resolver, refs, _ = images(1)
    started, release = threading.Event(), threading.Event()
    original = analysis_media._read_image
    def read(*args):
        started.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(analysis_media, '_read_image', read)
    async def prepare():
        async with preparation(refs, resolver):
            pytest.fail('cancelled preparation must not yield model media')
    task = asyncio.create_task(prepare())
    await asyncio.to_thread(started.wait, 3)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done() and _gate().used > 0
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _gate().used == 0


async def test_gemini_reuses_multipart_upload_with_explicit_overseas_route(images, monkeypatch):
    resolver, refs, _ = images(3)
    original = json.dumps(refs)
    monkeypatch.setenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV, "http://127.0.0.1:7891")
    real_client = httpx.AsyncClient
    options, sent = [], []
    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={"code": 200, "success": True,
            "data": {"downloadUrl": f"https://kie.invalid/temporary/{len(sent)}.png"}})
    def factory(**kwargs):
        options.append(dict(kwargs))
        kwargs.pop("proxy")
        return real_client(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr("services.agent.image.analysis_media.httpx.AsyncClient", factory)
    async with preparation(refs, resolver, model="gemini-3.8-flash", transport="kie_upload", api_key="platform-test") as urls:
        assert urls == [f"https://kie.invalid/temporary/{i}.png" for i in (1, 2, 3)]
        assert options[0]["proxy"].endswith(":7891") and options[0]["trust_env"] is False
        for request, ref in zip(sent, refs):
            assert str(request.url) == KieClient.FILE_STREAM_UPLOAD_ENDPOINT
            assert request.headers["authorization"] == "Bearer platform-test"
            assert resolver._target(ref).path.read_bytes() in request.content
    assert json.dumps(refs) == original and _gate().used == 0


async def test_upload_failure_never_yields_partial_group_or_falls_back(images, monkeypatch):
    resolver, refs, _ = images(3)
    monkeypatch.setenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV, "http://127.0.0.1:7891")
    calls = []
    async def upload(*args):
        calls.append(args)
        if len(calls) == 2:
            raise httpx.ReadTimeout("failed upload")
        return "https://kie.invalid/first.png"
    monkeypatch.setattr(KieClient, "upload_image_bytes", upload)
    with pytest.raises(AnalysisMediaError) as captured:
        async with preparation(refs, resolver, model="gemini-3.8-flash", transport="kie_upload", api_key="test"):
            pytest.fail("a partial group cannot reach a model")
    assert captured.value.code == "ANALYSIS_IMAGE_UPLOAD_FAILED"
    assert len(calls) == 2 and _gate().used == 0


async def test_missing_overseas_route_is_configuration_error(images, monkeypatch):
    resolver, refs, _ = images(1)
    monkeypatch.delenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV, raising=False)
    with pytest.raises(AnalysisMediaError) as captured:
        async with preparation(refs, resolver, model="gemini-3.8-flash", transport="kie_upload", api_key="test"):
            pass
    assert captured.value.code == "ANALYSIS_UPLOAD_CONFIG"


async def test_real_helper_gateway_receives_inline_bytes_and_json_mode_on_bounded_retry(images, monkeypatch):
    resolver, refs, rows = images(2)
    project = {"version": 1, "images": rows}
    adapter = DetailProjectRequirementAdapter(SimpleNamespace(db=None, get_ai_input_project=lambda _: project), "user-1", "org-1")
    data = adapter.adapt("project-1", RequirementSettings(requirement="  发财的感觉\n保留产品细节  "))
    captured = []
    payload = {"product_description": "红色存钱本", "selling_points": [], "creative_requirements": [], "supplement_questions": []}
    def respond(request):
        body = json.loads(request.content)
        captured.append(body)
        if len(captured) == 1:
            return httpx.Response(400, json={"error": {"code": "invalid_parameter_error",
                "message": "<400> InternalError.Algo.InvalidParameter: Download multimodal file timed out"}})
        chunk = {"choices": [{"delta": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
    def factory(model, **kwargs):
        result = DashScopeChatAdapter("test", model)
        result._client = httpx.AsyncClient(base_url="https://model.invalid", transport=httpx.MockTransport(respond))
        return result
    monkeypatch.setattr("services.model_gateway.get_model_gateway", lambda: ModelGateway(adapter_factory=factory))
    result = await RequirementAssistService(image_resolver=adapter.image_resolver).generate(data)
    assert result.result.product_description == "红色存钱本" and len(captured) == 2
    assert captured[0] == captured[1]
    assert captured[1]["response_format"] == {"type": "json_object"}
    content = captured[1]["messages"][1]["content"]
    urls = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
    assert [hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest() for url in urls] == [ref["content_sha256"] for ref in refs]
    assert "参考图 id=image-1" in content[3]["text"]
    assert json.loads(content[0]["text"])["用户需求原文"] == data.user_requirement


@pytest.mark.parametrize("stream", [True, False])
async def test_kimi_sync_and_stream_share_normalization_and_capabilities(stream):
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n\n") if stream else httpx.Response(200,
            json={"choices": [{"message": {"content": "{}"}}]})
    adapter = DashScopeChatAdapter("test", "kimi-k3")
    adapter._client = httpx.AsyncClient(base_url="https://model.invalid", transport=httpx.MockTransport(respond))
    messages = [{"role": "developer", "content": "JSON"}, {"role": "user", "content": [
        {"type": "input_text", "text": "JSON"}, {"type": "input_image", "image_url": "https://cdn.invalid/original.png"}]}]
    async def call(**kwargs):
        if stream:
            async for _ in adapter.stream_chat(messages, **kwargs):
                pass
        else:
            await adapter.chat_sync(messages, **kwargs)
    try:
        await call(reasoning_effort="high", response_format={"type": "json_object"})
        assert sent[0]["enable_thinking"] is True and sent[0]["reasoning_effort"] == "high"
        assert sent[0]["messages"][0]["role"] == "system"
        assert sent[0]["messages"][1]["content"][1] == {"type": "image_url", "image_url": {"url": "https://cdn.invalid/original.png"}}
        for kwargs in ({"reasoning_effort": "medium"}, {"enable_search": True}, {"response_format": {"type": "json_schema"}}):
            with pytest.raises(ValueError):
                await call(**kwargs)
        assert len(sent) == 1
    finally:
        await adapter.close()


def test_qwen_chat_keeps_cdn_transport_and_existing_thinking_policy():
    adapter = DashScopeChatAdapter("test", "qwen3.5-plus")
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://cdn.invalid/original.png"}}]}]
    body = adapter._request_body(messages, stream=True, reasoning_effort="medium", thinking_mode="disabled")
    assert body["messages"] == messages and body["enable_thinking"] is False
    assert adapter._transport_options(body) == {}


def test_inline_media_is_redacted_from_diagnostics():
    from services.kie_image_fallback_request import safe_error
    assert safe_error(ValueError("download data:image/png;base64,c2VjcmV0 token=hidden")) == "download [inline-media] token=[redacted]"
