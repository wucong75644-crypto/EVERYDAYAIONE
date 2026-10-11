from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from services.scheduler.monitoring import evaluate, normalize_observations, parse_observations, validate_policy
from services.scheduler.cron_utils import compose_cron, calc_next_run
from services.scheduler.task_definition_input import task_definition_input, TaskDefinitionInputError
from services.scheduler.chat_task_manager import ChatTaskManager, handle_form_submit
from tests.test_scheduled_task_structured_input import manager, TARGETS, _Db, TASK
from tests.test_scheduled_task_executor_delivery import _result, _task
from services.scheduler.task_executor import ScheduledTaskExecutor

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
POLICY = {"mode": "conditional", "metric": "likes", "comparison": "delta", "threshold": 500, "window_minutes": 60}


def row(value, minutes=0, identity='note'):
    return {"platform": "xiaohongshu", "entity_id": identity, "title": '素材', "url": 'https://example.test/note',
            "collected_at": (NOW + timedelta(minutes=minutes)).isoformat(), "metrics": {"likes": value}}


def test_baseline_silent_trigger_dedup_new_growth_and_rearm():
    policy = {**POLICY, "repeat_increment": 500}
    baseline = evaluate(policy, [row(100)], [], {}, NOW)
    assert baseline['status'] == 'baseline'
    hit = evaluate(policy, [row(700)], [row(100, -60)], baseline['state'], NOW)
    assert hit['status'] == 'matched'
    same = evaluate(policy, [row(700, 30)], [row(100, -30)], hit['state'], NOW)
    assert same['status'] == 'duplicate'
    more = evaluate(policy, [row(1200, 30)], [row(100, -30)], same['state'], NOW)
    assert more['status'] == 'matched'
    # Regression below a window threshold rearms; no-change does not retrigger.
    below = evaluate(policy, [row(1200, 60)], [row(1000, 0)], more['state'], NOW)
    assert below['status'] == 'below_threshold'
    unchanged = evaluate(policy, [row(1200, 90)], [row(100, 30)], below['state'], NOW)
    assert unchanged['status'] == 'duplicate'


@pytest.mark.parametrize('bad', [None, '1.2万', True, float('nan'), float('inf'), -1])
def test_missing_or_abbreviated_counts_do_not_become_zero_or_rearm(bad):
    normalized = normalize_observations([row(bad)], NOW)
    assert normalized == []
    initial = evaluate(POLICY, [row(700)], [row(100,-60)], {}, NOW)
    result = evaluate(POLICY, normalized, [row(100,-60)], initial['state'], NOW)
    assert result['status'] == 'incomplete'
    assert result['state'] == initial['state']


def test_window_requires_real_history_at_or_before_target_and_zero_percent_is_unknown():
    assert evaluate(POLICY, [row(700)], [row(100,-30)], {}, NOW)['status'] == 'incomplete'
    assert evaluate(POLICY, [row(700)], [row(100,-74)], {}, NOW)['status'] == 'incomplete'
    assert evaluate(POLICY, [row(700)], [row(100,-65)], {}, NOW)['status'] == 'matched'
    assert evaluate({**POLICY,'comparison':'percent'}, [row(700)], [row(0,-60)], {}, NOW)['status'] == 'incomplete'


def test_cooldown_is_not_a_repeat_trigger_and_entities_and_policy_are_isolated():
    policy = {**POLICY, 'cooldown_minutes':30}
    hit = evaluate(policy,[row(700)],[row(100,-60)],{},NOW)
    assert evaluate(policy,[row(701,60)],[row(100,0)],hit['state'],NOW)['status']=='duplicate'
    assert evaluate(policy,[row(700,60,'new')],[row(100,0,'new')],hit['state'],NOW)['status']=='matched'
    assert evaluate({**policy,'threshold':600},[row(700,60)],[row(100,0)],hit['state'],NOW)['status']=='matched'
    repeated = evaluate({**POLICY,'repeat_increment':100,'cooldown_minutes':60},[row(700)],[row(100,-60)],{},NOW)
    assert evaluate({**POLICY,'repeat_increment':100,'cooldown_minutes':60},[row(900,30)],[row(100,-30)],repeated['state'],NOW)['status']=='duplicate'


def test_strict_observation_envelope_and_collection_time():
    block = '报告\n```task_observations\n'+json.dumps([row(700)])+'\n```'
    text, items = parse_observations(block)
    assert text == '报告'
    assert normalize_observations(items,NOW)==items
    assert parse_observations(block+block)[1]==[]
    assert normalize_observations([row(700,-30)],NOW)==[]
    assert normalize_observations([row(700),row(701)],NOW)==[]
    assert normalize_observations([row(700,5)],NOW)==[]


@pytest.mark.parametrize('minutes,cron',[(1,'*/1 * * * *'),(30,'*/30 * * * *'),(60,'0 * * * *'),(120,'0 */2 * * *'),(1440,'0 */24 * * *')])
def test_interval_contract_and_actual_next_run(minutes,cron):
    definition={'prompt':'采集文具素材','schedule_type':'interval','interval_minutes':minutes,'notification_policy':POLICY}
    assert task_definition_input(definition,operation='create')['missing_fields']==[]
    assert compose_cron('interval',interval_minutes=minutes)==cron
    assert calc_next_run(cron,base=NOW)>NOW


@pytest.mark.parametrize('minutes',[0,7,90,300,1441])
def test_unsupported_interval_is_actionable(minutes):
    with pytest.raises(TaskDefinitionInputError):
        task_definition_input({'prompt':'采集','schedule_type':'interval','interval_minutes':minutes},operation='create')


@pytest.mark.parametrize('policy',[{'mode':'conditional'}, {**POLICY,'window_minutes':0}, {**POLICY,'threshold':float('nan')}, {**POLICY,'repeat_increment':0}])
def test_incomplete_policy_is_rejected(policy):
    with pytest.raises(ValueError): validate_policy(policy)


async def test_chat_form_policy_and_trusted_origin_survive_submission():
    host=manager()
    with patch('services.scheduler.chat_task_manager._load_push_targets',AsyncMock(return_value=TARGETS)):
        form=await host.handle('create',{'definition':{'prompt':'采集素材','schedule_type':'interval','interval_minutes':30,'notification_policy':POLICY},'recipient':'我'})
    data={f['name']:f.get('default_value') for f in form['fields']}
    assert data['notify_mode']=='conditional' and data['notify_threshold']==500
    data['_origin_conversation_id']='origin'
    with patch('services.permissions.checker.check_permission',AsyncMock(return_value=True)),patch('services.scheduler.chat_task_manager._propose_form_change',AsyncMock(return_value={'success':True})) as submit:
        await handle_form_submit(_Db(), 'u1','o1','scheduled_task_create', data)
    definition=submit.call_args.kwargs['definition']
    assert definition['origin_conversation_id']=='origin'
    assert definition['notification_policy']['window_minutes']==60
    assert definition['interval_minutes']==30


async def test_silent_result_has_no_web_or_wecom_outbox_and_state_is_atomic():
    db=MagicMock(); query=MagicMock()
    for name in ('select','eq','order','limit','gte','lte'):getattr(query,name).return_value=query
    query.execute.return_value=SimpleNamespace(data=[])
    db.table.return_value=query
    db.rpc.return_value.execute.return_value=SimpleNamespace(data={'outcome':'completed','push_status':'skipped'})
    task={**_task(),'notification_policy':POLICY,'origin_conversation_id':'origin'}
    result=_result();result.observations=[row(100)]
    executor=ScheduledTaskExecutor(db);executor._push_ws_event=AsyncMock()
    with patch('services.scheduler.task_executor.datetime') as clock:
        clock.now.return_value=NOW
        await executor._on_success(task,'run',result,NOW-timedelta(minutes=1))
    payload=db.rpc.call_args.args[1]
    assert payload['p_deliveries']==[]
    assert payload['p_last_result']['text']==result.text
    assert payload['p_last_result']['notification_decision']['status']=='baseline'
    assert task['_managed_notification'] is True


async def test_original_chat_web_delivery_does_not_change_other_recipient_conversation():
    executor=ScheduledTaskExecutor(MagicMock())
    task={**_task({'type':'web','user_id':'user'}),'origin_conversation_id':'origin'}
    deliveries=await executor._build_web_deliveries(task,_result())
    assert deliveries[0]['target_context']=={'type':'web','user_id':'user','conversation_id':'origin'}
    task['push_target']={'type':'web','user_id':'other'}
    with patch('services.message_gateway.MessageGateway._get_or_create_wecom_conversation',AsyncMock(return_value='other-conversation')):
        deliveries=await executor._build_web_deliveries(task,_result())
    assert deliveries[0]['target_context']['conversation_id']=='other-conversation'


@pytest.mark.parametrize('patch_fields, expected', [
    ({'schedule_type':'interval','interval_minutes':30}, '*/30 * * * *'),
    ({'interval_minutes':120}, '0 */2 * * *'),
])
async def test_interval_update_changes_actual_cron_and_keeps_notification_and_origin(patch_fields, expected):
    from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeAdapter
    from services.changeset.contracts import NormalizeRequest
    base={**TASK,'schedule_type':'cron','cron_expr':'0 * * * *','interval_minutes':60,
          'notification_policy':{**POLICY,'window_minutes':120},'origin_conversation_id':'origin'}
    context=SimpleNamespace(operation='update',base_snapshot=base,policy_snapshot={},resource_id='task-a')
    adapter=ScheduledTaskChangeAdapter(_Db(rows=[{'user_id':'u1'}]),user_id='u1',org_id='o1')
    with patch('services.conversation_service.ConversationService.get_conversation',AsyncMock(return_value={'id':'origin'})) as access:
        result=await adapter.normalize(NormalizeRequest(context=context,proposed_snapshot=patch_fields))
    assert result.proposed_snapshot['cron_expr']==expected
    assert result.proposed_snapshot['schedule_type']=='cron'
    assert result.proposed_snapshot['notification_policy']['window_minutes']==120
    access.assert_awaited_once_with('origin','u1','o1')


async def test_original_conversation_access_and_impossible_window_are_rejected():
    from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeAdapter,ScheduledTaskChangeError
    from services.changeset.contracts import NormalizeRequest
    adapter=ScheduledTaskChangeAdapter(_Db(),user_id='u1',org_id='o1')
    context=SimpleNamespace(operation='create',base_snapshot={},policy_snapshot={})
    definition={**TASK,'schedule_type':'interval','interval_minutes':120,'notification_policy':POLICY}
    definition.pop('cron_expr')
    with pytest.raises(ScheduledTaskChangeError,match='比较窗口'):
        await adapter.normalize(NormalizeRequest(context=context,proposed_snapshot=definition))
    definition.update(interval_minutes=30,origin_conversation_id='foreign-chat')
    with patch('services.conversation_service.ConversationService.get_conversation',AsyncMock(side_effect=PermissionError('foreign'))):
        with pytest.raises(PermissionError):
            await adapter.normalize(NormalizeRequest(context=context,proposed_snapshot=definition))


async def test_canonical_interval_snapshot_normalization_is_idempotent():
    from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeAdapter, ScheduledTaskChangeError
    from services.changeset.contracts import NormalizeRequest
    adapter=ScheduledTaskChangeAdapter(_Db(),user_id='u1',org_id='o1')
    context=SimpleNamespace(operation='create',base_snapshot={},policy_snapshot={})
    definition={**TASK,'schedule_type':'interval','interval_minutes':30,'notification_policy':POLICY}
    definition.pop('cron_expr')
    first=(await adapter.normalize(NormalizeRequest(context=context,proposed_snapshot=definition))).proposed_snapshot
    canonical_input={key: value for key, value in first.items() if key != 'skill_revision_snapshot'}
    second=(await adapter.normalize(NormalizeRequest(context=context,proposed_snapshot=canonical_input))).proposed_snapshot
    assert second['interval_minutes']==30 and second['cron_expr']=='*/30 * * * *'
    bad={**canonical_input,'cron_expr':'0 9 * * *'}
    with pytest.raises(ScheduledTaskChangeError,match='不一致'):
        await adapter.normalize(NormalizeRequest(context=context,proposed_snapshot=bad))
