import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.agent.agent_reach.contracts import ReachError
from services.agent.agent_reach.read_admission import reserve_read


def waiting(reason='interval', seconds=1):
    error = ReachError('ACCESS_BUDGET_WAIT', 'wait', retryable=True)
    error.wait_reason = reason
    error.next_eligible_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return error


@pytest.mark.asyncio
async def test_interactive_read_waits_after_reservation_transaction_closes(monkeypatch):
    class Budget:
        calls = 0
        in_transaction = False
        def reserve(self, *args):
            self.in_transaction = True
            try:
                self.calls += 1
                if self.calls == 1:
                    raise waiting()
                return 'attempt'
            finally:
                self.in_transaction = False
    budget = Budget()
    delays = []
    async def sleep(delay):
        assert not budget.in_transaction
        delays.append(delay)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    result = await reserve_read(budget, {}, None, SimpleNamespace(execution_mode='interactive'),
                               deadline=asyncio.get_running_loop().time()+60)
    assert result == 'attempt' and budget.calls == 2 and len(delays) == 1
    assert .05 <= delays[0] <= 1.05


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,reason,seconds,remaining', [
    ('scheduled', 'interval', 1, 60),
    ('preflight', 'interval', 1, 60),
    ('interactive', 'daily_limit', 1, 60),
    ('interactive', 'access_window', 1, 60),
    ('interactive', 'interval', 40, 60),
    ('interactive', 'interval', 10, 20),
])
async def test_read_does_not_sleep_for_background_or_long_or_unaffordable_wait(monkeypatch, mode, reason, seconds, remaining):
    error = waiting(reason, seconds)
    calls = []
    def reserve(*args):
        calls.append(1)
        raise error
    async def no_sleep(*args):
        pytest.fail('must not sleep')
    monkeypatch.setattr(asyncio, 'sleep', no_sleep)
    with pytest.raises(ReachError) as caught:
        await reserve_read(SimpleNamespace(reserve=reserve), {}, None, SimpleNamespace(execution_mode=mode),
                           deadline=asyncio.get_running_loop().time()+remaining)
    assert caught.value is error and len(calls) == 1


@pytest.mark.asyncio
async def test_cancel_during_interval_never_reserves_or_dispatches_again(monkeypatch):
    calls = []
    def reserve(*args):
        calls.append(1)
        raise waiting()
    async def cancel(*args):
        raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, 'sleep', cancel)
    with pytest.raises(asyncio.CancelledError):
        await reserve_read(SimpleNamespace(reserve=reserve), {}, None, SimpleNamespace(execution_mode='interactive'),
                           deadline=asyncio.get_running_loop().time()+60)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_wait_is_bounded_to_one_recheck_if_other_worker_consumes_slot(monkeypatch):
    calls = []
    def reserve(*args):
        calls.append(1)
        raise waiting()
    async def sleep(*args): pass
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    with pytest.raises(ReachError):
        await reserve_read(SimpleNamespace(reserve=reserve), {}, None, SimpleNamespace(execution_mode='interactive'),
                           deadline=asyncio.get_running_loop().time()+60)
    assert len(calls) == 2


@pytest.mark.parametrize('args', [
    {'action':'read'},
    {'task':'帮我查这家店的全部商品和销量','url':'https://www.xiaohongshu.com/shop/65263c56796c030001e298d4','page':7},
])
@pytest.mark.asyncio
async def test_wait_time_is_visible_in_model_text_not_only_metadata(monkeypatch, args):
    from core import config
    from services.agent.agent_reach import handler
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(agent_reach_enabled=True, agent_reach_timeout_seconds=60))
    error = waiting('daily_limit', 100)
    async def fail(operation, **kw):
        operation.close()
        raise error
    monkeypatch.setattr(handler.asyncio, 'wait_for', fail)
    result = await handler.execute_reach(SimpleNamespace(execution_budget=None), args)
    text = result.to_tool_content()
    assert error.next_eligible_at in text and '不要' in text and '未向平台发送' in text
    assert '当日访问额度已用完' in text and result.metadata['wait_reason'] == 'daily_limit'
    if args.get('url'):
        assert result.metadata['catalog_complete'] is False
        assert result.metadata['resume_request']['page'] == 7
        assert result.metadata['resume_request']['max_results'] == 10
        assert '全店读取尚未完成' in text and '不能把目录总数当作已读数量' in text


@pytest.mark.parametrize('consumed,next_seconds,hours,reason', [
    (0, 30, (0,24), 'interval'),
    (100, -1, (0,24), 'daily_limit'),
    (0, -1, ((datetime.now(timezone.utc).hour+2)%24, (datetime.now(timezone.utc).hour+3)%24), 'access_window'),
])
def test_admission_rejection_classifies_existing_policy_without_charging(consumed,next_seconds,hours,reason):
    from services.agent.agent_reach.access_budget import AccessBudgetService
    now = datetime.now(timezone.utc)
    row = {'timezone':'UTC','day':now.date(),'daily_limit':100,'consumed':consumed,
           'min_interval_seconds':30,'max_comment_pages':10,'breaker':'',
           'next_allowed_at':now+timedelta(seconds=next_seconds),'allowed_start_hour':hours[0],
           'allowed_end_hour':hours[1]}
    class Cursor:
        def execute(self, sql, *args):
            assert not sql.lstrip().startswith('UPDATE'), 'rejected request must not charge budget'
        def fetchone(self): return row
    class Budget(AccessBudgetService):
        @contextmanager
        def cursor(self, **kw): yield Cursor()
    with pytest.raises(ReachError) as caught:
        Budget(None,None).reserve({'platform':'xiaohongshu','account_id':'id'},SimpleNamespace(platform='xiaohongshu',action='read'))
    assert caught.value.wait_reason == reason and caught.value.next_eligible_at


@pytest.mark.asyncio
async def test_same_account_batch_reads_obey_interval_and_all_reach_platform(monkeypatch):
    from core import config
    from services.agent.agent_reach import handler, access_budget
    from services.agent.agent_result import AgentResult
    identity = '00000000-0000-0000-0000-000000000021'
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(
        agent_reach_enabled=True, agent_reach_timeout_seconds=60,
        agent_reach_platforms='xiaohongshu', social_ops_enabled=True))
    class Store:
        def __init__(self, pool, scope): self.scope = scope
        def list(self): return [{'platform':'xiaohongshu','id':identity}]
        def load(self, *args, **kw): return {'id':identity,'account_id':'interval-test','secret':{}}
    admissions = []
    settlements = []
    class Budget:
        next_at = datetime.now(timezone.utc)
        def __init__(self, *args): pass
        def reserve(self, *args):
            now = datetime.now(timezone.utc)
            if now < Budget.next_at:
                error = waiting(seconds=0)
                error.next_eligible_at = Budget.next_at.isoformat()
                raise error
            admissions.append(now)
            Budget.next_at = now+timedelta(seconds=.08)
            return str(len(admissions))
        def settle(self, connection, attempt, *args): settlements.append(attempt)
    requests = []
    async def execute(request, *args, **kw):
        requests.append(request.url)
        return AgentResult(summary='正文',status='success')
    monkeypatch.setattr(handler, 'Connections', Store)
    monkeypatch.setattr(handler, 'execute', execute)
    monkeypatch.setattr(access_budget, 'AccessBudgetService', Budget)
    executor = SimpleNamespace(execution_budget=None,execution_mode='interactive',db=SimpleNamespace(pool=None),
        org_id='00000000-0000-0000-0000-000000000001',user_id='00000000-0000-0000-0000-000000000011')
    results = await asyncio.gather(*(handler.execute_reach(executor,
        {'task':'读取详情','platform':'xiaohongshu','action':'read','url':f'https://www.xiaohongshu.com/explore/note{i}?xsec_token=test'}) for i in range(3)))
    assert [result.status for result in results] == ['success']*3
    assert len(requests) == len(set(requests)) == len(settlements) == 3
    assert all((second-first).total_seconds()>=.08 for first,second in zip(admissions,admissions[1:]))
