"""Video/link metadata and template trees retain isolation and atomic history."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db, payload
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle
from tests.test_knowledge_files import file_client, url

@pytest.fixture(scope='module')
def media_db(visual_db):
    make,actors=visual_db
    with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/318_knowledge_canvas_media_templates.sql').read_text())
    return make,actors

def template(r,cid,parent=None):
    return {'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'parent_id':parent,'topics':[{'title':'年度总结','parent_index':-1,'position':{'x':400,'y':200}},{'title':'年度成果','parent_index':0,'position':{'x':750,'y':0}},{'title':'下一年计划','parent_index':0,'position':{'x':750,'y':400}},{'title':'核心目标','parent_index':2,'position':{'x':1100,'y':400}}]}

def test_template_tree_is_one_undo_step_and_replays(media_db):
    make,_=media_db;r=make();root=create(r);cid=root['container_id'];p=template(r,cid,root['id']);cmd=command('create_template',p);oid=r.command(cmd)['object_id'];assert r.command(cmd)['status']=='replayed'
    ns=[n for n in r.canvas()['nodes'] if n['container_id']==cid];center=next(n for n in ns if str(n['id'])==oid);assert center['parent_id']==root['id'];assert len(ns)==5
    plan=next(n for n in ns if n['title']=='下一年计划');assert next(n for n in ns if n['title']=='核心目标')['parent_id']==plan['id']
    toggle(r);assert len([n for n in r.canvas()['nodes'] if n['container_id']==cid])==1
    toggle(r,'redo');assert len([n for n in r.canvas()['nodes'] if n['container_id']==cid])==5

@pytest.mark.parametrize('bad',['revision','parent','permission'])
def test_template_guards_do_not_partially_create(media_db,bad):
    make,_=media_db;r=make();root=create(r);p=template(r,root['container_id']);actor=r
    if bad=='revision':p['expected_container_revision']-=1
    if bad=='parent':p['parent_id']=create(r)['id']
    if bad=='permission':actor=make(1)
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('create_template',p))
    assert r.canvas()==before

@pytest.mark.parametrize('url',['javascript:alert(1)','data:text/html,hello','//example.com','https://u:p@example.com','https://example.com/\nfoo','https://example.com/\\evil'])
def test_link_contract_rejects_unsafe_url(url):
    with pytest.raises(ValueError):command('update_canvas_data',{'container_id':uuid4(),'expected_container_revision':1,'updates':[{'object_id':uuid4(),'expected_layout_revision':1,'canvas_data':{'link':{'url':url}}}]})

@pytest.mark.parametrize('parents',[[-1,-1],[-1,1],[-1,2],[0]])
def test_template_contract_rejects_disconnected_or_cyclic_tree(parents):
    p={'container_id':uuid4(),'expected_container_revision':1,'topics':[{'title':'主题','parent_index':i,'position':{'x':0,'y':0}} for i in parents]}
    with pytest.raises(ValueError):command('create_template',p)

def test_link_undo_and_copy_snapshot(media_db):
    make,_=media_db;r=make();a=create(r);cid=a['container_id'];link={'url':'https://example.com/video.mp4','title':'培训','kind':'video'};r.command(command('update_canvas_data',payload(r,[a],{'link':link})))
    toggle(r);assert not next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['canvas_data'];toggle(r,'redo')
    s=r.canvas();n=next(n for n in s['nodes'] if n['id']==a['id']);p={'snapshot_id':str(uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in s['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']}],'parent_id':None,'offset':{'x':500,'y':0}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copy=next(n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id']!=a['id']);assert copy['canvas_data']['link']==link

def test_video_fixed_version_copy_acl_and_undo(media_db,file_client):
    client,_,_,r,make=file_client;a=create(r);cid=a['container_id'];content=b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom'
    def upload(data,**extra):
        res=client.post(f'/api/knowledge/objects/{a["id"]}/files',data={'command_id':str(uuid4()),**extra},files={'file':('training.mp4',data,'video/mp4')});assert res.status_code==200,res.text;return res.json()['data']['binding_id']
    bid=upload(content);upload(content+b'new',binding_id=bid,expected_revision='1')
    video={'binding_id':bid,'revision':1,'title':'培训'};p=payload(r,[a],{'video':video});r.command(command('update_canvas_data',p));toggle(r);assert not next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['canvas_data'];toggle(r,'redo')
    s=r.canvas();n=next(n for n in s['nodes'] if n['id']==a['id']);p={'snapshot_id':str(uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in s['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']}],'parent_id':None,'offset':{'x':500,'y':0}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copy=next(n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id']!=a['id']);v=copy['canvas_data']['video'];assert v['binding_id']!=bid and v['revision']==1;assert client.get(url(copy['id'],v['binding_id'])).content==content
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):r.command(command('update_canvas_data',payload(r,[copy],{'video':video})))
    assert make(1).file_version(copy['id'],v['binding_id'],1) is None
