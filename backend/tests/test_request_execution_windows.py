"""A new input gets a bounded window; retries and historical effects stay pinned."""
import copy
from uuid import uuid4

import pytest

from services.task_execution.contracts import TaskState, TaskError
from services.task_execution.followup import payload, FollowupObservation
from services.task_execution.reducer import consume_model_round
from tests.test_task_execution_reducer import state


def test_new_window_preserves_lifetime_counters_and_operations_after_old_exhaustion():
    old = state().model_copy(update={'model_rounds_used': 35, 'correction_count': 3})
    message = str(uuid4())
    source = {'execution_window_version': 1, 'state': old.model_dump(mode='json'),
              'checkpoint': {'messages': [], 'next_model_round': 35}, 'input_message_id': message, 'message': '第三个改成两千，先别出图'}
    result = payload(source, {'messages': [], 'next_model_round': 2}, task_id=str(uuid4()), turn_id='turn',
        model_round=2, usage={}, observation=FollowupObservation(relation='amend', evidence=['第三个改成两千']))
    current = TaskState.model_validate(result['_task_execution'])
    assert current.model_rounds_used == 37 and current.budget_rounds_used == 2
    assert current.correction_count == 3 and current.budget_corrections_used == 0
    assert current.operations == old.operations and current.artifacts == old.artifacts
    assert result['next_model_round'] == 2
    # Serializing/resuming the same input does not open another window.
    restored = TaskState.model_validate_json(current.model_dump_json())
    for _ in range(28):
        restored = consume_model_round(restored)
    with pytest.raises(TaskError, match='TASK_BUDGET_EXHAUSTED'):
        consume_model_round(restored)
    assert restored.model_rounds_used == 65 and restored.budget_rounds_used == 30


def test_legacy_checkpoint_retains_original_budget_until_new_input():
    old = state().model_copy(update={'model_rounds_used': 30})
    assert 'execution_window' not in old.model_dump()
    with pytest.raises(TaskError, match='TASK_BUDGET_EXHAUSTED'):
        consume_model_round(TaskState.model_validate(old.model_dump()))


@pytest.mark.asyncio
async def test_model_directed_budget_stop_is_visible_and_retains_unfinished_state():
    from tests.test_task_execution_model_bridge import setup_case
    from services.task_execution.model_bridge import review_model_completion
    from services.handlers.chat.execution_engine import _build_replay_context
    from unittest.mock import AsyncMock
    from tests.test_content_objects import registered
    runtime, prepared, handler, _, _ = await registered()
    runtime.task_execution_state = runtime.task_execution_state.model_copy(update={'model_rounds_used': 30})
    sink = __import__('types').SimpleNamespace(on_block=AsyncMock())
    before = copy.deepcopy(runtime.task_execution_state)
    blocks = []
    await review_model_completion(runtime=runtime, prepared=prepared, blocks=blocks,
        build_checkpoint=_build_replay_context, turn=30, next_model_round=30,
        exhausted=True, sink=sink)
    assert blocks and blocks[-1]['stop_reason'] == 'max_turns'
    assert runtime.task_execution_state.status == 'partial'
    assert runtime.task_execution_state.operations == before.operations
    handler._execute_tool_calls.assert_not_called()
    sink.on_block.assert_awaited()
