import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest
import httpx
spec=importlib.util.spec_from_file_location('proxy', Path(__file__).parents[2]/'deploy/proxy/us_failover.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_build_keeps_routes_and_uses_all_us_nodes():
 base={'outbounds':[{'type':'direct','tag':'direct'},{'type':'vless','tag':m.GROUP}], 'inbounds':[{'type':'mixed','tag':'app','listen_port':7890}], 'route':{'final':'direct','rules':[{'domain':['api.kie.ai'],'outbound':m.GROUP}]}}
 nodes=[{'name':f'美国{i}','type':'vless','server':'node.invalid','port':443,'uuid':'test','tls':True} for i in range(16)]
 cfg,meta=m.build(base,{'proxies':nodes+[{'name':'日本','type':'trojan'}]},'testsecret')
 assert len(meta['nodes'])==16
 assert cfg['route']['final']=='direct' and cfg['route']['rules'][-1]==base['route']['rules'][0]
 assert cfg['inbounds'][0]==base['inbounds'][0]
 assert all(x['listen']=='127.0.0.1' for x in cfg['inbounds'][1:])
 group=next(x for x in cfg['outbounds'] if x['tag']==m.GROUP)
 assert len(group['outbounds'])==16 and group['interrupt_exist_connections'] is False
 assert base['outbounds'][-1]['type']=='vless'

@pytest.mark.parametrize('current,healthy,expected',[('a',{'b'},'b'),('b',{'a','b'},'b'),('b',{'a','c'},'c'),('c',{'a'},'a'),('a',set(),None)])
def test_failure_moves_forward_without_flapping(current,healthy,expected):
 assert m.choose(['a','b','c'],current,healthy)==expected

async def test_all_failed_alert_requires_two_rounds_and_clears_on_recovery(tmp_path,monkeypatch):
 meta={'nodes':[{'tag':'a','name':'US1','port':18000},{'tag':'b','name':'US2','port':18001}], 'controller':'http://controller.invalid','secret':'test','group':m.GROUP}
 selected='a'; switches=[]
 def handler(request):
  nonlocal selected
  if request.method=='PUT':
   import json
   selected=json.loads(request.content)['name'];switches.append(selected)
   return httpx.Response(204)
  return httpx.Response(200,json={'now':selected})
 original=httpx.AsyncClient
 monkeypatch.setattr(m.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
 results=[{'a':False,'b':False},{'a':False,'b':False},{'a':False,'b':True}]
 async def probe(_):return results.pop(0)
 monkeypatch.setattr(m,'probe_nodes',probe)
 status=tmp_path/'state.json'
 assert (await m.check(meta,status))['alert'] is None
 assert (await m.check(meta,status))['alert']=='all_failed'
 recovered=await m.check(meta,status)
 assert recovered['alert'] is None and recovered['healthy']==1
 assert recovered['selected']=='b' and switches==['b']


async def test_stalled_probe_has_total_deadline(tmp_path, monkeypatch):
 import asyncio
 class Stalled:
  async def __aenter__(self): return self
  async def __aexit__(self,*_): pass
  async def get(self,*_): await asyncio.Event().wait()
 monkeypatch.setattr(m.httpx,'AsyncClient',lambda **_:Stalled())
 original=asyncio.timeout
 deadlines=[]
 def deadline(seconds):
  deadlines.append(seconds)
  return original(.01)
 monkeypatch.setattr(m.asyncio,'timeout',deadline)
 result=await m.probe_nodes({'nodes':[{'tag':'a','port':1},{'tag':'b','port':2}]})
 assert result=={'a':False,'b':False} and deadlines==[6,6]
