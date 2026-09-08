"""KIE 图片临时空间旁路探测测试。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from urllib.parse import quote
from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_create_task_schedules_shadow_upload_without_changing_main_request():
    client = make_client()
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

    with patch.dict(os.environ, {client.SHADOW_OVERSEAS_PROXY_ENV: ""}), patch.object(
        client, "_get_client", return_value=main_http
    ), patch.object(client, "_run_shadow_upload", new_callable=AsyncMock) as shadow_upload:
        result = await client.create_task(request)
        await asyncio.gather(*client._shadow_upload_tasks)

    assert result.task_id == "task-123"
    main_http.post.assert_awaited_once()
    assert main_http.post.await_args.kwargs["json"] == request.model_dump(exclude_none=True)
    shadow_upload.assert_awaited_once_with(
        model=IMAGE_MODEL,
        task_id="task-123",
        source_urls=[SOURCE_URL],
        route="auto",
        proxy_url=None,
    )


@pytest.mark.asyncio
async def test_shadow_upload_uses_environment_proxy_and_uploads_bytes():
    client = make_client()
    upload_client = _UploadClient()

    with patch(
        "services.adapters.kie.client.httpx.AsyncClient",
        return_value=_AsyncContext(upload_client),
    ) as async_client:
        await client._run_shadow_upload(
            IMAGE_MODEL,
            "task-123",
            [SOURCE_URL],
            route="auto",
            proxy_url=None,
        )

    async_client.assert_called_once()
    assert async_client.call_args.kwargs["trust_env"] is True
    assert "proxy" not in async_client.call_args.kwargs
    upload_client.post.assert_awaited_once()
    assert upload_client.post.await_args.args == (client.FILE_STREAM_UPLOAD_ENDPOINT,)
    upload_kwargs = upload_client.post.await_args.kwargs
    assert upload_kwargs["data"]["uploadPath"] == "everydayai/input-media"
    assert upload_kwargs["files"]["file"][1:] == (
        b"image-bytes",
        "image/png",
    )


@pytest.mark.asyncio
async def test_create_task_records_task_id_for_both_shadow_routes():
    client = make_client()
    request = CreateTaskRequest(
        model=IMAGE_MODEL,
        input={"input_urls": [SOURCE_URL]},
    )

    with patch.dict(
        os.environ,
        {client.SHADOW_OVERSEAS_PROXY_ENV: "http://127.0.0.1:7891"},
    ), patch.object(client, "_run_shadow_upload", new_callable=AsyncMock) as shadow_upload:
        client._schedule_shadow_upload(request, task_id="task-123")
        await asyncio.gather(*client._shadow_upload_tasks)

    assert {
        call.kwargs["route"] for call in shadow_upload.await_args_list
    } == {"auto", "overseas"}
    assert {
        call.kwargs["task_id"] for call in shadow_upload.await_args_list
    } == {"task-123"}
    overseas = next(call.kwargs for call in shadow_upload.await_args_list if call.kwargs["route"] == "overseas")
    assert overseas["request_snapshot"] == {"model": IMAGE_MODEL, "input": request.input}
    assert "callBackUrl" not in overseas["request_snapshot"]


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
    assert upload_client.post.await_args.kwargs["files"]["file"][1] == b"image-bytes"


@pytest.mark.asyncio
async def test_overseas_shadow_upload_caches_returned_staged_urls():
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
        await client._run_shadow_upload(
            IMAGE_MODEL,
            "task-123",
            [SOURCE_URL],
            route="overseas",
            proxy_url="http://127.0.0.1:7891",
        )

    save_staged_urls.assert_awaited_once_with(
        "task-123",
        [SOURCE_URL],
        [STAGED_URL],
        request=None,
    )


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
        await client._run_shadow_upload(IMAGE_MODEL, "partial", [SOURCE_URL, SOURCE_URL + "?second"], "overseas", "http://127.0.0.1:7891")
    assert [call.args[1] for call in status.await_args_list] == ["pending", "failed"]
    ready.assert_not_awaited()


@pytest.mark.parametrize("route", ["auto", "overseas"])
@pytest.mark.parametrize("input_key", KieClient.SHADOW_IMAGE_INPUT_KEYS)
async def test_workspace_upload_preserves_mixed_source_order_and_repeated_positions(
    tmp_path, route, input_key,
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
    upload_client.post.side_effect = [
        MagicMock(status_code=200, json=lambda url=url: {
            "success": True, "code": 200, "data": {"downloadUrl": url},
        }) for url in staged
    ]
    snapshot = request.model_dump(mode="json", exclude={"callBackUrl"})
    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(upload_client)), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock, return_value=True) as ready:
        await client._run_shadow_upload(
            IMAGE_MODEL, "ordered", sources, route,
            "http://127.0.0.1:7891" if route == "overseas" else None, snapshot,
        )

    assert [call.kwargs["files"]["file"][1] for call in upload_client.post.await_args_list] == [
        b"image-2", b"image-0", b"image-1",
    ]
    if route == "overseas":
        ready.assert_awaited_once_with("ordered", sources, staged, request=snapshot)
        cache = {"source_urls": sources, "staged_urls": staged, "request": snapshot}
        replayed = replay_request({"model_id": IMAGE_MODEL}, cache, None)
        assert replayed.input[input_key] == [staged[0], staged[1], staged[2], staged[0]]
    else:
        ready.assert_not_awaited()
    assert request.input[input_key] == original_order


@pytest.mark.parametrize("route", ["auto", "overseas"])
@pytest.mark.parametrize("failure", ["missing", "unreadable", "upload"])
async def test_middle_file_failure_never_downloads_or_caches_partial_mapping(
    tmp_path, route, failure,
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
        upload_client.post.side_effect = [_UploadResponse(), httpx.ReadTimeout("test timeout"), _UploadResponse()]
    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(upload_client)) as http_client, \
         patch.object(client, "_read_shadow_image", side_effect=read), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_status", new_callable=AsyncMock) as status, \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready:
        await client._run_shadow_upload(
            IMAGE_MODEL, "missing-middle", sources, route,
            "http://127.0.0.1:7891" if route == "overseas" else None,
        )

    http_client.assert_called_once()  # 唯一 HTTP 客户端只有上传 POST，没有 CDN GET。
    assert [call.kwargs["files"]["file"][1] for call in upload_client.post.await_args_list] == (
        [b"image-bytes", b"middle-image", b"last-image"] if failure == "upload"
        else [b"image-bytes", b"last-image"]
    )
    ready.assert_not_awaited()
    if route == "overseas":
        assert [call.args[1] for call in status.await_args_list] == ["pending", "failed"]


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
@pytest.mark.parametrize("route", ["auto", "overseas"])
async def test_workspace_owner_mismatch_never_reads_or_uploads(user_id, org_id, route):
    client = KieClient("test-key", shadow_user_id=user_id, shadow_org_id=org_id)
    upload_client = _UploadClient()
    with patch("pathlib.Path.open", side_effect=AssertionError("must not read another owner's file")), \
         patch("services.adapters.kie.client.httpx.AsyncClient", return_value=_AsyncContext(upload_client)), \
         patch("services.adapters.kie.client.save_overseas_shadow_upload_urls", new_callable=AsyncMock) as ready:
        await client._run_shadow_upload(
            IMAGE_MODEL, "owner-check", [SOURCE_URL], route,
            "http://127.0.0.1:7891" if route == "overseas" else None,
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
         patch.object(client, "_schedule_shadow_upload"):
        await client.create_task(request)
    assert main_http.post.await_args.kwargs["json"] == request.model_dump(exclude_none=True)
