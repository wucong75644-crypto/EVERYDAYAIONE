"""Rehydrate sanitized, frozen synthetic production documents in loopback DB only."""
import json
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from tests.test_knowledge_search_progress import create,command
from services.knowledge_workspace.derivation import inputs


def clone(r):
 corpus=json.loads((Path(__file__).parent/'fixtures/knowledge_canvas_production_synthetic.json').read_text());mapping={};bindings={};scopes={}
 for name,docs in corpus['scopes'].items():
  root=None
  for doc in docs:
   node=create(r,title=doc['title']) if root is None else create(r,root['container_id'],mapping[doc['parent']]['id'],doc['title'])
   root=root or node;mapping[doc['key']]=node
   r.command(command('save_document',{'object_id':node['id'],'expected_revision':1,'body':doc['body']}))
   if doc['note'] or doc['labels']:
    scene=r.canvas();container=next(c for c in scene['containers'] if c['id']==root['container_id']);layout=next(n for n in scene['nodes'] if n['id']==node['id'])
    r.command(command('update_canvas_data',{'container_id':root['container_id'],'expected_container_revision':container['revision'],'updates':[{'object_id':node['id'],'expected_layout_revision':layout['layout_revision'],'canvas_data':{'note':doc['note'],'labels':doc['labels']}}]}))
   for file in doc['files']:
    uploaded=r.file_command(uuid4(),'file_upload',{'object_id':str(node['id']),'version_id':str(uuid4()),**{k:file[k] for k in ['name','mime_type','size','sha256','text_status','extracted_text']}})
    bindings[file['key']]=uploaded['binding_id']
  def remap(value):
   prefix,alias=value.split(':',1)
   return prefix+':'+str(bindings[alias] if prefix=='file' else mapping[alias]['id'])
  for doc in reversed(docs):
   oid=mapping[doc['key']]['id'];data=json.loads(json.dumps(doc['derived']))
   for value in data['sections']+data['chunks']:
    value['source_refs']=[remap(ref) for ref in value['source_refs']]
    if value.get('section_id') and not value['section_id'].startswith('section:'):value['section_id']=remap(value['section_id'])
   with r.cursor() as c:
    c.execute('SELECT public.kb_claim_derivation(%s) AS v',(oid,));claim=c.fetchone()['v']
   _,_,_,dependencies,_,fingerprint,_=inputs(r,oid)
   data.update(dependencies=dependencies,config_version='benchmark-frozen-production-ac2c8ccb')
   with r.cursor() as c:
    c.execute('SELECT public.kb_publish_derivation(%s,%s,%s,%s,%s) AS v',(oid,claim['token'],claim['dirty'],fingerprint,Jsonb(data)));assert c.fetchone()['v']
  scopes[name]=root
 return scopes
