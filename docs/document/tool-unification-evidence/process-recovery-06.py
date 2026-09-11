"""Isolated fresh-process recovery drill; never connects to production services."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from core.config import get_settings
from services.agent.agent_result import AgentResult
from services.agent.tool_output import FileRef, OutputFormat
from tests.test_tool_result_persistence_06 import LocalLedger
from tests.test_tool_production_integration import actor_harness, invoke, tc
from tests.tool_runtime_support import MockHandlerExecutor

phase, business_status, directory = sys.argv[1:]
root = Path(directory)
settings = get_settings()
assert settings.database_url.endswith('127.0.0.1:1/tool_test')
settings.file_workspace_root = str(root)
settings.file_workspace_enabled = True
settings.sandbox_enabled = True
row_file = root / (business_status + '.json')
artifact = root / 'org/o1/u1/result.csv'

async def main():
    with pytest.MonkeyPatch.context() as patch:
        if phase == 'write':
            assert settings.tool_result_payload_write_version == 1
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('value\n42\n')
            store = LocalLedger()
        else:
            assert settings.tool_result_payload_write_version == 0
            store = LocalLedger(row=json.loads(row_file.read_text()))
        host = actor_harness(store)
        executor = MockHandlerExecutor(agent_domain='general', task_id='task1')
        if phase == 'write':
            executor.handler.return_value = AgentResult(
                'isolated result', status=business_status,
                error_message='fixture failure' if business_status == 'error' else '',
                tokens_used=33, thinking_text='fixture thinking',
                metadata={'retryable':False, 'retry_context':{'prompt':'fixture', 'attempt':2}},
                format=OutputFormat.FILE_REF,
                file_ref=FileRef(str(artifact),'result.csv','csv',1,1,[]),
                emit_payloads=[{'kind':'file', 'workspace_path':str(artifact), 'filename':'result.csv'}],
            )
        else:
            executor.handler = AsyncMock(side_effect=AssertionError('Handler executed during recovery'))
        result, = await invoke('chat',executor,[tc('generate_image',{'prompt':'fixture'})],patch,host)
        assert result.status == business_status
        assert result.agent_context['tokens_used'] == 33
        assert result.agent_context['thinking_text'] == 'fixture thinking'
        assert result.metadata['retry_context']['attempt'] == 2
        assert result.raw.file_ref.path == str(artifact)
        assert artifact.read_text() == 'value\n42\n'
        assert result.raw.emit_payloads[0]['filename'] == 'result.csv'
        assert host._emit_tool_audit.call_count == 1
        audit = host._emit_tool_audit.call_args
        assert audit.args[9] == business_status
        if phase == 'write':
            assert executor.handler.await_count == 1
            assert len(store.completed) == 1 and store.row['status'] == 'succeeded'
            assert store.row['result']['tool_result']['version'] == 1
            row_file.write_text(json.dumps(store.row))
            assert result.chargeable_tokens == 33
        else:
            assert executor.handler.await_count == 0 and len(store.completed) == 0
            assert result.execution.replayed and result.execution.attempts == 0
            assert result.chargeable_tokens == 0
            assert audit.kwargs['execution']['replayed']
            assert audit.kwargs['execution']['chargeable_tokens'] == 0
            assert audit.kwargs['is_cached'] is False
        print(json.dumps({'phase':phase, 'business_status':business_status,
            'pid':os.getpid(), 'write_version':settings.tool_result_payload_write_version,
            'handler_calls':executor.handler.await_count, 'replayed':result.execution.replayed,
            'chargeable_tokens':result.chargeable_tokens, 'historical_tokens':33,
            'artifact_retry_thinking_preserved':True, 'audit_count':host._emit_tool_audit.call_count}))

asyncio.run(main())
