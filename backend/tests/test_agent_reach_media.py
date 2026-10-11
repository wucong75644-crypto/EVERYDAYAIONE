"""Publishing uses signed workspace references and never arbitrary URLs/paths."""
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
from pydantic import ValidationError

from services.agent.agent_reach.contracts import ReachError, ReachMedia, ReachRequest
from services.agent.agent_reach.media import media_snapshot
from services.agent.agent_reach.video_upload import youtube_upload
from services.file_executor import FileExecutor
from services.file_resources import FileReferenceCodec, file_version


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(
        file_workspace_root=str(tmp_path / 'workspace'), jwt_secret_key='media-tests-only'))
    owner = SimpleNamespace(user_id='user', workspace_user_id='user',org_id='org',context_scope='user',execution_mode='interactive')
    files = FileExecutor(str(tmp_path / 'workspace'), 'user', 'org')
    image = Path(files.workspace_root) / 'photo.png'
    Image.new('RGB',(2,2),'red').save(image)
    reference = FileReferenceCodec(org_id='org',owner_id='user').issue('photo.png',file_version(image))
    selected = ReachMedia(name='photo.png',resource_ref=reference,role='image')
    return owner, image, selected, str(tmp_path / 'staging')


@pytest.mark.asyncio
async def test_snapshot_copies_authorized_bytes_and_cleans_up(workspace):
    owner, image, selected, directory = workspace
    expected = image.read_bytes()
    async with media_snapshot(owner,[selected],directory) as items:
        snapshot = Path(items[0]['path'])
        assert snapshot.read_bytes() == expected
        assert items[0]['mime_type'] == 'image/png'
        assert items[0]['sha256']
        image.write_bytes(b'changed')
        assert snapshot.read_bytes() == expected
    assert not snapshot.exists()


@pytest.mark.asyncio
async def test_other_owner_signature_rejected(workspace):
    owner, image, selected, directory = workspace
    owner.org_id = 'other-org'
    with pytest.raises(ReachError) as error:
        async with media_snapshot(owner,[selected],directory):
            pytest.fail('foreign file must not be available')
    assert error.value.code == 'ACCESS_DENIED'


@pytest.mark.asyncio
async def test_changed_media_after_selection_rejected(workspace):
    owner, image, selected, directory = workspace
    image.write_bytes(b'changed')
    with pytest.raises(ReachError):
        async with media_snapshot(owner,[selected],directory):
            pytest.fail('old confirmation must not authorize changed media')


@pytest.mark.parametrize('value', ['https://example.com/picture.jpg','/etc/passwd','../photo.png'])
def test_raw_media_locators_rejected(value):
    with pytest.raises(ValidationError):
        ReachMedia(name='photo.png',resource_ref=value,role='image')


def video_request(**changes):
    data = dict(task='上传', platform='youtube',action='upload_video',title='My video',content='description',
        connection_id='00000000-0000-0000-0000-000000000001',category_id=22,visibility='public',
        media=[{'name':'clip.mp4','role':'video','resource_ref':'fref1_example'}])
    data.update(changes)
    return ReachRequest.model_validate(data)


def test_video_upload_needs_explicit_visibility_and_bili_cover():
    with pytest.raises(ValidationError):
        video_request(visibility=None)
    with pytest.raises(ValidationError):
        video_request(platform='bilibili')


@pytest.mark.asyncio
async def test_youtube_rejects_session_host_before_sending_video(tmp_path):
    calls = []
    class HTTP:
        async def get(self,*args,**kwargs):
            return b'{"items":[{"id":"channel"}]}'
        async def request(self,method,url,**kwargs):
            calls.append(method)
            return b'',{'location':'https://evil.example/upload'}
    with pytest.raises(ReachError) as error:
        await youtube_upload(video_request(),{'account_id':'channel','secret':{'access_token':'dummy'}},HTTP(),
            [{'path':str(tmp_path/'clip.mp4'),'role':'video','size':12,'mime_type':'video/mp4'}])
    assert error.value.code == 'WRITE_UNCERTAIN'
    assert calls == ['POST']


@pytest.mark.asyncio
async def test_youtube_upload_once_and_reports_actual_visibility(tmp_path):
    content = b'video-bytes'
    path = tmp_path/'clip.mp4';path.write_bytes(content)
    calls = []
    class HTTP:
        async def get(self,*args,**kwargs):
            return b'{"items":[{"id":"channel"}]}'
        async def request(self,method,url,**kwargs):
            calls.append(method)
            if method == 'POST':
                assert kwargs['json']['status']['privacyStatus'] == 'public'
                return b'',{'location':'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=example'}
            assert b''.join([chunk async for chunk in kwargs['content']]) == content
            return b'{"id":"abcdefghijk","status":{"privacyStatus":"private"}}'
    result = await youtube_upload(video_request(),{'account_id':'channel','secret':{'access_token':'dummy'}},HTTP(),
        [{'path':str(path),'role':'video','size':len(content),'mime_type':'video/mp4'}])
    assert calls == ['POST','PUT']
    assert result.status == 'partial'
    assert result.receipt['requested_visibility'] == 'public' and result.receipt['visibility'] == 'private'


@pytest.mark.asyncio
async def test_xhs_publish_uses_snapshot_paths_and_does_not_claim_online():
    from services.agent.agent_reach.xhs_adapter import XHSAdapter
    adapter = XHSAdapter('{"id":"http://127.0.0.1:18060"}', {'id':'id','account_id':'account','secret':{'service_token':'dummy'}})
    calls = []
    async def call(method,path,body=None):
        calls.append((path,body))
        return {'is_logged_in':True,'user_id':'account'} if path == 'login/status' else {'status':'发布完成'}
    adapter.call = call
    request = ReachRequest(task='发布',platform='xiaohongshu',action='create_post',title='标题',content='正文',
        visibility='private',connection_id='00000000-0000-0000-0000-000000000001',
        media=[{'name':'image.png','role':'image','resource_ref':'fref1_example'}])
    result = await adapter.write(request,media=[{'path':'/trusted/snapshot/image.png'}])
    assert calls[1][0] == 'publish'
    assert calls[1][1]['images'] == ['/trusted/snapshot/image.png']
    assert calls[1][1]['visibility'] == '仅自己可见'
    assert result.status == 'partial' and result.receipt['stage'] == 'backend_submitted'
