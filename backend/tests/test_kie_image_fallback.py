"""真实 KIE 适配/序列化 + 内存数据库/HTTP 边界，验证单次重试状态机。"""

import asyncio
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.adapters.base import ImageGenerateResult, TaskStatus
from services.adapters.kie.client import KieClient
from services.adapters.kie.image_adapter import KieImageAdapter
from services.kie_image_fallback_request import replay_request, safe_error
from services.kie_image_fallback_service import (
    ATTEMPTED_KEY, STATE_KEY, FallbackOutcome, KieImageFallbackService,
    _waiters, defer_stale_timeout, needs_fallback_resume,
)
from services.task_completion_service import TaskCompletionService

MODEL = "gpt-image-2-image-to-image"
SOURCES = ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"]
STAGED = ["https://tempfile.redpandaai.co/a.png", "https://tempfile.redpandaai.co/b.png"]


class MemoryDB:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.fail_bind = 0
        self.bind_error = RuntimeError("database temporarily unavailable")
        self.updates = []

    def table(self, name):
        assert name == "tasks"
        return Query(self)


class Query:
    def __init__(self, db):
        self.db, self.filters, self.payload, self.single = db, [], None, False

    def select(self, *args):
        return self

    def update(self, payload):
        self.payload = deepcopy(payload)
        return self

    def eq(self, key, value):
        self.filters.append((key, [value]))
        return self

    def in_(self, key, values):
        self.filters.append((key, values))
        return self

    def maybe_single(self):
        self.single = True
        return self

    def execute(self):
        if not all(self.db.row.get(key) in values for key, values in self.filters):
            return SimpleNamespace(data=None if self.single else [])
        if self.payload is not None:
            if "external_task_id" in self.payload and self.db.fail_bind:
                self.db.fail_bind -= 1
                raise self.db.bind_error
            self.db.row.update(self.payload)
            self.db.updates.append(self.payload)
        return SimpleNamespace(data=deepcopy(self.db.row) if self.single else [deepcopy(self.db.row)])


@pytest.fixture
def task():
    return {
        "id": "local-1", "external_task_id": "original-kie", "type": "image",
        "status": "pending", "model_id": MODEL, "version": 1,
        "user_id": "user-1", "batch_id": "batch-1", "image_index": 1,
        "client_task_id": "client-1", "placeholder_message_id": "message-1",
        "credit_transaction_id": "original-credit-lock", "credits_locked": 6,
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=9)).isoformat(),
        "request_params": {"prompt": "test image", "resolution": "1024x1024", "aspect_ratio": "1:1"},
    }


@pytest.fixture
def cache():
    return {"status": "ready", "source_urls": SOURCES, "staged_urls": STAGED,
            "updated_at": time.time(), "request": {"model": MODEL, "input": {
                "prompt": "actual per-image prompt", "input_urls": [SOURCES[1], SOURCES[0], SOURCES[1]],
                "aspect_ratio": "16:9", "resolution": "2K", "additional_parameter": "preserved",
            }}}


@pytest.fixture
def result():
    return ImageGenerateResult(task_id="original-kie", status=TaskStatus.FAILED,
                               fail_code="400", fail_msg="Image fetch failed")


@pytest.fixture
async def setup(task, cache, monkeypatch):
    db = MemoryDB(task)
    service = KieImageFallbackService(db)
    requests, clients = [], []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200, "msg": "success", "data": {"taskId": "retry-kie"}})

    def factory(model):
        assert model == MODEL
        client = KieClient("test-key")
        client._client = httpx.AsyncClient(base_url=client.BASE_URL, transport=httpx.MockTransport(handler))
        client._schedule_shadow_upload = MagicMock(side_effect=AssertionError("Retry must not upload again"))
        clients.append(client)
        return KieImageAdapter(client, model)

    cache_get = AsyncMock(return_value=cache)
    monkeypatch.setenv("KIE_IMAGE_FETCH_FALLBACK_ENABLED", "true")
    monkeypatch.setattr("services.adapters.factory.create_image_adapter", factory)
    monkeypatch.setattr("services.handlers.base.BaseHandler._build_callback_url", lambda *args: "https://app.example.com/callback")
    monkeypatch.setattr("services.kie_image_fallback_service.get_overseas_shadow_upload", cache_get)
    monkeypatch.setattr(service, "_schedule_wait", MagicMock())
    with patch("services.handlers.mixins.CreditMixin._lock_credits") as lock, \
         patch("services.handlers.mixins.CreditMixin._refund_credits") as refund:
        yield SimpleNamespace(db=db, service=service, requests=requests, clients=clients,
                              cache_get=cache_get, handler=handler, lock=lock, refund=refund)
    for waiter in list(_waiters.values()):
        waiter.cancel()
    if _waiters:
        await asyncio.gather(*list(_waiters.values()), return_exceptions=True)
    _waiters.clear()
    for client in clients:
        await client.close()


async def test_replay_only_changes_urls_and_keeps_same_local_task_and_credit(setup, task, cache, result):
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.PROCESSING
    assert len(setup.requests) == 1
    sent = setup.requests[0]
    expected = deepcopy(cache["request"]["input"])
    expected["input_urls"] = [STAGED[1], STAGED[0], STAGED[1]]
    assert sent["input"] == expected
    assert sent["model"] == MODEL
    assert sent["callBackUrl"] == "https://app.example.com/callback"
    assert setup.db.row["external_task_id"] == "retry-kie"
    assert setup.db.row["request_params"][STATE_KEY]["phase"] == "submitted"
    assert setup.db.row["request_params"][ATTEMPTED_KEY] is True
    for key in ("id", "batch_id", "image_index", "client_task_id", "placeholder_message_id", "credit_transaction_id", "credits_locked"):
        assert setup.db.row[key] == task[key]
    assert setup.db.row["started_at"] > task["started_at"]
    assert setup.db.row["status"] == "pending"
    setup.lock.assert_not_called()
    setup.refund.assert_not_called()
    assert cache["request"]["input"]["input_urls"][0] == SOURCES[1]


async def test_half_second_late_upload_waits_and_resumes_without_failure(setup, task, result, cache):
    setup.cache_get.return_value = {"status": "pending"}
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.PROCESSING
    deadline = setup.db.row["request_params"][STATE_KEY]["wait_deadline"]
    assert 59 < deadline - time.time() <= 60
    assert not setup.requests and setup.db.row["status"] == "pending"
    setup.cache_get.return_value = cache
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.PROCESSING
    assert len(setup.requests) == 1
    assert setup.db.row["request_params"][STATE_KEY]["wait_deadline"] == deadline
    setup.refund.assert_not_called()


@pytest.mark.parametrize("cache_result,expired", [(None, True), ({"status": "pending"}, True), ({"status": "failed"}, False)])
async def test_unavailable_or_failed_upload_is_final_without_submission(setup, task, result, cache_result, expired):
    setup.cache_get.return_value = {"status": "pending"}
    await setup.service.handle_failure(task, result)
    if expired:
        setup.db.row["request_params"][STATE_KEY]["wait_deadline"] = time.time() - 1
    setup.cache_get.return_value = cache_result
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert not setup.requests
    assert setup.db.row["request_params"][STATE_KEY]["phase"] == "failed"


async def test_poll_recovery_accepts_upload_completed_within_original_window(setup, task, result, cache):
    setup.db.row["request_params"][STATE_KEY] = {
        "phase": "waiting", "original_task_id": "original-kie", "wait_deadline": time.time() - 60,
    }
    cache["updated_at"] = time.time() - 61
    await setup.service.handle_failure(task, result)
    assert len(setup.requests) == 1


@pytest.mark.parametrize("params,expected", [
    ({"resolution": "1024x1024"}, ("1:1", "1K")),
    ({"resolution": "2048x2048"}, ("1:1", "2K")),
    ({"resolution": "", "aspect_ratio": "", "output_format": ""}, ("1:1", "1K")),
    ({"resolution": "2K", "aspect_ratio": "auto"}, ("auto", "1K")),
    ({"resolution": "4K", "aspect_ratio": "1:1"}, ("1:1", "2K")),
])
async def test_legacy_cache_uses_real_original_normalization(setup, task, cache, result, params, expected):
    cache.pop("request")
    setup.db.row["request_params"].update(params)
    await setup.service.handle_failure(task, result)
    assert len(setup.requests) == 1
    payload = setup.requests[0]["input"]
    assert (payload["aspect_ratio"], payload["resolution"]) == expected
    assert payload["input_urls"] == STAGED


async def test_legacy_batch_item_overrides_are_preserved(setup, task, cache, result):
    cache.pop("request")
    setup.db.row["request_params"]["_batch_prompts"] = [
        {"prompt": "first"}, {"prompt": "second", "aspect_ratio": "16:9", "resolution": "2K", "image_urls": SOURCES[::-1]},
    ]
    await setup.service.handle_failure(task, result)
    payload = setup.requests[0]["input"]
    assert (payload["prompt"], payload["aspect_ratio"], payload["resolution"]) == ("second", "16:9", "2K")
    assert payload["input_urls"] == STAGED[::-1]


@pytest.mark.parametrize("broken", ["model", "urls", "batch"])
async def test_incomplete_replay_fails_without_guessing(setup, task, cache, result, broken):
    if broken == "model":
        cache["request"]["model"] = "nano-banana-pro"
    elif broken == "urls":
        cache["request"]["input"]["input_urls"] = ["https://unknown.example.com/a.png"]
    else:
        cache.pop("request")
        setup.db.row["request_params"]["_batch_prompts"] = [{"prompt": "only-one"}]
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert not setup.requests


@pytest.mark.parametrize("error", [RuntimeError("database offline"), httpx.ReadTimeout("database response lost")])
async def test_database_binding_failure_logs_ids_without_rebinding_or_reposting(setup, task, result, error):
    setup.db.fail_bind = 1
    setup.db.bind_error = error
    with patch("services.kie_image_fallback_service.logger.info") as log:
        assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    binding_log = next(call for call in log.call_args_list if len(call.args) > 1 and call.args[1] == "BIND_FAILED")
    assert binding_log.args[2:5] == ("local-1", "original-kie", "retry-kie")
    assert binding_log.args[-1]["manual_review"] is True
    assert setup.db.row["request_params"][STATE_KEY]["phase"] == "failed"
    assert setup.db.row["request_params"][STATE_KEY]["retry_task_id"] == "retry-kie"
    assert setup.db.row["external_task_id"] == "original-kie"
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert setup.db.row["external_task_id"] == "original-kie"
    assert len(setup.requests) == 1
    setup.service._schedule_wait.assert_not_called()


async def test_persistent_database_failure_after_acceptance_never_reposts(setup, task, result, monkeypatch):
    real_save = setup.service._save_state

    def save_until_submitted(current, state, **fields):
        if state["phase"] in {"submitted", "failed"}:
            raise RuntimeError("database offline after acceptance")
        return real_save(current, state, **fields)

    monkeypatch.setattr(setup.service, "_save_state", save_until_submitted)
    with pytest.raises(RuntimeError, match="database offline"):
        await setup.service.handle_failure(task, result)
    assert setup.db.row["request_params"][STATE_KEY]["phase"] == "submitting"
    monkeypatch.setattr(setup.service, "_save_state", real_save)
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert setup.db.row["external_task_id"] == "original-kie"
    assert len(setup.requests) == 1
    setup.service._schedule_wait.assert_not_called()


async def test_network_timeout_is_final_without_reposting(setup, task, result, monkeypatch):
    calls = []

    async def send(client, request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost")

    monkeypatch.setattr(KieClient, "create_task_once", send)
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert setup.db.row["request_params"][STATE_KEY]["reason"] == "submission_unconfirmed"
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert len(calls) == 1
    setup.service._schedule_wait.assert_not_called()


async def test_duplicate_original_result_and_second_failure_do_not_submit_again(setup, task, result):
    await setup.service.handle_failure(task, result)
    await setup.service.handle_failure(task, result)
    assert len(setup.requests) == 1
    retry_task = deepcopy(setup.db.row)
    retry_result = ImageGenerateResult(task_id="retry-kie", status=TaskStatus.FAILED, fail_code="500")
    assert await setup.service.handle_failure(retry_task, retry_result) == FallbackOutcome.FINAL_FAILURE


async def test_previous_release_attempt_marker_is_honored(setup, task, result):
    task["request_params"][ATTEMPTED_KEY] = True
    setup.db.row["request_params"][ATTEMPTED_KEY] = True
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert not setup.requests


def test_timeout_uses_latest_retry_start(task):
    task["started_at"] = datetime.now(timezone.utc).isoformat()
    assert defer_stale_timeout(task) is True
    task["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    assert defer_stale_timeout(task) is False
    task["request_params"][STATE_KEY] = {"phase": "waiting"}
    assert needs_fallback_resume(task) is True
    assert defer_stale_timeout(task) is False  # 交给模块检查自己的等待截止时间。


async def test_completion_keeps_placeholder_until_retry_terminal_result(setup, task, result, monkeypatch):
    completion = TaskCompletionService(setup.db)
    monkeypatch.setattr(KieImageFallbackService, "_schedule_wait", MagicMock())
    failed = AsyncMock(return_value=True)
    smart = AsyncMock(return_value=False)
    monkeypatch.setattr("services.batch_completion_service.BatchCompletionService.handle_image_failure", failed)
    monkeypatch.setattr("services.async_retry_service.AsyncRetryService.attempt_retry", smart)
    assert await completion._process_result_locked(task["external_task_id"], result) is True
    failed.assert_not_awaited()
    setup.refund.assert_not_called()
    new_result = ImageGenerateResult(task_id="retry-kie", status=TaskStatus.FAILED, fail_code="400")
    assert await completion._process_result_locked("retry-kie", new_result) is True
    failed.assert_awaited_once()
    assert failed.await_args.args[0]["credit_transaction_id"] == "original-credit-lock"
    smart.assert_not_awaited()


async def test_locked_completion_ignores_old_timeout_after_retry_started(setup, task, result):
    await setup.service.handle_failure(task, result)
    completion = TaskCompletionService(setup.db)
    completion._handle_failure = AsyncMock()
    timeout = ImageGenerateResult(task_id="retry-kie", status=TaskStatus.FAILED, fail_code="TIMEOUT")
    assert await completion._process_result_locked("retry-kie", timeout) is True
    completion._handle_failure.assert_not_awaited()


def test_safe_error_redacts_urls_and_credentials():
    message = safe_error(ValueError("bad resolution 1024x1024 https://cdn.example.com/a?token=secret Bearer hidden api_key=secret"))
    assert "1024x1024" in message
    assert "hidden" not in message and "secret" not in message and "cdn.example.com" not in message


@pytest.mark.parametrize("model,key", [
    ("google/nano-banana-edit", "image_urls"), ("nano-banana-pro", "image_input"),
    (MODEL, "input_urls"),
])
def test_snapshot_replays_each_kie_image_input_schema(task, cache, model, key):
    task["model_id"] = model
    cache["request"] = {"model": model, "input": {
        "prompt": "snapshot", key: [SOURCES[0], SOURCES[1], SOURCES[0]], "output_format": "jpg",
    }}
    prepared = replay_request(task, cache, None)
    assert prepared.input[key] == [STAGED[0], STAGED[1], STAGED[0]]
    assert prepared.input["output_format"] == "jpg"
    assert prepared.model == model


async def test_main_api_client_keeps_default_environment_proxy():
    client = KieClient("test-key")
    with patch("services.adapters.kie.client.httpx.AsyncClient") as factory:
        await client._get_client()
    kwargs = factory.call_args.kwargs
    assert kwargs["base_url"] == "https://api.kie.ai"
    assert kwargs.get("trust_env", True) is True
    assert "proxy" not in kwargs


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="invalid JSON"),
    httpx.Response(200, json={"code": 200, "msg": "success", "data": {}}),
    httpx.Response(200, json=[]),
    httpx.Response(502, json={"code": 502, "msg": "gateway error"}),
])
async def test_unusable_acknowledgement_is_uncertain_not_resubmitted(setup, task, result, monkeypatch, response):
    calls = []

    async def send(request):
        calls.append(request)
        return response

    def factory(model):
        client = KieClient("test-key")
        client._client = httpx.AsyncClient(base_url=client.BASE_URL, transport=httpx.MockTransport(send))
        setup.clients.append(client)
        return KieImageAdapter(client, model)

    monkeypatch.setattr("services.adapters.factory.create_image_adapter", factory)
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert setup.db.row["request_params"][STATE_KEY]["phase"] == "failed"
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert len(calls) == 1
    setup.service._schedule_wait.assert_not_called()


async def test_explicit_kie_rejection_is_final_without_second_post(setup, task, result, monkeypatch):
    from services.adapters.kie.client import KieAPIError
    post = AsyncMock(side_effect=KieAPIError("invalid image", status_code=400))
    monkeypatch.setattr(KieClient, "create_task_once", post)
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.FINAL_FAILURE
    assert post.await_count == 1


async def test_short_wait_handles_late_upload_and_notifies_completion_once(setup, task, result, cache, monkeypatch):
    setup.cache_get.side_effect = [{"status": "pending"}, cache, cache]
    monkeypatch.delattr(setup.service, "_schedule_wait")
    completion = TaskCompletionService(setup.db)
    # 该测试只隔离分布式锁；状态 CAS/适配器/序列化/后台续处理都是真实代码。
    process = AsyncMock(side_effect=completion._process_result_locked)
    monkeypatch.setattr(TaskCompletionService, "process_result", process)
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.PROCESSING
    assert setup.db.row["status"] == "pending"
    await asyncio.wait_for(asyncio.gather(*list(_waiters.values())), timeout=5)
    assert len(setup.requests) == 1
    assert setup.db.row["external_task_id"] == "retry-kie"
    assert not _waiters
    process.assert_awaited_once()


async def test_wait_deadline_background_completion_emits_failure_only_once(setup, task, result, monkeypatch):
    setup.cache_get.return_value = {"status": "pending"}
    monkeypatch.setattr("services.kie_image_fallback_service.WAIT_SECONDS", 1)
    monkeypatch.delattr(setup.service, "_schedule_wait")
    failures = []

    async def fail(_self, failed_task, **kwargs):
        failures.append(failed_task)
        setup.db.row["status"] = "failed"
        return True

    monkeypatch.setattr("services.batch_completion_service.BatchCompletionService.handle_image_failure", fail)
    completion = TaskCompletionService(setup.db)
    monkeypatch.setattr(TaskCompletionService, "process_result", lambda self, ext, data: completion._process_result_locked(ext, data))
    await setup.service.handle_failure(task, result)
    assert not failures
    await asyncio.wait_for(asyncio.gather(*list(_waiters.values())), timeout=4)
    assert len(failures) == 1
    assert failures[0]["credit_transaction_id"] == "original-credit-lock"
    assert not setup.requests


async def test_upload_wait_does_not_start_database_recovery_loop(setup, task, result, cache, monkeypatch):
    setup.cache_get.side_effect = [{"status": "pending"}, cache]
    monkeypatch.delattr(setup.service, "_schedule_wait")
    process = AsyncMock(side_effect=RuntimeError("database offline"))
    monkeypatch.setattr(TaskCompletionService, "process_result", process)
    await setup.service.handle_failure(task, result)
    await asyncio.wait_for(asyncio.gather(*list(_waiters.values())), timeout=3)
    process.assert_awaited_once()
    assert not _waiters and not setup.requests


async def test_parallel_webhook_and_poll_share_lock_and_only_post_once(setup, task, result, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    send_once = KieClient.create_task_once

    async def slow_send(client, request):
        entered.set()
        await release.wait()
        return await send_once(client, request)

    monkeypatch.setattr(KieClient, "create_task_once", slow_send)
    held = set()

    async def acquire(key, **kwargs):
        if key in held:
            return None
        held.add(key)
        return "lock-token"

    async def unlock(key, token):
        held.discard(key)

    monkeypatch.setattr("core.redis.RedisClient.acquire_lock", acquire)
    monkeypatch.setattr("core.redis.RedisClient.release_lock", unlock)
    completion = TaskCompletionService(setup.db)
    first = asyncio.create_task(completion.process_result(task["external_task_id"], result))
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert await completion.process_result(task["external_task_id"], result) is True
    release.set()
    assert await first is True
    assert len(setup.requests) == 1 and not held


async def test_cas_loser_does_not_submit(setup, task, cache, result, monkeypatch):
    real_save = setup.service._save_state

    def save_without_claim(current, state, **fields):
        if state["phase"] == "submitting":
            return False
        return real_save(current, state, **fields)

    monkeypatch.setattr(setup.service, "_save_state", save_without_claim)
    assert await setup.service.handle_failure(task, result) == FallbackOutcome.PROCESSING
    assert not setup.requests


@pytest.mark.parametrize("code", ["401", "429", "500", "TIMEOUT"])
async def test_other_failures_keep_original_flow(setup, task, code):
    other = ImageGenerateResult(task_id=task["external_task_id"], status=TaskStatus.FAILED, fail_code=code)
    assert await setup.service.handle_failure(task, other) == FallbackOutcome.NOT_APPLICABLE
    assert not setup.db.updates and not setup.requests


async def test_success_returns_through_original_completion_with_same_credit(setup, task, result, monkeypatch):
    await setup.service.handle_failure(task, result)
    completion = TaskCompletionService(setup.db)
    completed = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "_handle_success", completed)
    success = ImageGenerateResult(task_id="retry-kie", status=TaskStatus.SUCCESS, image_urls=["https://cdn.example.com/result.png"])
    assert await completion._process_result_locked("retry-kie", success) is True
    completed.assert_awaited_once()
    assert completed.await_args.args[0]["credit_transaction_id"] == task["credit_transaction_id"]
    assert completed.await_args.args[0]["placeholder_message_id"] == task["placeholder_message_id"]
