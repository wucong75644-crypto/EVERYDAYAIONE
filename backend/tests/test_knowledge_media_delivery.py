"""Immutable thumbnail and native Range playback retain live canonical ACL checks."""
import io
from types import SimpleNamespace
import pytest
from PIL import Image
from tests.test_knowledge_files import file_client, uploaded
from tests.test_knowledge_workspace_v2 import local_db,document,command
from services.knowledge_workspace import media_delivery as media


def base(oid,bid):return f'/api/knowledge/objects/{oid}/files/{bid}/versions/1'
def mp4():return b'\x00\x00\x00\x18ftypmp42'+b'\x00'*4+b'mp42isom'+bytes(range(256))*16


def test_range_playback_live_acl_and_integrity(file_client,local_db,monkeypatch):
    client,ctx,settings,r,make=file_client;_,actors=local_db
    obj=document(r);oid=obj['id'];data=mp4();bid=uploaded(client,oid,data,'验收.mp4')['binding_id'];path=base(oid,bid)
    assert client.get(path+'/stream').status_code==401
    r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}));ctx.user_id=actors[1]
    lease=client.post(path+'/playback');assert lease.status_code==200,lease.text
    assert 'HttpOnly' in lease.headers['set-cookie'] and 'SameSite=strict' in lease.headers['set-cookie']
    stream=lease.json()['data']['url'];assert stream==path+'/stream' and '?' not in stream
    from services.knowledge_workspace import files
    monkeypatch.setattr(files,'read_version',lambda *a:pytest.fail('stream must not materialize the whole Blob'))
    for value,start,end in [('bytes=0-9',0,9),('bytes=24-',24,len(data)-1),('bytes=-12',len(data)-12,len(data)-1)]:
        response=client.get(stream,headers={'Range':value});assert response.status_code==206,response.text
        assert response.content==data[start:end+1]
        assert response.headers['content-range']==f'bytes {start}-{end}/{len(data)}'
    head=client.head(stream,headers={'Range':'bytes=0-9'});assert head.status_code==206 and not head.content and head.headers['content-length']=='10'
    assert client.get(stream,headers={'Range':'bytes=999999-'}).status_code==416
    assert client.get(stream,headers={'Range':'bytes=0-2,4-6'}).status_code==416
    assert client.get(stream,headers={'Range':'bytes=0-9','If-Range':'"different"'}).content==data
    # Cookie replay cannot change resource/version, and grant revocation beats a valid lease.
    token=next(c.value for c in client.cookies.jar if c.name=='kb_media')
    other=client.get(stream.replace('/versions/1/','/versions/2/'),headers={'Cookie':'kb_media='+token});assert other.status_code==401
    r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}))
    assert client.get(stream).status_code==404
    ctx.user_id=actors[0];assert client.post(path+'/playback').status_code==200
    settings.knowledge_workspace_enabled=False;assert client.get(stream).status_code==404;settings.knowledge_workspace_enabled=True
    version=r.file_version(oid,bid,1);target=files.private_path(settings.file_workspace_root,version['storage_org'],version['storage_owner'],version['version_id']);target.chmod(0o600);target.write_bytes(b'x'*len(data))
    assert client.get(stream).status_code==503


def test_thumbnail_size_conditional_cache_and_revocation(file_client,local_db):
    client,ctx,_,r,_=file_client;_,actors=local_db;obj=document(r)
    out=io.BytesIO();Image.new('RGB',(1600,900),'red').save(out,format='PNG')
    bid=uploaded(client,obj['id'],out.getvalue(),'原图.png')['binding_id'];path=base(obj['id'],bid)+'/thumbnail'
    r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'active'}));ctx.user_id=actors[1]
    response=client.get(path);assert response.status_code==200,response.text
    with Image.open(io.BytesIO(response.content)) as image:assert image.width==512 and image.height==288
    assert response.headers['cache-control']=='private, max-age=0, must-revalidate'
    headers={'If-None-Match':response.headers['etag']}
    assert client.get(path,headers=headers).status_code==304
    r.command(command('set_member',{'container_id':obj['container_id'],'user_id':actors[1],'role':'viewer','status':'removed'}))
    denied=client.get(path,headers=headers);assert denied.status_code==404 and denied.headers['cache-control']=='no-store'


@pytest.mark.parametrize('header,expected',[('bytes=0-0',(0,0)),('bytes=1-99',(1,9)),('bytes=-99',(0,9)),('bytes=9-',(9,9)),(None,(0,9))])
def test_ranges(header,expected):assert media.byte_range(header,10)==expected

@pytest.mark.parametrize('header',['items=0-1','bytes=10-','bytes=-0','bytes=5-4','bytes=0-1,2-3','bytes=-','garbage'])
def test_bad_ranges(header):
    with pytest.raises(ValueError):media.byte_range(header,10)


def test_lease_expiration_tampering_and_scope(monkeypatch):
    token=media.lease('synthetic',SimpleNamespace(user_id='u',org_id='o'),'node','file',1)
    assert media.read_lease('synthetic',token,'node','file',1)==('u','o')
    from core.exceptions import AppException as FileTargetError
    for candidate,node,rev in [(token+'x','node',1),(token,'other',1),(token,'node',2),('garbage','node',1)]:
        with pytest.raises(FileTargetError):media.read_lease('synthetic',candidate,node,'file',rev)
    monkeypatch.setattr(media.time,'time',lambda:10**12)
    with pytest.raises(FileTargetError):media.read_lease('synthetic',token,'node','file',1)


def test_partial_generator_releases_file_on_cancel():
    data=io.BytesIO(b'x'*600000);iterator=media.chunks(data,0,599999)
    assert len(next(iterator))==256*1024;iterator.close();assert data.closed
