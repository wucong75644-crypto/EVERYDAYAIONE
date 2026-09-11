"""06: isolated payload/rollback drills and actual Actor/ToolLoop entrypoints."""
import asyncio
import ast
import json
import subprocess
import threading
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from core.config import get_settings
from services.agent.agent_result import AgentResult
from services.agent.loop_hooks import ToolAuditHook
from services.agent.tool_audit import record_tool_audit
from services.agent.tool_output import OutputFormat, FileRef, ColumnMeta
from services.agent.tool_result_cache import ToolResultCache
from services.handlers.chat_tool_mixin import ChatToolMixin
from services.tool_invocation_store import serialize_tool_result, deserialize_tool_result, hash_tool_arguments
from services.tools import ToolCall, ToolResult
from services.tools.result_payload import encode_result, restore_result, ToolPayloadError, MAX_STRING, MAX_BYTES
from tests.test_tool_production_integration import setup, tc, actor_harness, InvocationStore, loop_for, invoke
from tests.test_tool_result_consumption import sample, CASES, normalize
from tests.tool_runtime_support import MockHandlerExecutor

BASE = 'cdba58f9018ff45be2ebde0b802471ca634d8727'


def test_writer_rollout_default_and_explicit_rollback(monkeypatch):
    from core.config import Settings
    monkeypatch.delenv('TOOL_RESULT_PAYLOAD_WRITE_VERSION', raising=False)
    assert Settings(_env_file=None).tool_result_payload_write_version == 1
    monkeypatch.setenv('TOOL_RESULT_PAYLOAD_WRITE_VERSION', '0')
    assert Settings(_env_file=None).tool_result_payload_write_version == 0


def envelope(raw):
    executor = MockHandlerExecutor(agent_domain='general', task_id='task1')
    call = ToolCall('call', 'generate_image', {'prompt': 'fixture'})
    context = executor.tool_runtime.context(call.call_id)
    decision = executor.tool_runtime.policy.decide(call.name, context, call.arguments)
    return ToolResult.wrap(raw, call=call, context=context, decision=decision), (call, context, decision)


def restore(payload, binding):
    call, context, decision = binding
    return restore_result(json.loads(json.dumps(payload)), call=call, context=context, decision=decision)


def old_function(name):
    source = subprocess.check_output(['git', 'show', f'{BASE}:backend/services/tool_invocation_store.py'], text=True)
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {'Any': object, 'json': json}
    exec(compile(module, f'{BASE}:{name}', 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('case', CASES)
async def test_new_write_new_read_all_projections_and_artifacts(setup, case):
    _, root = setup
    _, raw = sample(case, root)
    if isinstance(raw, AgentResult):
        raw.tokens_used = 29; raw.thinking_text = '保留思考'
        raw.confidence = .6; raw.insights = ['结论']; raw.follow_up = ['后续']
        raw.metadata.update(retryable=False, retry_context={'prompt': 'fixture', 'attempt': 2}, title='审计标题')
    result, binding = envelope(raw)
    result = result.with_model_content('tool_loop', 'staged model view', truncated=True)
    recovered = restore(encode_result(result), binding)
    for consumer in ('chat', 'tool_loop'):
        assert recovered.model_content(consumer) == result.model_content(consumer)
        assert recovered.collect_payloads(consumer) == result.collect_payloads(consumer)
    assert recovered.display == result.display
    assert recovered.artifacts == result.artifacts
    assert recovered.model_image_blocks == result.model_image_blocks
    assert recovered.error == result.error
    assert recovered.agent_context == result.agent_context
    assert recovered.metadata == result.metadata
    assert recovered.execution == result.execution
    assert recovered.audit['origin']['tool_call_id'] == 'call'
    assert recovered.audit['origin']['args_hash'] == hash_tool_arguments({'prompt': 'fixture'})
    assert 'args' not in encode_result(result)['tool_result']['audit']


@pytest.mark.parametrize('payload', [
    {'kind': 'agent_result', 'summary': '旧失败', 'status': 'error', 'error_message': 'bad', 'emit_payloads': [{'kind': 'image', 'failed': True}]},
    {'kind': 'agent_result', 'summary': 'old'},
    {'kind': 'scalar', 'value': 'legacy'}, {'kind': 'json', 'value': {'old': True}},
    {'kind': 'scalar', 'value': 3}, None,
])
def test_legacy_payload_defaults_do_not_invent_history(payload):
    _, binding = envelope('binding')
    recovered = restore(payload, binding)
    old = old_function('deserialize_tool_result')(payload)
    if isinstance(old, AgentResult):
        assert recovered.raw.summary == old.summary and recovered.status == old.status
        assert recovered.raw.emit_payloads == old.emit_payloads
        assert recovered.agent_context['tokens_used'] == 0 and recovered.agent_context['thinking_text'] == ''
        assert recovered.error is None or recovered.error.retryable is None
        assert recovered.metadata == {}
    else:
        assert recovered.raw == (old if isinstance(old, str) else str(old))
    assert 'origin' not in recovered.audit and 'payload_version' not in recovered.audit
    call,context,decision=binding
    assert 'origin' not in recovered.reused(call=call,context=context,decision=decision,replayed=True).audit


@pytest.mark.parametrize('status', ['success', 'error', 'timeout', 'partial'])
def test_cross_version_rollback_shell_and_information_limits(tmp_path, monkeypatch, status):
    raw = AgentResult('结果', status=status, error_message='业务错误', tokens_used=31, thinking_text='thinking',
                      metadata={'retryable': False}, emit_payloads=[{'kind':'file','url':'https://example.test/a.csv'}])
    result, binding = envelope(raw)
    monkeypatch.setattr(get_settings(), 'tool_result_payload_write_version', 0)
    assert serialize_tool_result(result) == old_function('serialize_tool_result')(raw)
    monkeypatch.setattr(get_settings(), 'tool_result_payload_write_version', 1)
    payload = serialize_tool_result(result)
    target = tmp_path / 'isolated-ledger.json'; target.write_text(json.dumps(payload))
    recovered = old_function('deserialize_tool_result')(json.loads(target.read_text()))
    assert recovered.status == status and recovered.summary == raw.summary
    assert recovered.error_message == raw.error_message and recovered.emit_payloads == raw.emit_payloads
    # cdba58f9 is readable but lossy. Only the compatible reader release provides
    # full rollback; reverting its write flag does not remove its read support.
    assert recovered.tokens_used == 0 and recovered.thinking_text == '' and recovered.metadata == {}
    monkeypatch.setattr(get_settings(), 'tool_result_payload_write_version', 0)
    full = restore(json.loads(target.read_text()), binding)
    assert full.agent_context == result.agent_context and full.error == result.error
    assert serialize_tool_result(full).get('tool_result') is None


def test_safe_metadata_normalization_and_omission_without_repr():
    class Handle:
        def __repr__(self): raise AssertionError('never repr a handle')
    raw = AgentResult('ok', metadata={'db': Handle(), 'lock': threading.Lock(), 'exception': RuntimeError('secret'),
        'count': Decimal('1.25'), 'at': datetime(2026,9,11,tzinfo=timezone.utc), 'id': UUID(int=1),
        'retry_context': {'attempt': 1}})
    result, binding = envelope(raw)
    payload = encode_result(result); recovered = restore(payload, binding)
    assert set(payload['tool_result']['omitted_metadata']) == {'$.raw.metadata.db','$.raw.metadata.lock','$.raw.metadata.exception'}
    assert recovered.metadata == {'count': 1.25, 'at': '2026-09-11T00:00:00+00:00', 'id': str(UUID(int=1)), 'retry_context': {'attempt': 1}}
    assert all(word not in json.dumps(payload) for word in ('secret', 'object at', 'ToolContext'))


@pytest.mark.parametrize('case', ['text', 'rows', 'total', 'depth', 'retry_object', 'artifact_object', 'nan'])
def test_unsafe_or_oversized_payload_explicitly_rejected(case):
    raw = AgentResult('ok')
    if case == 'text': raw.summary = 'x' * (MAX_STRING + 1)
    if case == 'rows': raw.data = [{'n':i} for i in range(201)]
    if case == 'total': raw.emit_payloads = [{'kind':'diagram','source':'x'*MAX_STRING} for _ in range(5)]
    if case == 'depth':
        value = {}; raw.metadata = value
        for _ in range(20): value['next'] = {}; value = value['next']
    if case == 'retry_object': raw.metadata = {'retry_context': {'db': object()}}
    if case == 'artifact_object': raw.emit_payloads = [{'kind':'image','db': object()}]
    if case == 'nan': raw.confidence = float('nan')
    with pytest.raises(ToolPayloadError): encode_result(envelope(raw)[0])


@pytest.mark.parametrize('version', [2, True, None, '1'])
def test_unknown_version_is_not_downgraded_to_shell_success(version):
    result, binding = envelope('ok'); payload = encode_result(result)
    payload['tool_result']['version'] = version
    with pytest.raises(ToolPayloadError): restore(payload, binding)


class LocalLedger(InvocationStore):
    def begin(self, **kwargs):
        outcome = super().begin(**kwargs)
        if outcome['outcome'] == 'execute':
            self.row = {'tool_name': kwargs['tool_name'], 'args_hash': kwargs['args_hash'], 'status': 'running'}
        return outcome

    def complete(self, **kwargs):
        super().complete(**kwargs)
        self.row = {'tool_name':'generate_image', 'args_hash':hash_tool_arguments({'prompt':'fixture'}),
                    'status':kwargs['status'], 'result':json.loads(json.dumps(kwargs['result']))}


@pytest.mark.parametrize('failure', [False, True])
async def test_actor_completed_roundtrip_handler_zero_extra_and_current_audit(setup, monkeypatch, failure):
    monkeypatch.setattr(get_settings(), 'tool_result_payload_write_version', 1)
    store = LocalLedger(); host = actor_harness(store)
    raw = AgentResult('业务失败' if failure else '完成', status='error' if failure else 'success', error_message='bad' if failure else '',
        tokens_used=33, thinking_text='思考', metadata={'retryable':False,'retry_context':{'prompt':'fixture'}},
        emit_payloads=[{'kind':'image','failed':failure,'url':'https://example.test/a.png','retry_context':{'prompt':'fixture'}}])
    executor = MockHandlerExecutor(agent_domain='general', task_id='task1'); executor.handler.return_value = raw
    outputs = []
    for _ in range(2):
        outputs += await invoke('chat', executor, [tc('generate_image',{'prompt':'fixture'})], monkeypatch, host)
    assert executor.handler.await_count == 1 and len(store.completed) == 1
    assert store.row['status'] == 'succeeded'
    assert outputs[1].status == outputs[0].status and outputs[1].is_failure == failure
    assert outputs[1].execution.replayed and outputs[1].execution.attempts == 0
    assert outputs[1].error == outputs[0].error and outputs[1].artifacts == outputs[0].artifacts
    assert outputs[1].agent_context == outputs[0].agent_context
    assert host._erp_agent_tokens == 33
    assert host._emit_tool_audit.call_count == 2
    normal, replay = host._emit_tool_audit.call_args_list
    assert normal.args[9] == replay.args[9] == raw.status
    assert normal.kwargs['execution']['chargeable_tokens'] == 33
    assert replay.kwargs['execution']['chargeable_tokens'] == 0 and replay.kwargs['execution']['replayed']
    assert not replay.kwargs['is_cached']


@pytest.mark.parametrize('state', ['uncertain','running','in_progress'])
async def test_actor_unknown_effects_do_not_retry(setup, state):
    store = LocalLedger(row={'tool_name':'generate_image','args_hash':hash_tool_arguments({'prompt':'fixture'}),'status':state})
    host = actor_harness(store); executor = MockHandlerExecutor(agent_domain='general',task_id='task1')
    for _ in range(2):
        out = await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
        assert out[1].execution.status == 'uncertain' and out[1].is_failure and not out[1].error.safe_to_retry
    executor.handler.assert_not_awaited(); assert not store.completed and 'begin' not in store.trace


async def test_policy_denial_before_any_cache_ledger_payload_read(setup):
    store = LocalLedger(); host = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain='general',permission_mode='plan',task_id='task1')
    cache = Mock(); cache.get.side_effect = AssertionError('unauthorized read')
    from services.handlers.chat.tool_lifecycle import ActorToolLifecycle
    out = await executor.tool_runtime.execute('generate_image',{'prompt':'fixture'},cache=cache,
            lifecycle=ActorToolLifecycle(host,executor.tool_runtime.context()))
    assert out.status == 'denied' and out.execution.status == 'not_started'
    assert not store.trace and not store.completed; cache.get.assert_not_called(); executor.handler.assert_not_awaited()
    out = await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
    assert host._emit_tool_audit.call_args.args[9] == 'denied' and out[1].execution.status == 'not_started'


@pytest.mark.parametrize('artifact', ['file_ref','emit'])
async def test_new_replay_resource_escape_rejected_before_rehydrate_or_handler(setup, monkeypatch, artifact):
    _,root = setup; raw = AgentResult('completed')
    if artifact == 'emit': raw.emit_payloads = [{'kind':'file','workspace_path':'../u2/private.csv'}]
    else: raw.file_ref = FileRef(str(root/'org/o1/u2/private.parquet'),'private.parquet','parquet',1,1,[])
    payload = encode_result(envelope(raw)[0])
    store = LocalLedger(row={'tool_name':'generate_image','args_hash':hash_tool_arguments({'prompt':'fixture'}),'status':'succeeded','result':payload})
    host = actor_harness(store); executor = MockHandlerExecutor(agent_domain='general',task_id='task1')
    decode = Mock(side_effect=AssertionError('rehydrate before resource check'))
    monkeypatch.setattr('services.tools.result_payload.decode_raw',decode)
    out = await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
    assert out[1].is_failure and 'scope_mismatch' in out[3]
    decode.assert_not_called(); executor.handler.assert_not_awaited()


@pytest.mark.parametrize('status', ['success','error','timeout'])
async def test_cache_snapshot_preserves_business_state_and_no_handler_or_tokens(setup,status):
    executor = MockHandlerExecutor(agent_domain='general'); cache = ToolResultCache()
    raw = AgentResult('result',status=status,error_message='bad',tokens_used=12,thinking_text='trace',metadata={'retryable':False})
    executor.handler.return_value = raw
    first = await executor.tool_runtime.execute('search_knowledge',{'file':'overview'},call_id='a',cache=cache)
    raw.summary = 'mutated after caching'
    second = await executor.tool_runtime.execute('search_knowledge',{'file':'overview'},call_id='b',cache=cache)
    assert second.raw.summary == 'result' and second.status == status
    assert second.error == first.error and second.execution.cached and second.chargeable_tokens == 0
    assert second.agent_context['tokens_used'] == 12 and second.agent_context['thinking_text'] == 'trace'
    assert second.audit['tool_call_id'] == 'b'; executor.handler.assert_awaited_once()


async def test_cache_scope_isolation_and_permission_revocation(setup):
    cache = ToolResultCache()
    a = MockHandlerExecutor(user_id='u1',agent_domain='general'); b = MockHandlerExecutor(user_id='u2',agent_domain='general')
    a.handler.return_value = AgentResult('u1'); b.handler.return_value = AgentResult('u2')
    await a.tool_runtime.execute('search_knowledge',{'file':'overview'},call_id='a',cache=cache)
    out = await b.tool_runtime.execute('search_knowledge',{'file':'overview'},call_id='b',cache=cache)
    assert out.raw.summary == 'u2' and not out.execution.cached
    a.db.active = False
    read = Mock(wraps=cache.get); cache.get = read
    denied = await a.tool_runtime.execute('search_knowledge',{'file':'overview'},call_id='c',cache=cache)
    assert denied.is_failure; read.assert_not_called()


@pytest.mark.parametrize('failure', ['cache_read','cache_write','audit_dispatch','audit_db','display','completion','payload'])
async def test_failures_observable_and_never_redo_business(setup, monkeypatch, failure):
    monkeypatch.setattr(get_settings(),'tool_result_payload_write_version',1)
    from loguru import logger
    logs=[]; sink=logger.add(lambda msg:logs.append(str(msg)))
    try:
        executor = MockHandlerExecutor(agent_domain='general',task_id='task1')
        executor.handler.return_value = AgentResult('x'*(MAX_STRING+1) if failure=='payload' else 'ok')
        if failure.startswith('cache'):
            cache=Mock(get=Mock(return_value=None),put=Mock())
            getattr(cache,'get' if failure=='cache_read' else 'put').side_effect=RuntimeError('offline')
            out=await executor.tool_runtime.execute('search_knowledge',{'file':'overview'},cache=cache)
            assert executor.handler.await_count==(0 if failure=='cache_read' else 1)
            assert out.is_failure==(failure=='cache_read')
        else:
            store=LocalLedger();host=actor_harness(store)
            if failure=='completion': store.complete=Mock(side_effect=RuntimeError('offline'))
            if failure=='audit_dispatch': host._emit_tool_audit.side_effect=RuntimeError('offline')
            if failure=='audit_db':
                host.db=Mock();host.db.table.side_effect=RuntimeError('offline')
                host._emit_tool_audit=lambda *a,**kw:ChatToolMixin._emit_tool_audit(host,*a,**kw)
            if failure=='display': host._execution_sink=SimpleNamespace(on_tool_result=AsyncMock(side_effect=ConnectionError('offline')))
            operation=host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
            if failure=='display':
                with pytest.raises(ConnectionError): await operation
                host._execution_sink=None
                replay=await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
                assert replay[1].execution.replayed
            else: await operation
            assert executor.handler.await_count==1
            if failure in ('payload','completion'):
                assert not store.completed and store.row['status']=='running'
                restarted=MockHandlerExecutor(agent_domain='general',task_id='task1')
                out=await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),restarted,'task1','c1','m1','u1',1)
                assert out[1].execution.status=='uncertain'
                restarted.handler.assert_not_awaited()
        await asyncio.sleep(.03)
        assert any('failed' in line.lower() for line in logs),logs
    finally: logger.remove(sink)


async def test_loop_audit_once_per_consumption_and_model_tokens_once_per_turn(setup,monkeypatch):
    executor=MockHandlerExecutor(agent_domain='general');executor.handler.return_value=AgentResult('error',status='error',error_message='bad',tokens_used=23)
    loop,ctx=loop_for(executor);loop.hooks=[ToolAuditHook()]
    entries=[]
    async def record(db,entry): entries.append(entry)
    monkeypatch.setattr('services.agent.tool_audit.record_tool_audit',record)
    for suffix in ('a','b'):
        await loop._execute_tools([tc('search_knowledge',{'file':'overview'},suffix),tc('web_search',{'query':'x'},suffix+'2')],[], '',ctx,
                                  turn_prompt_tokens=7,turn_completion_tokens=3)
    await asyncio.sleep(0)
    assert len(entries)==4 and executor.handler.await_count==2
    assert [e.status for e in entries]==['error']*4
    assert [e.is_cached for e in entries]==[False,False,True,True]
    assert sum(e.prompt_tokens for e in entries)==14 and sum(e.completion_tokens for e in entries)==6
    assert sum(e.execution['chargeable_tokens'] for e in entries)==46


async def test_cancellation_payload_remains_cancelled_and_actor_does_not_complete(setup,monkeypatch):
    result,binding=envelope('unused');call,context,decision=binding
    result=ToolResult.from_exception(asyncio.CancelledError(),call=call,context=context,decision=decision,handler_started=True)
    recovered=restore(encode_result(result),binding)
    assert recovered.execution.cancelled and recovered.is_failure
    with pytest.raises(asyncio.CancelledError): recovered.model_content('chat')
    store=LocalLedger();host=actor_harness(store);executor=MockHandlerExecutor(agent_domain='general',task_id='task1')
    executor.handler.side_effect=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
    assert not store.completed and executor.handler.await_count==1
    host._emit_tool_audit.assert_not_called()


async def test_actual_audit_writer_preserves_database_contract_and_replay_log(setup, monkeypatch):
    from loguru import logger
    monkeypatch.setattr(get_settings(),'tool_result_payload_write_version',1)
    rows=[];logs=[];done=asyncio.Event()
    loop=asyncio.get_running_loop()
    class DB:
        def table(self,name): assert name=='tool_audit_log';return self
        def insert(self,row,returning=False):
            assert returning is False; rows.append(row);return self
        def execute(self):
            if len(rows)==3: loop.call_soon_threadsafe(done.set)
    store=LocalLedger();host=actor_harness(store);host.db=DB()
    host._emit_tool_audit=lambda *a,**kw:ChatToolMixin._emit_tool_audit(host,*a,**kw)
    executor=MockHandlerExecutor(agent_domain='general',task_id='task1')
    executor.handler.return_value=AgentResult('失败',status='error',error_message='bad',tokens_used=13)
    sink=logger.add(lambda m:logs.append(m.record))
    try:
        for _ in range(2):
            await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
        executor.permission_mode='plan'
        await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'},'denied'),executor,'task1','c1','m1','u1',1)
        await asyncio.wait_for(done.wait(),1)
        assert [r['status'] for r in rows]==['error','error','denied']
        assert all(r['user_id']=='u1' and r['conversation_id']=='c1' and r['task_id']=='task1' for r in rows)
        assert all('execution' not in r for r in rows)  # no undeployed DB columns
        events=[r['extra']['tool_execution'] for r in logs if 'tool_execution' in r['extra']]
        assert len(events)==3 and events[1]['replayed'] and events[2]['status']=='not_started'
        assert [e['chargeable_tokens'] for e in events]==[13,0,0]
        assert executor.handler.await_count==1 and len(store.completed)==1
    finally: logger.remove(sink)


def test_file_ref_all_fields_and_database_cell_types(tmp_path):
    cols=[ColumnMeta('qty','numeric','数量')]
    ref=FileRef(str(tmp_path/'report.parquet'),'report.parquet','parquet',300,800,cols,
        preview='qty=2',created_at=123.,id='artifact-id',mime_type='application/x-parquet',
        created_by='erp_agent',ttl_seconds=172800,derived_from=('input-a','input-b'))
    raw=AgentResult('file',format=OutputFormat.FILE_REF,file_ref=ref,columns=cols,source='erp_agent')
    original,binding=envelope(raw);recovered=restore(encode_result(original),binding)
    assert recovered.raw.file_ref==ref
    raw=AgentResult('table',format=OutputFormat.TABLE,columns=cols,data=[{'qty':Decimal('2.5'),'id':UUID(int=2),'at':datetime(2026,9,11)}])
    original,binding=envelope(raw);recovered=restore(encode_result(original),binding)
    assert recovered.model_content('chat')==original.model_content('chat')
    assert recovered.model_content('tool_loop')==original.model_content('tool_loop')


async def test_loop_delivery_failure_still_audits_completed_calls_once(setup,monkeypatch):
    executor=MockHandlerExecutor(agent_domain='general');executor.handler.return_value=AgentResult('ok')
    loop,ctx=loop_for(executor);loop.hooks=[ToolAuditHook()]
    entries=[]
    async def record(db,entry): entries.append(entry)
    monkeypatch.setattr('services.agent.tool_audit.record_tool_audit',record)
    loop._register_result_files=Mock(side_effect=ConnectionError('artifact delivery unavailable'))
    with pytest.raises(ConnectionError):
        await loop._execute_tools([tc('search_knowledge',{'file':'overview'},'a'),tc('web_search',{'query':'x'},'b')],[], '',ctx)
    await asyncio.sleep(0)
    assert executor.handler.await_count==2 and len(entries)==2
    assert [entry.status for entry in entries]==['success','success']


@pytest.mark.parametrize('case', ['file','image','form','string'])
def test_rollback_reader_handles_new_non_text_shell_with_documented_type_loss(setup,case):
    _,raw=sample(case,setup[1]);result,binding=envelope(raw);payload=encode_result(result)
    legacy=old_function('deserialize_tool_result')(payload)
    assert isinstance(legacy, (AgentResult,str))
    if case=='string': assert legacy==raw
    elif case=='file':
        assert legacy.summary==raw.to_tool_content() and legacy.file_ref is None
    else:
        assert legacy.summary==result.display['text']
        assert not hasattr(legacy,'form') and not hasattr(legacy,'image_url')
    assert restore(payload,binding).artifacts==result.artifacts


@pytest.mark.parametrize('exception', [RuntimeError('provider lost'), TimeoutError('late'), PermissionError('denied')])
@pytest.mark.parametrize('started', [False, True])
def test_exception_payload_contains_descriptors_only(exception,started):
    _,binding=envelope('unused');call,context,decision=binding
    original=ToolResult.from_exception(exception,call=call,context=context,decision=decision,handler_started=started)
    recovered=restore(encode_result(original),binding)
    assert recovered.status==original.status and recovered.execution==original.execution
    assert recovered.error==original.error and recovered.is_failure
    assert recovered.model_content('chat')==original.model_content('chat')
    assert recovered.exception is not exception  # recreate a safe builtin, never deserialize an exception object
    assert recovered.error.safe_to_retry is False


async def test_corrupt_cache_never_falls_back_to_business(setup):
    result,_=envelope('ok');payload=encode_result(result);payload['tool_result']['version']=999
    executor=MockHandlerExecutor(agent_domain='general');cache=Mock(get=Mock(return_value=payload))
    out=await executor.tool_runtime.execute('search_knowledge',{'file':'overview'},cache=cache)
    assert out.is_failure and out.execution.status=='not_started'
    executor.handler.assert_not_awaited();cache.put.assert_not_called()


@pytest.mark.parametrize('write_version',[0,1])
def test_reader_first_writer_also_excludes_runtime_handles(monkeypatch,write_version):
    monkeypatch.setattr(get_settings(),'tool_result_payload_write_version',write_version)
    class DB:
        def __str__(self): raise AssertionError('runtime handle must not be stringified')
    raw=AgentResult('table',format=OutputFormat.TABLE,metadata={'db':DB()},columns=[])
    result,_=envelope(raw)
    payload=serialize_tool_result(result)
    assert 'db:' not in payload['summary']
    assert 'object at' not in json.dumps(payload)


def test_cache_bounds_include_artifacts_even_with_small_summary(setup):
    cache=ToolResultCache()
    result,_=envelope(AgentResult('ok',emit_payloads=[{'kind':'diagram','source':'x'*8100}]))
    cache.put('search_knowledge',{'file':'overview'},result)
    assert cache.get('search_knowledge',{'file':'overview'}) is None


def test_legacy_shell_of_cancelled_result_is_never_success():
    _,binding=envelope('unused');call,context,decision=binding
    result=ToolResult.from_exception(asyncio.CancelledError('cancelled'),call=call,context=context,decision=decision,handler_started=True)
    legacy=old_function('deserialize_tool_result')(encode_result(result))
    assert legacy.is_failure and legacy.status=='error'


@pytest.mark.parametrize('value', [object(), threading.Lock(), RuntimeError('private'),
    {'db': object()}, 'x' * (MAX_STRING + 1), {'data': ['x' * MAX_STRING] * 5}])
def test_legacy_raw_writer_cannot_bypass_payload_safety(value):
    with pytest.raises(ToolPayloadError): serialize_tool_result(value)
