"""Opt-in real model/embedding test; synthetic data in a disposable loopback DB only."""
import asyncio,json,os,re
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import pytest
from dotenv import dotenv_values
from tests.test_knowledge_search_progress import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,start,ref)

pytestmark=pytest.mark.skipif(os.getenv('KB_LIVE_ACCEPTANCE')!='1',reason='explicit live-provider opt-in required')


@pytest.mark.parametrize('complex_media',[False,True])
def test_live_missing_normal_mixed_and_expand(derived_db,monkeypatch,complex_media):
 from core.config import get_settings
 from services.knowledge_workspace.derivation import process,_client
 from services.knowledge_workspace.chat import KnowledgeRun
 from services.tools.definitions.knowledge import build_specs
 from services.tools.spec import thaw
 from services.knowledge_workspace.model_view import present
 from core.exceptions import AppException
 settings=get_settings();values={}
 for name in ['/Users/wucong/EVERYDAYAIONE/.env','/Users/wucong/EVERYDAYAIONE/backend/.env']:
  values.update({k:v for k,v in dotenv_values(name).items() if k in {'DASHSCOPE_API_KEY','DASHSCOPE_BASE_URL'} and v})
 assert values.get('DASHSCOPE_API_KEY'),'Configure a development model key outside test output'
 monkeypatch.setattr(settings,'dashscope_api_key',values['DASHSCOPE_API_KEY'])
 if values.get('DASHSCOPE_BASE_URL'):monkeypatch.setattr(settings,'dashscope_base_url',values['DASHSCOPE_BASE_URL'])
 monkeypatch.setattr(settings,'knowledge_media_enabled',False)
 monkeypatch.setattr(settings,'knowledge_derivation_enabled',True);monkeypatch.setattr(settings,'knowledge_semantic_enabled',True)
 r=derived_db[0]();missing=create(r,title='合成空目录');chain=[missing]
 for title in ['一级目录','二级目录','空资料节点']:chain.append(create(r,missing['container_id'],chain[-1]['id'],title))
 for node in chain:r.command(command('save_document',{'object_id':node['id'],'expected_revision':1,'body':'人工正文 MANUAL-TEST：仅用于独立验收，必须保留。'}))
 normal=create(r,title='售后规则');parents=[];branch=normal
 if complex_media:
  for title in ['售后目录','拼多多漏发处理']:
   branch=create(r,normal['container_id'],branch['id'],title);parents.append(branch)
 empty=create(r,normal['container_id'],normal['id'],'未整理分类');leaf=create(r,normal['container_id'],branch['id'],'拼多多漏发补寄')
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':1,'body':'拼多多漏发处理：核对订单数量和客户照片后免费补寄。食品拆封不适用本流程，需人工另行确认。证据编号 VALID-1025。'}))
 duplicate=create(r,normal['container_id'],branch['id'],'漏发处理图片摘录')
 r.command(command('save_document',{'object_id':duplicate['id'],'expected_revision':1,'body':'拼多多漏发处理：核对订单数量和客户照片后免费补寄。食品拆封不适用，需人工另行确认。与文字版本内容相同。'}))
 media_nodes=[]
 if complex_media:
  from uuid import uuid4
  from hashlib import sha256
  for index,(name,mime) in enumerate([('procedure.txt','text/plain'),('scan.png','image/png'),('scan.pdf','application/pdf'),('video.mp4','video/mp4')]):
   node=[leaf,duplicate][index] if index<2 else create(r,normal['container_id'],branch['id'],'合成资料 '+name)
   media_nodes.append(node)
   r.command(command('save_document',{'object_id':node['id'],'expected_revision':2 if index<2 else 1,'body':'人工保留原文 MANUAL-TEST，不能覆盖。'}))
   text='仅合成测试。拼多多漏发：先核对订单数量和客户照片，再免费补寄。食品拆封不适用，需人工另行确认。'
   if index>0:text+=' 扫描资料验证码 SCAN-1025。'
   if index==3:text+=' [语音0–5秒]核对订单数量和客户照片。视频采样不代表每帧完整。'
   # Use already-extracted media transcripts: this acceptance tests retrieval,
   # not OCR/ASR decoding (covered by media extraction tests and prior acceptance).
   r.file_command(uuid4(),'file_upload',{'object_id':str(node['id']),'version_id':str(uuid4()),'name':name,'mime_type':mime,'size':len(text.encode()),'sha256':sha256(text.encode()).hexdigest(),'text_status':'truncated' if index>=2 else 'ready','extracted_text':text})
 conflict=create(r,title='换货规则冲突');rules=[]
 for title,days in [('橙鹿换货甲版',7),('橙鹿换货乙版',30)]:
  node=create(r,conflict['container_id'],conflict['id'],title);rules.append(node)
  r.command(command('save_document',{'object_id':node['id'],'expected_revision':1,'body':f'橙鹿换货：签收后{days}天内申请，须商品照片；未说明版本优先级。'}))
 async def summary(sources,title,path):
  sections=[{'title':'资料说明' if source['kind']=='manual' else '下级资料','text':source['text'],'source_refs':[source['key']]} for source in sources]
  if sources and not any('拼多多' in source['text'] for source in sources):
   sections.append({'title':'适用条件、步骤与缺失资料','text':'当前只有人工正文和备注。未提供具体业务适用条件、期限、处理步骤及例外，不能据此推定业务规则。','source_refs':[sources[0]['key']]})
  return {'sections':sections,'tags':['合成验收']}
 async def complex_summary(sources,title,path):
  # A frozen post-ingestion fixture shaped like the production failure: many
  # small sections, placeholders, provenance/conflict metadata and rollups.
  # Retrieval/answering and embeddings use real providers; ingestion is isolated.
  sections=[]
  for source in sources:
   key=source['key'];kind=source['kind'];text=source['text']
   values=[('人工保留原文' if kind=='manual' else '下级资料',text)]
   if kind=='file':
    values=[('适用条件','仅合成测试，拼多多订单漏发适用。'),('操作步骤','先核对订单数量和客户照片，再免费补寄。'),('例外','食品拆封不适用，需人工另行确认。'),('来源关系与冲突状态','人工保护仅表示存储不覆盖；没有业务优先级。冲突状态未判定，不能凭状态推断存在冲突。')]
    if 'SCAN-1025' in text:values.append(('扫描资料验证信息','扫描资料验证码 SCAN-1025；其他文件未注明此验证码，无法推定业务优先级。'))
    if '语音' in text:values.append(('采样范围','视频采样不代表每帧完整，语音记载核对订单数量与客户照片。'))
   for heading,body in values:sections.append({'title':source['title']+' · '+heading,'text':body,'source_refs':[key]})
  return {'sections':sections,'tags':['合成验收','售后','漏发']}
 evidence={};protected={str(n['id']):r.object(n['id'])['body'] for n in [leaf,duplicate,*media_nodes,*parents]}
 async def ask(case,root,question,warmup=False,history=None):
  run,_,_=start(derived_db[0],r,[ref(root)]);rh=run.introduction()['roots'][0]['handle']
  messages=[{'role':'system','content':json.dumps(run.introduction(),ensure_ascii=False)}]
  if history:messages.extend([{'role':'user','content':'先前的合成资料规则是什么？'},{'role':'assistant','content':history}])
  messages.append({'role':'user','content':question});trace=[]
  if warmup=='route':
   handle=next(i['handle'] for i in run.snapshot['items'] if i['type']=='object' and i['id']==str(branch['id']))
   for index,(action,args) in enumerate([('search',{'root_handle':rh,'mode':'route','query':'拼多多漏发'}),('read',{'root_handle':rh,'item_handles':[handle],'context':True})]):
    result=await asyncio.to_thread(run.execute,action,args);name='knowledge_'+action;call_id='route-warmup-'+str(index)
    messages.extend([{'role':'assistant','content':None,'tool_calls':[{'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},{'role':'tool','tool_call_id':call_id,'content':json.dumps(present(result),ensure_ascii=False,default=str)}])
    trace.append({'tool':name,'args':args,'stop_reason':result.get('stop_reason'),'result':result})
  elif warmup:
   handle=next(i['handle'] for i in run.snapshot['items'] if i['type']=='object' and i['id']==str(empty['id']))
   args={'root_handle':rh,'scope_handle':handle,'query':'漏发'};result=await asyncio.to_thread(run.execute,'search',args)
   assert not result['items'] and result['stop_reason']=='not_found_in_scope'
   messages.extend([{'role':'assistant','content':None,'tool_calls':[{'id':'narrow-search','type':'function','function':{'name':'knowledge_search','arguments':json.dumps(args)}}]}, {'role':'tool','tool_call_id':'narrow-search','content':json.dumps(present(result),ensure_ascii=False)}])
   trace.append({'tool':'knowledge_search','args':args,'stop_reason':result.get('stop_reason')})
  client=await _client.get();answer=''
  for _ in range(12):
   response=await client.post('/chat/completions',json={'model':settings.knowledge_derivation_model,'messages':messages,'tools':[thaw(x.schema) for x in build_specs()],'tool_choice':'auto','max_tokens':2000,'temperature':0.1})
   response.raise_for_status();message=response.json()['choices'][0]['message'];messages.append(message);calls=message.get('tool_calls',[])
   if not calls:answer=message.get('content','');break
   for call in calls:
    name=call['function']['name'];args=json.loads(call['function']['arguments']);assert name.startswith('knowledge_')
    try:result=await asyncio.to_thread(run.execute,name.removeprefix('knowledge_'),args)
    except AppException as error:result={'error':{'code':error.code,'message':error.message}}
    trace.append({'tool':name,'args':args,'stop_reason':result.get('stop_reason'),'items':len(result.get('items',[])),'result':result})
    messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(present(result),ensure_ascii=False,default=str)})
  answer=await asyncio.to_thread(run.normalize_citations,answer);footer=await asyncio.to_thread(run.evidence_footer)
  visible=lambda text:re.sub(r'/knowledge\?[^\s]+','[来源]',re.sub(r'\[([^\]]+)\]\([^)]+\)',r'\1',text))
  evidence[case]={'visible_length':len(visible(answer)),'visible_total_length':len(visible(answer+footer)),'answer':answer,'footer':footer,'trace':trace,'length':len(answer),'total_length':len(answer+footer),'model':settings.knowledge_derivation_model,'snapshot_items':len(run.snapshot['items']),'complex_media':complex_media}
  Path('.cloud-dev/knowledge-complex-live.json' if complex_media else '.cloud-dev/knowledge-missing-live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
  assert answer and not any(x in answer for x in ['knowledge_search','knowledge_read','not_found_in_scope','budget_exhausted'])
  assert not any(t.get('stop_reason')=='budget_exhausted' for t in trace)
  if case=='missing':
   assert len(trace)<=6,trace
   assert len(visible(answer))<=220 and len(visible(answer+footer))<=650,answer+footer
   assert any(x in answer for x in ['未找到','没有找到','没有','缺少','无法','未提供'])
   assert not re.search(r'(?:7|30)\s*天',answer)
  else:
   assert any(t['tool']=='knowledge_read' for t in trace),trace
   if case!='conflict':assert '核对' in answer and '照片' in answer and '食品' in answer
   assert '/knowledge?' in answer+footer
   assert len(trace)<=10,trace
   if case!='detailed':assert len(visible(answer))<=220,answer
   if case!='conflict':assert '免费' in answer and any(x in answer for x in ['人工','另行确认']),answer
   else:assert '7' in answer and '30' in answer and any(x in answer for x in ['冲突','不一致','不同','矛盾','两个版本','两种']) and any(x in answer for x in ['确认','无法确定','不能确定']),answer
   known={tuple(sorted((k,tuple(v)) for k,v in parse_qs(urlparse(run._source(i)['url']).query).items())) for i in run.snapshot['items']}
   for url in re.findall(r'\]\(([^)]+)\)',answer+footer):
    parsed=urlparse(url)
    if parsed.path!='/knowledge':continue
    assert not parsed.netloc and tuple(sorted((k,tuple(v)) for k,v in parse_qs(parsed.query).items())) in known
   if case=='detailed':assert all(x in answer for x in ['核对','订单','照片','免费','食品','人工']),answer
   if case=='mixed':assert any(x in answer for x in ['未找到','没有','缺少','未提供','无法']) and not re.search(r'30\s*天',answer)
  return run
 async def scenario():
  try:
   generator=complex_summary if complex_media else summary
   for node in reversed(chain):assert await process(r,node['id'],summary)
   for node in (media_nodes if complex_media else [leaf,duplicate]):assert await process(r,node['id'],generator)
   for node in reversed(parents):assert await process(r,node['id'],generator)
   assert await process(r,normal['id'],generator)
   for node in rules:assert await process(r,node['id'],summary)
   assert await process(r,conflict['id'],summary)
   # Real embeddings must still recall different wording without a lexical match.
   semantic,_,_=start(derived_db[0],r,[ref(normal)]);rh=semantic.introduction()['roots'][0]['handle']
   result=await asyncio.to_thread(semantic.execute,'search',{'root_handle':rh,'query':'客人说包裹里的数量不够，如何处理？'})
   assert any(i['type']=='generated' for i in result['items'])
   await ask('missing',missing,'请查询当前知识库：橙鹿换货期限与照片要求是什么？蓝鲸预约需要提前多久？',history='上一版合成文件说：橙鹿换货须照片，签收后30天；蓝鲸预约提前7天。')
   await ask('normal',normal,'请查知识库：拼多多漏发如何处理？食品拆封能用这个流程吗？给出来源。')
   if complex_media:await ask('routed',normal,'请查询知识库：拼多多订单漏发要先核对什么？食品拆封能按此流程处理吗？请给来源。',warmup='route')
   await ask('expand',normal,'请查知识库：拼多多漏发如何处理，食品拆封适用吗？给来源。',warmup=True)
   await ask('conflict',conflict,'橙鹿换货期限是多少？请查资料并给来源。')
   await ask('detailed',normal,'请详细说明拼多多漏发的核对步骤、费用、食品拆封的例外和下一步，并给出来源。')
   await ask('mixed',normal,'请查知识库分别回答：拼多多漏发怎么办，食品拆封适用吗？橙鹿换货期限是什么？有依据的给来源，缺失的明确说明。')
   assert all(r.object(oid)['body']==body for oid,body in protected.items())
  finally:await _client.close()
 asyncio.run(scenario())
