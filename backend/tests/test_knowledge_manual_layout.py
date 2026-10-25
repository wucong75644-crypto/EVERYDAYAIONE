"""Manual creation anchors and position intent survive reload, copy and history atomically."""
import json
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
import pytest
from tests.test_knowledge_summary_scope import summary_db, geometry_db, groups_db, editing_db, entry_db, rev, directional_db, structure_db, current, visual_db, tools_db, payload, local_db, command, layout_db, before_db, create, toggle, theme_db, rich_db

ANCHORS={'source_anchor':{'x':1,'y':.5},'target_anchor':{'x':0,'y':.5}}

@pytest.fixture(scope='module')
def manual_db(summary_db):
 make,actors=summary_db
 with psycopg.connect(**make.admin_args) as c:
  c.execute((Path(__file__).resolve().parents[1]/'migrations/328_knowledge_manual_creation_layout.sql').read_text())
 return make,actors

def request(r,root,layout=ANCHORS):
 return {'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'parent_id':root['id'],'kind':'topic','title':'拉线主题','body':'','position':{'x':900,'y':450},'manual_layout':layout}


def test_manual_creation_reload_title_history_copy_and_replay(manual_db):
 make,_=manual_db;r=make();root=create(r);cmd=command('create_object',request(r,root));result=r.command(cmd);oid=result['object_id'];n=current(r,oid)
 assert (n['x'],n['y'])==(900,450);assert n['parent_id']==root['id'];assert n['canvas_data']['manual_layout']==ANCHORS
 assert r.command(cmd)['status']=='replayed'
 toggle(r);assert not any(v['id']==oid for v in r.canvas()['nodes'])
 toggle(r,'redo');assert current(r,oid)['canvas_data']['manual_layout']==ANCHORS
 r.command(command('update_title',{'object_id':oid,'expected_revision':current(r,oid)['revision'],'title':'继续输入后确认'}));assert (current(r,oid)['x'],current(r,oid)['y'])==(900,450)
 toggle(r);assert current(r,oid)['canvas_data']['manual_layout']==ANCHORS
 n=current(r,oid);p={'snapshot_id':str(uuid4()),'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'objects':[{'object_id':oid,'expected_revision':n['revision']}],'parent_id':root['id'],'offset':{'x':500,'y':100}}
 r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p))
 copy=next(v for v in r.canvas()['nodes'] if v['container_id']==root['container_id'] and str(v['id']) not in [str(root['id']),str(oid)]);assert copy['canvas_data']['manual_layout']==ANCHORS;assert (copy['x'],copy['y'])==(1400,550)
 toggle(r);toggle(r,'redo');assert current(r,copy['id'])['canvas_data']['manual_layout']==ANCHORS


@pytest.mark.parametrize('bad',[None,[],{'source_anchor':{'x':1,'y':.5}},{'source_anchor':{'x':.5,'y':.5},'target_anchor':{'x':0,'y':.5}},{'source_anchor':{'x':1,'y':2},'target_anchor':{'x':0,'y':.5}},{'extra':True},{'source_anchor':{'x':'1','y':.5},'target_anchor':{'x':0,'y':.5}}])
def test_invalid_anchor_payload_rolls_back_entire_creation(manual_db,bad):
 r=manual_db[0]();root=create(r);p=request(r,root,bad);before=r.canvas()
 with pytest.raises(Exception,match='KB_INVALID_PLACEMENT'):
  with r.cursor() as c:c.execute('SELECT public.kb_canvas_command(%s,%s,%s)',(uuid4(),'create_object',Jsonb(p,dumps=lambda value:json.dumps(value,default=str))))
 assert r.canvas()==before


@pytest.mark.parametrize('failure',['permission','version'])
def test_manual_creation_retains_permission_and_version_guards(manual_db,failure):
 make,_=manual_db;r=make();root=create(r);p=request(r,root)
 if failure=='version':p['expected_container_revision']+=1
 before=r.canvas()
 with pytest.raises(Exception,match='KB_'):(make(1) if failure=='permission' else r).command(command('create_object',p))
 assert r.canvas()==before


def test_sibling_manual_position_and_metadata_update_are_undoable(manual_db):
 r=manual_db[0]();root=create(r);result=r.command(command('create_object',request(r,root,{})));n=current(r,result['object_id']);assert n['canvas_data']['manual_layout']=={}
 r.command(command('update_canvas_data',payload(r,[n],{'note':'备注','manual_layout':ANCHORS})));assert current(r,n['id'])['canvas_data']['manual_layout']==ANCHORS
 toggle(r);assert current(r,n['id'])['canvas_data']['manual_layout']=={}


def test_manual_layout_contract_rejects_unpaired_and_non_edge_anchors():
 from services.knowledge_workspace.contracts import ManualLayout
 with pytest.raises(ValueError):ManualLayout(source_anchor={'x':1,'y':.5})
 with pytest.raises(ValueError):ManualLayout(source_anchor={'x':.5,'y':.5},target_anchor={'x':0,'y':.5})
 assert ManualLayout().model_dump(mode='json')=={}
