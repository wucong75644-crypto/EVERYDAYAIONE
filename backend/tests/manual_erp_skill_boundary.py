"""Opt-in real ERP model -> PostgreSQL -> chat model -> kernel regression.

Only synthetic orders in a fixture-owned socket database; no production writes.
HTTP/Actor, media, delivery and recovery are covered by manual_skill_formal_entry.
"""
import json
import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from dotenv import dotenv_values

from tests.test_erp_scope_trend_postgres import trend_db, isolated_db, classifier, ORG
from core.config import get_settings
from services.agent.erp_agent import ERPAgent
from services.handlers.chat.content_tools import model_tools
from services.handlers.chat.tool_argument_repairs import display_rejection
from services.model_gateway import ModelCallRequest, ModelGateway
from services.sandbox.executor import SandboxExecutor
from services.sandbox.kernel_manager import KernelManager
from services.tools.catalog import build_tool_catalog
from services.tools.model_contracts import offered_argument_error, project_model_tool
from utils.time_context import RequestContext, TimePoint

pytestmark = pytest.mark.skipif(os.environ.get('RUN_ERP_SKILL_BOUNDARY_REAL') != '1', reason='Explicit paid real-model regression')
RESULTS = []


@pytest.mark.asyncio
@pytest.mark.parametrize('repeat', range(1, 4))
async def test_real_erp_to_chart_and_followup(trend_db, classifier, monkeypatch, tmp_path, repeat):
    import services.model_gateway as gateway_module

    settings = get_settings()
    secrets = dotenv_values(os.environ['SKILL_SUPPLIER_SETTINGS_FILE'])
    monkeypatch.setattr(settings, 'dashscope_api_key', secrets['DASHSCOPE_API_KEY'])
    gateway = ModelGateway()
    # Sampling/experience persistence is test-local; provider calls and domain work are real.
    class IsolatedSampling:
        def open_chat(self, request):
            return gateway.open_chat(replace(request, db=None, org_id=None, task_id=None))
    monkeypatch.setattr(gateway_module, 'get_model_gateway', lambda: IsolatedSampling())
    now = datetime.fromisoformat('2026-10-18T14:00:00+08:00')
    context = RequestContext(now=now, today=TimePoint.from_datetime(now, reference=now), user_id='fixture-user', org_id=ORG)
    agent = ERPAgent(trend_db, 'fixture-user', 'fixture-chat', ORG, request_ctx=context)
    agent._push_thinking = AsyncMock()
    agent._experience.record = AsyncMock()
    registry = build_tool_catalog()
    tools = [project_model_tool(t) for t in model_tools([
        registry.require(name).to_schema() for name in ('erp_agent', 'code_execute')])]
    offers = {t['function']['name']: t['function']['parameters'] for t in tools}
    assert 'content_source' not in offers['code_execute']['properties']
    manager = KernelManager()
    await manager.start()
    for name in ('workspace', 'staging', 'output'):
        (tmp_path/name).mkdir()
    sandbox = SandboxExecutor(kernel_manager=manager, conversation_id=f'erp-{repeat}',
        workspace_dir=str(tmp_path/'workspace'), staging_dir=str(tmp_path/'staging'), output_dir=str(tmp_path/'output'))
    session = gateway.open_chat(ModelCallRequest(model_id='qwen3.8-max', timeout=180))
    messages = [{'role':'system','content':'当前时间：2026年10月18日14:00，周日。你是经营分析助手，根据真实数据回答用户。'}]
    rows = RESULTS
    report = Path(os.environ['ERP_BOUNDARY_REPORT'])
    def persist():
        report.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        report.chmod(0o600)
    try:
        for request, scope, amount in [
            ('看看淘宝这周每天的销售变化，按付款时间。', 'valid', 170),
            ('这次只排除刷单，关闭订单也留着。', 'exclude_scalping', 200),
        ]:
            row = {'repeat':repeat, 'request':request, 'calls':[], 'started':time.monotonic()}
            rows.append(row); persist()
            extract = agent._llm_extract
            async def traced_extract(query):
                try:
                    return await extract(query)
                except Exception as error:
                    row.setdefault('extraction_errors', []).append({'type':type(error).__name__, 'message':str(error)})
                    persist()
                    raise
            agent._llm_extract = traced_extract
            messages.append({'role':'user','content':request})
            charts, erp_results = [], []
            for turn in range(8):
                calls, text = {}, ''
                async for chunk in session.stream_chat(messages=messages, tools=tools, thinking_mode='disabled'):
                    text += chunk.content or ''
                    for delta in chunk.tool_calls or ():
                        call = calls.setdefault(delta.index, {'id':'', 'name':'', 'arguments':''})
                        if delta.id: call['id'] = delta.id
                        if delta.name: call['name'] = delta.name
                        call['arguments'] += delta.arguments_delta or ''
                assistant = {'role':'assistant','content':text}
                if calls:
                    assistant['tool_calls'] = [{'id':v['id'],'type':'function','function':{
                        'name':v['name'],'arguments':v['arguments']}} for v in calls.values()]
                messages.append(assistant)
                if not calls:
                    row['final'] = text
                    break
                for call in calls.values():
                    args = json.loads(call['arguments'])
                    evidence = {'name':call['name'], 'arguments':args}
                    row['calls'].append(evidence)
                    error = offered_argument_error(call['name'], args, offers[call['name']])
                    if error:
                        result_text = error
                        evidence.update(ok=False, output=display_rejection(error))
                    elif call['name'] == 'erp_agent':
                        out = await agent.execute(args.get('task') or args['query'], args.get('conversation_context', ''))
                        evidence.update(ok=not out.is_failure, output=out.summary, metadata=out.metadata, data=out.data)
                        result_text = out.to_tool_content()
                        erp_results.append(out)
                    else:
                        out = await sandbox.execute(args['code'], args['description'])
                        evidence.update(ok=not out.is_failure, output=out.summary, payloads=out.emit_payloads)
                        result_text = out.to_tool_content()
                        charts.extend(p for p in out.emit_payloads if p['kind']=='chart')
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':result_text})
                    persist()
            else:
                raise AssertionError('Real model did not finish within the existing tool-round budget')
            row['seconds'] = time.monotonic()-row['started']
            assert erp_results and all(not r.is_failure for r in erp_results)
            result = erp_results[-1]
            assert result.metadata['order_scope'] == scope
            assert result.metadata['time_column'] == 'pay_time'
            assert sum(r['order_amount'] for r in result.data) == amount
            filters = json.loads(trend_db.calls[-1][1]['p_filters'])
            assert any(f['field']=='platform' and f['value']=='tb' for f in filters)
            assert charts and row['final'].strip()
            assert any(sum(series.get('data', [])) == amount for chart in charts
                       for series in chart['option']['series'] if all(isinstance(v,(int,float)) for v in series.get('data',[])))
            assert all(c['ok'] for c in row['calls']), row['calls']
            row['ok'] = True
            persist()
            agent._llm_extract = extract
    finally:
        persist()
        await session.close()
        await gateway.close()
        await manager.shutdown()
