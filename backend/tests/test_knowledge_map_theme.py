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

@pytest.fixture(scope='module')
def theme_db(editing_db):
 make,actors=editing_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/323_knowledge_map_theme.sql').read_text())
 return make,actors

def request(r,root,theme=None):return {'container_id':root['container_id'],'expected_container_revision':rev(r,root['container_id']),'theme':theme or {'palette':'ocean','background':'#f0f4ff','font':'serif','font_size':20},'updates':[],'nodes':[]}
def theme(r,cid):return next(c['canvas_theme'] for c in r.canvas()['containers'] if c['id']==cid)

def test_theme_history_persistence_replay_and_space_isolation(theme_db):
 make,_=theme_db;r=make();a=create(r);b=create(r);cid=a['container_id'];cmd=command('update_map_theme',request(r,a));r.command(cmd);assert r.command(cmd)['status']=='replayed';assert theme(r,cid)['font']=='serif';assert theme(r,b['container_id'])=={};toggle(r);assert theme(r,cid)=={};toggle(r,'redo');assert theme(r,cid)['palette']=='ocean';r.command(command('update_title',{'object_id':a['id'],'expected_revision':a['revision'],'title':'新标题'}));toggle(r);assert theme(r,cid)['palette']=='ocean';assert current(r,a['id'])['title']==a['title']

def test_reset_preserves_note_width_and_body_and_restores_style(theme_db):
 make,_=theme_db;r=make();a=create(r);r.command(command('update_canvas_data',payload(r,[a],{'note':'保留备注','width':360,'style':{'color':'#222222','background':'#ffffff','border':'#444444','font_size':16}})));n=current(r,a['id']);p=request(r,a);p['updates']=[{'object_id':a['id'],'expected_layout_revision':n['layout_revision'],'style':None}];p['nodes']=[{'node_id':n['node_id'],'expected_revision':n['layout_revision']+1,'position':{'x':700,'y':500}}];r.command(command('update_map_theme',p));after=current(r,a['id']);assert after['canvas_data']['note']=='保留备注';assert after['canvas_data']['width']==360;assert 'style' not in after['canvas_data'];assert after['x']==700;toggle(r);old=current(r,a['id']);assert old['canvas_data']['style']['font_size']==16;assert old['x']==n['x'];assert theme(r,a['container_id'])=={};toggle(r,'redo');assert current(r,a['id'])['x']==700

@pytest.mark.parametrize('bad',['container','permission','layout','style','foreign'])
def test_theme_and_layout_failure_roll_back_everything(theme_db,bad):
 make,_=theme_db;r=make();a=create(r);p=request(r,a);actor=r
 if bad=='container':p['expected_container_revision']+=1
 if bad=='permission':actor=make(1)
 if bad=='layout':p['nodes']=[{'node_id':current(r,a['id'])['node_id'],'expected_revision':999,'position':{'x':700,'y':500}}]
 if bad=='style':p['updates']=[{'object_id':a['id'],'expected_layout_revision':999,'style':None}]
 if bad=='foreign':p['updates']=[{'object_id':create(r)['id'],'expected_layout_revision':1,'style':None}]
 before=r.canvas()
 with pytest.raises(Exception,match='KB_'):actor.command(command('update_map_theme',p))
 assert r.canvas()==before

def test_level_rules_persist_for_new_nodes_without_changing_identity(theme_db):
 make,_=theme_db;r=make();a=create(r);p=request(r,a,{'palette':'forest','levels':[{'depth':1,'style':{'color':'#112233','background':'#ffffff','border':'#334455','font_size':18}}]});r.command(command('update_map_theme',p));b=create(r,a['container_id'],a['id'],'新主题');assert current(r,b['id'])['parent_id']==a['id'];assert theme(r,a['container_id'])['levels'][0]['depth']==1;toggle(r);assert theme(r,a['container_id'])['levels'][0]['style']['font_size']==18

@pytest.mark.parametrize('bad',[{'background':'url(javascript:alert(1))'},{'font_size':100},{'palette':'unknown'},{'font':'remote-font'},{'levels':[{'depth':40,'style':{}}]},{'levels':[{'depth':1,'style':{}},{'depth':1,'style':{}}]}])
def test_theme_contract_rejects_invalid_styles(bad):
 from services.knowledge_workspace.contracts import MapTheme
 with pytest.raises(ValueError):MapTheme(**bad)

def test_theme_updates_cannot_change_content_or_files():
 from services.knowledge_workspace.contracts import MapStyleUpdate
 from uuid import uuid4
 with pytest.raises(ValueError):MapStyleUpdate(object_id=uuid4(),expected_layout_revision=1,canvas_data={'note':'overwrite'})

def test_legacy_history_without_theme_does_not_reset_current_settings(theme_db):
 make,_=theme_db;r=make();a=create(r);r.command(command('update_map_theme',request(r,a)));cmd=command('update_title',{'object_id':a['id'],'expected_revision':a['revision'],'title':'旧客户端改名'});r.command(cmd)
 with psycopg.connect(**make.admin_args) as c:c.execute("UPDATE public.kb_canvas_history SET before_state=before_state-'canvas_theme',after_state=after_state-'canvas_theme' WHERE command_id=%s",(cmd.command_id,))
 toggle(r);assert theme(r,a['container_id'])['palette']=='ocean';assert current(r,a['id'])['title']==a['title']

def test_empty_space_theme_is_still_undoable(theme_db):
 make,_=theme_db;r=make();a=create(r);r.command(command('delete_topic',{'container_id':a['container_id'],'expected_container_revision':rev(r,a['container_id']),'object_id':a['id'],'expected_revision':a['revision'],'nodes':[]}));r.command(command('update_map_theme',request(r,a)));assert theme(r,a['container_id'])['palette']=='ocean';toggle(r);assert theme(r,a['container_id'])=={};toggle(r,'redo');assert theme(r,a['container_id'])['palette']=='ocean'


def test_automatic_layout_is_atomic_undoable_and_preserves_custom_width(theme_db):
 make,_=theme_db;r=make();a=create(r);b=create(r,a['container_id'],a['id'],'子主题')
 r.command(command('update_canvas_data',payload(r,[b],{'width':420,'note':'保留内容'})))
 before=current(r,b['id']);settings={'auto_balance':True,'compact':True,'equal_width':True}
 p=request(r,a,{'layout':settings});p['nodes']=[{'node_id':before['node_id'],'expected_revision':before['layout_revision'],'position':{'x':740,'y':210}}]
 cmd=command('update_map_theme',p);r.command(cmd)
 assert theme(r,a['container_id'])['layout']==settings
 assert current(r,b['id'])['x']==740
 assert current(r,b['id'])['canvas_data']['width']==420
 assert r.command(cmd)['status']=='replayed'
 toggle(r);assert theme(r,a['container_id'])=={}
 assert current(r,b['id'])['x']==before['x']
 assert current(r,b['id'])['parent_id']==a['id']
 toggle(r,'redo');assert theme(r,a['container_id'])['layout']==settings
 assert current(r,b['id'])['x']==740


def test_automatic_layout_contract_preserves_legacy_shape():
 from services.knowledge_workspace.contracts import MapTheme
 assert 'layout' not in MapTheme().model_dump(mode='json')
 assert MapTheme(layout={'compact':True}).model_dump(mode='json')['layout']=={'auto_balance':False,'compact':True,'equal_width':False}
 with pytest.raises(ValueError):MapTheme(layout={'unknown':True})
