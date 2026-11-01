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
from services.knowledge_workspace.answer_policy import DeferredKnowledgeScope
from tests.test_knowledge_chat_v3 import v3_db,current_snapshot_db,local_db,start,document
from tests.test_chat_gateway_retry_integration import environment,make_adapter,web_request,websocket


@pytest.mark.asyncio
@pytest.mark.parametrize('question,image,held,prior_evidence',[
    ('你好',False,False,False),('分析这张图片',True,False,False),
    ('帮我写一段风景文案',False,True,False),('你好',False,True,True),
    ('根据公司规则分析这张图片',True,True,False),
])
async def test_full_entry_starts_model_without_default_snapshot_and_obeys_release_policy(
        environment,current_snapshot_db,monkeypatch,question,image,held,prior_evidence):
    make,_=current_snapshot_db;r=make();r.initialize();document(r)
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
    user={'role':'user','content':question}
    if image:
        parts.append(ImagePart(url='https://example.invalid/original.png'))
        user['content']=[{'type':'text','text':question},{'type':'image_url','image_url':{'url':parts[-1].url}}]
    h=environment.handler;h.org_id=r.scope.org_id
    messages=PromptBuilder._compose_messages(
        TOOL_SYSTEM_PROMPT,'权限和工具说明','时间',[],None,
        SimpleNamespace(workspace_system_block='',attachments_system_block='',user_message=user))
    if prior_evidence:
        messages.insert(-1,{'role':'assistant','content':'此前读取的依据：[来源](/knowledge?object=original)'})
    h._build_llm_messages=AsyncMock(return_value=messages)
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
    ws=websocket()
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
    h.on_error.assert_not_awaited();h.on_complete.assert_awaited_once()
    assert factory.call_count==1
    assert audit.await_count==(1 if held else 0)
    events=[call.args[2] for call in ws.send_to_task_or_user.await_args_list]
    assert any(e.get('type')=='message_chunk' for e in events)
    # Settlement remains owned by the original Web lifecycle, once only.
    h._deduct_directly.assert_called_once()
