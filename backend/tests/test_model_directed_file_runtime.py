"""Canonical file arguments are committed before the original replay/approval boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.conversation_state import ConversationPauseRequested
from tests.test_file_target_execution import fixture


@pytest.mark.parametrize('paused',[False,True])
async def test_original_file_resolution_is_bound_before_any_replay_or_handler(fixture,paused):
    executor,_,create=fixture
    target=create('report.csv')
    observed=[]
    async def bind(original,resolved,context,decision):
        observed.append((dict(original.arguments),dict(resolved.arguments)))
        assert target.exists()
        executor.tool_confirmer.assert_not_awaited()
        if paused:raise ConversationPauseRequested('test pause')
    lifecycle=SimpleNamespace(bind_resolved_arguments=bind,replay=AsyncMock(return_value=None),
        begin=AsyncMock(return_value=None),complete=AsyncMock(return_value=None))
    if paused:
        with pytest.raises(ConversationPauseRequested):
            await executor.tool_runtime.execute('file_delete',{'files':'report.csv'},call_id='original',lifecycle=lifecycle)
        lifecycle.replay.assert_not_awaited()
        executor.tool_confirmer.assert_not_awaited()
        executor._handlers['file_delete'].assert_not_awaited()
        assert target.exists()
    else:
        result=await executor.tool_runtime.execute('file_delete',{'files':'report.csv'},call_id='original',lifecycle=lifecycle)
        assert result.execution.handler_started and not target.exists()
        executor.tool_confirmer.assert_awaited_once()
        lifecycle.begin.assert_awaited_once()
    assert observed==[({'files':'report.csv'},{'files':(str(target),)})]
