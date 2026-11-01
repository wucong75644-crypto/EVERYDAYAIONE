"""Single actual SDK create boundary; no provider requests or billing."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.adapters.kie.video_adapter import KieVideoAdapter,KieVideoSubmissionUncertainError


def case():
    client=SimpleNamespace(create_task_once=AsyncMock(return_value=SimpleNamespace(is_success=True,task_id='native-video-1')),
        create_task=AsyncMock(return_value=SimpleNamespace(task_id='legacy-video')),create_and_wait=AsyncMock())
    return KieVideoAdapter(client,'sora-2-text-to-video'),client


@pytest.mark.asyncio
async def test_single_submit_records_dispatch_and_acceptance_before_return():
    adapter,client=case();events=[]
    async def observe(phase,**facts):events.append((phase,facts))
    result=await adapter.generate(prompt='原始视频提示词',wait_for_result=False,_chat_video_single_submit=True,_native_video_observer=observe)
    assert result.task_id=='native-video-1' and result.video_url is None
    assert [v[0] for v in events]==['dispatch','accepted']
    assert events[0][1]['request']==events[1][1]['request'] and events[1][1]['external_task_id']=='native-video-1'
    client.create_task_once.assert_awaited_once();client.create_task.assert_not_awaited();client.create_and_wait.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['dispatch','accepted'])
async def test_unconfirmed_native_marker_or_ack_never_submits_again(phase):
    adapter,client=case()
    async def observe(current,**facts):
        if current==phase:raise RuntimeError('native checkpoint unconfirmed')
    with pytest.raises(RuntimeError,match='checkpoint unconfirmed'):
        await adapter.generate(prompt='原始视频提示词',wait_for_result=False,_chat_video_single_submit=True,_native_video_observer=observe)
    assert client.create_task_once.await_count==(0 if phase=='dispatch' else 1)
    client.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_unknown_preserves_type_and_never_enters_create_retry_wrapper():
    adapter,client=case();client.create_task_once.side_effect=httpx.ReadTimeout('acknowledgement lost')
    with pytest.raises(httpx.ReadTimeout):await adapter.generate(prompt='原始视频提示词',wait_for_result=False,_chat_video_single_submit=True)
    client.create_task_once.assert_awaited_once();client.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_accepted_id_is_uncertain_not_empty_success():
    adapter,client=case();client.create_task_once.return_value=SimpleNamespace(is_success=True,task_id='')
    with pytest.raises(KieVideoSubmissionUncertainError):await adapter.generate(prompt='原始视频提示词',wait_for_result=False,_chat_video_single_submit=True)
    client.create_task_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_managed_synchronous_mode_rejected_before_provider_io():
    adapter,client=case()
    with pytest.raises(ValueError):await adapter.generate(prompt='原始视频提示词',wait_for_result=True,_chat_video_single_submit=True)
    client.create_task_once.assert_not_awaited();client.create_task.assert_not_awaited();client.create_and_wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_unmanaged_video_create_path_remains_available():
    adapter,client=case();result=await adapter.generate(prompt='原始视频提示词',wait_for_result=False)
    assert result.task_id=='legacy-video';client.create_task.assert_awaited_once();client.create_task_once.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['duration','model'])
async def test_frozen_request_cannot_change_model_or_priced_duration_before_dispatch(change):
    from services.adapters.kie.models import CreateTaskRequest
    adapter,client=case()
    request=CreateTaskRequest(model='other-model' if change=='model' else adapter.model_id,
        input={'prompt':'原提示词','n_frames':'15' if change=='duration' else '10','aspect_ratio':'landscape','remove_watermark':True})
    observer=AsyncMock()
    with pytest.raises(ValueError):await adapter.submit_prepared_once(request,10,observer=observer)
    client.create_task_once.assert_not_awaited();observer.assert_not_awaited()
