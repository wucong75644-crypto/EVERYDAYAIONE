"""Task decisions cannot replace user requirements or bypass durable commit."""
from uuid import uuid4

import pytest

from services.task_execution.controller import TaskController
from services.task_execution.contracts import TaskError, TaskState
from tests.test_task_execution_reducer import state


@pytest.mark.asyncio
async def test_control_waits_for_commit_and_rejects_model_owned_requirements():
    s = state()
    committed = []

    async def commit(candidate, expected):
        committed.append((candidate, expected))
        return {'outcome': 'saved'}
    controller = TaskController(s, owner_task_id=s.root_task_id, commit=commit)
    invalid = await controller.control('update_task_plan', {'expected_revision': 0,
        'requirements': [], 'steps': []}, call_id='one')
    assert invalid.result['code'] == 'TASK_CONTROL_INPUT_INVALID'
    assert committed == [] and controller.state is s
    outcome = await controller.control('update_task_plan', {'expected_revision': 0,
        'steps': [{'id': 'report', 'title': '报告', 'requirement_ids': ['report']}]}, call_id='two')
    assert outcome.result['ok'] is True
    assert committed[0][1] == 0 and controller.state.revision == 1


@pytest.mark.asyncio
async def test_failed_checkpoint_does_not_adopt_or_display_uncommitted_text():
    from tests.test_task_execution_reducer import plan
    s = plan(state())

    async def commit(*args):
        return {'outcome': 'disabled'}
    controller = TaskController(s, owner_task_id=s.root_task_id, commit=commit)
    with pytest.raises(TaskError, match='NOT_SAVED'):
        await controller.control('save_task_text', {'expected_revision': s.revision,
            'step_id': 'research', 'text': 'actual research'}, call_id='one')
    assert controller.state is s


@pytest.mark.asyncio
async def test_text_is_real_saved_content_but_cannot_satisfy_file_requirement():
    from services.task_execution.contracts import PlanStep
    from tests.test_task_execution_reducer import plan
    s = plan(state(), (PlanStep(id='report', title='报告', requirement_ids=('report',)),))

    async def commit(*args):
        return {'outcome': 'saved'}
    controller = TaskController(s, owner_task_id=s.root_task_id, commit=commit)
    value = await controller.control('save_task_text', {'expected_revision': s.revision,
        'step_id': 'report', 'text': 'I created a file'}, call_id='one')
    assert value.display_text == 'I created a file'
    saved = controller.state.artifacts[0]
    assert saved.inline_text == value.display_text
    assert TaskState.model_validate_json(controller.state.model_dump_json()).artifacts[0] == saved
    result = await controller.control('complete_task_step', {'expected_revision': controller.state.revision,
        'step_id': 'report', 'artifact_ids': [saved.id]}, call_id='two')
    assert result.result['code'] == 'TASK_STEP_DELIVERY_INCOMPLETE'


@pytest.mark.asyncio
async def test_text_replay_with_new_call_id_reuses_existing_evidence():
    from tests.test_task_execution_reducer import plan
    s = plan(state())

    async def commit(*args):
        return {'outcome': 'saved'}
    controller = TaskController(s, owner_task_id=uuid4(), commit=commit)
    args = {'expected_revision': s.revision, 'step_id': 'research', 'text': 'Research result'}
    first = await controller.control('save_task_text', args, call_id='first')
    current = controller.state
    second = await controller.control('save_task_text', {**args, 'expected_revision': current.revision}, call_id='second')
    assert first.result['ok'] and second.result['ok']
    assert controller.state is current and len(current.artifacts) == 1
    assert second.display_text is None


def test_model_control_schemas_have_no_authorization_or_evidence_registration():
    async def commit(*args):
        raise AssertionError('not called')
    controller = TaskController(state(), owner_task_id=uuid4(), commit=commit)
    for schema in controller.schemas():
        properties = schema['function']['parameters']['properties']
        assert not {'actor_user_id', 'requirements', 'validations', 'capability_gates', 'execution_identity'} & set(properties)


@pytest.mark.asyncio
async def test_blank_text_cannot_become_delivery_evidence():
    from tests.test_task_execution_reducer import plan
    s = plan(state())
    async def commit(*args):
        raise AssertionError('blank text must not be saved')
    controller = TaskController(s, owner_task_id=s.root_task_id, commit=commit)
    result = await controller.control('save_task_text', {'expected_revision': s.revision,
        'step_id': 'research', 'text': ' \n\t '}, call_id='blank')
    assert result.result['code'] == 'TASK_CONTROL_INPUT_INVALID' and controller.state is s


@pytest.mark.asyncio
async def test_result_adapter_retry_preserves_original_delivery_owner():
    from tests.test_task_execution_reducer import plan, artifact
    s = plan(state())
    async def commit(*args):
        return {'outcome': 'saved'}
    controller = TaskController(s, owner_task_id=s.root_task_id, commit=commit)
    source = artifact(s, 'research')
    await controller.accept_artifacts([source])
    first = controller.state
    assert first.artifacts[0].delivery_task_id == s.root_task_id
    controller.owner_task_id = uuid4()
    await controller.accept_artifacts([source])
    assert controller.state is first
