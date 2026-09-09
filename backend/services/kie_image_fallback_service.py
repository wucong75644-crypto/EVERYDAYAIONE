"""KIE 图片获取失败的独立单次重试；不负责结算或前端结果持久化。"""

import asyncio
import os
import time
from datetime import datetime, timezone
from enum import Enum

import httpx
from loguru import logger

from core.task_config import IMAGE_TASK_TIMEOUT_MINUTES
from services.adapters.base import ImageGenerateResult, TaskStatus
from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
from services.adapters.kie.image_adapter import KieSubmissionUncertainError
from services.adapters.kie.shadow_upload_store import get_overseas_shadow_upload
from services.kie_image_fallback_request import (
    legacy_generate_kwargs, replay_request, request_params, safe_error,
)

STATE_KEY = "_kie_image_fetch_fallback"
ATTEMPTED_KEY = "_kie_image_fetch_fallback_attempted"
FROM_KEY = "_kie_image_fetch_fallback_from_task_id"
WAIT_SECONDS = 60
SUBMIT_SECONDS = 60
_waiters: dict[tuple[str, str], asyncio.Task] = {}


class FallbackOutcome(Enum):
    NOT_APPLICABLE = "not_applicable"
    PROCESSING = "processing"
    FINAL_FAILURE = "final_failure"


def is_kie_image(task: dict) -> bool:
    return task.get("type") == "image" and task.get("model_id") in IMAGE_MODEL_CONFIGS


def fallback_state(task: dict) -> dict:
    state = request_params(task).get(STATE_KEY, {})
    return state if isinstance(state, dict) else {}


def needs_fallback_resume(task: dict) -> bool:
    return is_kie_image(task) and fallback_state(task).get("phase") == "waiting"


def preserve_fallback_on_restart(task: dict) -> bool:
    """专用重试仍走原完成/结算入口，不能由启动时流式孤儿清理抢先退款。"""
    if not is_kie_image(task) or not task.get("external_task_id"):
        return False
    params = request_params(task)
    return bool(params.get(STATE_KEY) or params.get(ATTEMPTED_KEY))


def defer_stale_timeout(task: dict) -> bool:
    """必须在完成处理锁内使用最新记录；上传等待有自己的截止时间。"""
    if not is_kie_image(task) or needs_fallback_resume(task):
        return False
    started = task.get("started_at")
    if not started:
        return False
    elapsed = time.time() - datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
    return elapsed < IMAGE_TASK_TIMEOUT_MINUTES * 60


class KieImageFallbackService:
    def __init__(self, db):
        self.db = db

    def _get_task(self, local_id: str) -> dict | None:
        return self.db.table("tasks").select("*").eq("id", local_id).maybe_single().execute().data

    def _save_state(self, task: dict, state: dict, **fields) -> bool:
        params = {**request_params(task), STATE_KEY: state, FROM_KEY: state["original_task_id"]}
        if state["phase"] != "waiting":
            params[ATTEMPTED_KEY] = True
        version = task.get("version", 1)
        update = {"request_params": params, "version": version + 1, **fields}
        result = (
            self.db.table("tasks").update(update).eq("id", task["id"])
            .eq("external_task_id", task["external_task_id"]).eq("version", version)
            .in_("status", ["pending", "running"]).execute()
        )
        if not result.data:
            return False
        task.update(update)
        return True

    async def handle_failure(self, task: dict, result: ImageGenerateResult) -> FallbackOutcome:
        if not is_kie_image(task):
            return FallbackOutcome.NOT_APPLICABLE
        params = request_params(task)
        known = bool(params.get(STATE_KEY) or params.get(ATTEMPTED_KEY))
        enabled = os.getenv("KIE_IMAGE_FETCH_FALLBACK_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        if not known and (not enabled or str(result.fail_code or "") != "400"):
            return FallbackOutcome.NOT_APPLICABLE

        current = self._get_task(task["id"])
        if not current or current["status"] not in {"pending", "running"}:
            return FallbackOutcome.PROCESSING
        if current["external_task_id"] != task["external_task_id"]:
            return FallbackOutcome.PROCESSING
        task = current
        state = fallback_state(task)
        if state.get("phase") in {"submitted", "failed"}:
            return FallbackOutcome.FINAL_FAILURE
        if not state and request_params(task).get(ATTEMPTED_KEY):
            return FallbackOutcome.FINAL_FAILURE  # 兼容上一版已经发出的重试。
        if not state:
            state = {
                "phase": "waiting", "original_task_id": task["external_task_id"],
                "wait_deadline": time.time() + WAIT_SECONDS,
                "original_started_at": task.get("started_at"),
            }
            if not self._save_state(task, state, started_at=self._now(), status="pending"):
                return FallbackOutcome.PROCESSING
            self._log(task, "WAITING")
        if state["phase"] == "submitting":
            # 上一次已进入提交，不能用原任务的失败回调再次发起 POST。
            return self._fail(task, state, "previous_submission_unfinished")
        return await self._wait_or_submit(task, state)

    async def _wait_or_submit(self, task: dict, state: dict) -> FallbackOutcome:
        cache = await get_overseas_shadow_upload(state["original_task_id"])
        if cache and cache["status"] == "failed":
            return self._fail(task, state, "shadow_upload_failed")
        # 上传在窗口内完成，即使恢复轮询较晚仍可使用；旧缓存没有时间则只在窗口内使用。
        ready_in_time = cache and cache["status"] == "ready" and cache.get("updated_at", time.time()) <= state["wait_deadline"]
        if not ready_in_time:
            if time.time() >= state["wait_deadline"]:
                return self._fail(task, state, "shadow_upload_wait_timeout")
            self._schedule_wait(task, state["wait_deadline"])
            return FallbackOutcome.PROCESSING
        return await self._submit(task, state, cache)

    async def _submit(self, task: dict, state: dict, cache: dict) -> FallbackOutcome:
        from services.adapters.factory import create_image_adapter
        from services.handlers.base import BaseHandler

        adapter = None
        try:
            adapter = create_image_adapter(task["model_id"])
            callback = BaseHandler._build_callback_url(None, adapter.provider.value)
            prepared = replay_request(task, cache, callback)
            kwargs = legacy_generate_kwargs(task, cache, adapter, callback) if prepared is None else None
        except Exception as exc:
            if adapter:
                await self._close_adapter(adapter, task)
            return self._fail(task, state, "prepare", exc)

        retry_task_id = None
        try:
            state = {**state, "phase": "submitting"}
            if not self._save_state(task, state):
                return FallbackOutcome.PROCESSING
            self._log(task, "SUBMITTING")
            async with asyncio.timeout(SUBMIT_SECONDS):
                if prepared is not None:
                    response = await adapter.submit_prepared_fallback(prepared)
                else:
                    response = await adapter.generate(**kwargs)
            if not response.task_id:
                raise ValueError("Fallback response missing task id")
            retry_task_id = response.task_id
            self._log(task, "ACCEPTED", retry_task_id=retry_task_id)
            bound = self._save_state(
                task, {**state, "phase": "submitted", "retry_task_id": retry_task_id},
                external_task_id=retry_task_id, status="pending", started_at=self._now(),
                error_message=None, completed_at=None,
            )
            self._log(task, "SUBMITTED" if bound else "BIND_NOT_APPLIED",
                      retry_task_id=retry_task_id, manual_review=not bound)
            return FallbackOutcome.PROCESSING
        except Exception as exc:
            if retry_task_id:
                # 保留日志供人工核查，不增加回执缓存、补绑定或再次提交机制。
                self._log(task, "BIND_FAILED", retry_task_id=retry_task_id,
                          manual_review=True, error_type=type(exc).__name__, error=safe_error(exc))
                return self._fail(task, {**state, "retry_task_id": retry_task_id}, "bind", exc)
            if isinstance(exc, (httpx.RequestError, TimeoutError, KieSubmissionUncertainError)):
                return self._fail(task, state, "submission_unconfirmed", exc)
            return self._fail(task, state, "submit", exc)
        finally:
            await self._close_adapter(adapter, task)

    def _fail(self, task: dict, state: dict, reason: str, exc: Exception | None = None) -> FallbackOutcome:
        self._log(task, "FAILED", reason=reason, error_type=type(exc).__name__ if exc else "", error=safe_error(exc) if exc else "")
        if not self._save_state(task, {**state, "phase": "failed", "reason": reason}):
            return FallbackOutcome.PROCESSING
        return FallbackOutcome.FINAL_FAILURE

    def _schedule_wait(self, task: dict, deadline: float) -> None:
        key = (task["id"], fallback_state(task)["original_task_id"])
        running = _waiters.get(key)
        if running and not running.done():
            return
        _waiters[key] = asyncio.create_task(
            self._wait_for_upload(key, deadline), name=f"kie-image-fallback:{key[0]}",
        )

    async def _wait_for_upload(self, key: tuple[str, str], deadline: float) -> None:
        """仅等待旁路缓存；就绪或到期后通知原完成入口一次，不做故障恢复循环。"""
        from services.task_completion_service import TaskCompletionService

        try:
            while time.time() < deadline:
                await asyncio.sleep(min(1, max(0, deadline - time.time())))
                cache = await get_overseas_shadow_upload(key[1])
                if cache and cache["status"] != "pending":
                    break
            result = ImageGenerateResult(task_id=key[1], status=TaskStatus.FAILED, fail_code="400", fail_msg="Image fetch failed")
            await TaskCompletionService(self.db).process_result(key[1], result)
        except Exception as exc:
            logger.warning("KIE_IMAGE_FETCH_FALLBACK_WAIT_FAILED | local_task_id={} | error_type={}", key[0], type(exc).__name__)
        finally:
            if _waiters.get(key) is asyncio.current_task():
                _waiters.pop(key, None)

    @staticmethod
    async def _close_adapter(adapter, task: dict) -> None:
        try:
            await adapter.close()
        except Exception as exc:
            logger.warning("KIE_IMAGE_FETCH_FALLBACK_CLOSE_FAILED | local_task_id={} | error_type={}", task["id"], type(exc).__name__)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _log(task: dict, event: str, **details) -> None:
        state = fallback_state(task)
        started = state.get("wait_deadline", time.time() + WAIT_SECONDS) - WAIT_SECONDS
        logger.info(
            "KIE_IMAGE_FETCH_FALLBACK_{} | local_task_id={} | original_task_id={} | "
            "retry_task_id={} | phase={} | duration_ms={} | details={}",
            event, task["id"], state.get("original_task_id", task["external_task_id"]),
            details.pop("retry_task_id", state.get("retry_task_id", "")), state.get("phase", ""),
            max(0, int((time.time() - started) * 1000)), details,
        )
