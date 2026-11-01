"""Checkpoint adapter; the database commits task facts and Actor facts atomically."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from .contracts import TaskError, TaskState
from .database_scope import task_database


class TaskCheckpointStore:
    def __init__(self, db):
        self.db = db

    async def write(self, *, task_id, execution_token, boundary, payload, state: TaskState,
                    expected_revision: int):
        value = state.model_dump(mode='json')
        if '_task_execution' in payload and payload['_task_execution'] != value:
            raise TaskError('TASK_EXECUTION_CHECKPOINT_MISMATCH')
        response = await task_database(self.db, state).rpc('save_agent_task_checkpoint', {
            'p_task_id': str(task_id), 'p_execution_token': str(execution_token),
            'p_safe_point': getattr(boundary, 'value', boundary),
            'p_state': Jsonb({**payload, '_task_execution': value}),
            'p_expected_revision': expected_revision,
        }).execute()
        result = response.data if response else None
        if not isinstance(result, dict):
            raise TaskError('TASK_EXECUTION_WRITE_RESULT_INVALID')
        return result
