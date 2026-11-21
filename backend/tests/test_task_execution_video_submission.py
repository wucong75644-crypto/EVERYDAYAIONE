"""Single actual SDK create boundary; no provider requests or billing."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.adapters.kie.video_adapter import KieVideoAdapter,KieVideoSubmissionUncertainError


def case(model='sora-2-text-to-video'):
    client=SimpleNamespace(create_task_once=AsyncMock(return_value=SimpleNamespace(is_success=True,task_id='native-video-1')),
        create_task=AsyncMock(return_value=SimpleNamespace(task_id='legacy-video')),create_and_wait=AsyncMock())
    return KieVideoAdapter(client,model),client


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


@pytest.mark.asyncio
@pytest.mark.parametrize('aspect_ratio,provider_ratio', [('landscape', '16:9'), ('portrait', '9:16')])
async def test_kling_chat_video_submits_exactly_once_with_its_own_format(aspect_ratio, provider_ratio):
    from config.kie_models import KLING_TEXT_VIDEO, calculate_video_cost
    from services.task_execution.video_operations import CHAT_VIDEO_MODEL_ID

    adapter, client = case(KLING_TEXT_VIDEO)
    observer = AsyncMock()
    result = await adapter.generate(prompt='原视频脚本', duration_seconds=10, aspect_ratio=aspect_ratio,
        wait_for_result=False, _chat_video_single_submit=True, _native_video_observer=observer)
    assert result.task_id == 'native-video-1'
    request, = client.create_task_once.await_args.args
    assert request.model == CHAT_VIDEO_MODEL_ID
    assert request.input == {'prompt': '原视频脚本', 'duration': '10',
                             'aspect_ratio': provider_ratio, 'sound': False}
    assert calculate_video_cost(request.model, 10)['user_credits'] == 127
    assert observer.await_count == 2
    client.create_task_once.assert_awaited_once()
    client.create_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('delivery_fails', [False, True])
async def test_native_video_acceptance_delivers_persisted_placeholder(monkeypatch, delivery_fails):
    from unittest.mock import MagicMock
    from uuid import uuid4

    from services.task_execution import video_operations
    from services.websocket_manager import ws_manager

    parent_id, child_id, message_id, actor_id, conversation_id = (str(uuid4()) for _ in range(5))
    parent = {'id': parent_id, 'user_id': actor_id, 'conversation_id': conversation_id}
    placeholder = {
        'id': message_id, 'conversation_id': conversation_id, 'role': 'assistant', 'status': 'pending',
        'content': '[{"type":"text","text":"视频任务排队中"}]',
        'generation_params': {'origin': 'agent_video', 'type': 'video', 'task_id': child_id},
    }
    receipt = {'outcome': 'accepted', 'task_id': child_id, 'message_id': message_id, 'submission_state': 'queued'}
    db = MagicMock()
    for table, row in [('tasks', parent), ('messages', placeholder)]:
        query = MagicMock()
        query.select.return_value.eq.return_value.single.return_value.execute.return_value.data = row
        if table == 'tasks':
            task_query = query
        else:
            message_query = query
    db.table.side_effect = {'tasks': task_query, 'messages': message_query}.__getitem__
    db.rpc.return_value.execute.return_value.data = receipt
    monkeypatch.setattr(video_operations, 'frozen_request', lambda *_: {})
    send = AsyncMock(side_effect=OSError('disconnected') if delivery_fails else None)
    monkeypatch.setattr(ws_manager, 'send_to_task_or_user', send)
    owner = SimpleNamespace(db=db, _task_id=parent_id, image_execution_token=str(uuid4()), org_id=None)

    assert await video_operations.accept(owner, {'prompt': '第四条视频脚本'}) == receipt
    db.rpc.assert_called_once()
    send.assert_awaited_once()
    task_id, user_id, event = send.await_args.args
    assert (task_id, user_id) == (child_id, actor_id)
    assert event['type'] == 'message_pending'
    assert event['message_id'] == message_id
    assert event['payload']['message']['content'] == [{'type': 'text', 'text': '视频任务排队中'}]
