from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
from uuid import uuid4

import pytest

from services.agent.runtime.application.media_projection_worker import (
    RuntimeMediaProjectionWorker, WebsocketMediaProjectionNotifier,
    _projection_action,
)
from services.agent.runtime.application.media_persistence import RuntimeMediaPersistence
from services.agent.runtime.domain import (
    EventDurability, EventSequence, RuntimeActorType, RuntimeEvent,
    RuntimeScope, ScopeKind,
)
from services.agent.runtime.ports.media_projection import (
    MediaProjectionAssetRequest,
)
from services.agent.runtime.ports.projection import ProjectionClaim
from services.agent.runtime.infrastructure.postgres.media_projection import (
    MediaSlotReleaseClaim,
)


def _claim(
    event_type: str, payload: Mapping[str, object] | None = None,
) -> ProjectionClaim:
    event = RuntimeEvent(
        event_id=f"event-{event_type}", session_id="session",
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user", user_id="user", org_id=None,
        ), event_type=event_type, event_version=1,
        durability=EventDurability.DURABLE, correlation_id="correlation",
        actor_type=RuntimeActorType.EXECUTOR, payload_hash="hash",
        occurred_at=datetime.now(timezone.utc), redaction_revision="v1",
        payload=payload or {},
        sequence=EventSequence(1),
        action_id=None if event_type.startswith("run.") else "action",
    )
    return ProjectionClaim(
        outbox_id="outbox", projection_kind="web_runtime", lease_token="lease",
        lease_expires_at=datetime.now(timezone.utc), attempt_count=1,
        checkpoint={}, event=event,
    )


def _slot_release_claim() -> MediaSlotReleaseClaim:
    return MediaSlotReleaseClaim(
        release_id=str(uuid4()), lease_token=str(uuid4()),
        task={
            "id": uuid4(), "user_id": uuid4(), "org_id": None,
            "conversation_id": uuid4(),
            "request_params": {"_task_slot_id": "limit-slot"},
        },
    )


class _Projection:
    def __init__(
        self, event_type: str, readback: Mapping[str, object],
        payload: Mapping[str, object] | None = None,
        slot_releases: tuple[MediaSlotReleaseClaim, ...] = (),
    ) -> None:
        self.claim_value = _claim(event_type, payload)
        self.readback_value = readback
        self.applied: list[tuple[str, Mapping[str, object] | None]] = []
        self.failed: list[str] = []
        self.isolated: list[str] = []
        self.slot_releases = slot_releases
        self.acked_releases = []
        self.failed_releases = []

    async def claim(self, batch_size: int = 50, lease_seconds: int = 60):
        return (self.claim_value,)

    async def read(self, claim):
        return self.readback_value

    async def apply(self, claim, action: str, content_part=None):
        self.applied.append((action, content_part))
        return {"outcome": "applied", "notification": {"message_id": "message"}}

    async def fail(self, claim, error_code: str):
        self.failed.append(error_code)

    async def isolate(self, claim, error_code: str):
        self.isolated.append(error_code)
        return True

    async def read_result(self, claim):
        return None

    async def claim_slot_releases(
        self, batch_size: int = 50, lease_seconds: int = 60,
    ):
        return self.slot_releases

    async def ack_slot_release(self, claim):
        self.acked_releases.append(claim.release_id)

    async def fail_slot_release(self, claim, error_code):
        self.failed_releases.append((claim.release_id, error_code))


class _Persistence:
    def __init__(self) -> None:
        self.requests: list[MediaProjectionAssetRequest] = []

    async def persist(self, request: MediaProjectionAssetRequest):
        self.requests.append(request)
        return {"url": "https://cdn.example/image.png", "source_url": request.source_url}


class _Notifier:
    def __init__(self, fail: bool = False) -> None:
        self.payloads: list[Mapping[str, object]] = []
        self.fail = fail

    async def notify(self, payload: Mapping[str, object]) -> None:
        self.payloads.append(payload)
        if self.fail:
            raise RuntimeError("websocket unavailable")


class _AlreadyAppliedProjection(_Projection):
    async def read(self, claim):
        return {"outcome": "already_applied"}


class _Websocket:
    def __init__(self) -> None:
        self.calls = []

    async def send_to_task_or_user(self, **kwargs):
        self.calls.append(kwargs)


class _AssetRegistry:
    def __init__(self) -> None:
        self.calls = []

    def register_runtime_media_asset(self, request, payload):
        self.calls.append((request.identity, dict(payload)))
        return {"asset": {"id": "asset-1"}}


def _facts() -> dict[str, object]:
    return {
        "outcome": "found",
        "action_facts": {
            "media_kind": "image",
            "binding": {
                "action_id": "action", "action_index": 0,
                "slot_id": "stable-slot",
                "user_id": "user", "conversation_id": "conversation",
                "output_message_id": "message", "task_id": "task",
                "pricing_model_id": "model",
            },
            "task": {
                "id": "task", "user_id": "user", "org_id": None,
                "conversation_id": "conversation",
                "request_params": {"prompt": "p", "_task_slot_id": "limit-slot"},
            },
            "result_urls": ["https://provider.example/result.png"],
        },
    }


@pytest.mark.parametrize(
    ("event_type", "expected"),
    (
        ("action.cost.reserve", "checkpoint_only"),
        ("action.cost.settle", "checkpoint_only"),
        ("action.provider.accepted", "action_progress"),
        ("action.completed", "action_progress"),
        ("run.completed", "run_completed"),
    ),
)
def test_projection_action_matches_runtime_media_event_contract(
    event_type: str, expected: str,
) -> None:
    assert _projection_action(event_type) == expected


@pytest.mark.asyncio
async def test_completed_uses_readback_facts_and_persists_before_apply() -> None:
    projection = _Projection("action.completed", _facts())
    persistence = _Persistence()
    notifier = _Notifier()

    count = await RuntimeMediaProjectionWorker(
        projection, persistence, notifier,
    ).run_once()

    assert count == 1
    assert persistence.requests[0].source_url == "https://provider.example/result.png"
    assert persistence.requests[0].identity == "runtime-media:image:action:stable-slot"
    assert projection.applied[0][0] == "action_progress"
    assert projection.applied[0][1]["source_url"] == "https://provider.example/result.png"
    assert projection.failed == []
    assert notifier.payloads == [{"message_id": "message"}]


@pytest.mark.asyncio
async def test_unknown_never_persists_or_refunds_and_ws_failure_is_best_effort() -> None:
    projection = _Projection("action.unknown", {"outcome": "found"})
    persistence = _Persistence()
    notifier = _Notifier(fail=True)

    await RuntimeMediaProjectionWorker(
        projection, persistence, notifier,
    ).run_once()

    assert persistence.requests == []
    assert projection.applied == [("action_progress", None)]
    assert projection.failed == []


@pytest.mark.asyncio
async def test_missing_authoritative_facts_isolates_terminal_poison() -> None:
    projection = _Projection(
        "action.completed", {"outcome": "found", "action_facts": {}},
    )

    await RuntimeMediaProjectionWorker(
        projection, _Persistence(), _Notifier(),
    ).run_once()

    assert projection.applied == []
    assert projection.failed == []
    assert projection.isolated == ["contract_persistencecontracterror"]


@pytest.mark.asyncio
async def test_duplicate_readback_does_not_persist_or_apply_again() -> None:
    projection = _AlreadyAppliedProjection("action.completed", _facts())
    persistence = _Persistence()

    await RuntimeMediaProjectionWorker(projection, persistence).run_once()

    assert persistence.requests == []
    assert projection.applied == []


@pytest.mark.asyncio
async def test_persistence_and_asset_registration_are_identity_idempotent() -> None:
    workspace_calls = []

    async def workspace_persist(**kwargs):
        workspace_calls.append(kwargs)
        return {"url": "https://oss.example/runtime.png", "name": "runtime.png"}

    registry = _AssetRegistry()
    persistence = RuntimeMediaPersistence(
        workspace_persist=workspace_persist, asset_registry=registry,
    )
    request = MediaProjectionAssetRequest(
        action_id="action", slot_id="stable-slot", slot_index=0,
        source_url="https://provider.example/result.png", user_id="user",
        org_id=None, conversation_id="conversation", message_id="message",
        task_id="task", model_id="model", prompt="p", aspect_ratio="1:1",
        resolution=None,
    )

    first = await persistence.persist(request)
    second = await persistence.persist(request)

    assert first == second
    assert len(workspace_calls) == 1
    assert len(registry.calls) == 1


@pytest.mark.asyncio
async def test_prepared_video_uses_result_urls_and_video_persistence() -> None:
    facts = _facts()
    action_facts = facts["action_facts"]
    assert isinstance(action_facts, dict)
    action_facts["media_kind"] = "video"
    binding = action_facts["binding"]
    assert isinstance(binding, dict)
    binding.pop("slot_id")
    binding.pop("action_index")
    action_facts["result_urls"] = ["https://provider.example/result.mp4"]
    projection = _Projection("action.completed", facts)
    persistence = _Persistence()

    await RuntimeMediaProjectionWorker(projection, persistence).run_once()

    request = persistence.requests[0]
    assert request.media_kind == "video"
    assert request.slot_id == "action"
    assert request.slot_index == 0
    assert request.identity == "runtime-media:video:action:action"


@pytest.mark.asyncio
async def test_completed_after_cancel_persists_before_normalized_apply() -> None:
    facts = _facts()
    action_facts = facts["action_facts"]
    assert isinstance(action_facts, dict)
    action_facts["media_kind"] = "video"
    action_facts["result_urls"] = ["https://provider.example/late.mp4"]
    projection = _Projection("action.completed_after_cancel", facts)
    persistence = _Persistence()

    await RuntimeMediaProjectionWorker(projection, persistence).run_once()

    assert persistence.requests[0].source_url == "https://provider.example/late.mp4"
    assert projection.applied[0][0] == "action_progress"
    assert projection.applied[0][1]["source_url"] == (
        "https://provider.example/late.mp4"
    )


@pytest.mark.asyncio
async def test_prepared_image_batch_uses_task_image_index() -> None:
    facts = _facts()
    action_facts = facts["action_facts"]
    assert isinstance(action_facts, dict)
    binding = action_facts["binding"]
    task = action_facts["task"]
    assert isinstance(binding, dict)
    assert isinstance(task, dict)
    binding.pop("slot_id")
    binding.pop("action_index")
    task["image_index"] = 7
    projection = _Projection("action.completed", facts)
    persistence = _Persistence()

    await RuntimeMediaProjectionWorker(projection, persistence).run_once()

    request = persistence.requests[0]
    assert request.slot_id == "action"
    assert request.slot_index == 7
    assert request.identity == "runtime-media:image:action:action"


@pytest.mark.asyncio
async def test_durable_slot_release_acks_only_after_redis_success() -> None:
    release_claim = _slot_release_claim()
    projection = _Projection(
        "action.failed", _facts(), slot_releases=(release_claim,),
    )
    released = []

    async def release(task: Mapping[str, object]) -> None:
        released.append(task["id"])

    count = await RuntimeMediaProjectionWorker(
        projection, _Persistence(), release_task_slot=release,
    ).run_once()

    assert count == 2
    assert released == [release_claim.task["id"]]
    assert projection.acked_releases == [release_claim.release_id]
    assert projection.failed_releases == []


@pytest.mark.asyncio
async def test_durable_slot_release_failure_is_retained_for_retry() -> None:
    release_claim = _slot_release_claim()
    projection = _Projection(
        "action.failed", _facts(), slot_releases=(release_claim,),
    )

    async def release(task: Mapping[str, object]) -> None:
        raise ConnectionError("redis unavailable")

    await RuntimeMediaProjectionWorker(
        projection, _Persistence(), release_task_slot=release,
    ).run_once()

    assert projection.acked_releases == []
    assert projection.failed_releases == [(
        release_claim.release_id, "slot_release_connectionerror",
    )]


@pytest.mark.asyncio
async def test_ws_protocol_contains_stable_slot_contract() -> None:
    websocket = _Websocket()
    await WebsocketMediaProjectionNotifier(websocket).notify({
        "task_id": "task", "user_id": "user", "message_id": "message",
        "org_id": "org", "slot_id": "action", "slot_index": 0,
        "slot_status": "completed", "slot_revision": 7,
        "content_part": {"type": "image", "url": "https://oss.example/a"},
    })

    payload = websocket.calls[0]["message"]["payload"]
    assert payload["slot_id"] == "action"
    assert payload["slot_index"] == 0
    assert payload["slot_status"] == "completed"
    assert payload["slot_revision"] == 7
