"""Runtime media Projection worker; ActionLoop remains the Provider owner."""

from __future__ import annotations

from typing import Awaitable, Callable, Mapping, Protocol

from loguru import logger

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.media_projection import (
    asset_request_from_readback,
)
from services.agent.runtime.infrastructure.postgres.media_projection import (
    PostgresMediaProjection,
)
from services.agent.runtime.ports.media_projection import (
    MediaPersistencePort, ProjectionNotifierPort,
)
from services.agent.runtime.ports.projection import ProjectionClaim


class MediaProjectionPort(Protocol):
    async def claim(self, batch_size: int = 50, lease_seconds: int = 60) -> tuple[ProjectionClaim, ...]: ...
    async def read(self, claim: ProjectionClaim) -> Mapping[str, object] | None: ...
    async def apply(self, claim: ProjectionClaim, action: str, content_part: Mapping[str, object] | None = None) -> Mapping[str, object]: ...
    async def fail(self, claim: ProjectionClaim, error_code: str) -> None: ...
    async def isolate(self, claim: ProjectionClaim, error_code: str) -> bool: ...
    async def read_result(self, claim: ProjectionClaim) -> Mapping[str, object] | None: ...


class RuntimeMediaProjectionWorker:
    """Apply ordered facts, persist completed media, then notify clients."""

    def __init__(
        self, projection: MediaProjectionPort,
        persistence: MediaPersistencePort,
        notifier: ProjectionNotifierPort | None = None,
        release_task_slot: Callable[[Mapping[str, object]], Awaitable[None]] | None = None,
    ) -> None:
        self._projection = projection
        self._persistence = persistence
        self._notifier = notifier
        self._release_task_slot = release_task_slot

    async def run_once(self, batch_size: int = 50) -> int:
        claims = await self._projection.claim(batch_size=batch_size)
        for claim in claims:
            await self._process(claim)
        return len(claims)

    async def _process(self, claim: ProjectionClaim) -> None:
        readback: Mapping[str, object] | None = None
        try:
            readback = await self._projection.read(claim)
            if readback is None:
                return
            if readback.get("outcome") == "already_applied":
                await self._release_terminal_slot(claim, readback)
                return
            action = _projection_action(claim.event.event_type)
            content_part = None
            if claim.event.event_type == "action.completed":
                request = asset_request_from_readback(claim, readback)
                content_part = dict(await self._persistence.persist(request))
                content_part.setdefault("source_url", request.source_url)
            result = await self._projection.apply(claim, action, content_part)
            if result.get("outcome") in {"applied", "already_applied"}:
                await self._release_terminal_slot(claim, readback)
            if result.get("outcome") == "applied":
                await self._notify(result.get("notification"))
        except PersistenceContractError as error:
            error_code = _error_code("contract", error)
            if await self._projection.isolate(claim, error_code):
                if readback is not None:
                    await self._release_terminal_slot(claim, readback)
            else:
                await self._projection.fail(claim, error_code)
        except Exception as error:
            if await self._projection.read_result(claim) is not None:
                if readback is not None:
                    await self._release_terminal_slot(claim, readback)
                return
            logger.warning(
                "runtime_media_projection_retry | outbox_id={} | event_id={} | error={}",
                claim.outbox_id, claim.event.event_id, type(error).__name__,
            )
            await self._projection.fail(claim, _error_code("apply", error))

    async def _release_terminal_slot(
        self, claim: ProjectionClaim, readback: Mapping[str, object],
    ) -> None:
        if self._release_task_slot is None:
            return
        task = _terminal_task(claim, readback)
        if task is None:
            return
        if not isinstance(task.get("request_params"), (Mapping, str)):
            raise PersistenceContractError("terminal media task facts required")
        await self._release_task_slot(task)

    async def _notify(self, payload: object) -> None:
        if self._notifier is None or not isinstance(payload, Mapping):
            return
        try:
            await self._notifier.notify(payload)
        except Exception as error:
            logger.warning(
                "runtime_media_projection_ws_failed | error={}", type(error).__name__,
            )


def _projection_action(event_type: str) -> str:
    if event_type.startswith("action."):
        return "action_progress"
    return {
        "run.created": "run_pending",
        "run.claimed": "run_running",
        "run.resumed": "run_running",
        "run.waiting": "run_waiting",
        "run.completed": "run_completed",
        "run.failed": "run_failed",
        "run.cancelled": "run_cancelled",
    }.get(event_type, "checkpoint_only")


def _terminal_task(
    claim: ProjectionClaim, readback: Mapping[str, object],
) -> Mapping[str, object] | None:
    event = claim.event
    action_terminal = event.event_type in {
        "action.completed", "action.failed", "action.rejected", "action.cancelled",
    }
    facts = readback.get("action_facts")
    if not isinstance(facts, Mapping):
        if action_terminal:
            raise PersistenceContractError("terminal media task facts required")
        return None
    task = facts.get("task")
    if action_terminal:
        if not isinstance(task, Mapping):
            raise PersistenceContractError("terminal media task facts required")
        return task
    run = facts.get("run")
    snapshot = run.get("capability_snapshot") if isinstance(run, Mapping) else None
    if event.event_type not in {"run.completed", "run.failed", "run.cancelled"}:
        return None
    if not isinstance(snapshot, Mapping):
        return None
    if (
        snapshot.get("source") == "runtime_media_retry"
        and snapshot.get("execution_mode") == "one_shot_action"
        and snapshot.get("projection_mode") == "media_action_only"
    ):
        if not isinstance(task, Mapping):
            raise PersistenceContractError("terminal media retry task facts required")
        return task
    if facts.get("run_projection_mode") == "runtime_media_initial":
        if not isinstance(task, Mapping):
            raise PersistenceContractError("terminal media run task facts required")
        slot_id = facts.get("chat_task_slot_id")
        if (
            not isinstance(slot_id, str) or not slot_id.strip()
            or slot_id != _task_slot_id(task)
        ):
            raise PersistenceContractError("terminal media task slot facts invalid")
        return task
    return None


def _task_slot_id(task: Mapping[str, object]) -> object:
    params = task.get("request_params")
    if isinstance(params, Mapping):
        return params.get("_task_slot_id")
    return None


def _error_code(prefix: str, error: Exception) -> str:
    return f"{prefix}_{type(error).__name__}".lower()[:200]


class WebsocketMediaProjectionNotifier:
    """Adapt the committed notification to the existing task/user WS route."""

    def __init__(self, websocket_manager: object) -> None:
        self._websocket_manager = websocket_manager

    async def notify(self, payload: Mapping[str, object]) -> None:
        task_id = _required_text(payload.get("task_id"), "task_id")
        user_id = _required_text(payload.get("user_id"), "user_id")
        message_id = _required_text(payload.get("message_id"), "message_id")
        message = {
            "type": "image_partial_update",
            "task_id": task_id,
            "message_id": message_id,
            "payload": {
                key: payload[key]
                for key in (
                    "slot_id", "slot_index", "slot_status", "slot_revision",
                    "content_part",
                ) if key in payload
            },
        }
        sender = getattr(self._websocket_manager, "send_to_task_or_user")
        await sender(
            task_id=task_id, user_id=user_id, message=message,
            org_id=payload.get("org_id"),
        )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"RUNTIME_MEDIA_WS_{name.upper()}_REQUIRED")
    return value


def build_runtime_media_projection_worker(
    database: object, persistence: MediaPersistencePort,
    websocket_manager: object | None = None,
) -> RuntimeMediaProjectionWorker:
    """Provide the later composition root a projection-only builder."""

    notifier = (
        WebsocketMediaProjectionNotifier(websocket_manager)
        if websocket_manager is not None else None
    )
    from services.task_limit_service import release_task_slot

    return RuntimeMediaProjectionWorker(
        PostgresMediaProjection(database), persistence, notifier,
        release_task_slot,
    )


__all__ = [
    "RuntimeMediaProjectionWorker", "WebsocketMediaProjectionNotifier",
    "build_runtime_media_projection_worker",
]
