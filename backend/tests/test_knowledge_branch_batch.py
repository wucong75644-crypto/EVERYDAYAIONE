"""Whole branch commands and history on a private loopback PostgreSQL."""
from pathlib import Path
import pytest
import psycopg
from tests.test_knowledge_workspace_v2 import local_db, command
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle, visible

@pytest.fixture(scope='module')
def batch_db(local_db):
    make, actors = local_db
    with psycopg.connect(**make.admin_args) as c:
        for name in ['307_knowledge_branch_drag.sql','308_knowledge_canvas_undo.sql','309_knowledge_branch_batch.sql']:
            c.execute((Path(__file__).resolve().parents[1]/'migrations'/name).read_text())
    return make, actors

def data(r, items, parent=None):
    cid=items[0]['container_id']
    return {'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),
            'objects':[{'object_id':n['id'],'expected_revision':n['revision']} for n in items],
            'parent_id':parent,'offset':{'x':80,'y':300}}

def test_copy_complete_branch_deduplicates_and_roundtrips(batch_db):
    make,_=batch_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,a['id'],'B')
    r.command(command('save_document',{'object_id':b['id'],'expected_revision':1,'body':'正文'}));b=r.object(b['id'])
    r.command(command('connect',{'source_object_id':a['id'],'target_object_id':b['id'],'relation_type':'related'}))
    before=visible(r,cid);cmd=command('copy_branches',data(r,[a,b],root['id']));r.command(cmd)
    added=[n for n in r.canvas()['nodes'] if n['container_id']==cid and n['id'] not in [root['id'],a['id'],b['id']]]
    assert len(added)==2
    ca=next(n for n in added if n['title']=='A');cb=next(n for n in added if n['title']=='B')
    assert ca['parent_id']==root['id'] and cb['parent_id']==ca['id'] and cb['x']==180 and cb['y']==400
    assert r.object(cb['id'])['body']=='正文'
    assert any(e['source_object_id']==ca['id'] and e['target_object_id']==cb['id'] for e in r.canvas()['relations'])
    after=visible(r,cid);assert r.command(cmd)['status']=='replayed' and visible(r,cid)==after
    toggle(r);assert visible(r,cid)==before;toggle(r,'redo');assert visible(r,cid)==after

def test_delete_includes_hidden_folded_descendants_and_restores_relations(batch_db):
    make,_=batch_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id']);b=create(r,cid,a['id']);c=create(r,cid,root['id'])
    r.command(command('connect',{'source_object_id':b['id'],'target_object_id':c['id'],'relation_type':'related'}))
    before=visible(r,cid);r.command(command('delete_branches',data(r,[a])))
    assert not any(n['id'] in [a['id'],b['id']] for n in r.canvas()['nodes'])
    assert r.version(b['id'],1) and r.search(cid,'Node')['items']
    after=visible(r,cid);toggle(r);assert visible(r,cid)==before;toggle(r,'redo');assert visible(r,cid)==after

@pytest.mark.parametrize('bad',['version','container','other_space','incomplete_copy','duplicate','permission'])
def test_batch_rejects_stale_and_unauthorized_atomically(batch_db,bad):
    make,actors=batch_db;r=make();root=create(r);a=create(r,root['container_id'],root['id']);b=create(r,root['container_id'],a['id'])
    p=data(r,[a,b]);actor=r
    if bad=='version':p['objects'][0]['expected_revision']=99
    if bad=='container':p['expected_container_revision']-=1
    if bad=='other_space':p['parent_id']=create(r)['id']
    if bad=='incomplete_copy':p['objects']=p['objects'][:1]
    if bad=='duplicate':p['objects'].append(p['objects'][0])
    if bad=='permission':actor=make(1)
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('copy_branches',p))
    assert r.canvas()==before


def test_repeat_paste_with_fresh_container_revision_and_original_source(batch_db):
    make,_=batch_db;r=make();root=create(r);a=create(r,root['container_id'],root['id'],'repeat')
    for _ in range(2):r.command(command('copy_branches',data(r,[a],root['id'])))
    assert len([n for n in r.canvas()['nodes'] if n['container_id']==root['container_id'] and n['title']=='repeat'])==3

from tests.test_knowledge_files import file_client, uploaded, url

def test_copy_files_pin_current_version_and_restore_with_branch(batch_db,file_client):
    client,ctx,_,r,_=file_client
    root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'File source')
    bid=uploaded(client,a['id'],'固定附件第一版')['binding_id']
    before={n['id'] for n in r.canvas()['nodes']}
    r.command(command('copy_branches',data(r,[a],root['id'])))
    copied=next(n for n in r.canvas()['nodes'] if n['id'] not in before)
    binding=client.get(f"/api/knowledge/objects/{copied['id']}/files").json()['data'][0]
    new_bid=binding['id'];assert new_bid!=bid
    assert client.get(url(copied['id'],new_bid)).content=='固定附件第一版'.encode()
    toggle(r);assert not any(n['id']==copied['id'] for n in r.canvas()['nodes'])
    toggle(r,'redo');assert client.get(url(copied['id'],new_bid)).content=='固定附件第一版'.encode()
    uploaded(client,a['id'],'源附件更新第二版',binding_id=bid,expected_revision='1')
    assert client.get(url(copied['id'],new_bid)).content=='固定附件第一版'.encode()
    assert client.get(url(a['id'],bid,2)).content=='源附件更新第二版'.encode()
