"""Visual changes retain ACL, version guards and reversible canvas state."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_canvas_tools import tools_db, payload
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle
@pytest.fixture(scope='module')
def visual_db(tools_db):
    make,actors=tools_db
    with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/317_knowledge_canvas_visual_editing.sql').read_text())
    return make,actors

def test_style_atomic_history(visual_db):
    make,_=visual_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'B')
    r.command(command('update_canvas_data',payload(r,[a,b],{'style':{'color':'#ff0000','bold':True,'font_size':24}})))
    assert all(n['canvas_data']['style']['bold'] for n in r.canvas()['nodes'] if n['id'] in [a['id'],b['id']])
    toggle(r);assert all(not n['canvas_data'] for n in r.canvas()['nodes'] if n['id'] in [a['id'],b['id']])
    toggle(r,'redo');assert next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['canvas_data']['style']['color']=='#ff0000'

def test_edit_group_range_preserves_tree(visual_db):
    make,_=visual_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'A');b=create(r,root['container_id'],root['id'],'B');c=create(r,root['container_id'],root['id'],'C')
    r.command(command('update_canvas_data',payload(r,[a],{'boundary':{'members':[a['id'],b['id']],'title':'范围'}})))
    r.command(command('update_canvas_data',payload(r,[a],{'boundary':{'members':[a['id'],b['id'],c['id']],'title':'范围'}})))
    toggle(r);assert len(next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['canvas_data']['boundary']['members'])==2
    assert all(n['parent_id']==root['id'] for n in r.canvas()['nodes'] if n['id'] in [a['id'],b['id'],c['id']])
    with pytest.raises(Exception,match='KB_INVALID_PLACEMENT'):r.command(command('update_canvas_data',payload(r,[a],{'boundary':{'members':[root['id'],b['id']],'title':'错误'}})))

def test_relation_appearance_reversible_and_guarded(visual_db):
    make,_=visual_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'B')
    r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'}))
    scene=r.canvas();relation=next(x for x in scene['relations'] if x['source_object_id']==a['id']);cid=a['container_id']
    p={'container_id':cid,'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),'relation_id':relation['id'],'appearance':{'text':'需要审批','bend':120}}
    cmd=command('update_relation',p);r.command(cmd);assert r.command(cmd)['status']=='replayed'
    assert next(x for x in r.canvas()['relations'] if x['id']==relation['id'])['appearance']['bend']==120
    toggle(r);assert next(x for x in r.canvas()['relations'] if x['id']==relation['id'])['appearance']=={}
    toggle(r,'redo');assert next(x for x in r.canvas()['relations'] if x['id']==relation['id'])['appearance']['text']=='需要审批'
    with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):r.command(command('update_relation',p))
    with pytest.raises(Exception,match='KB_ACCESS_DENIED'):make(1).command(command('update_relation',p))
@pytest.mark.parametrize('data',[{'style':{'color':'url(evil)'}},{'style':{'font_size':100}},{'image':{'binding_id':'bad','revision':0}}])
def test_visual_contract_rejects_invalid_values(data):
    from uuid import uuid4
    with pytest.raises(ValueError):command('update_canvas_data',{'container_id':uuid4(),'expected_container_revision':1,'updates':[{'object_id':uuid4(),'expected_layout_revision':1,'canvas_data':data}]})

from tests.test_knowledge_files import file_client, uploaded, url

def test_pinned_image_copy_and_undo(visual_db,file_client):
    from uuid import uuid4
    client,_,_,r,_=file_client;a=create(r);cid=a['container_id']
    # Exact bytes are irrelevant to storage semantics; MIME and version remain fixed.
    response=client.post(f'/api/knowledge/objects/{a["id"]}/files',data={'command_id':str(uuid4())},files={'file':('image.png',b'\x89PNG\r\n\x1a\nold','image/png')})
    assert response.status_code==200,response.text
    bid=response.json()['data']['binding_id']
    response=client.post(f'/api/knowledge/objects/{a["id"]}/files',data={'command_id':str(uuid4()),'binding_id':bid,'expected_revision':'1'},files={'file':('image.png',b'\x89PNG\r\n\x1a\nnew','image/png')})
    assert response.status_code==200,response.text
    r.command(command('update_canvas_data',payload(r,[a],{'image':{'binding_id':bid,'revision':1,'width':180,'placement':'top'}})))
    s=r.canvas();n=next(n for n in s['nodes'] if n['id']==a['id']);p={'snapshot_id':str(uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in s['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']}],'parent_id':None,'offset':{'x':500,'y':0}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copy=next(n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id']!=a['id']);image=copy['canvas_data']['image']
    assert image['binding_id']!=bid and image['revision']==1
    assert client.get(url(copy['id'],image['binding_id'])).content.endswith(b'old')
    toggle(r);assert not any(n['id']==copy['id'] for n in r.canvas()['nodes']);toggle(r,'redo');assert any(n['id']==copy['id'] for n in r.canvas()['nodes'])

def test_visual_size_and_layout_are_one_history_step(visual_db):
    make,_=visual_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'B')
    p=payload(r,[a],{'style':{'font_size':40}});s=r.canvas();old=next(n for n in s['nodes'] if n['id']==b['id'])
    p['nodes']=[{'node_id':old['node_id'],'expected_revision':old['layout_revision'],'position':{'x':old['x'],'y':old['y']+300}}]
    r.command(command('update_canvas_data',p));assert next(n for n in r.canvas()['nodes'] if n['id']==b['id'])['y']==old['y']+300
    toggle(r);restored=next(n for n in r.canvas()['nodes'] if n['id']==b['id']);assert restored['y']==old['y'];assert not next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['canvas_data']

def test_visual_stale_layout_rolls_back_metadata(visual_db):
    make,_=visual_db;r=make();a=create(r);p=payload(r,[a],{'style':{'font_size':40}})
    p['nodes']=[{'node_id':next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['node_id'],'expected_revision':999,'position':{'x':0,'y':500}}]
    with pytest.raises(Exception,match='KB_VERSION_CONFLICT'):r.command(command('update_canvas_data',p))
    assert not next(n for n in r.canvas()['nodes'] if n['id']==a['id'])['canvas_data']

def test_relation_appearance_copied_with_branch_snapshot(visual_db):
    from uuid import uuid4
    make,_=visual_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'B');cid=a['container_id']
    r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'}));scene=r.canvas();relation=next(x for x in scene['relations'] if x['source_object_id']==a['id'])
    r.command(command('update_relation',{'container_id':cid,'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),'relation_id':relation['id'],'appearance':{'text':'复制此说明','bend':-80}}))
    scene=r.canvas();p={'snapshot_id':str(uuid4()),'container_id':cid,'expected_container_revision':next(c['revision'] for c in scene['containers'] if c['id']==cid),'objects':[{'object_id':n['id'],'expected_revision':n['revision']} for n in scene['nodes'] if n['container_id']==cid],'parent_id':None,'offset':{'x':600,'y':0}}
    r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copied=next(x for x in r.canvas()['relations'] if x['container_id']==cid and x['id']!=relation['id']);assert copied['appearance']=={'text':'复制此说明','bend':-80};assert copied['source_object_id']!=a['id']
