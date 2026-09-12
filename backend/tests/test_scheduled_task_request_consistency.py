from unittest.mock import AsyncMock, patch

import pytest

from services.scheduler.task_request_context import current_creation_text
from services.scheduler.task_nl_parser import parse_task_request


def test_clarification_context_is_bounded_by_current_request_and_execution():
    original = {'role': 'user', 'content': '创建定时任务，查询A店昨天订单'}
    question = {'role': 'assistant', 'content': '每天几点执行？'}
    answer = {'role': 'user', 'content': '每天8点'}
    assert current_creation_text([original, question, answer]) == original['content'] + '\n每天8点'
    # More than one clarification is kept, but a new request supersedes it.
    target_question = {'role': 'assistant', 'content': '推送给谁？'}
    target_answer = {'role': 'user', 'content': '发给我'}
    assert current_creation_text([original, question, answer, target_question, target_answer]).endswith('每天8点\n发给我')
    new = {'role': 'user', 'content': '创建B店日报，每天10点'}
    assert current_creation_text([original, question, new]) == new['content']
    for boundary in [
        {'role': 'assistant', 'content': '已创建，几点执行？', 'tool_calls': [{'id': 'c'}]},
        {'role': 'tool', 'content': '补充任务表单'},
        {'role': 'assistant', 'content': '已创建。'},
    ]:
        assert current_creation_text([original, boundary, answer]) == '每天8点'
    assert current_creation_text([original, question, {'role': 'user', 'content': '算了'}, target_question, target_answer]) == '发给我'
    assert current_creation_text([{'role': 'user', 'content': '谈谈昨天的新闻'}, question, answer]) == '每天8点'
    assert current_creation_text(None) == ''


@pytest.mark.parametrize(('source', 'clock', 'accepted'), [
    ('9点', '25:00', False), ('9点', '08:00', False), ('9点', '09:00', True),
    ('九点半', '09:30', True), ('九点半', '09:00', False),
    ('下午两点十五分', '14:15', True), ('下午两点十五分', '02:15', False),
    ('08:05', '08:05', True), ('十点三刻', '10:45', True),
    ('上午9点或10点', '09:00', False), ('晚上', '20:00', False),
])
async def test_explicit_clock_must_match_source(source, clock, accepted):
    text = f'改到{source}'
    raw = {'changes': {'time_str': clock}, 'evidence': {'time_str': source}}
    with patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(return_value=raw)):
        result = await parse_task_request(text, operation='update')
    assert ('time_str' in result['changes']) is accepted
    assert ('time_str' in result['missing_fields']) is not accepted


async def test_short_quote_cannot_drop_an_afternoon_modifier():
    raw = {'changes': {'time_str': '09:00'}, 'evidence': {'time_str': '九点'}}
    with patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(return_value=raw)):
        result = await parse_task_request('改到下午九点', operation='update')
    assert result['missing_fields'] == ['time_str']


@pytest.mark.parametrize('run_at', ['2030-10-01T08:00:00+08:00', '2030-10-02T09:00:00+08:00', '2030-10-01T09:00:00'])
async def test_one_shot_rejects_wrong_clock_date_or_missing_timezone(run_at):
    source = '2030年10月1日9点'
    raw = {'changes': {'run_at': run_at}, 'evidence': {'run_at': source}}
    with patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(return_value=raw)):
        result = await parse_task_request(source, operation='update')
    assert 'run_at' not in result['changes']
    assert 'run_at' in result['missing_fields']


async def test_wrong_clock_cannot_enter_direct_creation():
    from services.scheduler.chat_task_manager import ChatTaskManager
    from tests.test_scheduled_task_changeset_adapter import _Db
    raw = {'changes': {'name': '日报', 'prompt': '查询A店昨天的订单', 'schedule_type': 'daily', 'time_str': '08:00'},
           'evidence': {'prompt': '查询A店昨天的订单', 'schedule_type': '每天', 'time_str': '9点'},
           'recipient': '', 'request_parts': [{'kind': 'schedule', 'text': '每天9点'},
                                            {'kind': 'execution', 'text': '查询A店昨天的订单'}]}
    manager = ChatTaskManager(_Db(), 'u', 'org', submission_mode='apply_if_allowed')
    with patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(return_value=raw)), \
         patch('services.scheduler.chat_task_manager._load_push_targets', AsyncMock(return_value=[])), \
         patch.object(manager, '_begin_request', AsyncMock()) as submit:
        form = await manager.handle('create', {'description': '每天9点查询A店昨天的订单'})
    submit.assert_not_awaited()
    assert next(f for f in form['fields'] if f['name'] == 'time_str')['default_value'] == ''
    assert next(f for f in form['fields'] if f['name'] == 'prompt')['default_value'] == '查询A店昨天的订单'


async def test_short_quote_cannot_drop_minutes():
    raw = {'changes': {'time_str': '09:00'}, 'evidence': {'time_str': '九点'}}
    with patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(return_value=raw)):
        result = await parse_task_request('改到九点半', operation='update')
    assert result['missing_fields'] == ['time_str']


async def test_explicit_frequency_must_match_source():
    raw = {'changes': {'schedule_type': 'weekly', 'time_str': '09:00'},
           'evidence': {'schedule_type': '每天', 'time_str': '9点'}}
    with patch('services.scheduler.task_nl_parser._call_llm', AsyncMock(return_value=raw)):
        result = await parse_task_request('改到每天9点', operation='update')
    assert result['missing_fields'] == ['schedule_type']
