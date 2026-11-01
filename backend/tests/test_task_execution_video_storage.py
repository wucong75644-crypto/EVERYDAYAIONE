"""Owned byte recovery without any network or OSS account."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4

import pytest
from services.handlers.native_video_storage import persist,verify,object_key
from services.handlers.native_video_queue import NativeVideoQueue


def storage():
    task={'id':str(uuid4()),'user_id':str(uuid4()),'org_id':None};objects={}
    def put(key,data,headers):
        if key in objects:raise RuntimeError('no overwrite')
        objects[key]=(data,headers)
    def meta(key):
        data,headers=objects[key]
        return SimpleNamespace(headers=headers,etag=hashlib.md5(data).hexdigest(),content_length=len(data))
    bucket=SimpleNamespace(object_exists=Mock(side_effect=lambda key:key in objects),put_object=Mock(side_effect=put),get_object_meta=Mock(side_effect=meta))
    downloader=SimpleNamespace(download=AsyncMock(return_value=(b'\x00\x00\x00\x18ftypmp42'+b'video-data','video/mp4')))
    oss=SimpleNamespace(bucket=bucket,_downloader=downloader,MAX_VIDEO_SIZE=1024,get_url=lambda key:'https://owned.invalid/'+key)
    return task,oss,objects


@pytest.mark.asyncio
async def test_owned_byte_recovery_does_not_redownload_or_overwrite_after_lost_ack():
    task,oss,objects=storage();expected=await persist(oss,task,'https://provider.invalid/video')
    assert await persist(oss,task,'https://provider.invalid/expired')==expected
    assert await verify(oss,task,expected)==expected
    oss._downloader.download.assert_awaited_once();oss.bucket.put_object.assert_called_once()
    headers=objects[object_key(task)][1];headers['x-oss-meta-source-task']=str(uuid4())
    with pytest.raises(ValueError,match='UNCONFIRMED'):await verify(oss,task,expected)


@pytest.mark.asyncio
async def test_upload_return_lost_can_recover_only_the_original_owned_object():
    task,oss,objects=storage();put=oss.bucket.put_object.side_effect
    def lost(*args,**kwargs):put(*args,**kwargs);raise TimeoutError('ACK lost')
    oss.bucket.put_object.side_effect=lost
    with pytest.raises(TimeoutError):await persist(oss,task,'https://provider.invalid/video')
    recovered=await persist(oss,task,'https://provider.invalid/expired')
    assert recovered['content_sha256']==hashlib.sha256(objects[object_key(task)][0]).hexdigest()
    oss._downloader.download.assert_awaited_once();oss.bucket.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_invalid_bytes_never_publish_a_temporary_url():
    task,oss,_=storage();oss._downloader.download.return_value=(b'<html>error</html>','text/html')
    with pytest.raises(ValueError,match='MP4_BYTES_INVALID'):await persist(oss,task,'https://provider.invalid/video')
    oss.bucket.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_paused_gate_does_not_acquire_capacity_or_create_an_adapter(monkeypatch):
    from services.task_execution.video_operations import REQUEST,LIFECYCLE
    from services.adapters import factory
    create=Mock();monkeypatch.setattr(factory,'create_video_adapter',create)
    queue=NativeVideoQueue(None,SimpleNamespace())
    queue.rpc=AsyncMock(return_value={'outcome':'paused'})
    await queue.submit({'request_params':{REQUEST:{},LIFECYCLE:{'phase':'queued'}}})
    create.assert_not_called();assert queue.rpc.await_count==1
    await queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['lost_ack','paused_after_claim'])
async def test_worker_uses_single_sdk_boundary_and_preserves_paused_claim(monkeypatch,mode):
    import httpx
    from config.kie_models import calculate_video_cost
    from services.adapters.factory import DEFAULT_VIDEO_MODEL_ID
    from services.handlers.video_handler import VideoHandler
    from services.handlers.chat_image_request import canonical_hash
    from services.task_execution.video_operations import REQUEST,LIFECYCLE
    from tests.test_task_execution_video_submission import case
    import api.deps
    from services.adapters import factory
    cost=calculate_video_cost(model_name=DEFAULT_VIDEO_MODEL_ID,duration_seconds=10)
    snapshot={'model':DEFAULT_VIDEO_MODEL_ID,'prompt':'原视频提示词','duration_seconds':10,'aspect_ratio':'landscape','remove_watermark':True,
        'estimated_credits':cost['user_credits'],'estimated_provider_credits':cost['kie_cost']}
    snapshot['request_hash']=canonical_hash(snapshot)
    task={'id':str(uuid4()),'user_id':str(uuid4()),'org_id':None,'conversation_id':str(uuid4()),
        'request_params':{REQUEST:snapshot,LIFECYCLE:{'phase':'queued'}}}
    adapter,client=case();adapter.close=AsyncMock();client.create_task_once.side_effect=httpx.ReadTimeout('ACK lost')
    monkeypatch.setattr(factory,'create_video_adapter',lambda _:adapter)
    monkeypatch.setattr(VideoHandler,'_build_callback_url',lambda *_:'https://callback.invalid/?token=local')
    limiter=SimpleNamespace(acquire_image_slot=AsyncMock(),release=AsyncMock())
    monkeypatch.setattr(api.deps,'get_task_limit_service',AsyncMock(return_value=limiter))
    queue=NativeVideoQueue(None,SimpleNamespace(chat_image_uncertain_timeout_seconds=900))
    queue.refresh=AsyncMock(side_effect=lambda _:task)
    calls=[]
    async def rpc(task,name,**params):
        calls.append(name);state=task['request_params'][LIFECYCLE]
        if name=='agent_video_submission_gate':return {'outcome':'paused' if mode=='paused_after_claim' and state['phase']=='submitting' else 'ready'}
        if name=='claim_agent_video_submission':state.update(phase='submitting',claim_token=params['p_claim_token']);return {'outcome':'claimed'}
        if name=='mark_agent_video_dispatch':
            if mode=='paused_after_claim':return {'outcome':'not_active'}
            state['dispatch_started_at']='now';return {'outcome':'dispatch'}
        if name=='mark_agent_video_uncertain':state['phase']='uncertain';return {'outcome':'uncertain'}
        raise AssertionError(name)
    queue.rpc=rpc;queue.scoped=lambda _:None
    await queue.submit(task)
    adapter.close.assert_awaited_once();limiter.acquire_image_slot.assert_awaited_once()
    if mode=='lost_ack':
        client.create_task_once.assert_awaited_once();assert task['request_params'][LIFECYCLE]['phase']=='uncertain'
        limiter.release.assert_not_awaited()
    else:
        client.create_task_once.assert_not_awaited();assert task['request_params'][LIFECYCLE]['phase']=='submitting'
        limiter.release.assert_awaited_once()
    client.create_task.assert_not_awaited();assert 'fail_agent_video_before_dispatch' not in calls
    await queue.close()
