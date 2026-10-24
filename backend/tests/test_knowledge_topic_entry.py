"""Outline forests and parent insertion preserve content, groups and atomic history."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_knowledge_directional_paste import directional_db
from tests.test_knowledge_topic_structure import structure_db,current
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db,payload
from tests.test_knowledge_workspace_v2 import local_db,command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

@pytest.fixture(scope='module')
def entry_db(directional_db):
    make,actors=directional_db
    with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/321_knowledge_topic_entry.sql').read_text())
    return make,actors

def rev(r,cid):return next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid)
def parent_request(r,target):
    cid=target['container_id'];return {'container_id':cid,'expected_container_revision':rev(r,cid),'object_id':target['id'],'expected_revision':target['revision'],'parent_id':target['parent_id'],'topics':[{'title':'新父主题','parent_index':-1,'position':{'x':300,'y':200}}],'nodes':[]}

def test_outline_forest_is_one_history_step_with_replay(entry_db):
    make,_=entry_db;r=make();root=create(r);cid=root['container_id'];p={'container_id':cid,'expected_container_revision':rev(r,cid),'parent_id':root['id'],'topics':[{'title':title,'parent_index':parent,'position':{'x':x,'y':y}} for title,parent,x,y in [('目标',-1,-400,200),('指标',0,-800,200),('执行',-1,-400,500)]]};cmd=command('create_topics',p);r.command(cmd);assert r.command(cmd)['status']=='replayed';nodes=[n for n in r.canvas()['nodes'] if n['container_id']==cid];assert len(nodes)==4;goal=next(n for n in nodes if n['title']=='目标');assert next(n for n in nodes if n['title']=='指标')['parent_id']==goal['id'];assert next(n for n in nodes if n['title']=='执行')['parent_id']==root['id'];toggle(r);assert len([n for n in r.canvas()['nodes'] if n['container_id']==cid])==1;toggle(r,'redo');assert len([n for n in r.canvas()['nodes'] if n['container_id']==cid])==4

def test_parent_preserves_identity_body_kind_descendants_and_order(entry_db):
    make,_=entry_db;r=make();root=create(r);cid=root['container_id'];oid=r.command(command('create_object',{'container_id':cid,'parent_id':root['id'],'kind':'document','title':'原文档','body':'固定正文','position':{'x':400,'y':0}}))['object_id'];target=r.object(oid);child=create(r,cid,target['id'],'后代');peer=create(r,cid,root['id'],'同级');r.command(command('update_canvas_data',payload(r,[target],{'note':'保留备注','labels':['重要']})));before=current(r,target['id']);p=parent_request(r,target);p['nodes']=[{'node_id':before['node_id'],'expected_revision':before['layout_revision'],'position':{'x':700,'y':0}}];cmd=command('insert_parent',p);new=r.command(cmd)['object_id'];assert r.command(cmd)['status']=='replayed';after=r.object(oid);assert after['parent_id']==current(r,new)['id'];assert after['title']=='原文档' and after['body']=='固定正文' and after['kind']=='document';assert current(r,target['id'])['canvas_data']['note']=='保留备注';assert current(r,child['id'])['parent_id']==target['id'];assert current(r,new)['sort_order']==before['sort_order'];assert current(r,peer['id'])['sort_order']>current(r,new)['sort_order'];toggle(r);assert r.object(oid)['parent_id']==root['id'];assert current(r,target['id'])['x']==before['x'];toggle(r,'redo');assert str(r.object(oid)['parent_id'])==new

def test_parent_updates_group_members_and_transfers_boundary_owner(entry_db):
    make,_=entry_db;r=make();root=create(r);cid=root['container_id'];a=create(r,cid,root['id'],'A');b=create(r,cid,root['id'],'B');r.command(command('update_canvas_data',payload(r,[a],{'boundary':{'title':'组','members':[a['id'],b['id']]},'note':'原节点备注'})));sid=r.command(command('create_summaries',{'container_id':cid,'expected_container_revision':rev(r,cid),'groups':[{'title':'汇总','members':[a['id'],b['id']],'position':{'x':1000,'y':0}}]}))['object_id'];new=r.command(command('insert_parent',parent_request(r,a)))['object_id'];assert current(r,new)['canvas_data']['boundary']['members']==[new,str(b['id'])];assert current(r,sid)['canvas_data']['summary']['members']==[new,str(b['id'])];assert current(r,a['id'])['canvas_data']['note']=='原节点备注';assert 'boundary' not in current(r,a['id'])['canvas_data'];toggle(r);assert current(r,a['id'])['canvas_data']['boundary']['members']==[str(a['id']),str(b['id'])];toggle(r,'redo');assert current(r,new)['canvas_data']['boundary']['members'][0]==new

def test_wrap_root_and_summary_keep_their_semantics(entry_db):
    make,_=entry_db;r=make();a=create(r);cid=a['container_id'];r.command(command('update_canvas_data',payload(r,[a],{'structure':'left'})));p=parent_request(r,a);p['topics'][0]['structure']='left';new=r.command(command('insert_parent',p))['object_id'];assert current(r,new)['parent_id'] is None;assert current(r,new)['canvas_data']['structure']=='left';assert current(r,a['id'])['canvas_data']['structure']=='left'
    b=create(r,cid,current(r,new)['id'],'B');c=create(r,cid,current(r,new)['id'],'C');sid=r.command(command('create_summaries',{'container_id':cid,'expected_container_revision':rev(r,cid),'groups':[{'title':'概要','members':[b['id'],c['id']],'position':{'x':1000,'y':0}}]}))['object_id'];summary=r.object(sid);wrapper=r.command(command('insert_parent',parent_request(r,summary)))['object_id'];assert current(r,wrapper)['canvas_data']['summary']['members']==[str(b['id']),str(c['id'])];assert 'summary' not in current(r,sid)['canvas_data'];assert str(current(r,sid)['parent_id'])==wrapper

@pytest.mark.parametrize('bad',['revision','container','parent','permission','layout'])
def test_insert_conflict_never_leaves_a_wrapper_or_changed_parent(entry_db,bad):
    make,_=entry_db;r=make();a=create(r);p=parent_request(r,a);actor=r
    if bad=='revision':p['expected_revision']+=1
    if bad=='container':p['expected_container_revision']+=1
    if bad=='parent':p['parent_id']=create(r)['id']
    if bad=='permission':actor=make(1)
    if bad=='layout':p['nodes']=[{'node_id':current(r,a['id'])['node_id'],'expected_revision':999,'position':{'x':900,'y':0}}]
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('insert_parent',p))
    assert r.canvas()==before

@pytest.mark.parametrize('parents',[[0],[-1,1],[-1,9]])
def test_bad_outline_hierarchy_is_rejected(parents):
    with pytest.raises(ValueError):command('create_topics',{'container_id':uuid4(),'expected_container_revision':1,'topics':[{'title':'主题','parent_index':pi,'position':{'x':0,'y':0}} for pi in parents]})

@pytest.mark.parametrize('bad',['revision','permission','parent'])
def test_outline_failure_does_not_create_a_partial_forest(entry_db,bad):
    make,_=entry_db;r=make();root=create(r);cid=root['container_id'];p={'container_id':cid,'expected_container_revision':rev(r,cid),'parent_id':root['id'],'topics':[{'title':'A','parent_index':-1,'position':{'x':0,'y':0}},{'title':'B','parent_index':-1,'position':{'x':0,'y':300}}]};actor=r
    if bad=='revision':p['expected_container_revision']+=1
    if bad=='permission':actor=make(1)
    if bad=='parent':p['parent_id']=create(r)['id']
    before=r.canvas()
    with pytest.raises(Exception,match='KB_'):actor.command(command('create_topics',p))
    assert r.canvas()==before
