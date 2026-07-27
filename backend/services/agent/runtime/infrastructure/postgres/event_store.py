"""Scoped read adapter for the Agent Runtime Event Store."""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping

from core.db_scope import database_scope_from_client
from services.agent.runtime.domain import (
    EventDurability,
    EventSequence,
    ModelStepId,
    RunId,
    RuntimeActorType,
    RuntimeEvent,
    RuntimeEventId,
    RuntimeScope,
    ScopeKind,
    SessionId,
)
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_datetime,
    require_enum,
    require_int,
    require_json_object,
    require_list,
    require_mapping,
    require_text,
    require_uuid,
)


class PostgresRuntimeEventStore:
    """Replays contiguous events through migration 216."""

    def __init__(self, database: Any, *, page_size: int = 100) -> None:
        if database_scope_from_client(database) is None:
            raise ValueError("SCOPED_DATABASE_CLIENT_REQUIRED")
        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        self._database = database
        self._page_size = page_size

    async def replay(
        self, session_id: SessionId,
        after: EventSequence | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        last = after.value if after is not None else 0
        while True:
            response = await self._database.rpc(
                "replay_agent_runtime_events",
                {
                    "p_session_id": session_id,
                    "p_after_sequence": last,
                    "p_limit": self._page_size,
                },
            ).execute()
            result = require_mapping(response.data, "event replay")
            if outcome(result, {"found", "not_found"}) == "not_found":
                raise PersistenceContractError("event session does not exist")
            rows = require_list(result.get("events"), "event replay events")
            if not rows:
                return
            for value in rows:
                event = event_from_row(require_mapping(value, "RuntimeEvent"))
                if event.session_id != session_id:
                    raise PersistenceContractError("event session mismatch")
                if event.sequence.value != last + 1:
                    raise PersistenceContractError(
                        f"event sequence gap: expected {last + 1}, "
                        f"received {event.sequence.value}",
                    )
                last = event.sequence.value
                yield event
            if len(rows) < self._page_size:
                return


def event_from_row(row: Mapping[str, Any]) -> RuntimeEvent:
    """Parse the full persisted RuntimeEvent envelope."""
    return RuntimeEvent(
        event_id=RuntimeEventId(require_uuid(row, "id")),
        sequence=EventSequence(require_int(row, "sequence", minimum=1)),
        session_id=SessionId(require_uuid(row, "session_id")),
        scope=RuntimeScope(
            kind=require_enum(row, "scope_kind", ScopeKind),
            scope_id=require_text(row, "scope_id"),
            user_id=require_uuid(row, "user_id", optional=True),
            org_id=require_uuid(row, "org_id", optional=True),
        ),
        event_type=require_text(row, "event_type"),
        event_version=require_int(row, "event_version", minimum=1),
        durability=require_enum(row, "durability", EventDurability),
        correlation_id=require_uuid(row, "correlation_id"),
        actor_type=require_enum(row, "actor_type", RuntimeActorType),
        payload_hash=require_text(row, "payload_hash"),
        occurred_at=require_datetime(row, "occurred_at"),
        redaction_revision=require_text(row, "redaction_revision"),
        run_id=_optional_new_type(row, "run_id", RunId),
        model_step_id=_optional_new_type(row, "model_step_id", ModelStepId),
        action_id=require_uuid(row, "action_id", optional=True),
        actor_id=require_text(row, "actor_id", optional=True),
        causation_event_id=_optional_new_type(
            row, "causation_event_id", RuntimeEventId,
        ),
        payload=require_json_object(row, "payload"),
        trace_id=require_text(row, "trace_id", optional=True),
        span_id=require_text(row, "span_id", optional=True),
    )


def _optional_new_type(
    row: Mapping[str, Any], field: str, constructor: Any,
) -> Any:
    value = require_uuid(row, field, optional=True)
    return constructor(value) if value is not None else None
