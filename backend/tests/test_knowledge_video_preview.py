"""Bounded low-bandwidth playback derivatives never replace the authorized original."""
import io
import hashlib
import shutil
import subprocess
from pathlib import Path
import pytest
from tests.test_knowledge_files import file_client,uploaded
from tests.test_knowledge_workspace_v2 import local_db,document,command
from services.knowledge_workspace import video_preview,media_delivery

@pytest.fixture
def real_video(tmp_path):
 if not shutil.which('ffmpeg'):pytest.skip('ffmpeg unavailable')
 path=tmp_path/'test.mp4'
 subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i','testsrc2=size=1280x720:rate=30','-t','2','-c:v','libx264','-b:v','7M','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],check=True,timeout=20)
 return path.read_bytes()


def test_preview_range_original_preservation_cache_and_revocation(file_client,local_db,real_video,monkeypatch):
 client,ctx,settings,r,_=file_client;_,actors=local_db;obj=document(r);bid=uploaded(client,obj['id'],real_video,'高清验收.mp4')['binding_id']
 base=f'/api/knowledge/objects/{obj["id"]}/files/{bid}/versions/1'
 r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}));ctx.user_id=actors[1]
 lease=client.post(base+'/playback?quality=preview');assert lease.status_code==200,lease.text
 url=lease.json()['data']['url'];assert url.endswith('/stream?quality=preview')
 full=client.get(url);assert full.status_code==200
 assert len(full.content)<len(real_video) and full.content!=real_video
 assert client.get(base+'/content').content==real_video
 part=client.get(url,headers={'Range':'bytes=20-99'});assert part.status_code==206 and part.content==full.content[20:100]
 assert part.headers['content-range']==f'bytes 20-99/{len(full.content)}'
 assert client.head(url).headers['content-length']==str(len(full.content))
 assert client.get(url,headers={'Range':f'bytes={len(full.content)}-'}).status_code==416
 original=client.post(base+'/playback?quality=original').json()['data']['url'];assert client.get(original).content==real_video
 assert client.get(original).headers['etag']!=full.headers['etag']
 monkeypatch.setattr(video_preview,'run',lambda *a,**k:pytest.fail('cached preview must not encode again'))
 assert client.get(url).content==full.content
 r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}))
 assert client.get(url).status_code==404
 assert client.post(base+'/playback?quality=preview').status_code==404
 ctx.user_id=actors[0];assert client.post(base+'/playback?quality=original').status_code==200
 from services.knowledge_workspace.files import private_path
 version=r.file_version(obj['id'],bid,1);source=private_path(settings.file_workspace_root,version['storage_org'],version['storage_owner'],version['version_id']);source.chmod(0o600);source.write_bytes(b'x'*len(real_video))
 assert client.get(url).status_code==503


def test_revocation_during_preparation_does_not_issue_playback_lease(file_client,local_db,monkeypatch):
 client,ctx,_,r,_=file_client;_,actors=local_db;obj=document(r);bid=uploaded(client,obj['id'],b'\x00\x00\x00\x18ftypmp42'+b'x'*20,'clip.mp4')['binding_id']
 r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}));ctx.user_id=actors[1]
 def prepare(*args):
  r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}));return {'data':b'x'}
 monkeypatch.setattr(video_preview,'prepare',prepare)
 response=client.post(f'/api/knowledge/objects/{obj["id"]}/files/{bid}/versions/1/playback?quality=preview');assert response.status_code==404 and 'set-cookie' not in response.headers


def test_encoder_failure_releases_capacity_and_original_still_works(file_client,monkeypatch):
 client,_,_,r,_=file_client;obj=document(r);bid=uploaded(client,obj['id'],b'\x00\x00\x00\x18ftypmp42'+b'x'*20,'broken.mp4')['binding_id'];base=f'/api/knowledge/objects/{obj["id"]}/files/{bid}/versions/1'
 monkeypatch.setattr(video_preview,'run',lambda *a,**k:(_ for _ in ()).throw(subprocess.TimeoutExpired('ffmpeg',35)))
 assert client.post(base+'/playback?quality=preview').status_code==503
 assert video_preview._encoder.acquire(blocking=False);video_preview._encoder.release()
 assert client.post(base+'/playback?quality=original').status_code==200


def test_capacity_and_cache_bounds(file_client,monkeypatch):
 client,_,settings,r,_=file_client;obj=document(r);body=b'\x00\x00\x00\x18ftypmp42'+b'x'*20;bid=uploaded(client,obj['id'],body,'low.mp4')['binding_id'];version=r.file_version(obj['id'],bid,1)
 monkeypatch.setattr(video_preview,'_cache',video_preview.OrderedDict())
 video_preview._encoder.acquire()
 try:
  response=client.post(f'/api/knowledge/objects/{obj["id"]}/files/{bid}/versions/1/playback?quality=preview');assert response.status_code==429
 finally:video_preview._encoder.release()
 monkeypatch.setattr(video_preview.shutil,'which',lambda *args:'/synthetic/decoder')
 monkeypatch.setattr(video_preview,'probe',lambda p:{'format':{'duration':10},'streams':[{'codec_type':'video','codec_name':'h264','width':640,'height':360}]})
 for i in range(20):
  synthetic={**version,'sha256':str(i)}
  monkeypatch.setattr(media_delivery,'verified_file',lambda *args:io.BytesIO(body))
  assert video_preview.prepare(settings.file_workspace_root,synthetic)['data'] is None
 assert len(video_preview._cache)==16
