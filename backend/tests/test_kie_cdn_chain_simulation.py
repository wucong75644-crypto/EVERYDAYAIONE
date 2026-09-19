"""真实 Handler→CDN首次生成/后台下载上传→400一次重试→结算；仅模拟外部边界。"""

import asyncio
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from core.config import settings
from schemas.message import ImagePart, TextPart
from services.adapters.factory import create_image_adapter
from services.adapters.kie.client import KieClient
from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.shadow_upload_store import get_overseas_shadow_upload
from services.handlers.base import TaskMetadata
from services.handlers.image_handler import ImageHandler
from services.kie_image_fallback_service import _waiters
from services.task_completion_service import TaskCompletionService


USER = "11111111-1111-4111-8111-111111111111"
ORG = "simulation-org"
MODEL = "gpt-image-2-image-to-image"
RESULT = "https://cdn.example.com/workspace/generated.png"
NOW = datetime.now(timezone.utc).isoformat()


class Database:
    """数据库边界：保留过滤/CAS、任务改绑、消息写入和积分账本。"""

    def __init__(self):
        self.tables = {
            "users": [{"id": USER, "credits": 1000}],
            "tasks": [], "credit_transactions": [],
            "messages": [{"id": "placeholder", "status": "pending", "content": [], "created_at": NOW}],
            "conversations": [{"id": "conversation"}],
        }
        self.refunds = 0

    def table(self, name):
        return Query(self.tables.setdefault(name, []))

    def rpc(self, name, params):
        assert name == "atomic_refund_credits"
        tx = next(row for row in self.tables["credit_transactions"] if row["id"] == params["p_transaction_id"])
        refunded = tx["status"] == "pending"
        if refunded:
            self.tables["users"][0]["credits"] += tx["amount"]
            tx["status"] = "refunded"
            self.refunds += 1
        return SimpleNamespace(execute=lambda: SimpleNamespace(data={"refunded": refunded}))


class Query:
    def __init__(self, rows):
        self.rows, self.filters, self.payload = rows, [], None
        self.operation, self.one, self.order_key = "select", False, None

    def select(self, *args):
        return self

    def eq(self, key, value):
        self.filters.append((key, [value]))
        return self

    def in_(self, key, values):
        self.filters.append((key, values))
        return self

    def single(self):
        self.one = True
        return self

    maybe_single = single

    def order(self, key):
        self.order_key = key
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", deepcopy(payload)
        return self

    def update(self, payload):
        self.operation, self.payload = "update", deepcopy(payload)
        return self

    def upsert(self, payload, **kwargs):
        self.operation, self.payload = "upsert", deepcopy(payload)
        return self

    def execute(self):
        rows = [row for row in self.rows if all(row.get(key) in values for key, values in self.filters)]
        if self.operation == "insert":
            row = {"version": 1, "created_at": NOW, **self.payload}
            self.rows.append(row)
            rows = [row]
        elif self.operation == "upsert":
            row = next((row for row in self.rows if row["id"] == self.payload["id"]), None)
            if row is None:
                row = {"created_at": NOW}
                self.rows.append(row)
            row.update(self.payload)
            rows = [row]
        elif self.operation == "update":
            for row in rows:
                row.update(self.payload)
        if self.order_key:
            rows.sort(key=lambda row: row[self.order_key])
        return SimpleNamespace(data=deepcopy((rows[0] if rows else None) if self.one else rows))


class Redis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, **kwargs):
        self.data[key] = value
        return True


@pytest.fixture
async def chain(tmp_path, monkeypatch):
    # 其他测试可能清空配置缓存；工厂与旁路须使用同一套隔离配置。
    monkeypatch.setattr("services.adapters.factory.get_settings", lambda: settings)
    state = SimpleNamespace(
        db=Database(), posts=[], uploads={"auto": [], "overseas": []}, events=[],
        clients=[], http_options=[], downloads=[], shadow_jobs=set(), mode=None, gate=asyncio.Event(),
        upload_started=asyncio.Event(), fail_code="400", success=False,
    )
    for key, value in {
        "file_workspace_root": str(tmp_path), "oss_cdn_domain": "cdn.example.com",
        "kie_api_key": "test-key", "callback_base_url": "https://app.example.com",
        "callback_token": "test-callback",
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("KIE_SHADOW_OVERSEAS_PROXY", "http://127.0.0.1:7891")
    monkeypatch.setenv("KIE_IMAGE_FETCH_FALLBACK_ENABLED", "true")
    monkeypatch.setattr("services.circuit_breaker.is_provider_available", lambda *args: True)

    redis = Redis()
    async def get_redis():
        return redis
    async def lock(*args, **kwargs):
        return "simulation-lock"
    async def noop(*args, **kwargs):
        return True
    async def send(**kwargs):
        state.events.append(deepcopy(kwargs["message"]))
    async def persist(**kwargs):
        assert kwargs["user_id"] == USER and kwargs["org_id"] == ORG
        return {"kind": "image", "url": RESULT, "workspace_path": "下载/result.png"}

    monkeypatch.setattr("services.adapters.kie.shadow_upload_store.get_redis", get_redis)
    monkeypatch.setattr("core.redis.RedisClient.acquire_lock", lock)
    monkeypatch.setattr("core.redis.RedisClient.release_lock", noop)
    monkeypatch.setattr("services.task_limit_service.release_task_slot", noop)
    monkeypatch.setattr("services.batch_completion_service.ws_manager.send_to_task_or_user", send)
    monkeypatch.setattr("services.file_upload.download_url_to_workspace", persist)

    def result_data(task_id):
        return {
            "taskId": task_id, "state": "success" if state.success else "fail",
            "failCode": None if state.success else state.fail_code,
            "failMsg": None if state.success else "Image fetch failed",
            "resultJson": json.dumps({"resultUrls": ["https://kie.example.com/generated.png"]}) if state.success else None,
        }
    state.result_data = result_data
    original_schedule = KieClient._schedule_shadow_route
    def schedule(client, *args, **kwargs):
        original_schedule(client, *args, **kwargs)
        state.shadow_jobs.update(client._shadow_upload_tasks)
    monkeypatch.setattr(KieClient, "_schedule_shadow_route", schedule)
    real_http_client = httpx.AsyncClient

    def http_client(**kwargs):
        original_options = dict(kwargs)
        state.http_options.append(original_options)
        route = "overseas" if kwargs.get("proxy") == "http://127.0.0.1:7891" else "auto"

        async def transport(request):
            if request.url.host == "api.kie.ai":
                if request.method == "GET":
                    return httpx.Response(200, json={"code": 200, "msg": "success", "data": result_data(request.url.params["taskId"])})
                assert request.url.path == KieClient.TASK_CREATE_ENDPOINT
                state.posts.append(json.loads(request.content))
                task_id = "original-kie" if len(state.posts) == 1 else "retry-kie"
                assert len(state.posts) <= 2, "must not submit a third generation"
                return httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": task_id}})
            if request.url.host == "cdn.example.com":
                assert request.method == "GET" and route == "auto"
                assert "authorization" not in request.headers
                assert state.posts, "primary must be accepted before shadow download"
                state.downloads.append(str(request.url))
                index = next(i for i, url in enumerate(state.urls) if httpx.URL(url).path == request.url.path)
                if index == 1 and state.mode in {"cdn_download_failure", "main_success_download_failure"}:
                    return httpx.Response(404)
                return httpx.Response(200, content=f"image-{'ABC'[index]}".encode(),
                                      headers={"content-type": "image/png"})
            assert str(request.url) == KieClient.FILE_STREAM_UPLOAD_ENDPOINT
            assert request.method == "POST"
            assert state.posts, "shadow upload must not block primary creation"
            if route == "overseas":
                state.upload_started.set()
                if state.mode in {"delayed_ready", "wait_expired", "main_success_slow_shadow"}:
                    await state.gate.wait()
            multipart = message_from_bytes(
                b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content,
                policy=policy.default,
            )
            data = next(part.get_payload(decode=True) for part in multipart.iter_parts() if part.get_filename())
            state.uploads[route].append(data)
            if data == b"image-B" and state.mode in {f"{route}_upload_failure", "main_success_upload_failure"}:
                raise httpx.ReadTimeout("simulated upload timeout", request=request)
            return httpx.Response(200, json={"success": True, "code": 200, "data": {
                "downloadUrl": f"https://tempfile.redpandaai.co/{route}/{data.decode()}.png",
            }})

        # 只记录代理意图，禁止测试连真实代理；MockTransport 取代传输层。
        kwargs.pop("proxy", None)
        kwargs["trust_env"] = False
        client = real_http_client(**kwargs, transport=httpx.MockTransport(transport))
        state.clients.append(client)
        return client

    monkeypatch.setattr("services.adapters.kie.client.httpx.AsyncClient", http_client)
    state.paths, state.urls = [], []
    for label, directory in zip("ABC", ["上传", "工作区素材", "其他对话"]):
        path = tmp_path / f"org/{ORG}/{USER}/{directory}/same.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"image-{label}".encode())
        state.paths.append(path)
        state.urls.append(f"https://cdn.example.com/workspace/{quote(str(path.relative_to(tmp_path)))}")
    state.input_urls = [state.urls[2] + "#image", state.urls[0] + "?v=123", state.urls[1], state.urls[2] + "#image"]

    try:
        yield state
    finally:
        state.gate.set()
        if state.shadow_jobs:
            await asyncio.gather(*state.shadow_jobs, return_exceptions=True)
        for task in list(_waiters.values()):
            task.cancel()
        if _waiters:
            await asyncio.gather(*list(_waiters.values()), return_exceptions=True)
        _waiters.clear()
        for client in state.clients:
            await client.aclose()


MODES = [
    "main_success", "retry_success", "retry_failure", "overseas_upload_failure",
    "cdn_download_failure", "missing_local_file", "delayed_ready", "wait_expired",
    "non400_failure", "local_timeout_late_success",
    "main_success_download_failure", "main_success_upload_failure", "main_success_slow_shadow",
]


@pytest.mark.parametrize("delivery", ["callback", "poll"])
@pytest.mark.parametrize("mode", MODES)
async def test_cdn_shadow_chain(chain, mode, delivery):
    s = chain
    s.mode = mode
    if mode == "missing_local_file":
        # CDN仍有图片时，本地源文件缺失不再影响旁路。
        s.paths[1].unlink()
    s.input_urls[2] += "?x-oss-process=image/resize,w_800&Signature=test"

    handler = ImageHandler(s.db)
    handler.org_id = ORG
    parts = [TextPart(text="Keep reference order C A B C"), *[
        ImagePart(url=url, original_url=url) for url in s.input_urls
    ]]
    await asyncio.wait_for(handler.start(
        message_id="placeholder", conversation_id="conversation", user_id=USER, content=parts,
        params={"model": MODEL, "resolution": "1K", "aspect_ratio": "1:1", "_org_id": ORG},
        metadata=TaskMetadata(client_task_id="client-task"),
    ), timeout=3)
    assert len(s.db.tables["tasks"]) == len(s.db.tables["credit_transactions"]) == len(s.posts) == 1
    row = s.db.tables["tasks"][0]
    local_id, transaction_id = row["id"], row["credit_transaction_id"]
    cost = row["credits_locked"]
    assert row["external_task_id"] == "original-kie"
    assert s.posts[0]["input"]["input_urls"] == s.input_urls
    assert s.db.tables["users"][0]["credits"] == 1000 - cost
    assert s.events == []

    delayed = mode in {"delayed_ready", "wait_expired", "main_success_slow_shadow"}
    failed_upload = mode in {
        "overseas_upload_failure", "cdn_download_failure",
        "main_success_download_failure", "main_success_upload_failure",
    }
    if delayed:
        await asyncio.wait_for(s.upload_started.wait(), timeout=3)
        assert not s.gate.is_set()
    else:
        await asyncio.wait_for(asyncio.gather(*s.shadow_jobs), timeout=3)
    cache = await get_overseas_shadow_upload("original-kie")
    assert cache["status"] == ("pending" if delayed else "failed" if failed_upload else "ready")
    assert cache["request"]["input"]["input_urls"] == s.input_urls

    svc = TaskCompletionService(s.db)
    async def deliver(task_id, success=False, fail_code="400"):
        s.success, s.fail_code = success, fail_code
        if delivery == "callback":
            result = KieImageAdapter.parse_callback({"code": 200, "msg": "success", "data": s.result_data(task_id)})
        else:
            adapter = create_image_adapter(MODEL)
            try:
                result = await adapter.query_task(task_id)
            finally:
                await adapter.close()
        assert await svc.process_result(task_id, result)

    if mode == "local_timeout_late_success":
        row["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    await deliver(
        "original-kie", success=mode.startswith("main_success"),
        fail_code="500" if mode == "non400_failure" else "TIMEOUT" if mode == "local_timeout_late_success" else "400",
    )

    if mode in {"delayed_ready", "wait_expired"}:
        assert row["status"] == "pending" and s.events == [] and s.db.refunds == 0
        state = row["request_params"]["_kie_image_fetch_fallback"]
        assert state["phase"] == "waiting" and 59 < state["wait_deadline"] - time.time() <= 60
        if mode == "delayed_ready":
            s.gate.set()
            await asyncio.wait_for(asyncio.gather(*s.shadow_jobs), timeout=3)
            await asyncio.wait_for(asyncio.gather(*list(_waiters.values())), timeout=3)
        else:
            state["wait_deadline"] = time.time() - 1
            await deliver("original-kie")

    retry = mode in {"retry_success", "retry_failure", "delayed_ready", "missing_local_file"}
    success = mode.startswith("main_success") or mode in {"retry_success", "delayed_ready", "missing_local_file"}
    if retry:
        assert row["external_task_id"] == "retry-kie" and row["status"] == "pending"
        assert len(s.posts) == 2 and s.events == [] and s.db.refunds == 0
        assert row["id"] == local_id and row["credit_transaction_id"] == transaction_id
        expected = deepcopy(s.posts[0])
        expected["input"]["input_urls"] = [
            f"https://tempfile.redpandaai.co/overseas/image-{label}.png" for label in "CABC"
        ]
        assert s.posts[1] == expected
        media_before = deepcopy((s.uploads, s.downloads))
        await deliver("retry-kie", success=success)
        assert (s.uploads, s.downloads) == media_before, "fallback must not download or upload again"
    else:
        assert len(s.posts) == 1

    assert row["status"] == ("completed" if success else "failed")
    message = s.db.tables["messages"][0]
    assert message["id"] == "placeholder" and message["status"] == row["status"]
    assert message["credits_cost"] == (cost if success else 0)
    assert s.db.tables["users"][0]["credits"] == 1000 - (cost if success else 0)
    assert len(s.db.tables["credit_transactions"]) == 1
    assert s.db.tables["credit_transactions"][0]["status"] == ("confirmed" if success else "refunded")
    assert s.db.refunds == (0 if success else 1)
    assert [event["type"] for event in s.events] == ["image_partial_update", "message_done"]
    if success:
        assert message["content"][0]["url"] == RESULT
    else:
        assert message["content"][0]["failed"] is True

    # 晚到的旁路结果不得把已结束任务再次生成或改写结算。
    s.gate.set()
    await asyncio.wait_for(asyncio.gather(*s.shadow_jobs), timeout=3)
    assert s.uploads["auto"] == [], "domestic shadow upload must not run"
    assert s.uploads["overseas"] == (
        [b"image-C", b"image-A"] if mode in {"cdn_download_failure", "main_success_download_failure"}
        else [b"image-C", b"image-A", b"image-B"]
    )
    assert len(s.downloads) == 3, "duplicate image C should be downloaded only once"
    assert "x-oss-process" in s.downloads[2] and "Signature=test" in s.downloads[2]
    prior = deepcopy((s.db.tables, s.posts, s.events, s.db.refunds))
    await deliver(row["external_task_id"], success=True if mode == "local_timeout_late_success" else success)
    assert (s.db.tables, s.posts, s.events, s.db.refunds) == prior

    api_options = [item for item in s.http_options if item.get("base_url")]
    assert all("proxy" not in item and item.get("trust_env", True) for item in api_options)
    download_options, upload_options = [item for item in s.http_options if not item.get("base_url")]
    assert download_options["trust_env"] is True and "proxy" not in download_options
    assert download_options["follow_redirects"] is True
    assert upload_options["proxy"] == "http://127.0.0.1:7891" and upload_options["trust_env"] is False
