"""Attachment removal clears only its live media in the same guarded transaction."""
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from tests.test_knowledge_topic_resize import resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db

@pytest.fixture(scope='module')
def cleanup_db(resize_db):
 make,actors=resize_db
 with psycopg.connect(**make.admin_args) as c:c.execute((Path(__file__).resolve().parents[1]/'migrations/330_knowledge_attachment_media_cleanup.sql').read_text())
 return make,actors

def upload(r,oid,mime,name):
 return r.file_command(uuid4(),'file_upload',{'object_id':str(oid),'version_id':str(uuid4()),'name':name,'mime_type':mime,'size':10,'sha256':'a'*64,'text_status':'unsupported','extracted_text':''})['binding_id']

def media(r,n):
 pic=upload(r,n['id'],'image/png','图.png');video=upload(r,n['id'],'video/mp4','视频.mp4')
 data={'note':'保留说明','image':{'binding_id':pic,'revision':1,'width':200,'placement':'bottom','aspect':1,'alt':'图片'},'video':{'binding_id':video,'revision':1,'title':'视频'}}
 r.command(command('update_canvas_data',payload(r,[current(r,n['id'])],data)))
 return pic,video,current(r,n['id'])

@pytest.mark.parametrize('kind',['image','video'])
def test_remove_clears_only_matching_media_and_keeps_reference_history(cleanup_db,kind):
 r=cleanup_db[0]();n=create(r);pic,video,before=media(r,n);bid=pic if kind=='image' else video;target=create(r)
 ref=r.file_command(uuid4(),'file_reference',{'object_id':str(target['id']),'source_object_id':str(n['id']),'source_binding_id':bid,'source_revision':1})
 key=uuid4();p={'object_id':str(n['id']),'binding_id':bid,'expected_revision':1};r.file_command(key,'file_remove',p);after=current(r,n['id'])
 assert kind not in after['canvas_data'];assert after['canvas_data']['note']=='保留说明';other='video' if kind=='image' else 'image';assert after['canvas_data'][other]==before['canvas_data'][other]
 assert after['layout_revision']==before['layout_revision']+1
 head=r.canvas_history()['undo'];assert head is None or not head['can_apply']
 assert r.file_command(key,'file_remove',p)['status']=='replayed';assert current(r,n['id'])['layout_revision']==after['layout_revision']
 assert next(f for f in r.files(target['id']) if str(f['id'])==ref['binding_id'])['version']['revision']==1
 assert not any(str(f['id'])==bid for f in r.files(n['id']))

@pytest.mark.parametrize('guard',['version','permission'])
def test_failed_removal_leaves_media_and_binding_untouched(cleanup_db,guard):
 make,_=cleanup_db;r=make();n=create(r);pic,_,before=media(r,n);p={'object_id':str(n['id']),'binding_id':pic,'expected_revision':2 if guard=='version' else 1}
 with pytest.raises(Exception,match='KB_'):(make(1) if guard=='permission' else r).file_command(uuid4(),'file_remove',p)
 assert current(r,n['id'])==before;assert any(str(f['id'])==pic for f in r.files(n['id']))
