"""No synthetic planner retry, no lost body, no false executable registration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.handlers.chat.execution_engine import _run_loop
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from tests.test_skill_runtime_actor import actor,prepared,handler
from tests.test_chat_execution_engine import _request


@pytest.mark.asyncio
@pytest.mark.parametrize('body,finish',[
    ('完整但未登记的模型草稿。','stop'),('需要确认商品是什么？','stop'),
    ('未写完的正文','length'),('模型拒绝说明','content_filter'),
])
async def test_missing_planner_receipt_preserves_output_without_guessing_or_retrying(monkeypatch,body,finish):
    runtime,p,h=actor(),prepared(),handler()
    p.ecom_workflow=SimpleNamespace(needs_planner=AsyncMock(return_value=True),context_message=lambda:None)
    totals=StreamTotals(last_finish_reason=finish)
    reader=AsyncMock(return_value=(body,'',[],set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',reader)
    blocks=[]
    await _run_loop(handler=h,request=_request(),prepared=p,cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(),totals=totals,blocks=blocks,runtime=runtime)
    reader.assert_awaited_once();h._execute_tool_calls.assert_not_awaited()
    assert body in blocks[-1]['text'] and '尚未登记' in blocks[-1]['text']
    assert blocks[-1]['content_incomplete'] is True
    assert not any(b.get('content_artifact_id') for b in blocks)
    assert '_planner_handoff_v1' not in runtime.checkpoint_base
