"""CDN 首次提交与后台海外旁路隔离；所有网络边界均模拟。"""

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
               "prompt": "keep parameters", "resolution": "4K", "aspect_ratio": "16:9",
               "output_format": "png", "extra_parameter": {"values": [1, 2]}},
        callBackUrl="https://app.example.com/callback",
    )


@pytest.fixture
async def setup(monkeypatch):
    client = KieClient("test-key", shadow_user_id="compat-user", shadow_org_id="compat-org")
    upload = AsyncMock()
    post = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {
        "code": 200, "msg": "success", "data": {"taskId": "accepted-kie"},
    }))
    monkeypatch.setenv(client.SHADOW_OVERSEAS_PROXY_ENV, "http://127.0.0.1:7891")
    monkeypatch.setattr(client, "_run_shadow_upload", upload)
    monkeypatch.setattr(client, "_get_client", AsyncMock(return_value=MagicMock(post=post)))
    yield SimpleNamespace(client=client, upload=upload, post=post)
    tasks = list(client._shadow_upload_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def finish_shadow(setup):
    await asyncio.gather(*setup.client._shadow_upload_tasks)


@pytest.mark.parametrize("input_key", KieClient.SHADOW_IMAGE_INPUT_KEYS)
async def test_cdn_first_request_and_shadow_snapshot_preserve_order_and_parameters(setup, input_key):
    original = request(input_key)
    before = original.model_dump(exclude_none=True)
    result = await setup.client.create_task(original)
    await finish_shadow(setup)
    assert result.task_id == "accepted-kie"
    assert setup.post.await_args.kwargs["json"] == before
    assert original.model_dump(exclude_none=True) == before
    setup.upload.assert_awaited_once()
    args = setup.upload.await_args.kwargs
    assert args["task_id"] == "accepted-kie"
    assert args["source_urls"] == [SOURCES[1], SOURCES[0]]
    assert args["route"] == "overseas" and args["proxy_url"] == "http://127.0.0.1:7891"
    snapshot = args["request_snapshot"]
    assert "callBackUrl" not in snapshot
    cache = {"request": snapshot, "source_urls": args["source_urls"], "staged_urls": [STAGED[1], STAGED[0]]}
    retry = replay_request({"model_id": MODEL}, cache, original.callBackUrl)
    expected = original.model_dump(exclude_none=True)
    expected["input"][input_key] = [STAGED[1], STAGED[0], STAGED[1]]
    assert retry.model_dump(exclude_none=True) == expected


async def test_slow_shadow_does_not_delay_primary_or_stop_when_adapter_closes(setup):
    started, release = asyncio.Event(), asyncio.Event()
    async def upload(**kwargs):
        setup.post.assert_awaited_once()
        started.set()
        await release.wait()
    setup.upload.side_effect = upload
    result = await asyncio.wait_for(setup.client.create_task(request()), timeout=1)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert result.task_id == "accepted-kie"
    await setup.client.close()
    assert setup.client._shadow_upload_tasks
    assert all(not task.done() for task in setup.client._shadow_upload_tasks)
    release.set()
    await finish_shadow(setup)


@pytest.mark.parametrize("proxy", ["", None])
async def test_missing_overseas_proxy_never_starts_domestic_shadow(setup, monkeypatch, proxy):
    if proxy is None:
        monkeypatch.delenv(setup.client.SHADOW_OVERSEAS_PROXY_ENV)
    else:
        monkeypatch.setenv(setup.client.SHADOW_OVERSEAS_PROXY_ENV, proxy)
    await setup.client.create_task(request())
    await finish_shadow(setup)
    setup.upload.assert_not_awaited()
    assert setup.post.await_args.kwargs["json"] == request().model_dump(exclude_none=True)


@pytest.mark.parametrize("kind", ["text", "video"])
async def test_text_and_video_skip_shadow(setup, kind):
    original = request(model="sora-2-image-to-video") if kind == "video" else CreateTaskRequest(
        model="gpt-image-2-text-to-image", input={"prompt": "no reference image"},
    )
    await setup.client.create_task(original)
    await finish_shadow(setup)
    setup.upload.assert_not_awaited()
    assert setup.post.await_args.kwargs["json"] == original.model_dump(exclude_none=True)


async def test_primary_network_retry_schedules_shadow_only_after_acceptance(setup, monkeypatch):
    good = setup.post.return_value
    setup.post.side_effect = [httpx.ConnectTimeout("temporary"), good]
    monkeypatch.setattr(setup.client._create_task_with_retry.retry, "wait", lambda _: 0)
    await setup.client.create_task(request())
    await finish_shadow(setup)
    assert setup.post.await_count == 2
    assert setup.post.await_args_list[0] == setup.post.await_args_list[1]
    setup.upload.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["rejected", "missing_task_id", "connect_timeout"])
async def test_no_shadow_without_accepted_primary_task(setup, monkeypatch, outcome):
    if outcome == "rejected":
        setup.post.return_value = MagicMock(status_code=400, json=lambda: {"code": 400, "msg": "invalid input"})
        with pytest.raises(KieAPIError):
            await setup.client.create_task(request())
    elif outcome == "connect_timeout":
        from tenacity import RetryError
        setup.post.side_effect = httpx.ConnectTimeout("unavailable")
        monkeypatch.setattr(setup.client._create_task_with_retry.retry, "wait", lambda _: 0)
        with pytest.raises(RetryError):
            await setup.client.create_task(request())
        assert setup.post.await_count == 3
    else:
        setup.post.return_value = MagicMock(status_code=200, json=lambda: {"code": 200, "msg": "success", "data": {}})
        assert (await setup.client.create_task(request())).task_id is None
    await finish_shadow(setup)
    setup.upload.assert_not_awaited()


async def test_cancellation_before_acceptance_does_not_start_shadow(setup):
    started = asyncio.Event()
    async def post(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    setup.post.side_effect = post
    pending = asyncio.create_task(setup.client.create_task(request()))
    await asyncio.wait_for(started.wait(), timeout=1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    setup.upload.assert_not_awaited()
    assert not setup.client._shadow_upload_tasks


async def test_concurrent_creates_keep_task_and_reference_mapping_separate(setup):
    async def post(*args, **kwargs):
        task_id = kwargs["json"]["input"]["prompt"]
        return MagicMock(status_code=200, json=lambda: {"code": 200, "msg": "success", "data": {"taskId": task_id}})
    setup.post.side_effect = post
    originals = [CreateTaskRequest(model=MODEL, input={"prompt": f"task-{i}", "input_urls": [url, url]})
                 for i, url in enumerate(SOURCES)]
    await asyncio.gather(*(setup.client.create_task(item) for item in originals))
    await finish_shadow(setup)
    assert setup.upload.await_count == setup.post.await_count == 2
    for call in setup.upload.await_args_list:
        index = int(call.kwargs["task_id"].removeprefix("task-"))
        assert call.kwargs["source_urls"] == [SOURCES[index]]
        assert call.kwargs["request_snapshot"]["input"] == originals[index].input
