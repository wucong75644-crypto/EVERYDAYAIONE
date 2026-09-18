"""KIE 图片临时空间旁路探测测试。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from urllib.parse import quote
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest

from services.adapters.kie.client import KieClient, settings
from services.adapters.kie.models import CreateTaskRequest
from services.kie_image_fallback_request import replay_request


USER_ID = "11111111-1111-4111-8111-111111111111"
ORG_ID = "org-a"
SOURCE_DIR = f"org/{ORG_ID}/{USER_ID}"
SOURCE_URL = f"https://cdn.example.com/workspace/{SOURCE_DIR}/input.png"
STAGED_URL = "https://tempfile.redpandaai.co/demo/shadow-input.png"
IMAGE_MODEL = "gpt-image-2-image-to-image"


def make_client():
    return KieClient("test-key", shadow_user_id=USER_ID, shadow_org_id=ORG_ID)


@pytest.fixture(autouse=True)
def workspace_source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "oss_cdn_domain", "cdn.example.com")
    source = tmp_path / f"{SOURCE_DIR}/input.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image-bytes")
    return source


@pytest.fixture(autouse=True)
def isolate_shadow_cache():
    with patch("services.adapters.kie.client.save_overseas_shadow_upload_status", new_callable=AsyncMock, return_value=True), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock, return_value=True):
        yield


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class _UploadResponse:
    status_code = 200

    def json(self):
        return {
            "success": True,
            "code": 200,
            "data": {"downloadUrl": STAGED_URL},
        }


class _UploadClient:
    def __init__(self):
        self.post = AsyncMock(return_value=_UploadResponse())


@pytest.mark.asyncio
@pytest.mark.parametrize("overseas_proxy", ["http://127.0.0.1:7891", "", None])
async def test_create_task_prefers_overseas_or_uses_cdn_when_proxy_missing(
    monkeypatch, overseas_proxy,
):
    client = make_client()
    if overseas_proxy is None:
        monkeypatch.delenv(client.SHADOW_OVERSEAS_PROXY_ENV, raising=False)
    else:
        monkeypatch.setenv(client.SHADOW_OVERSEAS_PROXY_ENV, overseas_proxy)
    main_http = MagicMock()
    main_http.post = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "code": 200,
                    "msg": "success",
                    "data": {"taskId": "task-123"},
                }
            ),
        )
    )
    request = CreateTaskRequest(
        model=IMAGE_MODEL,
        input={"input_urls": [SOURCE_URL, SOURCE_URL]},
    )

    with patch.object(
        client, "_get_client", return_value=main_http
    ), patch.object(client, "_run_shadow_upload", new_callable=AsyncMock, return_value=[STAGED_URL]) as shadow_upload, \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready, \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_status", new_callable=AsyncMock) as failed:
        result = await client.create_task(request)

    assert result.task_id == "task-123"
    main_http.post.assert_awaited_once()
    expected = request.model_dump(exclude_none=True)
    if not overseas_proxy:
        shadow_upload.assert_not_awaited()
        ready.assert_not_awaited()
        failed.assert_awaited_once_with(
            "task-123", "failed", [SOURCE_URL],
            request.model_dump(mode="json", exclude={"callBackUrl"}),
        )
        assert main_http.post.await_args.kwargs["json"] == expected
        return
    expected["input"]["input_urls"] = [STAGED_URL, STAGED_URL]
    assert main_http.post.await_args.kwargs["json"] == expected
    assert request.input["input_urls"] == [SOURCE_URL, SOURCE_URL]
    shadow_upload.assert_awaited_once_with(
        model=IMAGE_MODEL,
        task_id=ANY,
        source_urls=[SOURCE_URL],
        route="overseas",
        proxy_url=overseas_proxy,
    )
    assert shadow_upload.await_args.kwargs["task_id"].startswith("preupload-")
    ready.assert_awaited_once_with(
        "task-123", [SOURCE_URL], [STAGED_URL],
        request=request.model_dump(mode="json", exclude={"callBackUrl"}),
    )
    failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_precedes_generation_and_cache_uses_real_task_id(monkeypatch):
    client = make_client()
    events = []
    request = CreateTaskRequest(
        model=IMAGE_MODEL,
        input={"input_urls": [SOURCE_URL]},
        callBackUrl="https://app.example.com/callback",
    )
    async def upload(**kwargs):
        events.append("upload")
        return [STAGED_URL]

    async def post(*args, **kwargs):
        assert events == ["upload"]
        assert kwargs["json"]["input"]["input_urls"] == [STAGED_URL]
        assert kwargs["json"]["callBackUrl"] == request.callBackUrl
        ready.assert_not_awaited()
        events.append("create")
        return MagicMock(status_code=200, json=lambda: {
            "code": 200, "msg": "success", "data": {"taskId": "task-123"},
        })

    monkeypatch.setenv(client.SHADOW_OVERSEAS_PROXY_ENV, "http://127.0.0.1:7891")
    with patch.object(client, "_run_shadow_upload", side_effect=upload), \
         patch.object(client, "_get_client", return_value=MagicMock(post=AsyncMock(side_effect=post))), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready:
        result = await client.create_task(request)
    assert result.task_id == "task-123" and events == ["upload", "create"]
    ready.assert_awaited_once_with(
        "task-123", [SOURCE_URL], [STAGED_URL],
        request={"model": IMAGE_MODEL, "input": request.input},
    )


@pytest.mark.asyncio
async def test_shadow_upload_overseas_reads_workspace_and_uploads_via_explicit_proxy():
    client = make_client()
    upload_client = _UploadClient()
    overseas_proxy = "http://127.0.0.1:7891"

    with patch(
        "services.adapters.kie.client.httpx.AsyncClient",
        return_value=_AsyncContext(upload_client),
    ) as async_client:
        await client._run_shadow_upload(
            IMAGE_MODEL,
            "task-123",
            [SOURCE_URL],
            route="overseas",
            proxy_url=overseas_proxy,
        )

    async_client.assert_called_once()
    upload_call = async_client.call_args
    assert upload_call.kwargs["proxy"] == overseas_proxy
    assert upload_call.kwargs["trust_env"] is False
    upload_client.post.assert_awaited_once()
    assert upload_client.post.await_args.args == (client.FILE_STREAM_UPLOAD_ENDPOINT,)
    upload_kwargs = upload_client.post.await_args.kwargs
    assert upload_kwargs["data"]["uploadPath"] == "everydayai/input-media"
    assert upload_kwargs["files"]["file"][1:] == (b"image-bytes", "image/png")


@pytest.mark.asyncio
async def test_overseas_upload_returns_urls_without_caching_before_task_creation():
    client = make_client()
    upload_client = _UploadClient()

    with patch(
        "services.adapters.kie.client.httpx.AsyncClient",
        return_value=_AsyncContext(upload_client),
    ), patch(
        "services.adapters.kie.client.save_overseas_shadow_upload_urls",
        new_callable=AsyncMock,
        return_value=True,
    ) as save_staged_urls:
        staged = await client._run_shadow_upload(
            IMAGE_MODEL,
            "task-123",
            [SOURCE_URL],
            route="overseas",
            proxy_url="http://127.0.0.1:7891",
        )

    assert staged == [STAGED_URL]
    save_staged_urls.assert_not_awaited()


def test_shadow_upload_skips_non_image_kie_requests():
    client = make_client()
    image_request = CreateTaskRequest(
        model="sora-2-image-to-video",
        input={"image_urls": [SOURCE_URL]},
    )
    chat_request = CreateTaskRequest(
        model="gemini-3-pro",
        input={"input_urls": [SOURCE_URL]},
    )

    assert client._extract_shadow_image_urls(image_request) == []
    assert client._extract_shadow_image_urls(chat_request) == []


async def test_partial_overseas_upload_is_failed_not_ready():
    client = make_client()
    with patch("services.adapters.kie.client.httpx.AsyncClient", side_effect=lambda **kwargs: _AsyncContext(MagicMock())), \
         patch.object(client, "_shadow_upload_one", new_callable=AsyncMock, side_effect=[STAGED_URL, None]), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_status", new_callable=AsyncMock) as status, \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready:
        staged = await client._run_shadow_upload(IMAGE_MODEL, "partial", [SOURCE_URL, SOURCE_URL + "?second"], "overseas", "http://127.0.0.1:7891")
    assert staged is None
    status.assert_not_awaited()
    ready.assert_not_awaited()


async def test_parallel_upload_is_bounded_and_returns_input_order_when_finished_out_of_order():
    client = make_client()
    sources = [SOURCE_URL + f"?v={i}" for i in range(6)]
    entered = [asyncio.Event() for _ in sources]
    release = [asyncio.Event() for _ in sources]
    finished = [asyncio.Event() for _ in sources]
    active, peak, completion = set(), 0, []

    async def upload(**kwargs):
        nonlocal peak
        index = sources.index(kwargs["source_url"])
        active.add(index)
        peak = max(peak, len(active))
        entered[index].set()
        try:
            await release[index].wait()
            completion.append(index)
            return f"https://tempfile.redpandaai.co/{index}.png"
        finally:
            active.remove(index)
            finished[index].set()

    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(MagicMock())), \
         patch.object(client, "_shadow_upload_one", side_effect=upload):
        pending = asyncio.create_task(client._run_shadow_upload(
            IMAGE_MODEL, "parallel", sources, "overseas", "http://127.0.0.1:7891",
        ))
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered[:4])), 1)
            assert active == {0, 1, 2, 3}
            assert not entered[4].is_set() and not entered[5].is_set()
            for index in [3, 2, 5, 4, 1, 0]:
                await asyncio.wait_for(entered[index].wait(), 1)
                release[index].set()
                await asyncio.wait_for(finished[index].wait(), 1)
            returned = await asyncio.wait_for(pending, 1)
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    assert peak == 4 and active == set()
    assert completion == [3, 2, 5, 4, 1, 0]
    assert returned == [f"https://tempfile.redpandaai.co/{i}.png" for i in range(6)]


@pytest.mark.parametrize("stop", ["timeout", "caller_cancel"])
async def test_parallel_upload_cancels_running_and_queued_children_before_closing_client(monkeypatch, stop):
    client = make_client()
    monkeypatch.setenv(client.SHADOW_OVERSEAS_PROXY_ENV, "http://127.0.0.1:7891")
    monkeypatch.setattr(client, "SHADOW_PREPARE_TIMEOUT", 0.1 if stop == "timeout" else 60)
    sources = [SOURCE_URL + f"?v={i}" for i in range(6)]
    request = CreateTaskRequest(model=IMAGE_MODEL, input={"input_urls": sources})
    entered, active, cancelled = set(), set(), set()
    all_running = asyncio.Event()

    async def upload(**kwargs):
        index = sources.index(kwargs["source_url"])
        entered.add(index)
        active.add(index)
        if len(entered) == 4:
            all_running.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(index)
            cancelled.add(index)

    class UploadContext(_AsyncContext):
        async def __aexit__(self, *args):
            assert not active, "all uploads must stop before their shared HTTP client closes"
            return False

    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=UploadContext(MagicMock())), \
         patch.object(client, "_shadow_upload_one", side_effect=upload):
        pending = asyncio.create_task(client._prepare_shadow_upload(request, sources, "preupload-test"))
        try:
            await asyncio.wait_for(all_running.wait(), 1)
            if stop == "caller_cancel":
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                prepared, staged = await asyncio.wait_for(pending, 1)
                assert prepared is request and staged is None
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert entered == cancelled == {0, 1, 2, 3}
    assert not active


@pytest.mark.parametrize("input_key", KieClient.SHADOW_IMAGE_INPUT_KEYS)
async def test_workspace_upload_preserves_mixed_source_order_and_repeated_positions(
    tmp_path, input_key,
):
    client = make_client()
    # 上传、工作区引用、其他对话引用即使同名，也必须按完整 URL 精确读文件。
    paths = [f"{SOURCE_DIR}/{folder}/same.png" for folder in ["上传", "素材", "下载"]]
    for index, relative in enumerate(paths):
        file = tmp_path / relative
        file.parent.mkdir(parents=True)
        file.write_bytes(f"image-{index}".encode())
    urls = [f"https://cdn.example.com/workspace/{quote(path)}" for path in paths]
    original_order = [urls[2], urls[0], urls[1], urls[2]]
    request = CreateTaskRequest(model=IMAGE_MODEL, input={input_key: original_order})
    sources = client._extract_shadow_image_urls(request)
    staged = [f"https://tempfile.redpandaai.co/{i}.png" for i in [2, 0, 1]]
    upload_client = _UploadClient()
    async def post(*args, **kwargs):
        index = kwargs["files"]["file"][1].decode().removeprefix("image-")
        return MagicMock(status_code=200, json=lambda: {
            "success": True, "code": 200,
            "data": {"downloadUrl": f"https://tempfile.redpandaai.co/{index}.png"},
        })
    upload_client.post.side_effect = post
    snapshot = request.model_dump(mode="json", exclude={"callBackUrl"})
    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(upload_client)), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock, return_value=True) as ready:
        returned = await client._run_shadow_upload(
            IMAGE_MODEL, "ordered", sources, "overseas",
            "http://127.0.0.1:7891",
        )

    assert sorted(call.kwargs["files"]["file"][1] for call in upload_client.post.await_args_list) == [
        b"image-0", b"image-1", b"image-2",
    ]
    assert returned == staged
    ready.assert_not_awaited()
    cache = {"source_urls": sources, "staged_urls": staged, "request": snapshot}
    replayed = replay_request({"model_id": IMAGE_MODEL}, cache, None)
    assert replayed.input[input_key] == [staged[0], staged[1], staged[2], staged[0]]
    assert request.input[input_key] == original_order


@pytest.mark.parametrize("failure", ["missing", "unreadable", "upload"])
async def test_middle_file_failure_never_downloads_or_caches_partial_mapping(
    tmp_path, failure,
):
    client = make_client()
    sources = [SOURCE_URL, SOURCE_URL.replace("input.png", "middle.png"), SOURCE_URL.replace("input.png", "last.png")]
    (tmp_path / f"{SOURCE_DIR}/last.png").write_bytes(b"last-image")
    if failure != "missing":
        (tmp_path / f"{SOURCE_DIR}/middle.png").write_bytes(b"middle-image")
    read_image = client._read_shadow_image

    def read(url):
        if failure == "unreadable" and url == sources[1]:
            raise PermissionError("test unreadable")
        return read_image(url)

    upload_client = _UploadClient()
    if failure == "upload":
        async def post(*args, **kwargs):
            if kwargs["files"]["file"][1] == b"middle-image":
                raise httpx.ReadTimeout("test timeout")
            return _UploadResponse()
        upload_client.post.side_effect = post
    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(upload_client)) as http_client, \
         patch.object(client, "_read_shadow_image", side_effect=read), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_status", new_callable=AsyncMock) as status, \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready:
        returned = await client._run_shadow_upload(
            IMAGE_MODEL, "missing-middle", sources, "overseas",
            "http://127.0.0.1:7891",
        )

    http_client.assert_called_once()  # 唯一 HTTP 客户端只有上传 POST，没有 CDN GET。
    assert sorted(call.kwargs["files"]["file"][1] for call in upload_client.post.await_args_list) == (
        [b"image-bytes", b"last-image", b"middle-image"] if failure == "upload"
        else [b"image-bytes", b"last-image"]
    )
    ready.assert_not_awaited()
    assert returned is None
    status.assert_not_awaited()


@pytest.mark.parametrize("source", [
    "https://foreign.example.com/workspace/org/demo/input.png",
    "https://cdn.example.com/workspace-thumbnails/org/demo/input.png",
    "https://cdn.example.com/workspace/%2e%2e/outside.png",
    "https://cdn.example.com/workspace/org/demo/../input.png",
    "https://cdn.example.com/workspace/org/demo/%5cinput.png",
    "https://cdn.example.com/workspace/org/demo/%00input.png",
    "https://cdn.example.com/workspace/org/demo/input.png?x-oss-process=image/resize,w_100",
    "https://cdn.example.com/workspace/org/demo/.env",
    "https://cdn.example.com:8443/workspace/org/demo/input.png",
])
def test_workspace_reader_rejects_untrusted_or_nonoriginal_paths(source):
    with pytest.raises((ValueError, PermissionError)):
        make_client()._read_shadow_image(source)


@pytest.mark.parametrize("directory_link", [False, True])
def test_workspace_reader_rejects_symlinks(tmp_path, directory_link):
    source = tmp_path / f"{SOURCE_DIR}/input.png"
    link = source.parent / "link.png"
    if directory_link:
        link.symlink_to(source.parent, target_is_directory=True)
        url = SOURCE_URL.replace("input.png", "link.png/input.png")
    else:
        link.symlink_to(source)
        url = SOURCE_URL.replace("input.png", "link.png")
    with pytest.raises(ValueError):
        make_client()._read_shadow_image(url)


@pytest.mark.parametrize("content", [b"", b"12345"])
def test_workspace_reader_keeps_size_limit(workspace_source, content):
    workspace_source.write_bytes(content)
    client = make_client()
    client.SHADOW_UPLOAD_MAX_BYTES = 4
    with pytest.raises(ValueError):
        client._read_shadow_image(SOURCE_URL)


@pytest.mark.parametrize("suffix", [
    "?v=123", "#image", "?v=123#image", "?t=10&ts=20&_=30",
    "?OSSAccessKeyId=test&Expires=123&Signature=test&security-token=test",
])
def test_workspace_reader_accepts_nontransforming_parameters(suffix):
    assert make_client()._read_shadow_image(SOURCE_URL + suffix) == (b"image-bytes", "image/png")


@pytest.mark.parametrize("suffix", [
    "?x-oss-process=image/resize,w_100", "?versionId=old", "?v=123&versionId=old",
    "?v=123&%78-oss-process=image/crop,w_20", "?unknown=1", "?x-oss-process",
])
def test_workspace_reader_rejects_transforms_versions_and_unknown_parameters(suffix):
    with pytest.raises(ValueError):
        make_client()._read_shadow_image(SOURCE_URL + suffix)


@pytest.mark.parametrize("user_id,org_id", [
    (None, ORG_ID),
    ("22222222-2222-4222-8222-222222222222", ORG_ID),
    (USER_ID, "org-b"),
    (USER_ID, None),
])
async def test_workspace_owner_mismatch_never_reads_or_uploads(user_id, org_id):
    client = KieClient("test-key", shadow_user_id=user_id, shadow_org_id=org_id)
    upload_client = _UploadClient()
    with patch("pathlib.Path.open", side_effect=AssertionError("must not read another owner's file")), \
         patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(upload_client)), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready:
        await client._run_shadow_upload(
            IMAGE_MODEL, "owner-check", [SOURCE_URL], "overseas",
            "http://127.0.0.1:7891",
        )
    upload_client.post.assert_not_awaited()
    ready.assert_not_awaited()


@pytest.mark.parametrize("owner,org_id", [
    (USER_ID, None),
    ("channels/wecom/" + "a" * 24, ORG_ID),
])
def test_workspace_reader_supports_personal_and_trusted_channel_owners(tmp_path, owner, org_id):
    owner_dir = (
        f"org/{org_id}/{owner}" if org_id else
        "personal/" + hashlib.md5(owner.encode()).hexdigest()[:8]
    )
    file = tmp_path / owner_dir / "image.png"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"owned-image")
    client = KieClient("test-key", shadow_user_id=owner, shadow_org_id=org_id)
    url = f"https://cdn.example.com/workspace/{owner_dir}/image.png"
    assert client._read_shadow_image(url)[0] == b"owned-image"
    with pytest.raises(PermissionError):
        make_client()._read_shadow_image(url)


def test_workspace_reader_does_not_use_whitespace_normalization_to_escape_owner(tmp_path):
    file = tmp_path / f" workspace/{SOURCE_DIR}/input.png"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"not-owned")
    with pytest.raises(ValueError):
        make_client()._read_shadow_image(SOURCE_URL.replace("/workspace/", "/%20workspace/"))


@pytest.mark.asyncio
async def test_image_factory_passes_trusted_owner_without_changing_kie_payload():
    from services.adapters.factory import create_image_adapter

    with patch.object(settings, "kie_api_key", "test-key"), \
         patch("services.adapters.factory.get_settings", return_value=settings), \
         patch("services.circuit_breaker.is_provider_available", return_value=True):
        adapter = create_image_adapter(IMAGE_MODEL, shadow_user_id=USER_ID, shadow_org_id=ORG_ID)
    client = adapter.client
    assert client._read_shadow_image(SOURCE_URL)[0] == b"image-bytes"
    main_http = MagicMock()
    main_http.post = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=lambda: {"code": 200, "msg": "success", "data": {"taskId": "trusted-owner"}},
    ))
    request = CreateTaskRequest(model=IMAGE_MODEL, input={"input_urls": [SOURCE_URL]})
    with patch.object(client, "_get_client", return_value=main_http), \
         patch.dict(os.environ, {client.SHADOW_OVERSEAS_PROXY_ENV: ""}):
        await client.create_task(request)
    assert main_http.post.await_args.kwargs["json"] == request.model_dump(exclude_none=True)
