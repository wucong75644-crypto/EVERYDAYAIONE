"""Budget and no-retry guard tests: all HTTP is a local mock transport."""
import json
import httpx
import pytest
from tests.fixtures.skill_paid_probe import SkillProbeLedger,ApprovedSkillTransport,ProbeLimitReached


def body(text='hello'):
    return {'model':'qwen3.8-max','messages':[{'role':'user','content':text}],
        'max_tokens':6000,'stream':True,'enable_thinking':False}


def ledger(monkeypatch,tmp_path):
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_APPROVED','60')
    return SkillProbeLedger(tmp_path/'ledger.json')


def test_no_implicit_paid_approval(monkeypatch,tmp_path):
    monkeypatch.delenv('ORIGINAL_SKILL_PAID_APPROVED',raising=False)
    with pytest.raises(ProbeLimitReached):SkillProbeLedger(tmp_path/'ledger.json')


def test_lost_acceptance_and_duplicate_cannot_spend_again(monkeypatch,tmp_path):
    book=ledger(monkeypatch,tmp_path);identity=book.reserve(body(),'reference')
    with pytest.raises(ProbeLimitReached,match='uncertain'):book.reserve(body('different'),'reference')
    packet={'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':20}}
    book.finish(identity,('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode())
    with pytest.raises(ProbeLimitReached,match='duplicate'):book.reserve(body(),'reference')


def test_budget_and_model_bound_before_transport(monkeypatch,tmp_path):
    book=ledger(monkeypatch,tmp_path)
    with pytest.raises(ProbeLimitReached,match='Context'):book.reserve(body('a'*180001),'reference')
    invalid=body();invalid['enable_thinking']=True
    with pytest.raises(ProbeLimitReached,match='thinking'):book.reserve(invalid,'reference')
    book.path.write_text(json.dumps({'approval':60,'max_calls':8,'calls':[{'body_sha256':'other','status':'received','scenario':'other','settled_upper':59,'reserved_upper':59}]}))
    with pytest.raises(ProbeLimitReached,match='budget'):book.reserve(body(),'reference')


@pytest.mark.asyncio
async def test_native_gateway_adapter_stream_settles_even_when_consumer_breaks_at_done(monkeypatch,tmp_path):
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from services.model_gateway import ModelGateway,ModelCallRequest
    book=ledger(monkeypatch,tmp_path);seen=[]
    def respond(request):
        seen.append(json.loads(request.content))
        packets=[{'choices':[{'index':0,'delta':{'content':'已规划'},'finish_reason':None}]},
            {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':20}}]
        data=''.join('data: '+json.dumps(p)+'\n\n' for p in packets)+'data: [DONE]\n\n'
        return httpx.Response(200,content=data,headers={'content-type':'text/event-stream'})
    adapter=DashScopeChatAdapter('synthetic-never-network','qwen3.8-max',builtin_tools_enabled=False)
    adapter._client=httpx.AsyncClient(base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',transport=ApprovedSkillTransport(book,'reference',httpx.MockTransport(respond)))
    class Events:
        def publish(self,*args,**kwargs):pass
    gateway=ModelGateway(adapter_factory=lambda *_args,**_kwargs:adapter,event_publisher=Events())
    try:
        session=gateway.open_chat(ModelCallRequest(model_id='qwen3.8-max'))
        chunks=[c async for c in session.stream_chat(messages=[{'role':'user','content':'规划'}],thinking_mode='disabled',max_tokens=6000,tools=[])]
        assert ''.join(c.content or '' for c in chunks)=='已规划'
        state=json.loads(book.path.read_text());assert state['calls'][0]['status']=='received'
        assert len(seen)==1 and seen[0]['enable_thinking'] is False
    finally:await gateway.close()


@pytest.mark.asyncio
async def test_exact_received_response_reuse_never_reaches_network(monkeypatch,tmp_path):
    book=ledger(monkeypatch,tmp_path);sent=[]
    def respond(request):
        sent.append(True)
        packet={'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':20}}
        return httpx.Response(200,content='data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(transport=ApprovedSkillTransport(book,'reference',httpx.MockTransport(respond),reuse_received=True)) as client:
        for _ in range(2):
            response=await client.post('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',json=body())
            assert response.status_code==200
    assert len(sent)==1 and len(json.loads(book.path.read_text())['calls'])==1


def test_new_batch_cannot_reopen_old_approval_or_exceed_100_credits(monkeypatch,tmp_path):
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_APPROVED','100')
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_MAX_CALLS','12')
    book=SkillProbeLedger(tmp_path/'new.json')
    book.path.write_text(json.dumps({'approval':60,'max_calls':16,'calls':[]}))
    with pytest.raises(ProbeLimitReached,match='identity'):book.reserve(body(),'reference')
    book.path.write_text(json.dumps({'approval':100,'max_calls':12,'calls':[{'body_sha256':'old','status':'received','settled_upper':99,'reserved_upper':99}]}))
    with pytest.raises(ProbeLimitReached,match='budget'):book.reserve(body(),'reference')
    book.path.write_text(json.dumps({'approval':100,'max_calls':12,'calls':[{'body_sha256':str(i),'status':'received','settled_upper':0,'reserved_upper':0} for i in range(12)]}))
    with pytest.raises(ProbeLimitReached,match='allocation'):book.reserve(body(),'reference')


@pytest.mark.asyncio
async def test_prior_batch_reuse_requires_exact_request_and_never_modifies_prior(monkeypatch,tmp_path):
    old=ledger(monkeypatch,tmp_path)
    identity=old.reserve(body(),'reference')
    packet={'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':20}}
    old.finish(identity,('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode())
    before=old.path.read_bytes()
    monkeypatch.setenv('ORIGINAL_SKILL_PAID_APPROVED','100');monkeypatch.setenv('ORIGINAL_SKILL_PAID_MAX_CALLS','12')
    new=SkillProbeLedger(tmp_path/'new.json')
    def deny(_):raise AssertionError('exact replay must be free')
    async with httpx.AsyncClient(transport=ApprovedSkillTransport(new,'reference',httpx.MockTransport(deny),reuse_received=True,prior_received=old)) as client:
        assert (await client.post('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',json=body())).status_code==200
    assert old.path.read_bytes()==before and not new.path.exists()
    assert old.received(body('changed source')) is None


def test_frozen_scope_reuses_identity_and_rejects_changed_goal_or_method(tmp_path):
    from dataclasses import replace
    from tests.test_task_execution_bootstrap import bootstrap
    from tests.fixtures.skill_paid_probe import pinned_probe_scope
    first,_,anchor,_=bootstrap();path=tmp_path/'scope.json'
    bound={'revision':'v1','body_sha256':'a'*64,'original_images':['b'*64]}
    frozen,pinned=pinned_probe_scope(path,first,anchor,bound)
    second,_,other_anchor,_=bootstrap()
    restored,restored_anchor=pinned_probe_scope(path,second,other_anchor,bound)
    assert restored==frozen and restored_anchor==pinned
    assert restored.task_id!=second.task_id
    assert path.stat().st_mode & 0o777==0o600
    with pytest.raises(ProbeLimitReached,match='changed'):
        pinned_probe_scope(path,replace(second,original_goal='different'),other_anchor,bound)
    with pytest.raises(ProbeLimitReached,match='changed'):
        pinned_probe_scope(path,second,other_anchor,{**bound,'revision':'v2'})
