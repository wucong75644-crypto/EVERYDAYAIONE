"""All file consumers resolve the same authorized recognition result."""
import asyncio
from uuid import uuid4
from psycopg.types.json import Jsonb
from services.knowledge_workspace.extraction import media_config,resolve
from tests.test_knowledge_derivation import derived_db,isolated_model_configuration
from tests.test_knowledge_attachment_cleanup import cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db


def test_empty_invalid_and_partial_media_never_claim_complete_text():
    base={'text_status':'unsupported','extracted_text':None}
    assert resolve(base,{'text':'','coverage':'complete'})==base
    assert resolve(base,{'text':'x','coverage':'invalid'})==base
    assert resolve(base,{'text':'识别到一部分','coverage':'partial'})=={'text_status':'truncated','extracted_text':'识别到一部分','media_coverage':'partial'}
    ready={'text_status':'ready','extracted_text':'内置正文'}
    assert resolve(ready,{'text':'覆盖正文','coverage':'complete'})==ready


def test_cached_ocr_resolves_list_history_canvas_and_chat_without_mutating_file(derived_db,monkeypatch):
    from core.config import get_settings
    from services.knowledge_workspace import media
    from tests.test_knowledge_chat_v3 import start,ref
    settings=get_settings();monkeypatch.setattr(settings,'knowledge_media_enabled',True)
    r=derived_db[0]();n=create(r)
    uploaded=r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'version_id':str(uuid4()),'name':'截图.png',
        'mime_type':'image/png','size':10,'sha256':'c'*64,'text_status':'unsupported','extracted_text':None})
    binding=uploaded['binding_id']
    with r.cursor() as c:c.execute('SELECT public.kb_save_media_result(%s,%s,1,%s,%s)',(n['id'],binding,media_config(),Jsonb({'text':'尺寸75*140mm','coverage':'complete'})))
    assert r.files(n['id'])[0]['version']['text_status']=='ready'
    assert r.file_versions(n['id'],binding)['items'][0]['text_status']=='ready'
    assert r.file_version(n['id'],binding,1)['text_status']=='ready'
    node=next(v for v in r.canvas()['nodes'] if v['id']==n['id'])
    assert node['attachments']['items'][0]['version']['text_status']=='ready'
    run,_,_=start(derived_db[0],r,[ref(n)])
    file=next(i for i in run.snapshot['items'] if i['type']=='file' and i['id']==str(n['id']))
    assert file['text_status']=='ready'
    with r.cursor() as c:
        assert run._text(c,file)=='尺寸75*140mm'
        c.execute('SELECT public.kb_chat_file(%s,%s,1) AS v',(n['id'],binding))
        assert c.fetchone()['v']['text_status']=='unsupported'
    assert asyncio.run(media.extract(r,n['id'],binding,1))['text']=='尺寸75*140mm'
    monkeypatch.setattr(settings,'knowledge_vision_model','different-config')
    assert r.file_version(n['id'],binding,1)['text_status']=='unsupported'
    assert derived_db[0](1).file_version(n['id'],binding,1) is None


def test_partial_recognition_and_new_revision_do_not_inherit_a_complete_badge(derived_db,monkeypatch):
    from core.config import get_settings
    settings=get_settings();monkeypatch.setattr(settings,'knowledge_media_enabled',True)
    r=derived_db[0]();n=create(r)
    first=r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'version_id':str(uuid4()),'name':'视频.mp4',
        'mime_type':'video/mp4','size':12,'sha256':'d'*64,'text_status':'unsupported','extracted_text':None})
    binding=first['binding_id']
    with r.cursor() as c:c.execute('SELECT public.kb_save_media_result(%s,%s,1,%s,%s)',(n['id'],binding,media_config(),Jsonb({'text':'抽帧与语音片段','coverage':'partial'})))
    assert r.files(n['id'])[0]['version']['text_status']=='truncated'
    assert r.file_versions(n['id'],binding)['items'][0]['text_status']=='truncated'
    r.file_command(uuid4(),'file_upload',{'object_id':str(n['id']),'binding_id':str(binding),'expected_revision':1,
        'version_id':str(uuid4()),'name':'新版视频.mp4','mime_type':'video/mp4','size':14,'sha256':'e'*64,'text_status':'unsupported','extracted_text':None})
    assert r.files(n['id'])[0]['version']['text_status']=='unsupported'
    assert [v['text_status'] for v in r.file_versions(n['id'],binding)['items']]==['unsupported','truncated']
    monkeypatch.setattr(settings,'knowledge_media_enabled',False)
    assert r.file_version(n['id'],binding,1)['text_status']=='unsupported'
