"""Opt-in real model/embedding test; synthetic data in a disposable loopback DB only."""
import asyncio,json,os,re
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import pytest
from dotenv import dotenv_values
from tests.test_knowledge_search_progress import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,start,ref)

pytestmark=pytest.mark.skipif(os.getenv('KB_LIVE_ACCEPTANCE')!='1',reason='explicit live-provider opt-in required')


def test_live_missing_normal_mixed_and_expand(derived_db,monkeypatch):
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
 monkeypatch.setattr(settings,'knowledge_derivation_enabled',True);monkeypatch.setattr(settings,'knowledge_semantic_enabled',True)
 r=derived_db[0]();missing=create(r,title='合成空目录');chain=[missing]
 for title in ['一级目录','二级目录','空资料节点']:chain.append(create(r,missing['container_id'],chain[-1]['id'],title))
 for node in chain:r.command(command('save_document',{'object_id':node['id'],'expected_revision':1,'body':'人工正文 MANUAL-TEST：仅用于独立验收，必须保留。'}))
 normal=create(r,title='售后规则');empty=create(r,normal['container_id'],normal['id'],'未整理分类');leaf=create(r,normal['container_id'],normal['id'],'拼多多漏发补寄')
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':1,'body':'拼多多漏发处理：核对订单数量和客户照片后免费补寄。食品拆封不适用本流程，需人工另行确认。证据编号 VALID-1025。'}))
 async def summary(sources,title,path):
  sections=[{'title':'资料说明' if source['kind']=='manual' else '下级资料','text':source['text'],'source_refs':[source['key']]} for source in sources]
  if sources and not any('拼多多' in source['text'] for source in sources):
   sections.append({'title':'适用条件、步骤与缺失资料','text':'当前只有人工正文和备注。未提供具体业务适用条件、期限、处理步骤及例外，不能据此推定业务规则。','source_refs':[sources[0]['key']]})
  return {'sections':sections,'tags':['合成验收']}
 evidence={}
 async def ask(case,root,question,warmup=False,history=None):
  run,_,_=start(derived_db[0],r,[ref(root)]);rh=run.introduction()['roots'][0]['handle']
  messages=[{'role':'system','content':json.dumps(run.introduction(),ensure_ascii=False)}]
  if history:messages.extend([{'role':'user','content':'先前的合成资料规则是什么？'},{'role':'assistant','content':history}])
  messages.append({'role':'user','content':question});trace=[]
  if warmup:
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
    trace.append({'tool':name,'args':args,'stop_reason':result.get('stop_reason'),'items':len(result.get('items',[]))})
    messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(present(result),ensure_ascii=False,default=str)})
  answer=await asyncio.to_thread(run.normalize_citations,answer);footer=await asyncio.to_thread(run.evidence_footer)
  visible=lambda text:re.sub(r'/knowledge\?[^\s]+','[来源]',re.sub(r'\[([^\]]+)\]\([^)]+\)',r'\1',text))
  evidence[case]={'visible_length':len(visible(answer)),'visible_total_length':len(visible(answer+footer)),'answer':answer,'footer':footer,'trace':trace,'length':len(answer),'total_length':len(answer+footer)}
  Path('.cloud-dev/knowledge-missing-live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
  assert answer and not any(x in answer for x in ['knowledge_search','knowledge_read','not_found_in_scope','budget_exhausted'])
  assert not any(t.get('stop_reason')=='budget_exhausted' for t in trace)
  if case=='missing':
   assert len(trace)<=6,trace
   assert len(visible(answer))<=220 and len(visible(answer+footer))<=650,answer+footer
   assert any(x in answer for x in ['未找到','没有找到','没有','缺少','无法','未提供'])
   assert not re.search(r'(?:7|30)\s*天',answer)
  else:
   assert '核对' in answer and '照片' in answer and '食品' in answer
   assert '/knowledge?' in answer+footer
   assert len(trace)<=10,trace
   known={tuple(sorted((k,tuple(v)) for k,v in parse_qs(urlparse(run._source(i)['url']).query).items())) for i in run.snapshot['items']}
   for url in re.findall(r'\]\(([^)]+)\)',answer+footer):
    parsed=urlparse(url)
    if parsed.path!='/knowledge':continue
    assert not parsed.netloc and tuple(sorted((k,tuple(v)) for k,v in parse_qs(parsed.query).items())) in known
   if case=='mixed':assert any(x in answer for x in ['未找到','没有','缺少','未提供','无法']) and not re.search(r'30\s*天',answer)
  return run
 async def scenario():
  try:
   for node in reversed(chain):assert await process(r,node['id'],summary)
   assert await process(r,leaf['id'],summary);assert await process(r,normal['id'],summary)
   # Real embeddings must still recall different wording without a lexical match.
   semantic,_,_=start(derived_db[0],r,[ref(normal)]);rh=semantic.introduction()['roots'][0]['handle']
   result=await asyncio.to_thread(semantic.execute,'search',{'root_handle':rh,'query':'客人说包裹里的数量不够，如何处理？'})
   assert any(i['type']=='generated' for i in result['items'])
   await ask('missing',missing,'请查询当前知识库：橙鹿换货期限与照片要求是什么？蓝鲸预约需要提前多久？',history='上一版合成文件说：橙鹿换货须照片，签收后30天；蓝鲸预约提前7天。')
   await ask('normal',normal,'请查知识库：拼多多漏发如何处理？食品拆封能用这个流程吗？给出来源。')
   await ask('expand',normal,'请查知识库：拼多多漏发如何处理，食品拆封适用吗？给来源。',warmup=True)
   await ask('mixed',normal,'请查知识库分别回答：拼多多漏发怎么办，食品拆封适用吗？橙鹿换货期限是什么？有依据的给来源，缺失的明确说明。')
  finally:await _client.close()
 asyncio.run(scenario())
