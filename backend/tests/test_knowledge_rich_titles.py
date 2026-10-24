"""Container theme and explicit style resets share one history transaction."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_topic_editing import editing_db
from tests.test_knowledge_topic_entry import entry_db,rev
from tests.test_knowledge_directional_paste import directional_db
from tests.test_knowledge_topic_structure import structure_db,current
from tests.test_knowledge_canvas_visual import visual_db
from tests.test_knowledge_canvas_tools import tools_db,payload
from tests.test_knowledge_workspace_v2 import local_db,command
from tests.test_knowledge_title_layout import layout_db
from tests.test_knowledge_insert_before import before_db
from tests.test_knowledge_branch_drag import create
from tests.test_knowledge_canvas_undo import toggle

from tests.test_knowledge_map_theme import theme_db

@pytest.fixture(scope='module')
def rich_db(theme_db):
 make,actors=theme_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/324_knowledge_rich_titles.sql').read_text())
 return make,actors

FMT={'align':'center','runs':[{'text':'售后','bold':True},{'text':'😀方法','color':'#ff0000','italic':True,'font_size':24}]}
def title_request(r,n,title='售后😀方法',fmt=FMT):return {'object_id':n['id'],'expected_revision':current(r,n['id'])['revision'],'expected_container_revision':rev(r,n['container_id']),'title':title,'title_format':fmt}

def test_atomic_format_title_replay_and_history(rich_db):
 r=rich_db[0]();n=create(r);cmd=command('update_title',title_request(r,n));r.command(cmd);assert r.command(cmd)['status']=='replayed';after=current(r,n['id']);assert after['title']=='售后😀方法';assert after['canvas_data']['title_format']==FMT;toggle(r);assert current(r,n['id'])['title']==n['title'];assert 'title_format' not in current(r,n['id'])['canvas_data'];toggle(r,'redo');assert current(r,n['id'])['canvas_data']['title_format']==FMT

def test_create_with_format_and_plain_rename_cleanup(rich_db):
 r=rich_db[0]();root=create(r);p={'container_id':root['container_id'],'parent_id':root['id'],'kind':'topic','title':'售后😀方法','body':'','position':{'x':600,'y':500},'title_format':FMT};out=r.command(command('create_object',p));n=current(r,out['object_id']);assert n['canvas_data']['title_format']==FMT;toggle(r);assert all(x['id']!=n['id'] for x in r.canvas()['nodes']);toggle(r,'redo');n=current(r,n['id']);r.command(command('update_title',{'object_id':n['id'],'expected_revision':n['revision'],'title':'普通标题'}));assert 'title_format' not in current(r,n['id'])['canvas_data'];toggle(r);assert current(r,n['id'])['canvas_data']['title_format']==FMT

@pytest.mark.parametrize('bad',['text','version','permission','color','size'])
def test_invalid_format_rolls_back(rich_db,bad):
 import copy
 make,_=rich_db;r=make();n=create(r);p=title_request(r,n,fmt=copy.deepcopy(FMT));actor=r
 if bad=='text':p['title_format']['runs'][0]['text']='错'
 if bad=='version':p['expected_container_revision']+=1
 if bad=='permission':actor=make(1)
 if bad=='color':p['title_format']['runs'][0]['color']='red'
 if bad=='size':p['title_format']['runs'][0]['font_size']=100
 before=r.canvas()
 with pytest.raises(Exception):actor.command(command('update_title',p))
 assert r.canvas()==before

def test_formatted_template_root(rich_db):
 r=rich_db[0]();root=create(r);p={'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'parent_id':root['id'],'topics':[{'title':'售后😀方法','title_format':FMT,'parent_index':-1,'position':{'x':500,'y':400},'structure':'down'}]};out=r.command(command('create_template',p));assert current(r,out['object_id'])['canvas_data']['title_format']==FMT;toggle(r);toggle(r,'redo');assert current(r,out['object_id'])['canvas_data']['structure']=='down'

def test_format_only_and_metadata_mismatch(rich_db):
 r=rich_db[0]();n=create(r);fmt={'align':'right','runs':[{'text':n['title'],'italic':True}]};r.command(command('update_title',title_request(r,n,n['title'],fmt)));assert current(r,n['id'])['canvas_data']['title_format']==fmt;toggle(r);assert 'title_format' not in current(r,n['id'])['canvas_data'];toggle(r,'redo');before=r.canvas()
 with pytest.raises(Exception):r.command(command('update_canvas_data',payload(r,[current(r,n['id'])],{'title_format':FMT})))
 assert r.canvas()==before

def test_copy_snapshot_preserves_partial_formats(rich_db):
 from uuid import uuid4
 r=rich_db[0]();n=create(r);r.command(command('update_title',title_request(r,n)));n=current(r,n['id']);p={'snapshot_id':str(uuid4()),'container_id':n['container_id'],'expected_container_revision':rev(r,n['container_id']),'objects':[{'object_id':n['id'],'expected_revision':n['revision']}],'parent_id':None,'offset':{'x':500,'y':0}}
 r.branch_snapshot(p['snapshot_id'],command('copy_branches',p).payload.model_dump(mode='json'));r.command(command('copy_branches',p));copied=next(x for x in r.canvas()['nodes'] if x['container_id']==n['container_id'] and x['id']!=n['id']);assert copied['canvas_data']['title_format']==FMT;toggle(r);toggle(r,'redo');assert current(r,copied['id'])['canvas_data']['title_format']==FMT
