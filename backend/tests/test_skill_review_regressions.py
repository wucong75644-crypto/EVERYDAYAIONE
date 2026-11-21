"""Cross-boundary review: authority order, HTTP replay and explicit pins."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.deps import OrgContext
from api.routes import message as route
from core.exceptions import AppException, NotFoundError
from schemas.message import GenerateRequest, GenerationType, TextPart
from tests.test_message_routes import _make_request
from tests.test_skill_manual_selection import SELECTION


@pytest.fixture
def http_boundary(monkeypatch):
    monkeypatch.setattr(route, 'load_control_tasks', lambda *a, **kw: SimpleNamespace(running=None, paused=None))
    monkeypatch.setattr(route, 'resolve_generation_context', AsyncMock(return_value=GenerationType.CHAT))
    monkeypatch.setattr(route, 'get_handler', Mock(return_value=SimpleNamespace(org_id=None)))
    conversation = AsyncMock(return_value={'id': 'c1'})
    monkeypatch.setattr(route, 'get_conversation_service', Mock(return_value=SimpleNamespace(get_conversation=conversation)))
    create = AsyncMock(side_effect=AssertionError('no message should be written'))
    monkeypatch.setattr(route, 'create_user_message', create)
    skill = AsyncMock(side_effect=HTTPException(409, '所选 Skill 版本已变化'))
    monkeypatch.setattr('services.skills.retry.capture_intent', skill)
    return SimpleNamespace(conversation=conversation, create=create, skill=skill)


def body():
    return GenerateRequest(content=[TextPart(text='hello')], generation_type=GenerationType.CHAT,
                           selected_skill=SELECTION)


async def test_conversation_authority_precedes_skill_resolution(http_boundary):
    env = http_boundary
    env.conversation.side_effect = NotFoundError('对话', 'c1')
    with pytest.raises(AppException) as caught:
        await route._do_generate_message(_make_request(), 'c1', body(), OrgContext(user_id='u1'), Mock(), 'u1')
    assert caught.value.status_code == 404
    env.skill.assert_not_awaited()
    env.create.assert_not_awaited()


async def test_skill_rejection_preserves_409_in_idempotency_and_releases_slot(http_boundary, monkeypatch):
    env = http_boundary
    claim = SimpleNamespace(replay_response=None)
    service = Mock()
    service.claim.return_value = claim
    monkeypatch.setattr(route, 'MessageIdempotencyService', Mock(return_value=service))
    slots = SimpleNamespace(check_and_acquire=AsyncMock(return_value='slot'), release=AsyncMock())
    with pytest.raises(AppException) as caught:
        await route.generate_message(_make_request(), 'c1', body(), OrgContext(user_id='u1'), Mock(), slots)
    assert caught.value.status_code == 409
    service.fail.assert_called_once_with(claim, caught.value)
    service.fail_unexpected.assert_not_called()
    service.complete.assert_not_called()
    slots.release.assert_awaited_once_with('u1', 'c1', slot_id='slot', org_id=None)
    env.create.assert_not_awaited()


@pytest.mark.parametrize('generation', list(GenerationType))
@pytest.mark.parametrize('operation', ['send', 'retry', 'regenerate', 'regenerate_single'])
async def test_preparation_hook_preserves_preflight_and_operation_semantics(monkeypatch, generation, operation):
    from api.routes.message_request_preparation import prepare_generation_request
    from schemas.message import MessageOperation
    trace = []
    conversation = {'id': 'c1'}
    message = SimpleNamespace(id='input')
    async def authorize(*args):
        trace.append('authorize')
        return conversation
    async def validate():
        trace.append('skill')
    async def create(**kwargs):
        trace.append('write')
        assert kwargs['turn_id'] == 'turn'
        return message
    h = SimpleNamespace(preflight=lambda *args: trace.append('credits'))
    monkeypatch.setattr('api.routes.message_request_preparation.record_user_activity', Mock())
    request = GenerateRequest(content=[TextPart(text='hello')], operation=MessageOperation(operation),
                              generation_type=generation)
    result = await prepare_generation_request(Mock(), 'c1', request, generation, 'u', None, h,
        SimpleNamespace(get_conversation=authorize), create, turn_id='turn', before_message=validate)
    expected = ['authorize', *(['credits'] if generation == GenerationType.IMAGE else []), 'skill']
    writes = operation not in ('retry', 'regenerate_single')
    assert trace == expected + (['write'] if writes else [])
    assert result == (h, conversation, message if writes else None)


async def test_cancelled_skill_preparation_never_writes_a_message():
    import asyncio
    from api.routes.message_request_preparation import prepare_generation_request
    create = AsyncMock()
    validate = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await prepare_generation_request(Mock(), 'c1', body(), GenerationType.CHAT, 'u', None, Mock(),
            SimpleNamespace(get_conversation=AsyncMock(return_value={'id': 'c1'})), create,
            before_message=validate)
    create.assert_not_awaited()


async def test_cancelled_authority_read_does_not_dispatch_after_thread_finishes():
    import asyncio
    import threading
    from services.permissions.checker import PermissionChecker
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    checker = PermissionChecker(Mock())
    dispatch = AsyncMock()
    def load(*args):
        started.set()
        try:
            assert release.wait(2)
            return {'position_code': 'boss'}
        finally:
            finished.set()
    checker._load_assignment = load
    async def authorize_and_dispatch():
        if 'order.view' in await checker.check_many('u', 'o', ['order.view']):
            await dispatch()
    task = asyncio.create_task(authorize_and_dispatch())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        dispatch.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
