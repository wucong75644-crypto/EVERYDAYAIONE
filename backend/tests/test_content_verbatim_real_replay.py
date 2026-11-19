"""Replay captured final requests as indivisible text; never calls any provider."""
import base64,hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from services.task_execution import content_objects as co
from services.tools.catalog import build_capability_catalog
from services.file_executor import FileExecutor
from services.task_execution.image_resources import create_resolver
from services.handlers.chat_image_request import freeze_image_request,verify_frozen_request
from services.adapters.kie.image_adapter import KieImageAdapter
from services.agent.agent_result import AgentResult
from tests.test_model_directed_loop import case,execute
from tests.test_task_execution_model_bridge import call
from tests.test_content_objects import ref

@pytest.mark.asyncio
async def test_real_five_final_originals_single_all_and_revision_are_verbatim(monkeypatch,mock_db,tmp_path):
    folder=os.getenv('CONTENT_VERBATIM_RECORDS')
    if not folder:pytest.skip('Private final request captures and original image required')
    root=Path(folder);saved=json.loads((root/'verification.json').read_text())
    assert len(saved['local_requests'])==5
    originals=json.loads(Path(os.environ['REAL_INPUT_RETEST_DIR']).joinpath('live-originals.json').read_text())
    raw=base64.b64decode(originals['originals'][0]['raw_b64'])
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));monkeypatch.setattr(get_settings(),'chat_image_async_enabled',True)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    r,p,h,q=case();p.content_controls_available=True;ctx=p.execution_context
    files=FileExecutor(str(tmp_path),ctx.actor_user_id,None);files.resolve_safe_path('original.png').write_bytes(raw)
    source_id=str(uuid4());current_id=str(uuid4())
    mock_db.set_table_data('messages',[{'id':source_id,'role':'user','status':'completed','conversation_id':r.conversation_id,'org_id':None,'context_revision':1,'content':[{'type':'text','text':'写方案，保留原图参考'}, {'type':'image','workspace_path':'original.png','url':'https://local.invalid/original.png'}]},
        {'id':current_id,'role':'user','status':'completed','conversation_id':r.conversation_id,'org_id':None,'context_revision':None,'content':[{'type':'text','text':'用第二条执行'}]}])
    mock_db.set_table_data('conversations',[{'id':r.conversation_id,'user_id':ctx.actor_user_id,'org_id':None,'source':'web'}])
    mock_db.set_table_data('tasks',[{'id':r.task_id,'user_id':ctx.actor_user_id,'org_id':None,'conversation_id':r.conversation_id,'input_message_id':current_id,'base_context_revision':1,'request_params':{}}])
    h.db=mock_db;h._actor_runtime=r;r.task_image_resource_resolver=create_resolver(h,q,ctx)
    r.task_operation_store=SimpleNamespace(registry=build_capability_catalog())
    # Captured final supplier strings are the original inputs here. No production
    # parser, positive/negative extraction or handcrafted semantic mapping.
    texts=[entry['payload']['prompt'] for entry in saved['local_requests']]
    doc={'title':'真实五条完整原文','notes':'过程说明不参与执行','items':[
        {'title':f'条目{i}','body':text,'materials':[{'reference':{'message_id':source_id,'content_index':1},'role':'原产品参考','usage':'input'}],
        'options':{'aspect_ratio':'1:1','resolution':'1K','output_format':'png'}} for i,text in enumerate(texts,1)]}
    await execute(r,p,h,q,[call(co.SAVE,{'document':doc},'register-real')]);row=co.catalog(r.task_execution_state)[0]
    observed=[]
    async def submit(calls,*args,**kwargs):
        results=[]
        for c in calls:
            value=json.loads(c['arguments']);index=int(value['plan_item_id'].rsplit(':',1)[1])
            recipes=[json.loads(a.inline_text) for a in r.task_execution_state.artifacts if 'image.direct.recipe' in a.validations]
            recipe=next(v for v in recipes if v['normalized']['prompt']==value['prompt'])
            assert recipe['resolved'][0]['content_sha256']==hashlib.sha256(raw).hexdigest()
            frozen=freeze_image_request(recipe['normalized'],recipe['resolved'],origin={},max_requests=1,max_credits=300);verify_frozen_request(frozen)
            payload=KieImageAdapter(client=None,model=frozen['model'])._build_input_params(frozen['prompt'],['https://local.invalid/original.png'],'1:1','png','1K')
            assert value['prompt'].encode()==texts[index-1].encode()==frozen['prompt'].encode()==payload['prompt'].encode()
            assert frozen['prompt_sha256']==hashlib.sha256(texts[index-1].encode()).hexdigest()
            observed.append(index);results.append((c,AgentResult('本地序列化；未生图'),False,'本地序列化'))
        return results
    h._execute_tool_calls.side_effect=submit
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,2)},'single')]);assert observed==[2]
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,i)},f'all-{i}') for i in range(1,6)])
    assert observed==[2,1,2,3,4,5]
    # Changing one complete original replaces it; old strings are never appended.
    texts[1]='  用户修订完整原文\r\n禁止项：不可变形。\n保留：原结构。  '
    await execute(r,p,h,q,[call(co.SAVE,{'base':ref(row,2),'updates':[{'item_index':2,'item':{'body':texts[1]}}]},'edit-real')])
    row=co.catalog(r.task_execution_state)[0]
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,2)},'revised-single')]);assert observed[-1]==2

@pytest.mark.asyncio
async def test_six_original_execution_sections_are_copied_without_runtime_splitting():
    path=os.getenv('CHAT_CONTENT_REAL_REPLAY')
    if not path:pytest.skip('Private original six-plan conversation required')
    data=json.loads(Path(path).read_text())
    row=next(m for m in data['messages'] if m['id']=='4851cbe7-9390-44b7-8b3e-269c5133afcf')
    parts=json.loads(row['content']) if isinstance(row['content'],str) else row['content']
    original=next(p['text'] for p in parts if p.get('type')=='text')
    # Fixture-only literal boundaries of this recorded sample. Production has
    # no heading parser and never extracts/reassembles positive/negative text.
    import re
    boundaries=list(re.finditer(r'^# 方案[1-6]｜',original,re.M));assert len(boundaries)==6
    texts=[]
    for i,match in enumerate(boundaries):
        block=original[match.start():boundaries[i+1].start() if i<5 else len(original)]
        start=block.index('## 完整生图提示词\n')+len('## 完整生图提示词\n')
        end=block.rfind('\n---\n')
        texts.append(block[start:end if end>block.index('## 负面提示词') else len(block)])
    r,p,h,q=case();p.content_controls_available=True
    doc={'title':'六条完整原文','notes':'参考图分析不属于执行原文','items':[
        {'title':f'条目{i}','body':text,'materials':[],'options':{'aspect_ratio':'1:1','resolution':'1K'}} for i,text in enumerate(texts,1)]}
    await execute(r,p,h,q,[call(co.SAVE,{'document':doc},'six-recorded-texts')]);row=co.catalog(r.task_execution_state)[0]
    for index in (2,1,2,3,4,5,6):
        value,_=co.compile_input(r.task_execution_state,'generate_image',{},ref(row,index))
        assert value['prompt'].encode()==texts[index-1].encode()
        assert '## 负面提示词' in value['prompt'] and '# 参考图判断' not in value['prompt']
        assert hashlib.sha256(value['prompt'].encode()).hexdigest()==row['items'][index-1]['body_sha256']
    h._execute_tool_calls.assert_not_awaited()
