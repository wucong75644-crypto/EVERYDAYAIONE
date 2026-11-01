"""Diagnostics must be private, scoped and independent from task success."""
import asyncio
from unittest.mock import Mock
import pytest
from services.agent.observability import chat_lifecycle as timing


@pytest.mark.asyncio
async def test_attempt_identity_propagates_to_child_tasks_and_resets_afterwards():
    rows=[]
    with timing.capture_chat_lifecycle(rows):
        with timing.chat_lifecycle_scope('task',turn_id='turn',attempt=2):
            await asyncio.create_task(asyncio.to_thread(timing.chat_phase,'model.begin',model_round=3))
            timing.chat_phase('model.first_body',model_round=3)
        timing.chat_phase('unknown-do-not-log-content')
        timing.chat_phase('context.begin',task_id='next')
    assert len(rows)==3
    assert rows[0]['task_id']=='task' and rows[0]['attempt']==2 and rows[0]['turn_id']=='turn'
    assert rows[-1]['task_id']=='next' and rows[-1]['attempt'] is None
    assert rows[0]['clock_domain']==rows[-1]['clock_domain']
    assert rows[0]['monotonic_ns'] <= rows[1]['monotonic_ns']
    assert all(not isinstance(v,(list,dict)) for row in rows for v in row.values())


def test_observer_failure_does_not_change_chat_execution():
    token=timing._CAPTURE.set(Mock(side_effect=RuntimeError('private failure')))
    try: timing.chat_phase('model.begin',task_id='task')
    finally: timing._CAPTURE.reset(token)


@pytest.mark.asyncio
async def test_saturation_drops_diagnostics_without_starting_a_task(monkeypatch):
    monkeypatch.setattr(timing,'_PENDING',set(range(64)))
    monkeypatch.setattr(timing,'_publish',Mock(side_effect=AssertionError('must not publish')))
    timing.chat_phase('model.begin',task_id='task')
