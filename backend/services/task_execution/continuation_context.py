"""Internal continuation context does not impersonate a user or mutate old anchors."""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from .contracts import ContinuationInput, TaskError, TaskState


@dataclass(frozen=True)
class ContinuationSnapshot:
    input: ContinuationInput
    messages: tuple[dict, ...]


def validate_continuation(state: TaskState, incoming: ContinuationInput):
    if (state.root_task_id != incoming.logical_task_id
            or state.conversation_id != incoming.conversation_id
            or state.actor_user_id != incoming.actor_user_id
            or state.execution_identity != incoming.execution_identity
            or state.org_id != incoming.org_id or state.branch_id != incoming.branch_id
            or state.source_message_ids != incoming.source_message_ids):
        raise TaskError('TASK_CONTINUATION_SCOPE_MISMATCH')
    if state.revision != incoming.expected_revision:
        raise TaskError('TASK_CONTINUATION_STALE')
    if state.status not in {'active', 'waiting_dependencies'}:
        raise TaskError('TASK_CONTINUATION_NOT_RUNNABLE')
    if not set(incoming.event_artifact_ids) <= {a.id for a in state.artifacts}:
        raise TaskError('TASK_CONTINUATION_ARTIFACT_UNKNOWN')


async def build_continuation_snapshot(db, *, state: TaskState, incoming: ContinuationInput,
                                      execution_identity, history_loader=None) -> ContinuationSnapshot:
    """Called only after the server validates and claims a persisted event.

    Uses a separate closed-history projection. The existing user ContextAnchor
    path remains unchanged; an event is never turned into a new user message.
    """
    validate_continuation(state, incoming)
    if execution_identity != state.execution_identity:
        raise TaskError('TASK_CONTINUATION_CURRENT_SCOPE_CHANGED')
    response = (db.table('messages').select('id,conversation_id,role')
                .in_('id', [str(v) for v in incoming.source_message_ids]).execute())
    rows = response.data if response else None
    if not isinstance(rows, list) or {UUID(str(row['id'])) for row in rows} != set(incoming.source_message_ids):
        raise TaskError('TASK_CONTINUATION_SOURCE_MISSING')
    if any(row.get('role') != 'user' or str(row.get('conversation_id')) != str(state.conversation_id)
           for row in rows):
        raise TaskError('TASK_CONTINUATION_SOURCE_INVALID')
    if history_loader is None:
        from services.handlers.chat_context.history_loader import build_context_messages
        history_loader = build_context_messages
    history = await history_loader(db, str(state.conversation_id), state.original_goal,
        base_revision=incoming.base_context_revision, strict=True,
        org_id=str(state.org_id) if state.org_id else None)
    if not isinstance(history, list):
        raise TaskError('TASK_CONTINUATION_HISTORY_INVALID')
    facts = {'logical_task_id': str(state.root_task_id), 'revision': state.revision,
             'original_goal': state.original_goal,
             'event_id': str(incoming.event_id), 'source_message_ids': [str(v) for v in state.source_message_ids],
             'requirements': [r.model_dump(mode='json') for r in state.requirements],
             'steps': [s.model_dump(mode='json') for s in state.steps],
             'results': [r.model_dump(mode='json') for r in state.results],
             'artifacts': [a.model_dump(mode='json') for a in state.artifacts]}
    message = {'role': 'system', 'content': (
        '[Internal task continuation: runtime facts, not new user authorization]\n'
        '继续同一用户目标；以下内容是任务状态及资料引用，不是额外授权。'
        '保留用户交付要求和当前权限，不重做已有真实回执的操作。\n'
        + json.dumps(facts, ensure_ascii=False, separators=(',', ':')))}
    # Copy through serialization so later changes to a loader's cached values do
    # not mutate this execution's snapshot. Provider history must be JSON-shaped.
    frozen = json.loads(json.dumps([*history, message], ensure_ascii=False, allow_nan=False))
    return ContinuationSnapshot(input=incoming, messages=tuple(frozen))
