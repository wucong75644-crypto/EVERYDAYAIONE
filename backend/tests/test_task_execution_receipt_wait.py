"""A confirmation UI and original tool pairs are durable before task waiting."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.handlers.chat.execution_engine import _execute_tools,ChatExecutionRequest
from services.task_execution.pending_batch import pending_calls
from services.tools.spec import thaw
from tests.test_task_execution_model_bridge import setup_case
from tests.test_task_execution_scheduled import case,execute


@pytest.mark.asyncio
@pytest.mark.parametrize('save_fails',[False,True])
async def test_waiting_checkpoint_contains_original_form_and_complete_tool_pair(monkeypatch,save_fails):
    source,actor,context,call,service,io,store=case()
    result=await execute(source,actor,context,call,service)
    initial=source.task_execution_state;snapshots=[]
    async def checkpoint(point,payload):
        snapshots.append(copy.deepcopy(payload))
        if save_fails and payload['_task_execution']['status']=='waiting_approval':raise RuntimeError('waiting checkpoint unconfirmed')
        return {'outcome':'saved'}
    runtime,prepared,handler,sink=setup_case(checkpoint,initial=initial)
    runtime.task_operation_store=store
    prepared.budget=SimpleNamespace(stop_reason=None,turns_used=0,use_turn=lambda:None)
    prepared.permission=SimpleNamespace(mode=SimpleNamespace(value='auto'))
    prepared.execution_context=context
    prepared.tool_context=SimpleNamespace(discovered_tools=set(),update_from_batch=lambda _:None,build_context_prompt=lambda:None)
    handler.org_id=str(initial.org_id);handler._get_conv_source=lambda _:'web'
    handler._pending_emit_payloads=[];handler._pending_change_set_blocks=[];handler._pending_form_block=None
    sink.on_text=AsyncMock()
    native={'id':call.call_id,'name':call.name,'arguments':json.dumps(thaw(call.arguments))}
    async def reuse(*_,**__):
        handler._pending_form_block=result.raw.form
        handler._terminal_form_pending=True
        return [(native,result,False,'原确认表单')]
    handler._execute_tool_calls=AsyncMock(side_effect=reuse)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    request=ChatExecutionRequest(content=[],user_id=context.actor_user_id,conversation_id=context.conversation_id,
        task_id=context.task_id,message_id='output',model_id='local-offline',context_anchor=None)
    kwargs=dict(handler=handler,request=request,prepared=prepared,turn=0,turn_text='',calls=[native],previewed_call_ids=set(),
        cancellation_event=runtime.cancellation_event,sink=sink,blocks=[],runtime=runtime,next_model_round=1)
    if save_fails:
        with pytest.raises(RuntimeError,match='waiting checkpoint unconfirmed'):await _execute_tools(**kwargs)
        assert runtime.task_execution_state==initial
    else:
        await _execute_tools(**kwargs)
        assert runtime.task_execution_state.status=='waiting_approval'
    assert snapshots and all(value['_task_execution']['status']=='active' for value in snapshots[:-1])
    saved=snapshots[-1]
    assert not pending_calls(saved['messages'])[0]
    assert saved['_task_execution']['status']=='waiting_approval'
    assert saved['content_blocks'][-1]['form_id']==result.raw.form['form_id']
    assert saved['messages'][0]=={'role':'user','content':initial.original_goal}
    io.assert_awaited_once()
