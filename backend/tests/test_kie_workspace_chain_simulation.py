"""真实 Handler→KIE→旁路缓存→一次重试→结算/消息；仅模拟外部边界。"""

import asyncio
import json
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
from services.kie_image_fallback_service import STATE_KEY, _waiters
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
        clients=[], http_options=[], mode=None, gate=asyncio.Event(), fail_code="400", success=False,
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
            assert str(request.url) == KieClient.FILE_STREAM_UPLOAD_ENDPOINT, "unexpected network/CDN download"
            assert request.method == "POST"
            if route == "overseas" and state.mode == "delayed_ready":
                await state.gate.wait()
            multipart = message_from_bytes(
                b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content,
                policy=policy.default,
            )
            data = next(part.get_payload(decode=True) for part in multipart.iter_parts() if part.get_filename())
            state.uploads[route].append(data)
            if data == b"image-B" and state.mode == f"{route}_upload_failure":
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

    async def drain_uploads():
        tasks = [task for task in asyncio.all_tasks()
                 if getattr(task.get_coro(), "__qualname__", "") == "KieClient._run_shadow_upload"]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    state.drain_uploads = drain_uploads
    try:
        yield state
    finally:
        state.gate.set()
        for task in list(_waiters.values()):
            task.cancel()
        if _waiters:
            await asyncio.gather(*list(_waiters.values()), return_exceptions=True)
        _waiters.clear()
        await drain_uploads()
        for client in state.clients:
            await client.aclose()


MODES = [
    "main_success", "retry_success", "retry_failure", "auto_upload_failure",
    "overseas_upload_failure", "missing_file", "foreign_file", "delayed_ready",
    "non400_failure", "local_timeout_late_success", "main_success_missing",
]


@pytest.mark.parametrize("delivery", ["callback", "poll"])
@pytest.mark.parametrize("mode", MODES)
async def test_workspace_chain(chain, mode, delivery):
    s = chain
    s.mode = mode
    if mode in {"missing_file", "main_success_missing"}:
        s.paths[1].unlink()
    if mode == "foreign_file":
        s.input_urls[2] = s.input_urls[2].replace(USER, "22222222-2222-4222-8222-222222222222")
        # 文件确实存在，但属于其他用户；不能通过旁路读取。
        other = s.paths[1].parents[2] / "22222222-2222-4222-8222-222222222222" / "工作区素材/same.png"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"foreign-private-image")

    handler = ImageHandler(s.db)
    handler.org_id = ORG
    parts = [TextPart(text="Keep reference order C A B C"), *[
        ImagePart(url=url, original_url=url) for url in s.input_urls
    ]]
    await handler.start(
        message_id="placeholder", conversation_id="conversation", user_id=USER, content=parts,
        params={"model": MODEL, "resolution": "1K", "aspect_ratio": "1:1", "_org_id": ORG},
        metadata=TaskMetadata(client_task_id="client-task"),
    )
    assert len(s.db.tables["tasks"]) == len(s.db.tables["credit_transactions"]) == len(s.posts) == 1
    row = s.db.tables["tasks"][0]
    local_id, transaction_id = row["id"], row["credit_transaction_id"]
    cost = row["credits_locked"]
    assert row["external_task_id"] == "original-kie"
    assert s.posts[0]["input"]["input_urls"] == s.input_urls
    assert s.db.tables["users"][0]["credits"] == 1000 - cost
    assert s.events == []

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

    if mode == "delayed_ready":
        await deliver("original-kie")
        assert row["request_params"][STATE_KEY]["phase"] == "waiting"
        assert s.events == [] and len(s.posts) == 1 and s.db.refunds == 0
        assert s.db.tables["messages"][0]["status"] == "pending"
        s.gate.set()
        await s.drain_uploads()
        await asyncio.wait_for(asyncio.gather(*list(_waiters.values())), timeout=3)
    else:
        await s.drain_uploads()
        if mode == "local_timeout_late_success":
            row["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        await deliver(
            "original-kie", success=mode.startswith("main_success"),
            fail_code="500" if mode == "non400_failure" else "TIMEOUT" if mode == "local_timeout_late_success" else "400",
        )

    retry = mode in {"retry_success", "retry_failure", "auto_upload_failure", "delayed_ready"}
    success = mode in {"main_success", "main_success_missing", "retry_success", "auto_upload_failure", "delayed_ready"}
    if retry:
        assert row["external_task_id"] == "retry-kie" and row["status"] == "pending"
        assert len(s.posts) == 2 and s.events == [] and s.db.refunds == 0
        assert row["id"] == local_id and row["credit_transaction_id"] == transaction_id
        expected = deepcopy(s.posts[0])
        expected["input"]["input_urls"] = [
            f"https://tempfile.redpandaai.co/overseas/image-{label}.png" for label in "CABC"
        ]
        assert s.posts[1] == expected
        uploads_before = deepcopy(s.uploads)
        await deliver("retry-kie", success=success)
        assert s.uploads == uploads_before, "fallback must not upload again"
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
    assert all(b"foreign-private-image" not in values for values in s.uploads.values())
    if mode not in {"missing_file", "foreign_file", "main_success_missing"}:
        assert s.uploads == {route: [b"image-C", b"image-A", b"image-B"] for route in ["auto", "overseas"]}
    if mode == "overseas_upload_failure":
        assert (await get_overseas_shadow_upload("original-kie"))["status"] == "failed"

    # 终态重复通知（包括原先决定保留的“本地超时后 KIE 才成功”）不得再次结算/重提。
    prior = deepcopy((s.db.tables, s.posts, s.events, s.db.refunds))
    await deliver(row["external_task_id"], success=True if mode == "local_timeout_late_success" else success)
    assert (s.db.tables, s.posts, s.events, s.db.refunds) == prior
    api_options = [item for item in s.http_options if item.get("base_url")]
    assert all("proxy" not in item and item.get("trust_env", True) for item in api_options)
    upload_options = [item for item in s.http_options if not item.get("base_url")]
    assert len(upload_options) == 2
    assert any(item.get("proxy") == "http://127.0.0.1:7891" and item["trust_env"] is False for item in upload_options)
    assert any("proxy" not in item and item["trust_env"] is True for item in upload_options)
    print(f"CHAIN_SIMULATION mode={mode} delivery={delivery} status={row['status']} create_count={len(s.posts)} refunds={s.db.refunds}")
