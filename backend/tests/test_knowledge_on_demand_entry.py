"""Native RLS → real chat preparation/Gateway → WebSocket delivery ordering.

Only the provider and external WebSocket transport are synthetic. This checks
host ordering, not a live model's understanding or production wall-clock SLO.
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import psycopg
import pytest
from core.config import get_settings
from config.chat_tools import TOOL_SYSTEM_PROMPT
from schemas.message import TextPart,ImagePart
from services.adapters.types import StreamChunk
from services.handlers.context_snapshot import ContextAnchor
from services.handlers.chat.stream_runner import run_legacy_chat_stream
from services.prompt_builder.builder import PromptBuilder
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from services.knowledge_workspace.input_facts import KnowledgeInputFacts
from services.handlers.context_snapshot import ContextSnapshot
from services.knowledge_workspace.answer_policy import DeferredKnowledgeScope
from tests.test_knowledge_chat_v3 import v3_db,current_snapshot_db,local_db,start,document
from tests.test_chat_gateway_retry_integration import environment,make_adapter,web_request,websocket


@pytest.mark.asyncio
@pytest.mark.parametrize('replay', [False, 'structured', 'legacy', 'changed'])
@pytest.mark.parametrize('question,image,held,prior_evidence',[
    ('你好',False,False,False),('分析这张图片',True,False,False),
    ('帮我写一段风景文案',False,True,False),('你好',False,True,True),
    ('根据公司规则分析这张图片',True,True,False),
])
async def test_full_entry_starts_model_without_default_snapshot_and_obeys_release_policy(
        environment,current_snapshot_db,monkeypatch,question,image,held,prior_evidence,replay):
    make,_=current_snapshot_db;r=make();r.initialize();document(r)
    held = held or replay in ('legacy', 'changed')
    _,mid,cid=start(make,r,[])
    settings=get_settings().model_copy(update={'knowledge_workspace_enabled':True,
        'knowledge_default_scope_enabled':True,'knowledge_derivation_enabled':True,
        'knowledge_workspace_user_ids':'','knowledge_workspace_org_ids':''})
    monkeypatch.setattr('core.config.get_settings',lambda:settings)
    monkeypatch.setattr('services.knowledge_workspace.runtime.repository',lambda *_:r)
    monkeypatch.setattr('services.knowledge_workspace.answer_plan.collect',lambda *_:pytest.fail('automatic retrieval'))
    audit=AsyncMock(return_value={'requires_sources':False})
    monkeypatch.setattr('services.knowledge_workspace.grounding.request',audit)
    parts=[TextPart(text=question)]
    if image:
        parts.append(ImagePart(url='https://example.invalid/original.png'))
    h=environment.handler;h.org_id=r.scope.org_id
    rendered=UserLayer.render(UserMessageInput(text=question,
        workspace_files=[{'name':'original.png','path':'original.png',
                          'url':'https://example.invalid/original.png'}] if image else [],
        image_urls=[parts[-1].url] if image else []))
    messages=PromptBuilder._compose_messages(
        TOOL_SYSTEM_PROMPT,'权限和工具说明','时间',[],None,
        rendered)
    if prior_evidence:
        messages.insert(-1,{'role':'assistant','content':'此前读取的依据：[来源](/knowledge?object=original)'})
    async def build(*args,context_anchor,**kwargs):
        # Substitute only frozen history loading, not the actual renderer which
        # adds the production attachment locator to the same user message.
        snapshot=ContextSnapshot(context_anchor,messages[:-1],None,0,'')
        h._knowledge_input_facts=KnowledgeInputFacts.from_snapshot(snapshot,
            [part.model_dump(mode='json') for part in parts])
        return messages
    h._build_llm_messages=AsyncMock(side_effect=build)
    ready=asyncio.Event();finish=asyncio.Event();provider_done=asyncio.Event()
    async def stream(**kwargs):
        yield StreamChunk(content='第一段',prompt_tokens=3)
        ready.set()
        await finish.wait()
        yield StreamChunk(content='第二段',completion_tokens=2)
        provider_done.set()
    adapter=make_adapter();adapter.stream_chat=Mock(side_effect=stream)
    factory=environment.configure(adapter)
    request=replace(web_request(),content=parts,user_id=r.scope.actor_user_id,conversation_id=cid,
        context_anchor=ContextAnchor(task_id='task',conversation_id=cid,turn_id=str(uuid4()),
            input_message_id=mid,base_revision=0,through_message_id=None,org_id=r.scope.org_id))
    if replay:
        from services.knowledge_workspace.ai_journal import preference_message
        from services.knowledge_workspace.input_facts import CHECKPOINT_KEY
        frozen=KnowledgeInputFacts.from_snapshot(
            ContextSnapshot(request.context_anchor,messages[:-1],None,0,''),
            [part.model_dump(mode='json') for part in parts])
        from services.handlers.chat.execution_engine import ChatExecutionRequest
        checkpoint={'messages':[*messages,preference_message({'source':'/knowledge?object=preference'})]}
        if replay != 'legacy':
            checkpoint[CHECKPOINT_KEY]=frozen.checkpoint(changed=replay == 'changed')
        request=ChatExecutionRequest(**request.__dict__,replay_context=checkpoint)
    ws=websocket()
    if replay:
        from services.handlers.chat.execution_engine import execute_chat
        from services.handlers.chat.execution_sink import WebSocketExecutionSink
        sink=WebSocketExecutionSink(task_id=request.task_id,conversation_id=cid,
            message_id=request.message_id,user_id=request.user_id,model_id=request.model_id,
            websocket=ws,save_content=h._save_accumulated_content,save_blocks=h._save_accumulated_blocks)
        task=asyncio.create_task(execute_chat(handler=h,request=request,sink=sink))
    else:
        task=asyncio.create_task(run_legacy_chat_stream(handler=h,request=request,websocket=ws))
    try:
        await asyncio.wait_for(ready.wait(),5)
        assert not provider_done.is_set()
        assert h._knowledge_run is None
        assert isinstance(h._knowledge_answer_guard,DeferredKnowledgeScope) is held
        with psycopg.connect(**make.admin_args) as c:
            assert c.execute('SELECT count(*) FROM kb_read_snapshots WHERE input_message_id=%s',(mid,)).fetchone()[0]==0
        events=[call.args[2] for call in ws.send_to_task_or_user.await_args_list]
        visible=[e for e in events if e.get('type')=='message_chunk']
        assert bool(visible) is not held
        audit.assert_not_awaited()
    finally:
        finish.set()
        await asyncio.wait_for(task,5)
    h.on_error.assert_not_awaited()
    assert h.on_complete.await_count == (0 if replay else 1)
    assert factory.call_count==1
    assert audit.await_count==(1 if held else 0)
    events=[call.args[2] for call in ws.send_to_task_or_user.await_args_list]
    assert any(e.get('type')=='message_chunk' for e in events)
    # Settlement remains owned by the original Web lifecycle, once only.
    assert h._deduct_directly.call_count == (0 if replay else 1)
    assert h._build_llm_messages.await_count == (0 if replay else 1)
