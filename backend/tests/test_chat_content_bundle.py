"""Lossless item handoff and opt-in real-record replay; never generate media."""
import copy
import hashlib
import json

import pytest

from services.handlers.chat_content_bundle import bundle, verify
from services.handlers.chat import content_tools
from services.handlers.chat_image_request import freeze_image_request, verify_frozen_request
from tests.test_chat_content_real_replay import real_replay, select
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


DOCUMENT = '''通用要求：使用已确认素材，保留署名。
# 版本1｜春季
## 内容
春季正文
## 制作约束
不能改变商标
# 版本2｜夏季
## 内容
夏季正文
## 配音说明
只使用授权音频
## 附录
字幕保持完整
# 交付要求
输出必须可编辑。
'''


def test_item_keeps_all_fields_shared_context_and_excludes_sibling():
    text, proof = bundle(DOCUMENT, '夏季正文')
    assert '春季正文' not in text
    for content in ['夏季正文', '只使用授权音频', '字幕保持完整', '保留署名', '输出必须可编辑']:
        assert content in text
    assert verify(DOCUMENT, text, proof) == text
    assert {part['role'] for part in proof['spans']} == {'item', 'shared'}


@pytest.mark.parametrize('mutation', ['drop', 'rewrite', 'source', 'hash', 'offset'])
def test_omitted_or_changed_content_cannot_pass_coverage_proof(mutation):
    text, proof = bundle(DOCUMENT, '夏季正文')
    original = DOCUMENT
    if mutation == 'drop':
        proof['spans'].pop()
    elif mutation == 'rewrite':
        text = text.replace('只使用授权音频', '')
    elif mutation == 'source':
        original += '\n新增交付条件'
    elif mutation == 'hash':
        proof['document_sha256'] = '0' * 64
    else:
        proof['selection']['end'] = True
    with pytest.raises(ValueError, match='CHAT_SOURCE_BUNDLE_CHANGED'):
        verify(original, text, proof)


def test_unknown_plain_document_is_not_silently_reduced_to_one_paragraph():
    original = '正文：发布新品。\n额外说明：保留表格。\n限制：不能改价格。'
    text, proof = bundle(original, '发布新品')
    assert text == original
    fragment, fragment_proof = bundle(original, '发布新品', scope='fragment')
    assert fragment == '发布新品'
    assert fragment_proof['scope'] == 'fragment'


def test_numbered_plain_items_and_fenced_fake_headings():
    text, _ = bundle('共同要求\n1. 春季\n说明一\n2. 夏季\n说明二', '夏季')
    assert '说明二' in text and '说明一' not in text
    original = DOCUMENT.replace('夏季正文', '夏季正文\n```markdown\n# 版本3｜伪标题\n```')
    text, _ = bundle(original, '夏季正文')
    assert '只使用授权音频' in text and '春季正文' not in text


def test_distinct_quotes_cannot_silently_become_two_calls_for_one_unit():
    messages = [{'message_id': 'm', 'parts': [{'type': 'text', 'content_index': 0, 'text': DOCUMENT}]}]
    with pytest.raises(ValueError, match='CHAT_SOURCE_OVERLAPPING_UNITS'):
        content_tools.select_text_fragments(messages, {'text_exacts': ['夏季正文', '字幕保持完整']})


def test_ambiguous_sibling_boundaries_stop_instead_of_mixing_items():
    with pytest.raises(ValueError, match='CHAT_SOURCE_ITEM_BOUNDARY_REQUIRED'):
        bundle('# 苹果\n苹果内容\n# 梨\n梨内容', '梨内容')


@pytest.mark.asyncio
async def test_real_tampered_bundle_stops_before_dispatch_and_persists_failure(real_replay):
    replay = real_replay
    original = replay.arguments[1]
    await select(replay, [original], 'new')
    state = content_tools.checkpoint_state(replay.runtime)
    source = state['selected_sources'][0]
    source['bundle']['spans'] = [entry for entry in source['bundle']['spans'] if entry['role'] != 'shared']
    args = {k: v for k, v in original.items() if k != 'source_prompt'}
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request,
                  [call('generate_image', args, 'omitted-shared')])
    assert replay.generation == []
    assert 'CHAT_SOURCE_BUNDLE_CHANGED' in str(replay.prepared.messages), replay.prepared.messages[-1]
    assert replay.saved[-1][content_tools.STATE]['stop_message']


@pytest.mark.asyncio
async def test_real_adaptation_carries_complete_original_and_explicit_changes(real_replay):
    replay = real_replay
    await select(replay, replay.arguments[1:2], 'new')
    source = copy.deepcopy(content_tools.checkpoint_state(replay.runtime)['selected_sources'][0])
    source['mode'] = 'adapted'
    result = content_tools.bind_call(replay.db, replay.task['id'], replay.prepared.execution_context,
                                     'generate_image', {'mode': 'image_to_image', 'prompt': '将季节改成秋季。'}, source)
    assert source['text'] in result['prompt']
    assert '将季节改成秋季。' in result['prompt']
    from services.task_execution.image_resources import native_resolver
    resolver = native_resolver(replay.handler, replay.request, replay.prepared.execution_context)
    resolver.source_prompt(result['source_prompt'], result['prompt'])
    with pytest.raises(ValueError, match='CHAT_SOURCE_BUNDLE_CHANGED'):
        resolver.source_prompt(result['source_prompt'], '将季节改成秋季。')


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint', ['new', 'legacy', 'legacy_parallel', 'direct', 'raw_direct', 'new_selector', 'legacy_selector'])
@pytest.mark.parametrize('count', [1, 6])
async def test_latest_real_single_and_all_complete_at_native_snapshot(real_replay, entrypoint, count, record_property):
    replay = real_replay
    arguments = replay.arguments if count == 6 else replay.arguments[1:2]
    if entrypoint not in {'direct', 'raw_direct'}:
        await select(replay, arguments, entrypoint)
        assert len(content_tools.checkpoint_state(replay.runtime)['selected_sources']) == count
        replay.runtime.checkpoint_base[content_tools.STATE] = copy.deepcopy(replay.saved[-1][content_tools.STATE])
    calls = []
    for index, original in enumerate(arguments):
        args = copy.deepcopy(original)
        if entrypoint == 'direct':
            args['source_prompt'].pop('sha256', None)
        else:
            args.pop('source_prompt')
        if entrypoint.endswith('_selector'):
            replay.prepared.content_controls_available = True
            args.pop('prompt')
            args['chat_text_source'] = {**original['source_prompt'],
                'sha256': hashlib.sha256(original['prompt'].encode()).hexdigest(), 'mode': 'exact'}
        calls.append(call('generate_image', args, f'complete-{index}'))
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, calls)
    assert len(replay.generation) == count, str(replay.prepared.messages[-1])[:1500]
    recipes = [json.loads(artifact.inline_text) for artifact in replay.runtime.task_execution_state.artifacts
               if 'image.direct.recipe' in artifact.validations]
    assert len(recipes) == count
    original_digest = hashlib.sha256(__import__('base64').b64decode(replay.data['originals'][0]['raw_b64'])).hexdigest()
    sizes = []
    for original, recipe in zip(arguments, recipes):
        normalized = recipe['normalized']
        source = original['source_prompt']
        row = next(row for row in replay.rows if row['id'] == source['message_id'])
        parts = json.loads(row['content']) if isinstance(row['content'], str) else row['content']
        document = parts[source['content_index']]['text']
        # Independent oracle: every line in the selected top-level item,
        # including unknown headings and all constraints, reaches the snapshot.
        import re
        starts = list(re.finditer(r'(?m)^# 方案\d+[^\n]*', document))
        selected = next(i for i, match in enumerate(starts)
                        if match.start() <= document.index(original['prompt'])
                        < (starts[i+1].start() if i+1 < len(starts) else len(document)))
        stop = starts[selected+1].start() if selected+1 < len(starts) else len(document)
        for line in document[starts[selected].start():stop].splitlines():
            if line.strip():
                assert line in normalized['prompt']
        for i, match in enumerate(starts):
            assert (match[0] in normalized['prompt']) == (i == selected)
        assert document[:starts[0].start()] in normalized['prompt']
        assert '负面提示词' in normalized['prompt']
        assert normalized['aspect_ratio'] == '1:1'
        assert normalized['mode'] == 'image_to_image'
        assert len(recipe['resolved']) == 1
        assert recipe['resolved'][0]['content_sha256'] == original_digest
        assert recipe['resolved'][0]['workspace_path'] == replay.data['originals'][0]['workspace_path']
        refs = copy.deepcopy(recipe['resolved'])
        assert refs[0]['role'] == original['references'][0]['role']
        snapshot = freeze_image_request(normalized, refs, origin={'local_replay': True}, max_requests=count, max_credits=300)
        verify_frozen_request(snapshot)
        assert snapshot['prompt'] == normalized['prompt']
        from services.adapters.kie.image_adapter import KieImageAdapter
        adapter = KieImageAdapter(client=None, model=snapshot['model'])
        # Exercise the production serializer, with a local URL placeholder;
        # no upload, signed URL generation, HTTP client or supplier is invoked.
        urls = ['https://local-replay.invalid/original.jpg']
        payload = adapter._build_input_params(snapshot['prompt'], urls, snapshot['aspect_ratio'],
                                               snapshot['output_format'], snapshot['resolution'])
        assert payload['prompt'] == normalized['prompt']
        assert payload['input_urls'] == urls
        sizes.append(len(snapshot['prompt']))
    record_property('snapshot_prompt_chars', sizes)
    record_property('provider_calls', 0)
    record_property('reference_decodes_in_batch', replay.resolver_calls.call_count)
    # The real first image used a message locator; the later five used a
    # resource_ref. Preserve both identities and cache only identical selectors.
    assert replay.resolver_calls.call_count == len({json.dumps(args['references'], sort_keys=True)
                                                   for args in arguments})


@pytest.mark.asyncio
async def test_real_missing_reference_stops_before_dispatch(real_replay):
    replay = real_replay
    original = replay.arguments[1]
    await select(replay, [original], 'new')
    args = {k: v for k, v in original.items() if k != 'source_prompt'}
    args['references'] = []
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request,
                  [call('generate_image', args, 'missing-reference')])
    assert replay.generation == []
    assert 'IMAGE_MODE_REFERENCE_MISMATCH' in str(replay.prepared.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', ['generate_video', 'code_execute'])
async def test_complete_unit_uses_same_binding_for_video_and_file(real_replay, tool):
    replay = real_replay
    original = replay.arguments[1]
    await select(replay, [original], 'new')
    source = content_tools.checkpoint_state(replay.runtime)['selected_sources'][0]
    arguments = {'code': 'output = chat_source_text'} if tool == 'code_execute' else {}
    result = content_tools.bind_call(replay.db, replay.task['id'], replay.prepared.execution_context,
                                     tool, arguments, source)
    if tool == 'generate_video':
        assert result['prompt'] == source['text']
    else:
        namespace = {}
        exec(result['code'], namespace)
        assert namespace['output'] == source['text']
    assert '负面提示词' in source['text']
