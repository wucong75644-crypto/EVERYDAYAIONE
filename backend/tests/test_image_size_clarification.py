"""Pixel precision is a semantic fact, never guessed from user keywords."""
from types import SimpleNamespace

import pytest

from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
from services.handlers.chat_image_request import ChatImageInputResolver
from services.handlers.image_size_requirements import ImageSizeError, resolve_size_requirement


def semantic_size(size, text='正方形，一千像素就好'):
    resolver = object.__new__(ChatImageInputResolver)
    resolver.input_message_id = 'current'
    resolver._message = lambda _: {'id': 'current', 'role': 'user', 'content': [{'type': 'text', 'text': text}]}
    return resolver.size_context(use_previous=False, semantic_context=ImageRequestContext(size=size))[0]


def test_live_failure_requires_clarification_instead_of_assuming_exact_pixels():
    with pytest.raises(ImageSizeError, match='IMAGE_SIZE_PRECISION_UNRESOLVED'):
        intent = semantic_size({'mode': 'explicit', 'aspect_ratio': '1:1', 'width': 1000, 'height': 1000,
                                'approximate_pixels': None})
        resolve_size_requirement({'prompt': '灯塔封面', 'mode': 'image_to_image'}, [], intent=intent)


@pytest.mark.parametrize('text', ['正方形，一千像素就好', '成品必须精确1000×1000，不能缩放'])
def test_user_words_cannot_override_the_structured_precision_decision(text):
    intent = semantic_size({'aspect_ratio': '1:1', 'resolution': '1K'}, text)
    args, _ = resolve_size_requirement({'prompt': '完整原稿', 'mode': 'text_to_image'}, [], intent=intent)
    assert (args['aspect_ratio'], args['resolution'], args['prompt']) == ('1:1', '1K', '完整原稿')


@pytest.mark.parametrize('approximate,code', [(False, 'IMAGE_EXACT_SIZE_UNSUPPORTED'), (None, 'IMAGE_SIZE_PRECISION_UNRESOLVED')])
def test_explicit_precision_is_distinct_from_unknown_without_automatic_rounding(approximate, code):
    with pytest.raises(ImageSizeError, match=code):
        resolve_size_requirement({'prompt': '完整原稿', 'mode': 'text_to_image'}, [],
            intent={'width': 1000, 'height': 1000, 'aspect_ratio': '1:1', 'approximate_pixels': approximate})


def test_approximate_pixels_keep_native_mapping_and_original_provenance():
    intent = semantic_size({'width': 1000, 'height': 1000, 'approximate_pixels': True})
    args, target = resolve_size_requirement({'prompt': '完整原稿', 'mode': 'text_to_image'}, [], intent=intent)
    assert (args['aspect_ratio'], args['resolution']) == ('1:1', '1K')
    assert target['approximate'] and target['requested_width'] == target['requested_height'] == 1000


def test_legacy_native_exact_pixel_requests_remain_blocked():
    with pytest.raises(ImageSizeError, match='IMAGE_EXACT_SIZE_UNSUPPORTED'):
        resolve_size_requirement({'prompt': '原稿', 'mode': 'text_to_image',
            'size_requirement': {'mode': 'explicit', 'width': 1000, 'height': 1000}}, [])


@pytest.mark.asyncio
@pytest.mark.parametrize('approximate,code', [(False, 'IMAGE_EXACT_SIZE_UNSUPPORTED'), (None, 'IMAGE_SIZE_PRECISION_UNRESOLVED')])
async def test_size_rejection_is_not_dispatched_and_user_sees_a_plain_question(monkeypatch, approximate, code):
    from unittest.mock import AsyncMock
    from services.handlers.chat import content_tools
    from services.task_execution import content_objects as co
    from services.tools.catalog import build_tool_catalog
    from tests.test_model_directed_loop import case, execute
    from tests.test_task_execution_model_bridge import call

    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    image = build_tool_catalog().require('generate_image').to_schema()
    offered, = content_tools.model_tools([image], inline_image=True)
    prepared.content_visible_tools = {'generate_image': offered['function']}
    errors = []

    async def native_size(*args, **kwargs):
        try:
            resolve_size_requirement({'prompt': '完整已保存正文', 'mode': 'text_to_image'}, [],
                intent={'width': 1000, 'height': 1000, 'approximate_pixels': approximate})
        except ImageSizeError as error:
            errors.append(error.code)
            raise
    verify = AsyncMock(side_effect=native_size)
    runtime.task_image_resource_resolver = SimpleNamespace(prepare_request=verify)
    blocks = await execute(runtime, prepared, handler, request, [call('generate_image', {
        'draft': {'title': '灯塔方案', 'body': '完整已保存正文'}}, 'size-question')])
    handler._execute_tool_calls.assert_not_awaited()
    verify.assert_awaited_once()
    assert errors == [code]
    assert not co.objects(runtime.task_execution_state)
    assert not getattr(runtime.task_execution_state, 'operations', ())
    assert '1K' in handler._tool_result_stop_reason and '？' in handler._tool_result_stop_reason
    assert code not in handler._tool_result_stop_reason
    assert '请告知用户' not in handler._tool_result_stop_reason
    block, = [b for b in blocks if b.get('tool_call_id') == 'size-question']
    assert block['status'] == 'error' and code not in block['output']
