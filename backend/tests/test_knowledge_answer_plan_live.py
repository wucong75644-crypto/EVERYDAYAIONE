"""Paired real-provider comparison on the exact sanitized production corpus."""
import asyncio,json,os,re,time
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import pytest
from dotenv import dotenv_values
from tests.test_knowledge_search_progress import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,start,ref)
from tests.knowledge_corpus import clone

pytestmark=pytest.mark.skipif(os.getenv('KB_LIVE_ACCEPTANCE')!='1',reason='explicit real-provider opt-in required')


def test_paired_fast_synthesis_quality_and_latency(derived_db,monkeypatch):
 from core.config import get_settings
 from services.knowledge_workspace.answer_plan import collect,eligible,message
 from services.knowledge_workspace.derivation import _client
 from services.tools.definitions.knowledge import build_specs
 from services.tools.spec import thaw
 from services.knowledge_workspace.model_view import present
 settings=get_settings();values={}
 for name in ['/Users/wucong/EVERYDAYAIONE/.env','/Users/wucong/EVERYDAYAIONE/backend/.env']:
  values.update({k:v for k,v in dotenv_values(name).items() if k in {'DASHSCOPE_API_KEY','DASHSCOPE_BASE_URL'} and v})
 assert values.get('DASHSCOPE_API_KEY')
 monkeypatch.setattr(settings,'dashscope_api_key',values['DASHSCOPE_API_KEY'])
 if values.get('DASHSCOPE_BASE_URL'):monkeypatch.setattr(settings,'dashscope_base_url',values['DASHSCOPE_BASE_URL'])
 monkeypatch.setattr(settings,'knowledge_derivation_enabled',True);monkeypatch.setattr(settings,'knowledge_semantic_enabled',True)
 from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
 serializer=DashScopeChatAdapter(api_key=values['DASHSCOPE_API_KEY'],model=settings.knowledge_derivation_model,builtin_tools_enabled=False)
 r=derived_db[0]();scopes=clone(r);results=[]
 questions={
 'normal':'请查询知识库：拼多多订单漏发要先核对什么？食品拆封能按此流程处理吗？请给来源。',
 'media':'请查询知识库：视频里要求核对什么？扫描资料验证码是什么？食品拆封能按此流程处理吗？请给来源。',
 'missing':'请查询当前知识库：橙鹿换货期限与照片要求是什么？蓝鲸预约需要提前多久？',
 'complex':'请详细对比全部文字、图片、扫描PDF和视频中的核对要求与例外，指出真正的差异并给来源。'}
 def visible(text):return re.sub(r'\[([^\]]+)\]\([^)]+\)',r'\1',text)
 async def ask(case,path,round_index):
  begun=time.monotonic();run,_,_=start(derived_db[0],r,[ref(scopes['missing' if case=='missing' else 'normal'])]);q=questions[case]
  history=[{'role':'user','content':'先前的合成规则是什么？'},{'role':'assistant','content':'此前的文件曾说橙鹿换货签收后30天，蓝鲸预约提前7天。'}] if case=='missing' else []
  trace=[];plan=None;retrieval_ms=0
  messages=[{'role':'system','content':json.dumps(run.introduction(),ensure_ascii=False)},*history,{'role':'user','content':q}]
  if path=='fast' and eligible(q):
   t=time.monotonic();plan=await asyncio.to_thread(collect,run,q);retrieval_ms=(time.monotonic()-t)*1000
   if plan['mode'] in ('answer','missing'):messages=[*history,{'role':'user','content':q},message(plan)]
   elif plan.get('items'):messages.append(message(plan))
  is_fast=bool(plan and plan['mode'] in ('answer','missing'));count=0;answer='';model_ms=0
  client=await _client.get()
  for _ in range(12):
   await asyncio.to_thread(run.revalidate)
   t=time.monotonic();params={'model':settings.knowledge_derivation_model,'messages':messages,'max_tokens':2000,'temperature':0.1}
   if not is_fast:params.update(tools=[thaw(s.schema) for s in build_specs()])
   params=serializer._request_body(messages,stream=False,reasoning_effort=None,thinking_mode=None,temperature=0.1,tools=params.get('tools'))
   response=await client.post('/chat/completions',json=params);response.raise_for_status();model_ms+=(time.monotonic()-t)*1000;count+=1
   msg=response.json()['choices'][0]['message'];messages.append(msg);calls=msg.get('tool_calls',[])
   if is_fast:assert not calls
   if not calls:answer=msg.get('content','');break
   for call in calls:
    name=call['function']['name'];args=json.loads(call['function']['arguments']);t=time.monotonic()
    value=await asyncio.to_thread(run.execute,name.removeprefix('knowledge_'),args);retrieval_ms+=(time.monotonic()-t)*1000
    trace.append({'tool':name,'args':args,'result':value});messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(present(value),ensure_ascii=False,default=str)})
  answer=await asyncio.to_thread(run.normalize_citations,answer);footer=await asyncio.to_thread(run.evidence_footer)
  known={tuple(sorted((k,tuple(v)) for k,v in parse_qs(urlparse(run._source(i)['url']).query).items())) for i in run.snapshot['items']}
  links=re.findall(r'\]\(([^)]+)\)',answer+footer)
  valid_links=all(urlparse(url).path=='/knowledge' and not urlparse(url).netloc and tuple(sorted((k,tuple(v)) for k,v in parse_qs(urlparse(url).query).items())) in known for url in links)
  quality={'answer_present':bool(answer),'concise':len(visible(answer))<=220,'sources_valid':valid_links,'no_unverified_links':'来源未核验' not in answer,'no_budget_stop':not any(t['result'].get('stop_reason')=='budget_exhausted' for t in trace)}
  if case=='missing':quality['missing_not_old']=any(x in answer for x in ['未找到','没有','缺少','未查到','无法','未提供']) and not re.search(r'(?:7|30)\s*天',answer)
  else:quality['conditions_and_exception']=all(x in answer for x in ['订单','照片','食品']) and any(x in answer for x in ['不适用','不能','不可以']) and bool(links)
  if case=='media':quality['media_fact']='SCAN-1025' in answer
  result={'case':case,'path':path,'round':round_index,'effective_fast':is_fast,'plan_reason':(plan or {}).get('reason'),'model_calls':count,'retrieval_ms':round(retrieval_ms,1),'generation_ms':round(model_ms,1),'elapsed_ms':round((time.monotonic()-begun)*1000,1),'visible_length':len(visible(answer)),'visible_total_length':len(visible(answer+footer)),'quality':quality,'answer':answer,'footer':footer,'trace':trace,'program_evidence':plan,'model':settings.knowledge_derivation_model,'request_serialization':'production_dashscope_adapter','enable_thinking':params['enable_thinking']}
  results.append(result);Path('.cloud-dev/knowledge-fast-paired.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
  if path=='fast':
   if case!='complex':assert is_fast and count==1,result
   else:assert not is_fast and any(x['tool']=='knowledge_read' for x in trace),result
   assert all(v for k,v in quality.items() if k!='concise' or case!='complex'),result
 async def scenario():
  try:
   # Alternate order instead of systematically giving the new path warm caches.
   for round_index in range(2):
    for case in ['normal','media','missing']:
     for path in (['agent','fast'] if round_index==0 else ['fast','agent']):await ask(case,path,round_index)
   await ask('complex','fast',0)
  finally:await _client.close()
 asyncio.run(scenario())
