"""Opt-in live-provider acceptance with synthetic data and a disposable loopback DB."""
import asyncio
import json
import os
import re
from urllib.parse import urlparse,parse_qs
from pathlib import Path
import subprocess
from uuid import uuid4
import pytest
from dotenv import dotenv_values
from tests.test_knowledge_derivation import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,upload,document)

pytestmark=pytest.mark.skipif(os.getenv('KB_LIVE_ACCEPTANCE')!='1',reason='Explicit live-provider opt-in required')


def test_live_upload_rollup_query_and_revoke(derived_db,tmp_path,monkeypatch):
 from core.config import get_settings
 from services.knowledge_workspace.derivation import process,_client
 from services.knowledge_workspace.files import private_path,inspect_file
 from PIL import Image,ImageDraw,ImageFont
 settings=get_settings()
 values={}
 for file in ['/Users/wucong/EVERYDAYAIONE/.env','/Users/wucong/EVERYDAYAIONE/backend/.env']:
  values.update({k:v for k,v in dotenv_values(file).items() if k in {'DASHSCOPE_API_KEY','DASHSCOPE_BASE_URL'} and v})
 assert values.get('DASHSCOPE_API_KEY'),'A development model key must be configured outside the test output'
 monkeypatch.setattr(settings,'dashscope_api_key',values['DASHSCOPE_API_KEY'])
 if values.get('DASHSCOPE_BASE_URL'):monkeypatch.setattr(settings,'dashscope_base_url',values['DASHSCOPE_BASE_URL'])
 monkeypatch.setattr(settings,'knowledge_derivation_enabled',True)
 monkeypatch.setattr(settings,'file_workspace_root',str(tmp_path/'storage'))
 r=derived_db[0]();root=create(r,title='合成资料验收');platform=create(r,root['container_id'],root['id'],'拼多多售后');leaf=create(r,root['container_id'],platform['id'],'漏发处理')
 r.command(command('save_document',{'object_id':leaf['id'],'expected_revision':1,'body':'人工记录：仅适用于测试订单，不得覆盖此句。'}))
 text='拼多多漏发处理：先核对订单数量和客户照片，确认漏发后免费补寄。食品拆封不能按此流程处理。'
 upload(r,leaf,text)
 image=Image.new('RGB',(1100,260),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('/System/Library/Fonts/STHeiti Light.ttc',32)
 draw.text((25,30),'测试图片：补寄需核对订单与照片。',fill='black',font=font)
 draw.text((25,95),'食品拆封不适用此流程。',fill='black',font=font)
 image_path=tmp_path/'合成图片.png';image.save(image_path)
 speech=tmp_path/'speech.aiff';subprocess.run(['say','-v','Tingting','-o',str(speech),'拼多多漏发，需要核对订单和照片后补寄。食品拆封不适用此流程。'],check=True,timeout=40)
 video=tmp_path/'合成视频.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(image_path),'-i',str(speech),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(video)],check=True,timeout=40)
 def bind(path):
  data=path.read_bytes();meta=inspect_file(path,path.name);version=str(uuid4())
  result=r.file_command(uuid4(),'file_upload',{'object_id':str(leaf['id']),'version_id':version,**meta})
  target=private_path(settings.file_workspace_root,r.scope.org_id,r.scope.actor_user_id,version,create=True);target.write_bytes(data)
  return result
 image_binding=bind(image_path);video_binding=bind(video)
 before=r.object(leaf['id']);history=r.canvas_history();report={}
 async def scenario():
  try:
   for node in [leaf,platform,root]:assert await process(r,node['id'])
   material=document(r,leaf['id']);rollup=document(r,root['id']);content='\n'.join(s['text'] for s in material['sections'])
   assert '核对' in content and '食品' in content
   assert r.object(leaf['id'])==before and r.canvas_history()==history
   assert material['tags'] and rollup['sections']
   from tests.test_knowledge_chat_v3 import start,ref
   run,_,_=start(derived_db[0],r,[ref(root)]);handle=run.introduction()['roots'][0]['handle']
   # Different wording must find the order/photo requirement through hybrid recall.
   found=await asyncio.to_thread(run.execute,'search',{'root_handle':handle,'query':'客人说包裹里的数量不够，如何处理？'})
   generated=[i for i in found['items'] if i['type']=='generated'];assert generated
   read=await asyncio.to_thread(run.execute,'read',{'root_handle':handle,'item_handles':[generated[0]['handle']],'context':True})
   assert read['items'][0]['source']['url'].startswith('/knowledge?')
   client=await _client.get()
   from services.tools.definitions.knowledge import build_specs
   from services.tools.spec import thaw
   tools=[thaw(spec.schema) for spec in build_specs()]
   messages=[{'role':'system','content':json.dumps(run.introduction(),ensure_ascii=False)},
             {'role':'user','content':'拼多多订单漏发怎么办，食品拆封能按此处理吗？请查阅知识后回答并给出来源链接。'}]
   trace=[];answer=''
   for _ in range(12):
    response=await client.post('/chat/completions',json={'model':settings.knowledge_derivation_model,'messages':messages,'tools':tools,'tool_choice':'auto','max_tokens':3000})
    response.raise_for_status();message=response.json()['choices'][0]['message'];messages.append(message)
    calls=message.get('tool_calls',[])
    if not calls:
     answer=message.get('content','');break
    for call in calls:
     name=call['function']['name'];args=json.loads(call['function']['arguments'])
     assert name.startswith('knowledge_')
     from core.exceptions import AppException
     try:result=await asyncio.to_thread(run.execute,name.removeprefix('knowledge_'),args)
     except AppException as error:result={'error':{'code':error.code,'message':error.message}}
     trace.append({'tool':name,'args':args,'stop_reason':result.get('stop_reason')})
     from services.knowledge_workspace.model_view import present
     messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(present(result),ensure_ascii=False)})
   assert any(step['tool']=='knowledge_read' for step in trace)
   answer=await asyncio.to_thread(run.normalize_citations,answer)
   assert '食品' in answer and '核对' in answer and '/knowledge?' in answer
   assert 'example.com' not in answer
   known={tuple(sorted((key,tuple(value)) for key,value in parse_qs(urlparse(run._source(item)['url']).query).items())) for item in run.snapshot['items']}
   for link in re.findall(r'\]\(([^)]+)\)',answer):
    if '/knowledge?' not in link:continue
    parsed=urlparse(link)
    assert not parsed.netloc and parsed.path=='/knowledge'
    assert tuple(sorted((key,tuple(value)) for key,value in parse_qs(parsed.query).items())) in known
   assert 'unsupported' not in answer and 'truncated' not in answer
   assert '人工记录优先' not in answer and '优先作为' not in answer and '人工限制优先' not in answer
   # Removal invalidates all dependent summaries and live cache reads.
   r.file_command(uuid4(),'file_remove',{'object_id':str(leaf['id']),'binding_id':image_binding['binding_id'],'expected_revision':1})
   assert document(r,root['id'])['generation_id'] is None
   assert document(derived_db[0](1),root['id']) is None
   report.update(summary=material['sections'],tags=material['tags'],rollup=rollup['sections'],answer=answer,tool_trace=trace,media_status=material['coverage'],assertions='manual content/history unchanged; source invalidation; cross-account denied; source URL present')
  finally:await _client.close()
 asyncio.run(scenario())
 evidence=Path('.cloud-dev/knowledge-live-acceptance.json');evidence.write_text(json.dumps(report,ensure_ascii=False,indent=2))
