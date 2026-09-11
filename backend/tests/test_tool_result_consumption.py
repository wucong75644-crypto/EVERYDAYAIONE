"""05 live consumers: protocol goldens captured from main 6c0737ab, mock external IO only."""
import asyncio
import base64
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from schemas.multimodal import FileReadResult
from services.agent.agent_result import AgentResult
from services.agent.tool_output import FileRef, ColumnMeta, OutputFormat
from services.handlers.chat_tool_mixin import ChatToolMixin
from services.handlers.chat.tool_loop import apply_tool_results, append_tool_images
from services.handlers.chat.execution_engine import _consume_emit_payloads, _build_replay_context
from services.handlers.chat.actor_sink import ActorWebSink
from services.handlers.chat.execution_sink import WebSocketExecutionSink
from services.scheduler.chat_task_manager import FormBlockResult
from services.tools import ToolResult
from tests.test_tool_production_integration import ChatHarness, loop_for, tc, setup
from tests.tool_runtime_support import MockHandlerExecutor
from tests.test_chat_actor_sink import _DB, _WebSocket, _DeliveryStore, _delivery

CASES = ('string', 'file', 'image', 'sandbox', 'erp', 'media_image', 'media_video', 'media_failure', 'form')
FIXTURES = Path(__file__).parent / 'fixtures/tool_result_05'


def sample(case, root):
    workspace = root / 'org/o1/u1'
    (workspace / 'staging').mkdir(parents=True, exist_ok=True)
    (workspace / 'staging/report.csv').write_text('sku,qty\nA,2\n')
    # Existing offline image convention: a valid, locally resolvable 1x1 PNG.
    (workspace / 'sample.png').write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    file = dict(kind='file', url='https://example.test/report.csv', name='report.csv',
                mime_type='text/csv', size=12, workspace_path='staging/report.csv')
    image = dict(kind='image', url='https://example.test/sample.png', name='sample.png',
                 workspace_path='sample.png', width=1, height=1,
                 original_url='https://example.test/original.png', thumbnail_url='https://example.test/thumb.png',
                 preview_url='https://example.test/preview.png', download_url='https://example.test/download.png')
    if case == 'string': return tc('web_search', {'query':'fixture'}), '搜索结果'
    if case == 'file':
        import pandas as pd
        target = workspace / 'staging/data.parquet'
        pd.DataFrame({'qty':[2]}).to_parquet(target)
        return tc('web_search', {'query':'fixture'}), AgentResult('文件已生成', format=OutputFormat.FILE_REF,
            file_ref=FileRef(path=str(target),filename='data.parquet',format='parquet',columns=[],row_count=1,size_bytes=1024),
            emit_payloads=[file])
    if case == 'image': return tc('file_search', {'query':'sample.png'}), FileReadResult(type='image', text='图片信息', image_url=image['url'])
    if case == 'sandbox': return tc('code_execute', {'code':'print(1)'}), AgentResult('沙盒完成', emit_payloads=[file,image,
        {'kind':'chart','option':{'title':{'text':'数量'},'series':[{'type':'bar','data':[2]}]}},
        {'kind':'diagram','source':'graph LR; A-->B;','title':'流程'}], tokens_used=17, thinking_text='计算完成')
    if case == 'erp': return tc('erp_agent', {'task':'查询库存'}), AgentResult('库存摘要', format=OutputFormat.TABLE,
        data=[{'qty':2}],columns=[ColumnMeta('qty','int','数量')],metadata={'title':'库存'},emit_payloads=[file])
    if case == 'media_image': return tc('generate_image', {'prompt':'fixture'}), AgentResult('图片完成',emit_payloads=[image])
    if case == 'media_video': return tc('generate_video', {'prompt':'fixture'}), AgentResult('视频完成',emit_payloads=[{
        **file,'url':'https://example.test/sample.mp4','name':'sample.mp4','mime_type':'video/mp4','workspace_path':'videos/sample.mp4'}])
    if case == 'media_failure': return tc('generate_image', {'prompt':'fixture'}), AgentResult('图片生成失败',status='error',error_message='provider failure',
        metadata={'retryable':False,'retry_context':{'prompt':'fixture','model':'sample'}},emit_payloads=[{
            'kind':'image','failed':True,'error':'provider failure','retry_context':{'prompt':'fixture','model':'sample'}}])
    if case == 'form':
        from services.scheduler.chat_task_manager import _build_create_form
        from unittest.mock import patch
        from uuid import UUID
        # Full production form builder and fixed UUID input, including visibility,
        # push target, submit/cancel controls; no fields removed for comparison.
        with patch('services.scheduler.chat_task_manager.uuid4',return_value=UUID(int=1)):
            form=_build_create_form({'name':'日报','prompt':'汇总日报','schedule_type':'daily','time_str':'09:00'},
                [{'label':'推送给我（网页）','value':'{"type":"web","user_id":"u1"}'}])
        return tc('manage_scheduled_task', {'action':'create','description':'日报'}), FormBlockResult(form,'等待用户提交表单')
    raise AssertionError(case)


def normalize(value):
    """Only clock-derived fields change: validate their type, retain all keys/ordering.

    IDs are fixed inputs/session fixtures; no ID or delivery field is dropped.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == 'timestamp' and ('payload' in value and 'type' in value or value.get('role') == 'tool'):
                if type(item) is int:
                    assert item > 0
                    result[key] = '<UNIX_MILLISECONDS>'
                else:
                    assert isinstance(item,str)
                    datetime.fromisoformat(item.replace('Z','+00:00'))
                    result[key] = '<ISO_TIMESTAMP>'
            elif key == 'elapsed_ms' and value.get('type') == 'tool_step':
                assert type(item) is int and item >= 0
                result[key] = '<NONNEGATIVE_ELAPSED_MS>'
            else: result[key] = normalize(item)
        return result
    if isinstance(value,(list,tuple)): return [normalize(v) for v in value]
    return value


async def chat_run(case, transport, root, monkeypatch):
    call, raw = sample(case, root)
    executor = MockHandlerExecutor(agent_domain='general',task_id='task1')
    executor.handler.return_value = raw
    monkeypatch.setattr('services.tool_executor.ToolExecutor',lambda **_:executor)
    host = ChatHarness(executor.db)
    host._push_tool_step_update = ChatToolMixin._push_tool_step_update.__get__(host)
    ws = _WebSocket()
    ws.register_steer_listener = Mock(); ws.register_cancel_listener = Mock()
    ws.unregister_steer_listener = Mock(); ws.unregister_cancel_listener = Mock()
    store = _DeliveryStore()
    if transport == 'actor': sink = ActorWebSink(_DB([]),_delivery(),asyncio.Event(),ws,store)
    else: sink = WebSocketExecutionSink(task_id='task1',conversation_id='c1',message_id='m1',user_id='u1',model_id='model1',
        websocket=ws,save_content=AsyncMock(),save_blocks=AsyncMock())
    host._execution_sink = sink
    # ActorWebSink has no on_tool_result; legacy fallback stays in use, as on main.
    monkeypatch.setattr('services.handlers.chat_tool_mixin.ws_manager',ws)
    await sink.start()
    running = {'type':'tool_step','tool_call_id':call['id'],'tool_name':call['name'],'status':'running'}
    await sink.on_block(running)
    results = await host._execute_tool_calls([call],'task1','c1','m1','u1',1)
    messages=[]; blocks=[dict(running)]
    images=apply_tool_results(tool_results=results,messages=messages,content_blocks=blocks,start_times={},tool_context=Mock())
    append_tool_images(messages,images)
    await _consume_emit_payloads(host,blocks,sink)
    await sink.flush()
    checkpoint = _build_replay_context(messages,blocks,0,tool_call_ids=[call['id']],next_model_round=1)
    return dict(ws=normalize(ws.messages),events=normalize(store.events),checkpoint=normalize(checkpoint)), results, host, executor, raw


@pytest.mark.parametrize('case',CASES)
@pytest.mark.parametrize('transport',['legacy','actor'])
async def test_live_chat_protocol_matches_main(setup,case,transport,monkeypatch):
    _,root=setup
    actual,results,host,executor,raw=await chat_run(case,transport,root,monkeypatch)
    golden=FIXTURES / f'{transport}-{case}.json'
    if os.environ.get('TOOL05_RECORD_GOLDEN') == '6c0737ab':
        import hashlib
        import inspect
        assert hashlib.sha256(Path(inspect.getfile(ToolResult)).read_bytes()).hexdigest() == 'ccd5362d3e39dd2ac05018303b3c0cfc2e3c580ed6f6ab65019d65a4db51fc63'
        FIXTURES.mkdir(parents=True,exist_ok=True)
        golden.write_text(json.dumps(actual,ensure_ascii=False,indent=2)+'\n')
        return
    assert actual == json.loads(golden.read_text())
    assert isinstance(results[0][1],ToolResult)
    assert results[0][1].raw is raw
    executor.handler.assert_awaited_once()
    host._emit_tool_audit.assert_called_once()
    assert not host._pending_emit_payloads
    if case == 'form': assert host._terminal_form_pending and host._pending_form_block is None
    if case == 'erp': assert not any(b['type']=='table' for b in actual['checkpoint']['content_blocks'])
    if case == 'image':
        from PIL import Image
        with Image.open(root/'org/o1/u1/sample.png') as picture:
            picture.load()
            assert picture.size == (1,1)
        assert actual['checkpoint']['messages'][-1]['content'][1]['image_url']['url'] == raw.image_url
    if case == 'sandbox':
        assert host._erp_agent_tokens == raw.tokens_used
        assert results[0][1].display['thinking_text'] == raw.thinking_text
    if case == 'file':
        import pandas as pd
        assert pd.read_parquet(raw.file_ref.path)['qty'].tolist()==[2]
        assert (root/'org/o1/u1/staging/report.csv').read_text()=='sku,qty\nA,2\n'
    if case == 'media_failure': assert results[0][1].error.retry_context == raw.metadata['retry_context']


@pytest.mark.parametrize('status',['success','error','timeout','empty','partial','plan'])
@pytest.mark.parametrize('consumer',['chat','tool_loop'])
async def test_live_states_projection_metadata_and_audit(setup,status,consumer,monkeypatch):
    from services.agent.loop_hooks import ToolAuditHook
    from services.agent.stop_policy import classify_tool_result,ResultClass
    from tests.test_tool_result import representative_agent
    raw=representative_agent(OutputFormat.TABLE,status)
    executor=MockHandlerExecutor(agent_domain='general',task_id='task1')
    executor.handler.return_value=raw
    writes=[]
    async def audit(_db,entry): writes.append(entry)
    monkeypatch.setattr('services.agent.tool_audit.record_tool_audit',audit)
    if consumer=='chat':
        host=ChatHarness(executor.db)
        host._emit_tool_audit=ChatToolMixin._emit_tool_audit.__get__(host)
        result=(await host._execute_single_tool(tc('web_search',{'query':'fixture'}),executor,'task1','c1','m1','u1',1))[1]
        assert result.model_content('chat')==raw.to_message_content()
    else:
        loop,ctx=loop_for(executor);loop.hooks=[ToolAuditHook()]
        await loop._execute_tools([tc('web_search',{'query':'fixture'})],[],'',ctx,turn_prompt_tokens=11,turn_completion_tokens=7)
        result=loop._turn_tool_outcomes[0][1]
        assert ctx.messages[-1]['content']==raw.to_tool_content()
        assert writes==[]  # fire-and-forget writer, drained below
    await asyncio.sleep(0)
    assert isinstance(result,ToolResult) and result.raw is raw
    assert result.status==status and result.is_failure==raw.is_failure
    assert result.execution.status=='succeeded'  # invocation completion != business status
    assert result.agent_context['tokens_used']==321 and result.display['thinking_text']==raw.thinking_text
    assert result.metadata is raw.metadata
    assert len(writes)==1 and writes[0].status==status and writes[0].result_length==len(raw.summary)
    if consumer=='tool_loop': assert (writes[0].prompt_tokens,writes[0].completion_tokens)==(11,7)
    if raw.is_failure:
        assert result.error.message==raw.error_message and result.error.retryable is True
        assert classify_tool_result(result,'success') != ResultClass.SUCCESS
    else: assert classify_tool_result(result,'error')==ResultClass.SUCCESS


@pytest.mark.parametrize('case',['file','image','erp','sandbox','media_image','media_video','media_failure','form'])
async def test_tool_loop_result_projection_and_artifact_collection(setup,case):
    from services.handlers.emit_payloads import collect_agent_result_payloads
    _,root=setup
    call,raw=sample(case,root)
    executor=MockHandlerExecutor(agent_domain='general')
    executor.handler.return_value=raw
    loop,ctx=loop_for(executor)
    await loop._execute_tools([call],[],'',ctx)
    result=loop._turn_tool_outcomes[0][1]
    assert isinstance(result,ToolResult) and result.raw is raw
    expected=raw.to_tool_content() if isinstance(raw,AgentResult) else raw.text if isinstance(raw,FileReadResult) else raw.llm_hint
    assert next(m['content'] for m in ctx.messages if m['role']=='tool')==expected
    assert loop._emit_payloads==collect_agent_result_payloads(raw)
    if case=='erp': assert sum(p['kind']=='table' for p in loop._emit_payloads)==1
    if case=='image': assert ctx.messages[-1]['content'][1:]==result.model_image_blocks


async def test_scheduled_agent_consumes_unified_table_and_audits_once(setup,monkeypatch):
    from services.agent.scheduled_task_agent import ScheduledTaskAgent
    from services.agent.tool_executor import ToolExecutor
    from tests.test_scheduled_task_agent_integration import FakeAdapter
    from tests.tool_runtime_support import IdentityDB
    _,root=setup
    _,raw=sample('erp',root)
    adapter=FakeAdapter([{'tool_calls':[{'id':'a','name':'web_search','args':'{"query":"x"}'}]}, {'text':'已完成只读查询'}])
    monkeypatch.setattr('services.model_gateway.get_model_gateway',lambda:SimpleNamespace(open_chat=lambda _:adapter))
    handler=AsyncMock(return_value=raw)
    monkeypatch.setattr(ToolExecutor,'_web_search',handler)
    writes=[]
    async def audit(_db,entry): writes.append(entry)
    monkeypatch.setattr('services.agent.tool_audit.record_tool_audit',audit)
    task={'id':'scheduled','user_id':'u1','org_id':'o1','prompt':'读取','timeout_sec':60,
          'execution_policy':{'version':1,'allowed_tools':['web_search'],'required_tools':['web_search']}}
    result=await ScheduledTaskAgent(IdentityDB(),task,execution_mode='scheduled').execute()
    await asyncio.sleep(0)
    assert result.status=='success' and adapter.closed
    assert len(writes)==1 and writes[0].tool_name=='web_search'
    assert sum(p['type']=='table' for p in result.content_blocks)==1
    handler.assert_awaited_once()


@pytest.mark.parametrize('consumer',['chat','tool_loop'])
async def test_long_model_projection_keeps_full_file_and_payload(setup,consumer,monkeypatch):
    from services.agent.tool_result_envelope import set_staging_dir,clear_staging_dir
    _,root=setup
    staging=root/'org/o1/u1/staging';staging.mkdir(parents=True,exist_ok=True)
    set_staging_dir(str(staging))
    executor=MockHandlerExecutor(agent_domain='general')
    long_text=('complete row\n'*4000)+'FULL_OUTPUT_END'
    executor.handler.return_value=long_text
    try:
        if consumer=='chat':
            host=ChatHarness(executor.db)
            envelope=(await host._execute_single_tool(tc('web_search',{'query':'x'}),executor,'task1','c1','m1','u1',1))[1]
        else:
            loop,ctx=loop_for(executor)
            await loop._execute_tools([tc('web_search',{'query':'x'})],[],'',ctx)
            envelope=loop._turn_tool_outcomes[0][1]
        content=envelope.model_content(consumer)
        assert '<persisted-output>' in content and len(content)<len(long_text)
        assert envelope.raw==long_text and envelope.audit_fields()['truncated'] is True
        files=list(staging.glob('tool_result_*.txt'))
        assert len(files)==1 and files[0].read_text()==long_text
        _,raw=sample('file',root);raw.summary=long_text
        executor.handler.return_value=raw
        result=await executor.tool_runtime.execute('web_search',{'query':'y'},call_id='long-agent')
        assert result.model_content(consumer)==(raw.to_message_content() if consumer=='chat' else raw.to_tool_content())
        assert result.artifacts.file_ref is raw.file_ref
        assert result.collect_payloads(consumer)==raw.emit_payloads
    finally: clear_staging_dir()


@pytest.mark.parametrize('tool',['search_knowledge','generate_image'])
@pytest.mark.parametrize('failure',['error','timeout','cancel'])
async def test_exception_timeout_cancel_and_effect_certainty(setup,tool,failure):
    from services.agent.tool_loop_helpers import invoke_tool_result_with_cache
    from services.agent.stop_policy import classify_tool_result,ResultClass
    executor=MockHandlerExecutor(agent_domain='general')
    async def fail(*_):
        if failure=='error': raise RuntimeError('offline failure')
        if failure=='cancel': raise asyncio.CancelledError()
        await asyncio.sleep(30)
    executor.handler.side_effect=fail
    call=invoke_tool_result_with_cache(executor,Mock(get=Mock(return_value=None)),tool,{},None,.01)
    if failure=='cancel':
        with pytest.raises(asyncio.CancelledError): await call
        return
    result,status,cached,ms=await call
    assert isinstance(result,ToolResult) and result.is_failure and not cached
    assert result.execution.handler_started and result.execution.attempts==1
    assert status==('timeout' if failure=='timeout' else 'error')
    assert not result.execution.cancelled
    if tool=='generate_image':
        assert result.execution.status=='uncertain'
        assert classify_tool_result(result,'success')==ResultClass.FATAL
        assert '不要重复执行' in result.model_content('tool_loop')
    else: assert result.execution.status=='failed'
    assert result.error.safe_to_retry is False
    executor.handler.assert_awaited_once()


async def test_cache_failure_state_and_single_audit_per_consumption(setup,monkeypatch):
    from services.agent.loop_hooks import ToolAuditHook
    executor=MockHandlerExecutor(agent_domain='general')
    raw=AgentResult('失败',status='error',error_message='bad',metadata={'retryable':False},tokens_used=12,thinking_text='trace')
    executor.handler.return_value=raw
    loop,ctx=loop_for(executor);loop.hooks=[ToolAuditHook()]
    writes=[]
    async def audit(_db,entry): writes.append(entry)
    monkeypatch.setattr('services.agent.tool_audit.record_tool_audit',audit)
    for id in ('first','cached'):
        await loop._execute_tools([tc('search_knowledge',{'file':'overview'},id)],[],'',ctx)
        result=loop._turn_tool_outcomes[0][1]
        assert result.status=='error' and result.is_failure
        assert result.raw.summary==raw.summary and result.agent_context['tokens_used']==raw.tokens_used
        assert result.metadata==raw.metadata
        assert (result.raw is raw)==(id=='first')  # 06 caches an isolated snapshot
        assert result.execution.cached == (id=='cached')
    await asyncio.sleep(0)
    assert len(writes)==2 and [w.is_cached for w in writes]==[False,True]
    assert [w.status for w in writes]==['error','error']
    executor.handler.assert_awaited_once()


@pytest.mark.parametrize('case',CASES)
async def test_explicit_legacy_persistence_projection_and_old_reader(setup,case,monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), 'tool_result_payload_write_version', 0)
    from services.tool_invocation_store import serialize_tool_result,deserialize_tool_result
    _,root=setup
    call,raw=sample(case,root)
    executor=MockHandlerExecutor(agent_domain='general')
    executor.handler.return_value=raw
    result=await executor.tool_runtime.execute(call['name'],json.loads(call['arguments']),call_id=call['id'])
    assert isinstance(result,ToolResult) and result.raw is raw
    payload=serialize_tool_result(result.legacy_persistence_value())
    # Old writer/reader contract only; its historical lossy fields belong to 06.
    assert payload==serialize_tool_result(raw)
    recovered=deserialize_tool_result(json.loads(json.dumps(payload)))
    if isinstance(raw,AgentResult):
        assert recovered.summary==raw.to_tool_content()
        assert recovered.emit_payloads==raw.emit_payloads
        assert recovered.status==raw.status and recovered.error_message==raw.error_message
    else: assert recovered==(raw if isinstance(raw,str) else str(raw))
    assert 'ToolResult(' not in json.dumps(payload)
    assert serialize_tool_result(result)==payload  # Explicit reader-first/rollback write setting.
    with pytest.raises(TypeError,match='Project ToolResult'): _build_replay_context([{'content':result}],[],0)


@pytest.mark.parametrize('case',['form','uncertain'])
async def test_full_chat_loop_stops_after_terminal_result(setup,case,monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop,ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    from services.handlers.permission_mode import PermissionMode
    from services.handlers.tool_loop_context import ToolLoopContext
    from services.tools.runtime_context import chat_context
    from services.agent.execution_budget import ExecutionBudget
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    _,root=setup
    call,raw=sample('form' if case=='form' else 'media_image',root)
    executor=MockHandlerExecutor(agent_domain='general',task_id='task1')
    if case=='form': executor.handler.return_value=raw
    else: executor.handler.side_effect=RuntimeError('external response lost')
    monkeypatch.setattr('services.tool_executor.ToolExecutor',lambda **_:executor)
    host=ChatHarness(executor.db)
    budget=ExecutionBudget(max_turns=5); event=asyncio.Event()
    context=chat_context(host,user_id='u1',conversation_id='c1',task_id='task1',permission_mode='auto',budget=budget,cancellation=event)
    prepared=SimpleNamespace(budget=budget,permission=PermissionMode(mode='auto'),core_tools=[],execution_context=context,
        tool_context=ToolLoopContext(org_id='o1',agent_domain='general'),messages=[])
    model=AsyncMock(return_value=('', '', [call],set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    sink=CollectingExecutionSink(); blocks=[];totals=StreamTotals()
    host._execution_sink=sink
    await _run_loop(handler=host,request=ChatExecutionRequest(content=[],user_id='u1',conversation_id='c1',task_id='task1',
        message_id='m1',model_id='fixture',context_anchor=None),prepared=prepared,cancellation_event=event,sink=sink,totals=totals,blocks=blocks,runtime=None)
    model.assert_awaited_once();executor.handler.assert_awaited_once();host._emit_tool_audit.assert_called_once()
    if case=='form': assert sum(b['type']=='form' for b in blocks)==1 and host._terminal_form_pending
    else: assert '不要重复执行' in totals.text and any(b.get('status')=='error' for b in blocks)


async def test_full_tool_loop_uncertain_wraps_up_without_retry(setup,monkeypatch):
    executor=MockHandlerExecutor(agent_domain='general')
    executor.handler.side_effect=RuntimeError('external response lost')
    loop,ctx=loop_for(executor)
    loop._stream_one_turn=AsyncMock(return_value=({0:tc('generate_image',{'prompt':'fixture'})},'',5,3,2))
    synthesis=AsyncMock(return_value='请核验外部执行结果')
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up',synthesis)
    result=await loop.run([],[],[],ctx)
    loop._stream_one_turn.assert_awaited_once();executor.handler.assert_awaited_once();synthesis.assert_awaited_once()
    assert result.stop_reason=='wrap_up_failure' and result.failure_message=='external response lost'
    assert result.total_tokens==5
    assert result.tool_outcomes==[{'tool_name':'generate_image','status':'error'}]


async def test_steer_keeps_completed_artifacts_and_audits(setup,monkeypatch):
    from services.agent.loop_hooks import ToolAuditHook
    executor=MockHandlerExecutor(agent_domain='general')
    async def handle(name,args):
        return AgentResult(name,emit_payloads=[{'kind':'file','name':name+'.csv','url':'https://example.test/'+name+'.csv'}])
    executor.handler.side_effect=handle
    loop,ctx=loop_for(executor);ctx.task_id='task1';loop.hooks=[ToolAuditHook()]
    monkeypatch.setattr('services.websocket_manager.ws_manager.check_steer',lambda _: '用户打断')
    writes=[]
    async def audit(_db,entry): writes.append(entry)
    monkeypatch.setattr('services.agent.tool_audit.record_tool_audit',audit)
    await loop._execute_tools([tc('web_search',{'query':'x'},'a'),tc('search_knowledge',{'file':'overview'},'b')],[],'',ctx)
    await asyncio.sleep(0)
    assert executor.handler.await_count==2 and len(loop._emit_payloads)==2 and len(writes)==2
    assert {w.tool_call_id for w in writes}=={'a','b'}
    assert ctx.messages[-1]=={'role':'user','content':'用户打断'}
    assert '跳过此工具调用' in next(m['content'] for m in ctx.messages if m.get('tool_call_id')=='b')


@pytest.mark.parametrize('failure',['business','delivery'])
@pytest.mark.parametrize('write_version',[0,1])
async def test_actor_ledger_and_audit_written_once_without_business_redo(setup,monkeypatch,failure,write_version):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), 'tool_result_payload_write_version', write_version)
    from tests.test_tool_production_integration import InvocationStore,actor_harness
    from services.tool_invocation_store import serialize_tool_result
    store=InvocationStore();host=actor_harness(store)
    executor=MockHandlerExecutor(agent_domain='general',task_id='task1')
    raw=AgentResult('业务失败' if failure=='business' else '完成',status='error' if failure=='business' else 'success',
        error_message='failed' if failure=='business' else '',emit_payloads=[{'kind':'image','failed':True,'retry_context':{'prompt':'sample'}}])
    executor.handler.return_value=raw
    if failure=='delivery': host._execution_sink=SimpleNamespace(on_tool_result=AsyncMock(side_effect=ConnectionError('offline')))
    invoke=host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
    if failure=='delivery':
        with pytest.raises(ConnectionError): await invoke
    else:
        output=await invoke
        assert output[1].is_failure and output[2] is True
    executor.handler.assert_awaited_once();host._emit_tool_audit.assert_called_once()
    assert len(store.completed)==1 and store.completed[0]['status']=='succeeded'
    payload=store.completed[0]['result']
    if write_version == 0:
        assert payload==serialize_tool_result(raw)
    else:
        from services.tool_invocation_store import deserialize_tool_result
        assert payload['tool_result']['version']==1
        recovered=deserialize_tool_result(payload)
        assert recovered.summary==raw.summary and recovered.status==raw.status
        assert recovered.error_message==raw.error_message and recovered.emit_payloads==raw.emit_payloads
        assert recovered.metadata==raw.metadata and recovered.tokens_used==raw.tokens_used


async def test_current_actor_uncertain_never_reexecutes_or_counts_success(setup):
    from tests.test_tool_production_integration import InvocationStore,actor_harness
    from services.tool_invocation_store import hash_tool_arguments
    store=InvocationStore(row={'tool_name':'generate_image','args_hash':hash_tool_arguments({'prompt':'fixture'}),'status':'uncertain'})
    host=actor_harness(store);executor=MockHandlerExecutor(agent_domain='general',task_id='task1')
    output=await host._execute_single_tool(tc('generate_image',{'prompt':'fixture'}),executor,'task1','c1','m1','u1',1)
    result=output[1]
    assert result.execution.status=='uncertain' and not result.execution.handler_started and result.is_failure
    assert '不要重复执行' in host._tool_result_stop_reason
    assert host._emit_tool_audit.call_args.args[9]!='success'
    assert store.trace==['lookup'] and store.completed==[]
    executor.handler.assert_not_awaited()


@pytest.mark.parametrize('consumer',['chat','tool_loop'])
@pytest.mark.parametrize('media',['image','image_failure','video','video_failure'])
async def test_real_media_handler_with_existing_offline_provider_samples(setup,consumer,media,monkeypatch):
    """Reuse the provider result classes/URLs from test_media_tool_executor; no paid calls."""
    from tests.test_media_tool_executor import MockImageResult,MockVideoResult
    executor=MockHandlerExecutor(agent_domain='general')
    is_image=media.startswith('image');name='generate_image' if is_image else 'generate_video'
    executor._handlers[name]=getattr(executor,'_'+name)
    executor._lock_credits=Mock(return_value='offline-transaction')
    executor._confirm_deduct=Mock();executor._refund_credits=Mock()
    provider=AsyncMock()
    url='https://cdn.example.com/cat.png' if is_image else 'https://cdn.example.com/demo.mp4'
    if is_image: provider.generate.return_value=MockImageResult(image_urls=[] if 'failure' in media else [url],fail_msg='内容审核不通过')
    else: provider.generate.return_value=MockVideoResult(video_url=None if 'failure' in media else url,fail_msg='内容审核不通过')
    monkeypatch.setattr('services.adapters.factory.create_image_adapter' if is_image else 'services.adapters.factory.create_video_adapter',lambda *a,**kw:provider)
    monkeypatch.setattr('config.kie_models.calculate_image_cost' if is_image else 'config.kie_models.calculate_video_cost',lambda **kw:{'user_credits':18})
    monkeypatch.setattr('services.file_upload.download_url_to_workspace',AsyncMock(return_value=None))
    if consumer=='chat':
        host=ChatHarness(executor.db)
        result=(await host._execute_single_tool(tc(name,{'prompt':'a cute cat' if is_image else 'a sunset scene'}),executor,'task1','c1','m1','u1',1))[1]
    else:
        loop,ctx=loop_for(executor)
        await loop._execute_tools([tc(name,{'prompt':'a cute cat' if is_image else 'a sunset scene'})],[],'',ctx)
        result=loop._turn_tool_outcomes[0][1]
    assert isinstance(result,ToolResult) and result.kind=='agent'
    raw=result.raw
    assert result.model_content(consumer)==(raw.to_message_content() if consumer=='chat' else raw.to_tool_content())
    assert result.is_failure==('failure' in media)
    provider.generate.assert_awaited_once();provider.close.assert_awaited_once()
    assert provider.generate.call_args.kwargs['wait_for_result'] is True
    if 'failure' in media:
        executor._refund_credits.assert_called_once();executor._confirm_deduct.assert_not_called()
        assert result.error.retryable is True
    else:
        executor._confirm_deduct.assert_called_once();executor._refund_credits.assert_not_called()
        assert url in raw.summary
    if media=='image_failure':
        from services.handlers.emit_payloads import build_content_blocks_from_payloads
        block=build_content_blocks_from_payloads(result.collect_payloads(consumer))[0]
        assert block['failed'] is True and block['error']=='内容审核不通过'
        assert block['retry_context']==raw.emit_payloads[0]['retry_context']
        assert block['retry_context']['prompt']=='a cute cat'
    if media=='video': assert result.collect_payloads(consumer)==[]  # existing handler returns a summary URL
