"""Continuation scope and bounded history projection, without a synthetic user turn."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.task_execution.contracts import ContinuationInput, TaskError
from services.task_execution.continuation_context import build_continuation_snapshot, validate_continuation
from services.task_execution.inventory import inventory
from tests.test_task_execution_reducer import state


def incoming(s, **changes):
    return ContinuationInput(logical_task_id=s.root_task_id, event_id=uuid4(), expected_revision=s.revision,
        conversation_id=s.conversation_id, actor_user_id=s.actor_user_id, org_id=s.org_id,
        branch_id=s.branch_id, execution_identity=s.execution_identity,
        source_message_ids=s.source_message_ids, base_context_revision=17, **changes)


class SourceDB:
    def __init__(self, s, role='user'):
        self.rows = [{'id': str(s.source_message_ids[0]), 'conversation_id': str(s.conversation_id), 'role': role}]

    def table(self, name):
        assert name == 'messages'
        return self

    def select(self, *args):
        return self

    def in_(self, name, values):
        assert name == 'id'
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


@pytest.mark.asyncio
async def test_original_goal_survives_history_truncation_and_snapshot_does_not_alias_cache():
    s = state()
    s = s.model_copy(update={'original_goal': '只统计有效付款订单，保留退款口径，输出PDF且不发送给任何人'})
    history = [{'role': 'assistant', 'content': '旧回复'}]

    async def loader(db, conversation_id, current_text, **kwargs):
        assert conversation_id == str(s.conversation_id)
        assert kwargs == {'base_revision': 17, 'strict': True, 'org_id': None}
        return history  # the oldest user turn has already been budget-trimmed
    snapshot = await build_continuation_snapshot(SourceDB(s), state=s, incoming=incoming(s),
        execution_identity=s.execution_identity, history_loader=loader)
    assert all(message['role'] != 'user' for message in snapshot.messages)
    text = snapshot.messages[-1]['content']
    facts = json.loads(text[text.index('{'):])
    assert facts['original_goal'] == s.original_goal
    history[0]['content'] = 'later cache mutation'
    assert snapshot.messages[0]['content'] == '旧回复'


@pytest.mark.parametrize('field,value', [
    ('actor_user_id', uuid4()), ('org_id', uuid4()), ('branch_id', uuid4()),
    ('conversation_id', uuid4()), ('logical_task_id', uuid4()),
])
def test_continuation_cannot_switch_execution_scope(field, value):
    s = state()
    v = incoming(s).model_copy(update={field: value})
    with pytest.raises(TaskError, match='SCOPE_MISMATCH'):
        validate_continuation(s, v)


def test_stale_and_cancelled_continuations_do_not_run():
    s = state()
    v = incoming(s)
    with pytest.raises(TaskError, match='STALE'):
        validate_continuation(s.model_copy(update={'revision': 1}), v)
    with pytest.raises(TaskError, match='NOT_RUNNABLE'):
        validate_continuation(s.model_copy(update={'status': 'cancelled'}), v)


@pytest.mark.asyncio
async def test_tool_output_cannot_become_requirement_source():
    s = state()
    with pytest.raises(TaskError, match='SOURCE_INVALID'):
        await build_continuation_snapshot(SourceDB(s, role='tool'), state=s, incoming=incoming(s),
            execution_identity=s.execution_identity)


@pytest.mark.asyncio
async def test_current_workspace_permission_change_stops_before_history_io():
    s = state()
    identity = s.execution_identity.model_copy(update={'workspace_owner_id': 'other-owner'})
    with pytest.raises(TaskError, match='CURRENT_SCOPE_CHANGED'):
        await build_continuation_snapshot(None, state=s, incoming=incoming(s), execution_identity=identity)


def test_inventory_does_not_auto_admit_unknown_effects_or_mutate_original_registry():
    from services.tools.catalog import build_capability_catalog
    registry = build_capability_catalog()
    original = registry.specs()
    rows = inventory(registry)
    assert len(rows) == len(original)
    assert all(row.status == 'legacy_only' for row in rows)
    assert next(row for row in rows if row.tool_name == 'generate_image').status == 'legacy_only'
    assert registry.specs() == original
