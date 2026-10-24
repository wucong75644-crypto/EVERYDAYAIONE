"""Atomic relation-to-blank creation, geometry persistence and guards."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_knowledge_independent_groups import groups_db
from tests.test_knowledge_independent_groups import editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db

@pytest.fixture(scope='module')
def geometry_db(groups_db):
 make,actors=groups_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/326_knowledge_relation_geometry.sql').read_text())
 return make,actors

def floating(r,source,**extra):
 return command('create_relation_topic',{'container_id':source['container_id'],'expected_container_revision':rev(r,source['container_id']),'source_object_id':source['id'],'position':{'x':440,'y':123},**extra})
def edges(r,cid):return [e for e in r.canvas()['relations'] if e['container_id']==cid]

def test_blank_drop_creates_root_and_relation_as_one_undo_replay(geometry_db):
 r=geometry_db[0]();root=create(r);cmd=floating(r,root);result=r.command(cmd);assert r.command(cmd)['status']=='replayed';n=current(r,result['object_id']);assert n['parent_id'] is None;assert (n['x'],n['y'])==(440,123);assert n['title']=='自由主题';line=edges(r,root['container_id'])[0];assert line['source_object_id']==root['id'] and line['target_object_id']==n['id'];toggle(r);assert not edges(r,root['container_id']);assert not any(o['id']==n['id'] for o in r.canvas()['nodes']);toggle(r,'redo');assert current(r,n['id'])['parent_id'] is None;assert edges(r,root['container_id'])[0]['id']==line['id']

@pytest.mark.parametrize('endpoint',['source','target'])
def test_reconnect_blank_is_atomic_and_retains_other_end_geometry(geometry_db,endpoint):
 r=geometry_db[0]();a=create(r);b=create(r,a['container_id'],a['id']);r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'}));line=edges(r,a['container_id'])[0];appearance={'text':'线文字','bend':12,'source_anchor':{'x':1,'y':.2},'target_anchor':{'x':0,'y':.8},'source_control':{'x':160,'y':20},'target_control':{'x':-60,'y':40}};r.command(command('update_relation',{'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'relation_id':line['id'],'appearance':appearance}));fixed=b if endpoint=='source' else a;result=r.command(floating(r,fixed,relation_id=line['id'],endpoint=endpoint));changed=edges(r,a['container_id'])[0];assert str(changed[endpoint+'_object_id'])==str(result['object_id']);assert endpoint+'_anchor' not in changed['appearance'];assert changed['appearance']['text']=='线文字';other='target' if endpoint=='source' else 'source';assert changed['appearance'][other+'_control']==appearance[other+'_control'];toggle(r);assert {k:v for k,v in edges(r,a['container_id'])[0]['appearance'].items() if v is not None}==appearance;toggle(r,'redo');assert str(edges(r,a['container_id'])[0][endpoint+'_object_id'])==str(result['object_id'])

def test_two_control_vectors_and_fixed_anchors_survive_undo_redo(geometry_db):
 r=geometry_db[0]();a=create(r);r.command(floating(r,a));line=edges(r,a['container_id'])[0];data={'text':'曲线','bend':0,'source_anchor':{'x':.4,'y':0},'target_anchor':{'x':1,'y':.7},'source_control':{'x':-123,'y':-80},'target_control':{'x':212,'y':52}};cmd=command('update_relation',{'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'relation_id':line['id'],'appearance':data});r.command(cmd);assert all(edges(r,a['container_id'])[0]['appearance'][k]==v for k,v in data.items());toggle(r);assert edges(r,a['container_id'])[0]['appearance']=={};toggle(r,'redo');assert all(edges(r,a['container_id'])[0]['appearance'][k]==v for k,v in data.items())

@pytest.mark.parametrize('kind',['permission','version','foreign-source','missing-relation'])
def test_blank_creation_guards_leave_no_orphan(geometry_db,kind):
 make,_=geometry_db;r=make();a=create(r);cmd=floating(r,a);actor=r
 if kind=='permission':actor=make(1)
 if kind=='version':cmd.payload.expected_container_revision+=1
 if kind=='foreign-source':cmd.payload.source_object_id=create(r)['id']
 if kind=='missing-relation':cmd.payload.relation_id=uuid4()
 before=r.canvas()
 with pytest.raises(Exception):actor.command(cmd)
 assert r.canvas()==before

@pytest.mark.parametrize('field,value',[('source_anchor',{'x':.5,'y':.5}),('target_anchor',{'x':2,'y':0}),('source_control',{'x':float('inf'),'y':0}),('target_control',{'x':4001,'y':0})])
def test_geometry_contract_rejects_invalid_coordinates(field,value):
 with pytest.raises(ValueError):command('update_relation',{'container_id':str(uuid4()),'expected_container_revision':1,'relation_id':str(uuid4()),'appearance':{field:value}})


def test_repositioning_existing_endpoint_increments_revision_and_restores_geometry(geometry_db):
 r=geometry_db[0]();a=create(r);r.command(floating(r,a));line=edges(r,a['container_id'])[0];before=rev(r,a['container_id']);data={'text':'端点','bend':0,'target_anchor':{'x':1,'y':.3}}
 cmd=command('reconnect_relation',{'container_id':a['container_id'],'expected_container_revision':before,'relation_id':line['id'],'source_object_id':line['source_object_id'],'target_object_id':line['target_object_id'],'appearance':data});r.command(cmd);assert rev(r,a['container_id'])==before+1;assert edges(r,a['container_id'])[0]['appearance']==data;toggle(r);assert edges(r,a['container_id'])[0]['appearance']=={};toggle(r,'redo');assert edges(r,a['container_id'])[0]['appearance']==data

def test_compact_manual_width_roundtrip_and_undo(geometry_db):
 r=geometry_db[0]();a=create(r);r.command(command('update_canvas_data',payload(r,[a],{'width':80})));assert current(r,a['id'])['canvas_data']['width']==80;toggle(r);assert not current(r,a['id'])['canvas_data'];toggle(r,'redo');assert current(r,a['id'])['canvas_data']['width']==80
