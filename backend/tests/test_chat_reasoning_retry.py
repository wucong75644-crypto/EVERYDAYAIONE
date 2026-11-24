"""The empty-body continuation must retain the user's explicit off switch."""
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest


@pytest.mark.asyncio
async def test_empty_reply_after_tool_turn_never_reenables_thinking(monkeypatch):
    from schemas.message import TextPart
    from services.adapters.types import StreamChunk
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from tests.test_skill_runtime_actor import actor,prepared,handler
    from tests.test_chat_execution_engine import _request
    runtime,p,h=actor(),prepared(),handler()
    q=replace(_request(),thinking_mode='disabled',content=[TextPart(text='请总结刚才的结果。')])
    p.messages=[{'role':'user','content':q.content[0].text}];p.stream_kwargs={}
    p.budget.use_turn();seen=[]
    async def stream(**kwargs):
        seen.append(kwargs['thinking_mode'])
        if len(seen)>1:yield StreamChunk(content='结果已核实。')
        yield StreamChunk(finish_reason='stop')
    p.adapter.stream_chat=stream
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    blocks=[]
    await _run_loop(handler=h,request=q,prepared=p,cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(),totals=StreamTotals(),blocks=blocks,runtime=None)
    assert seen==['disabled','disabled']
    assert blocks[-1]['text']=='结果已核实。'
