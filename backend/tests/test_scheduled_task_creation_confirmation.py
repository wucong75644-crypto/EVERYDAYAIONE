"""Chat creation must wait for the user's form confirmation, even with complete fields."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from services.agent.tool_executor import ToolExecutor
from services.scheduler.chat_task_manager import FormBlockResult, handle_form_submit
from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeSetService
from tests.tool_runtime_support import IdentityDB
from tests.test_scheduled_task_structured_input import DEFINITION, TASK
from tests.test_scheduled_task_request_content import TARGETS

@pytest.mark.parametrize('schedule', [
    {'schedule_type': 'daily', 'time_str': '08:00'},
    {'schedule_type': 'weekly', 'time_str': '08:00', 'weekdays': [1, 5]},
    {'schedule_type': 'once', 'run_at': '2030-10-01T00:00:00+00:00'},
])
async def test_complete_chat_shows_editable_form_without_creating_or_planning(schedule):
    executor = ToolExecutor(IdentityDB(), 'u1', 'c1', 'o1', tool_entrypoint='model')
    definition = {'name': DEFINITION['name'], 'prompt': DEFINITION['prompt'], **schedule}
    with patch('services.scheduler.chat_task_manager._load_push_targets', AsyncMock(return_value=TARGETS)), \
         patch.object(ScheduledTaskChangeSetService, 'begin', AsyncMock(return_value={'id': 'wrong', 'status': 'draft'})) as begin, \
         patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(side_effect=AssertionError('no second parse'))) as parser:
        result = await executor.execute('manage_scheduled_task', {'action': 'create', 'definition': definition, 'recipient': '我'}, call_id='create-form')
    begin.assert_not_awaited()
    parser.assert_not_awaited()
    assert isinstance(result, FormBlockResult)
    assert result.form['submit_text'] == '确认创建'
    assert '尚未创建' in result.form['description']
    fields = {f['name']: f for f in result.form['fields']}
    for key in ('name', 'prompt', 'schedule_type', 'push_target'):
        assert fields[key]['type'] != 'hidden'
    assert fields['prompt']['default_value'] == DEFINITION['prompt']
    assert fields['time_str']['type'] == 'time'
    if schedule['schedule_type'] == 'daily':
        fixture = json.loads((Path(__file__).parent / 'fixtures/scheduled_task_creation_confirmation.json').read_text())
        assert {**result.form, 'form_id': fixture['form_id']} == fixture
    if schedule['schedule_type'] == 'once':
        assert fields['run_at']['type'] == 'datetime-local'
        assert fields['run_at']['default_value'] == '2030-10-01T08:00'
    else:
        assert fields['time_str']['default_value'] == '08:00'
    values = {k: f.get('default_value') for k, f in fields.items()}
    values['name'] = '用户确认的新名称'
    with patch('services.permissions.checker.check_permission', AsyncMock(return_value=True)), \
         patch('services.scheduler.chat_task_manager._propose_form_change', AsyncMock(return_value={'success': True})) as submit:
        await handle_form_submit(IdentityDB(), 'u1', 'o1', 'scheduled_task_create', values)
    assert submit.call_args.kwargs['definition']['name'] == '用户确认的新名称'
    assert submit.call_args.kwargs['definition']['prompt'] == DEFINITION['prompt']
