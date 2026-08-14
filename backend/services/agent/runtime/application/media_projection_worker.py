"""Runtime media Projection worker; ActionLoop remains the Provider owner."""

from __future__ import annotations

from typing import Awaitable, Callable, Mapping, Protocol

from loguru import logger

from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.media_projection import (
    asset_request_from_readback,
)
from services.agent.runtime.infrastructure.postgres.media_projection import (
    MediaSlotReleaseClaim, PostgresMediaProjection,
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
    async def claim_slot_releases(self, batch_size: int = 50, lease_seconds: int = 60) -> tuple[MediaSlotReleaseClaim, ...]: ...
    async def ack_slot_release(self, claim: MediaSlotReleaseClaim) -> None: ...
    async def fail_slot_release(self, claim: MediaSlotReleaseClaim, error_code: str) -> None: ...


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
        releases = await self._projection.claim_slot_releases(batch_size=batch_size)
        for release in releases:
            await self._release_slot(release)
        return len(claims) + len(releases)

    async def _process(self, claim: ProjectionClaim) -> None:
        try:
            readback = await self._projection.read(claim)
            if readback is None:
                return
            if readback.get("outcome") == "already_applied":
                return
            action = _projection_action(claim.event.event_type)
            content_part = None
            if claim.event.event_type == "action.completed":
                request = asset_request_from_readback(claim, readback)
                content_part = dict(await self._persistence.persist(request))
                content_part.setdefault("source_url", request.source_url)
            result = await self._projection.apply(claim, action, content_part)
            if result.get("outcome") == "applied":
                await self._notify(result.get("notification"))
        except PersistenceContractError as error:
            error_code = _error_code("contract", error)
            if not await self._projection.isolate(claim, error_code):
                await self._projection.fail(claim, error_code)
        except Exception as error:
            if await self._projection.read_result(claim) is not None:
                return
            logger.warning(
                "runtime_media_projection_retry | outbox_id={} | event_id={} | error={}",
                claim.outbox_id, claim.event.event_id, type(error).__name__,
            )
            await self._projection.fail(claim, _error_code("apply", error))

    async def _release_slot(self, claim: MediaSlotReleaseClaim) -> None:
        if self._release_task_slot is None:
            await self._projection.fail_slot_release(
                claim, "slot_release_service_unavailable",
            )
            return
        try:
            await self._release_task_slot(claim.task)
        except Exception as error:
            await self._projection.fail_slot_release(
                claim, _error_code("slot_release", error),
            )
            return
        await self._projection.ack_slot_release(claim)

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
    from services.task_limit_service import release_task_slot_checked

    return RuntimeMediaProjectionWorker(
        PostgresMediaProjection(database), persistence, notifier,
        release_task_slot_checked,
    )


__all__ = [
    "RuntimeMediaProjectionWorker", "WebsocketMediaProjectionNotifier",
    "build_runtime_media_projection_worker",
]
