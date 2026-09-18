"""首次生成的海外临时链接优先与整组 CDN 降级；外部边界全部模拟。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.adapters.kie.client import KieAPIError, KieClient
from services.adapters.kie.models import CreateTaskRequest
from services.kie_image_fallback_request import replay_request


MODEL = "gpt-image-2-image-to-image"
SOURCES = ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"]
STAGED = ["https://tempfile.redpandaai.co/a.png", "https://tempfile.redpandaai.co/b.png"]


def request(input_key="input_urls", model=MODEL):
    return CreateTaskRequest(
        model=model,
        input={input_key: [SOURCES[1], SOURCES[0], SOURCES[1]],
               "prompt": "keep all parameters", "resolution": "4K", "aspect_ratio": "16:9",
               "output_format": "png", "extra_parameter": {"values": [1, 2]}},
        callBackUrl="https://app.example.com/callback",
    )


@pytest.fixture
def setup(monkeypatch):
    client = KieClient("test-key")
    upload = AsyncMock(return_value=[STAGED[1], STAGED[0]])
    post = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {
        "code": 200, "msg": "success", "data": {"taskId": "accepted-kie"},
    }))
    ready, failed = AsyncMock(return_value=True), AsyncMock(return_value=True)
    monkeypatch.setenv(client.SHADOW_OVERSEAS_PROXY_ENV, "http://127.0.0.1:7891")
    monkeypatch.setattr(client, "_run_shadow_upload", upload)
    monkeypatch.setattr(client, "_get_client", AsyncMock(return_value=MagicMock(post=post)))
    monkeypatch.setattr("services.adapters.kie.client.save_overseas_shadow_upload_urls", ready)
    monkeypatch.setattr("services.adapters.kie.client.save_overseas_shadow_upload_status", failed)
    return SimpleNamespace(client=client, upload=upload, post=post, ready=ready, failed=failed)


@pytest.mark.parametrize("input_key", KieClient.SHADOW_IMAGE_INPUT_KEYS)
async def test_first_request_and_400_replay_use_identical_order_and_parameters(setup, input_key):
    original = request(input_key)
    before = original.model_dump(exclude_none=True)
    await setup.client.create_task(original)
    sent = setup.post.await_args.kwargs["json"]
    expected = original.model_dump(exclude_none=True)
    expected["input"][input_key] = [STAGED[1], STAGED[0], STAGED[1]]
    assert sent == expected and original.model_dump(exclude_none=True) == before
    setup.upload.assert_awaited_once()
    assert setup.upload.await_args.kwargs["source_urls"] == [SOURCES[1], SOURCES[0]]
    saved = setup.ready.await_args
    assert saved.args == ("accepted-kie", [SOURCES[1], SOURCES[0]], [STAGED[1], STAGED[0]])
    cache = {"source_urls": saved.args[1], "staged_urls": saved.args[2], "request": saved.kwargs["request"]}
    assert replay_request({"model_id": MODEL}, cache, original.callBackUrl).model_dump(exclude_none=True) == sent
    assert "callBackUrl" not in cache["request"]
    setup.failed.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["failed", "partial", "invalid_url", "exception", "timeout"])
async def test_upload_failure_uses_entire_cdn_request_once(setup, monkeypatch, outcome):
    original = request()
    cancelled = asyncio.Event()
    if outcome == "exception":
        setup.upload.side_effect = RuntimeError("upload initialization failed")
    elif outcome == "timeout":
        monkeypatch.setattr(setup.client, "SHADOW_PREPARE_TIMEOUT", 0.01)
        async def hang(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        setup.upload.side_effect = hang
    else:
        setup.upload.return_value = {"failed": None, "partial": [STAGED[1]],
                                     "invalid_url": [STAGED[1], "not-a-url"]}[outcome]
    result = await setup.client.create_task(original)
    assert result.task_id == "accepted-kie"
    setup.post.assert_awaited_once()
    assert setup.post.await_args.kwargs["json"] == original.model_dump(exclude_none=True)
    setup.upload.assert_awaited_once()
    setup.ready.assert_not_awaited()
    setup.failed.assert_awaited_once_with(
        "accepted-kie", "failed", [SOURCES[1], SOURCES[0]],
        original.model_dump(mode="json", exclude={"callBackUrl"}),
    )
    if outcome == "timeout":
        assert cancelled.is_set(), "timed-out upload must not continue as another background upload"


@pytest.mark.parametrize("kind", ["text", "video"])
async def test_text_and_video_requests_skip_image_preparation(setup, kind):
    original = request(model="sora-2-image-to-video") if kind == "video" else CreateTaskRequest(
        model="gpt-image-2-text-to-image", input={"prompt": "no reference image"},
    )
    await setup.client.create_task(original)
    setup.upload.assert_not_awaited()
    setup.ready.assert_not_awaited()
    setup.failed.assert_not_awaited()
    assert setup.post.await_args.kwargs["json"] == original.model_dump(exclude_none=True)


async def test_primary_network_retry_does_not_repeat_upload(setup, monkeypatch):
    setup.post.side_effect = [httpx.ConnectError("temporary connection failure"), setup.post.return_value]
    monkeypatch.setattr(setup.client._create_task_with_retry.retry, "wait", lambda _: 0)
    await setup.client.create_task(request())
    setup.upload.assert_awaited_once()
    assert setup.post.await_count == 2
    assert setup.post.await_args_list[0] == setup.post.await_args_list[1]
    setup.ready.assert_awaited_once()


@pytest.mark.parametrize("accepted", [False, True])
async def test_unavailable_cache_does_not_change_generation_response(setup, accepted):
    setup.upload.return_value = [STAGED[1], STAGED[0]] if accepted else None
    setup.ready.return_value = setup.failed.return_value = False
    result = await setup.client.create_task(request())
    assert result.task_id == "accepted-kie"
    setup.post.assert_awaited_once()
    assert setup.post.await_args.kwargs["json"]["input"]["input_urls"] == (
        [STAGED[1], STAGED[0], STAGED[1]] if accepted else [SOURCES[1], SOURCES[0], SOURCES[1]]
    )


@pytest.mark.parametrize("outcome", ["rejected", "missing_task_id"])
async def test_no_task_cache_when_creation_not_accepted(setup, outcome):
    if outcome == "rejected":
        setup.post.return_value = MagicMock(status_code=400, json=lambda: {"code": 400, "msg": "invalid input"})
        with pytest.raises(KieAPIError):
            await setup.client.create_task(request())
    else:
        setup.post.return_value = MagicMock(status_code=200, json=lambda: {"code": 200, "msg": "success", "data": {}})
        assert (await setup.client.create_task(request())).task_id is None
    setup.upload.assert_awaited_once()
    setup.post.assert_awaited_once()
    setup.ready.assert_not_awaited()
    setup.failed.assert_not_awaited()


async def test_caller_cancellation_does_not_submit_a_cdn_task(setup):
    started = asyncio.Event()
    async def hang(**kwargs):
        started.set()
        await asyncio.Event().wait()
    setup.upload.side_effect = hang
    pending = asyncio.create_task(setup.client.create_task(request()))
    await asyncio.wait_for(started.wait(), timeout=1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    setup.post.assert_not_awaited()
    setup.ready.assert_not_awaited()
    setup.failed.assert_not_awaited()


async def test_concurrent_creates_keep_each_upload_and_cache_mapping_separate(setup):
    async def upload(**kwargs):
        await asyncio.sleep(0)
        return [url.replace("cdn.example.com", "tempfile.redpandaai.co") for url in kwargs["source_urls"]]
    async def post(*args, **kwargs):
        task_id = kwargs["json"]["input"]["prompt"]
        return MagicMock(status_code=200, json=lambda: {"code": 200, "msg": "success", "data": {"taskId": task_id}})
    setup.upload.side_effect, setup.post.side_effect = upload, post
    originals = [CreateTaskRequest(model=MODEL, input={"prompt": f"task-{i}", "input_urls": [url, url]})
                 for i, url in enumerate(SOURCES)]
    await asyncio.gather(*(setup.client.create_task(item) for item in originals))
    assert setup.upload.await_count == setup.post.await_count == setup.ready.await_count == 2
    assert len({call.kwargs["task_id"] for call in setup.upload.await_args_list}) == 2
    for call in setup.ready.await_args_list:
        index = int(call.args[0].removeprefix("task-"))
        assert call.args[1:] == ([SOURCES[index]], [STAGED[index]])
        assert call.kwargs["request"]["input"] == originals[index].input
