"""Card dimensions and anchored edge moves are a single undoable update."""
from pathlib import Path
import psycopg
import pytest
from tests.test_knowledge_manual_layout import manual_db, summary_db, geometry_db, groups_db, editing_db, entry_db, rev, directional_db, structure_db, current, visual_db, tools_db, payload, local_db, command, layout_db, before_db, create, toggle, theme_db, rich_db

@pytest.fixture(scope='module')
def resize_db(manual_db):
 make,actors=manual_db
 with psycopg.connect(**make.admin_args) as c:
  c.execute((Path(__file__).resolve().parents[1]/'migrations/329_knowledge_topic_resize.sql').read_text())
 return make,actors

def test_dimensions_position_reload_history_and_replay(resize_db):
 r=resize_db[0]();n=create(r);old=current(r,n['id']);p=payload(r,[old],{'width':360,'height':240,'manual_layout':{}})
 p['nodes']=[{'node_id':old['node_id'],'expected_revision':old['layout_revision']+1,'position':{'x':old['x']-88,'y':old['y']-94}}]
 cmd=command('update_canvas_data',p);r.command(cmd);after=current(r,n['id']);assert all(after['canvas_data'][k]==v for k,v in {'width':360,'height':240,'manual_layout':{}}.items());assert (after['x'],after['y'])==(old['x']-88,old['y']-94)
 assert r.command(cmd)['status']=='replayed'
 toggle(r);back=current(r,n['id']);assert back['canvas_data']==old['canvas_data'];assert (back['x'],back['y'])==(old['x'],old['y'])
 toggle(r,'redo');assert current(r,n['id'])['canvas_data']==after['canvas_data']

@pytest.mark.parametrize('height',[0,79,1601,150.5,'240',True])
def test_invalid_height_rolls_back(resize_db,height):
 from uuid import uuid4
 from psycopg.types.json import Jsonb
 import json
 r=resize_db[0]();n=create(r);before=r.canvas();p=payload(r,[n],{'width':360,'height':height})
 with pytest.raises(Exception,match='KB_INVALID_PLACEMENT'):
  with r.cursor() as c:c.execute('SELECT public.kb_canvas_command(%s,%s,%s)',(uuid4(),'update_canvas_data',Jsonb(p,dumps=lambda v:json.dumps(v,default=str))))
 assert r.canvas()==before

@pytest.mark.parametrize('guard',['permission','version'])
def test_resize_keeps_permission_and_version_checks(resize_db,guard):
 make,_=resize_db;r=make();n=create(r);before=r.canvas();p=payload(r,[n],{'width':360,'height':240})
 if guard=='version':p['expected_container_revision']-=1
 with pytest.raises(Exception,match='KB_'):(make(1) if guard=='permission' else r).command(command('update_canvas_data',p))
 assert r.canvas()==before
