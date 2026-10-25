"""Project removal must be recoverable, scoped, manager-only and idempotent."""
from pathlib import Path
from uuid import uuid4
import asyncio
import psycopg
import pytest
from tests.test_knowledge_derivation import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db,upload,document,fake,process)

@pytest.fixture(scope='module')
def project_delete_db(derived_db):
 make,actors=derived_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/332_knowledge_project_delete.sql').read_text())
 return make,actors


def deletion(r,root):
 cid=root['container_id'];return command('delete_project',{'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid)})


def test_project_delete_preserves_sources_and_roundtrips_full_tree(project_delete_db):
 r=project_delete_db[0]();root=create(r);child=create(r,root['container_id'],root['id'],'人工资料');cid=root['container_id']
 r.command(command('save_document',{'object_id':child['id'],'expected_revision':1,'body':'保留人工正文'}));binding=upload(r,child)
 assert asyncio.run(process(r,child['id'],fake));assert asyncio.run(process(r,root['id'],fake))
 before=r.canvas();cmd=deletion(r,root);result=r.command(cmd);assert result['status']=='applied'
 assert not any(c['id']==cid for c in r.canvas()['containers']);assert not any(n['container_id']==cid for n in r.canvas()['nodes']);assert document(r,child['id']) is None
 from tests.test_knowledge_chat_v3 import start,ref
 from core.exceptions import AppException
 with pytest.raises(AppException):start(project_delete_db[0],r,[ref(root)])
 assert r.command(cmd)['status']=='replayed';head=r.canvas_history()['undo'];assert head['operation']=='delete_project' and head['can_apply']
 toggle(r);after=r.canvas();assert {n['id'] for n in after['nodes']}=={n['id'] for n in before['nodes']};assert any(c['id']==cid for c in after['containers'])
 assert r.object(child['id'])['body']=='保留人工正文';assert r.file_version(child['id'],binding['binding_id'],1)
 toggle(r,'redo');assert not any(c['id']==cid for c in r.canvas()['containers'])


def test_empty_project_is_removable_and_restorable(project_delete_db):
 r=project_delete_db[0]();root=create(r);cid=root['container_id']
 r.command(command('delete_branches',{'container_id':cid,'expected_container_revision':next(c['revision'] for c in r.canvas()['containers'] if c['id']==cid),'objects':[{'object_id':root['id'],'expected_revision':1}]}))
 r.command(deletion(r,root));assert not any(c['id']==cid for c in r.canvas()['containers']);toggle(r)
 assert any(c['id']==cid for c in r.canvas()['containers']);assert not any(n['container_id']==cid for n in r.canvas()['nodes'])


@pytest.mark.parametrize('guard',['editor','viewer','other_actor','other_org','stale','personal','team'])
def test_project_delete_rejects_unauthorized_and_system_spaces_atomically(project_delete_db,guard):
 make,actors=project_delete_db;r=make();root=create(r);cmd=deletion(r,root);actor=r
 if guard in ('editor','viewer'):
  r.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':guard,'status':'active'}));actor=make(1);cmd=deletion(r,root)
 elif guard=='other_actor':actor=make(1)
 elif guard=='other_org':actor=make(0,1)
 elif guard=='stale':cmd.payload.expected_container_revision-=1
 elif guard in ('personal','team'):
  r.initialize();space=next(c for c in r.canvas()['containers'] if c['kind']==guard);cmd=command('delete_project',{'container_id':space['id'],'expected_container_revision':space['revision']})
 before=r.canvas()
 with pytest.raises(psycopg.Error,match='KB_'):actor.command(cmd)
 assert r.canvas()==before


def test_project_delete_retry_conflict_and_permission_downgrade_block_restore(project_delete_db):
 make,actors=project_delete_db;owner=make();root=create(owner);cid=root['container_id'];owner.command(command('set_member',{'container_id':cid,'user_id':actors[1],'role':'manager','status':'active'}));manager=make(1)
 cmd=deletion(manager,root);manager.command(cmd)
 changed=cmd.model_copy(deep=True);changed.payload.expected_container_revision+=1
 with pytest.raises(psycopg.Error,match='KB_IDEMPOTENCY_PAYLOAD_CONFLICT'):manager.command(changed)
 # Simulate an administrative permission change while the project is hidden.
 with psycopg.connect(**make.admin_args) as c:
  c.execute("SET LOCAL ROLE everydayai_owner")
  c.execute("UPDATE public.kb_container_members SET role='editor' WHERE container_id=%s AND user_id=%s",(cid,actors[1]))
 assert not manager.canvas_history()['undo']['can_apply']
 with pytest.raises(psycopg.Error,match='KB_ACCESS_DENIED'):manager.command(command('undo_canvas',{'history_id':cmd.command_id}))
 assert not any(c['id']==cid for c in owner.canvas()['containers'])


def test_editor_can_still_undo_regular_node_edits(project_delete_db):
 make,actors=project_delete_db;owner=make();root=create(owner)
 owner.command(command('set_member',{'container_id':root['container_id'],'user_id':actors[1],'role':'editor','status':'active'}));editor=make(1)
 editor.command(command('update_title',{'object_id':root['id'],'expected_revision':1,'title':'编辑者改名'}));toggle(editor)
 assert editor.object(root['id'])['title']==root['title']


def test_project_delete_during_generation_prevents_stale_publication(project_delete_db):
 r=project_delete_db[0]();root=create(r);upload(r,root)
 async def remove(sources,title,path):
  r.command(deletion(r,root));return await fake(sources,title,path)
 assert asyncio.run(process(r,root['id'],remove)) is None
 assert document(r,root['id']) is None


def test_project_delete_does_not_grant_access_to_internal_restore_helpers(project_delete_db):
 r=project_delete_db[0]()
 with r.cursor() as c:
  for name in ['kb_canvas_restore_before_project_delete(uuid,text,jsonb)','kb_canvas_history_list_before_project_delete()']:
   c.execute("SELECT has_function_privilege(current_user,%s,'EXECUTE') AS allowed",('public.'+name,));assert not c.fetchone()['allowed']
