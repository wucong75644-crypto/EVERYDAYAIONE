"""Replay captured final requests as indivisible text; never calls any provider."""
import base64,hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from services.task_execution import content_objects as co
from services.tools.catalog import build_capability_catalog
from services.file_executor import FileExecutor
from services.task_execution.image_resources import create_resolver
from services.handlers.chat_image_request import freeze_image_request,verify_frozen_request
from services.adapters.kie.image_adapter import KieImageAdapter
from services.agent.agent_result import AgentResult
from tests.test_model_directed_loop import case,execute
from tests.test_task_execution_model_bridge import call
from tests.test_content_objects import ref

@pytest.mark.asyncio
async def test_real_five_final_originals_single_all_and_revision_are_verbatim(monkeypatch,mock_db,tmp_path):
    folder=os.getenv('CONTENT_VERBATIM_RECORDS')
    if not folder:pytest.skip('Private final request captures and original image required')
    root=Path(folder);saved=json.loads((root/'verification.json').read_text())
    assert len(saved['local_requests'])==5
    originals=json.loads(Path(os.environ['REAL_INPUT_RETEST_DIR']).joinpath('live-originals.json').read_text())
    raw=base64.b64decode(originals['originals'][0]['raw_b64'])
    from core.config import get_settings
    monkeypatch.setattr(get_settings(),'file_workspace_root',str(tmp_path));monkeypatch.setattr(get_settings(),'chat_image_async_enabled',True)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    r,p,h,q=case();p.content_controls_available=True;ctx=p.execution_context
    files=FileExecutor(str(tmp_path),ctx.actor_user_id,None);files.resolve_safe_path('original.png').write_bytes(raw)
    source_id=str(uuid4());current_id=str(uuid4())
    mock_db.set_table_data('messages',[{'id':source_id,'role':'user','status':'completed','conversation_id':r.conversation_id,'org_id':None,'context_revision':1,'content':[{'type':'text','text':'写方案，保留原图参考'}, {'type':'image','workspace_path':'original.png','url':'https://local.invalid/original.png'}]},
        {'id':current_id,'role':'user','status':'completed','conversation_id':r.conversation_id,'org_id':None,'context_revision':None,'content':[{'type':'text','text':'用第二条执行'}]}])
    mock_db.set_table_data('conversations',[{'id':r.conversation_id,'user_id':ctx.actor_user_id,'org_id':None,'source':'web'}])
    mock_db.set_table_data('tasks',[{'id':r.task_id,'user_id':ctx.actor_user_id,'org_id':None,'conversation_id':r.conversation_id,'input_message_id':current_id,'base_context_revision':1,'request_params':{}}])
    h.db=mock_db;h._actor_runtime=r;r.task_image_resource_resolver=create_resolver(h,q,ctx)
    r.task_operation_store=SimpleNamespace(registry=build_capability_catalog())
    # Captured final supplier strings are the original inputs here. No production
    # parser, positive/negative extraction or handcrafted semantic mapping.
    texts=[entry['payload']['prompt'] for entry in saved['local_requests']]
    doc={'title':'真实五条完整原文','notes':'过程说明不参与执行','items':[
        {'title':f'条目{i}','body':text,'materials':[{'reference':{'message_id':source_id,'content_index':1},'role':'原产品参考','usage':'input'}],
        'options':{'aspect_ratio':'1:1','resolution':'1K','output_format':'png'}} for i,text in enumerate(texts,1)]}
    await execute(r,p,h,q,[call(co.SAVE,{'document':doc},'register-real')]);row=co.catalog(r.task_execution_state)[0]
    observed=[]
    async def submit(calls,*args,**kwargs):
        results=[]
        for c in calls:
            value=json.loads(c['arguments']);index=int(value['plan_item_id'].rsplit(':',1)[1])
            recipes=[json.loads(a.inline_text) for a in r.task_execution_state.artifacts if 'image.direct.recipe' in a.validations]
            recipe=next(v for v in recipes if v['normalized']['prompt']==value['prompt'])
            assert recipe['resolved'][0]['content_sha256']==hashlib.sha256(raw).hexdigest()
            frozen=freeze_image_request(recipe['normalized'],recipe['resolved'],origin={},max_requests=1,max_credits=300);verify_frozen_request(frozen)
            payload=KieImageAdapter(client=None,model=frozen['model'])._build_input_params(frozen['prompt'],['https://local.invalid/original.png'],'1:1','png','1K')
            assert value['prompt'].encode()==texts[index-1].encode()==frozen['prompt'].encode()==payload['prompt'].encode()
            assert frozen['prompt_sha256']==hashlib.sha256(texts[index-1].encode()).hexdigest()
            observed.append(index);results.append((c,AgentResult('本地序列化；未生图'),False,'本地序列化'))
        return results
    h._execute_tool_calls.side_effect=submit
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,2)},'single')]);assert observed==[2]
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,i)},f'all-{i}') for i in range(1,6)])
    assert observed==[2,1,2,3,4,5]
    # Changing one complete original replaces it; old strings are never appended.
    texts[1]='  用户修订完整原文\r\n禁止项：不可变形。\n保留：原结构。  '
    await execute(r,p,h,q,[call(co.SAVE,{'base':ref(row,2),'updates':[{'item_index':2,'item':{'body':texts[1]}}]},'edit-real')])
    row=co.catalog(r.task_execution_state)[0]
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,2)},'revised-single')]);assert observed[-1]==2

@pytest.mark.asyncio
async def test_six_original_execution_sections_are_copied_without_runtime_splitting():
    path=os.getenv('CHAT_CONTENT_REAL_REPLAY')
    if not path:pytest.skip('Private original six-plan conversation required')
    data=json.loads(Path(path).read_text())
    row=next(m for m in data['messages'] if m['id']=='4851cbe7-9390-44b7-8b3e-269c5133afcf')
    parts=json.loads(row['content']) if isinstance(row['content'],str) else row['content']
    original=next(p['text'] for p in parts if p.get('type')=='text')
    # Fixture-only literal boundaries of this recorded sample. Production has
    # no heading parser and never extracts/reassembles positive/negative text.
    import re
    boundaries=list(re.finditer(r'^# 方案[1-6]｜',original,re.M));assert len(boundaries)==6
    texts=[]
    for i,match in enumerate(boundaries):
        block=original[match.start():boundaries[i+1].start() if i<5 else len(original)]
        start=block.index('## 完整生图提示词\n')+len('## 完整生图提示词\n')
        end=block.rfind('\n---\n')
        texts.append(block[start:end if end>block.index('## 负面提示词') else len(block)])
    r,p,h,q=case();p.content_controls_available=True
    doc={'title':'六条完整原文','notes':'参考图分析不属于执行原文','items':[
        {'title':f'条目{i}','body':text,'materials':[],'options':{'aspect_ratio':'1:1','resolution':'1K'}} for i,text in enumerate(texts,1)]}
    await execute(r,p,h,q,[call(co.SAVE,{'document':doc},'six-recorded-texts')]);row=co.catalog(r.task_execution_state)[0]
    for index in (2,1,2,3,4,5,6):
        value,_=co.compile_input(r.task_execution_state,'generate_image',{},ref(row,index))
        assert value['prompt'].encode()==texts[index-1].encode()
        assert '## 负面提示词' in value['prompt'] and '# 参考图判断' not in value['prompt']
        assert hashlib.sha256(value['prompt'].encode()).hexdigest()==row['items'][index-1]['body_sha256']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_recorded_six_plan_duplicate_delivery_and_second_image(monkeypatch, mock_db, tmp_path):
    """Original failed model response, original uploaded bytes, no external requests."""
    import copy
    import re
    from core.config import get_settings
    from schemas.message import serialize_content_parts
    from services.adapters.types import StreamChunk, ToolCallDelta
    from services.handlers.chat.outcome_builder import build_content_parts
    from services.skills.selection import SkillSelection
    from services.task_execution.contracts import TaskState
    from services.task_execution.direct_loop import context_message
    from services.tools.catalog import build_tool_catalog
    from tests.test_content_objects import model_save_chunk, run_content_loop
    from tests.test_task_execution_original_skills import OriginalSource, original_runtime

    path = os.getenv('CHAT_CONTENT_DUPLICATE_RECORD')
    if not path or not os.getenv('TASK_ORIGINAL_SKILLS_EXPORT'):
        pytest.skip('Private six-plan conversation, original image and original Skill required')
    data = json.loads(Path(path).read_text())
    mixed = data['mixed']
    draft = mixed['content']
    incomplete = data['document']
    saved_chat = next(m['content'] for m in data['messages'] if m['role'] == 'assistant')
    original_texts = [part['text'] for part in saved_chat if part['type'] == 'text']
    assert original_texts[0] == draft
    assert len(original_texts) == 2 and '## 负面提示词' not in original_texts[1]

    # This fixture supplies a corrected model response using known boundaries
    # in this one recording. The executor receives body as an indivisible string.
    headings = list(re.finditer(r'^# 方案[1-6]｜(.+)$', draft, re.M))
    corrected = copy.deepcopy(incomplete)
    assert len(headings) == len(corrected['items']) == 6
    for i, item in enumerate(corrected['items']):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(draft)
        section = draft[headings[i].end():end]
        body = section.split('## 完整生图提示词\n\n', 1)[1].removesuffix('\n\n---\n\n').rstrip('\n')
        assert body.split('\n\n## 负面提示词', 1)[0] == item['body']
        item.update(title=headings[i][1], body=body)

    r, p, h, q = case()
    original_skills = OriginalSource()
    skill_step = next(part for part in saved_chat if part['type'] == 'skill_step')
    selection = SkillSelection(skill_id='reference-image-prompts', revision=skill_step['revision'])
    r.skill_runtime = original_runtime(original_skills, turn_id=r.turn_id)
    await r.skill_runtime.initialize(selection=selection)
    assert (await r.skill_runtime.activate_manual(selection))['ok']
    rounds = 0
    save_call = next(c for c in mixed['tool_calls'] if c['function']['name'] == co.SAVE)

    async def replay_writer(**kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            for start in range(0, len(draft), 512):
                yield StreamChunk(content=draft[start:start + 512])
            yield StreamChunk(tool_calls=[
                ToolCallDelta(index=0, id=save_call['id'], name=co.SAVE,
                              arguments_delta=save_call['function']['arguments'])
            ])
        else:
            assert rounds == 2
            assert not co.objects(r.task_execution_state)
            assert not r.checkpoint_base['content_blocks']
            assert any(co.DRAFT_CONFLICT in (m.get('content') or '')
                       for m in kwargs['messages'] if m['role'] == 'tool')
            yield model_save_chunk(corrected)

    source = copy.deepcopy(next(m for m in data['messages'] if m['role'] == 'user'))
    p.messages[-1]['content'] = source['content'][0]['text']
    sink, blocks, totals = await run_content_loop(r, p, h, q, replay_writer)
    assert rounds == 2 and not sink.text
    h._execute_tool_calls.assert_not_awaited()
    rendered_parts = serialize_content_parts(build_content_parts(blocks, fallback_text=totals.text))
    assert len(rendered_parts) == len(sink.blocks) == 1
    shown = rendered_parts[0]['text']
    assert shown == sink.blocks[0]['text']
    assert shown.count('## 负面提示词') == 6
    assert all(item['body'] in shown and shown.count(item['body']) == 1 for item in corrected['items'])
    assert all(metadata not in shown for metadata in ('# 参考图判断', '输出规格', '素材用途'))

    r.task_execution_state = TaskState.model_validate_json(r.task_execution_state.model_dump_json())
    row, = co.catalog(r.task_execution_state)
    artifact, _, second = co.load(r.task_execution_state, ref(row, 2))
    assert (artifact.image_method.skill_key, artifact.image_method.revision) == (
        selection.skill_id, selection.revision)
    assert second.body == corrected['items'][1]['body']
    assert second.options == incomplete['items'][1]['options'] == {'aspect_ratio': '1:1'}

    settings = get_settings()
    monkeypatch.setattr(settings, 'file_workspace_root', str(tmp_path))
    monkeypatch.setattr(settings, 'chat_image_async_enabled', True)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    ctx = p.execution_context
    raw = base64.b64decode(data['original']['raw_b64'])
    path_in_workspace = data['original']['path']
    original_file = FileExecutor(str(tmp_path), ctx.actor_user_id, None).resolve_safe_path(path_in_workspace)
    original_file.parent.mkdir(parents=True, exist_ok=True)
    original_file.write_bytes(raw)
    source.update(conversation_id=r.conversation_id, org_id=None, context_revision=1)
    current = {'id': str(uuid4()), 'role': 'user', 'status': 'completed',
               'conversation_id': r.conversation_id, 'org_id': None, 'context_revision': None,
               'content': [{'type': 'text', 'text': '只用第二条生成图片'}]}
    mock_db.set_table_data('messages', [source, current])
    mock_db.set_table_data('conversations', [
        {'id': r.conversation_id, 'user_id': ctx.actor_user_id, 'org_id': None, 'source': 'web'}
    ])
    mock_db.set_table_data('tasks', [
        {'id': r.task_id, 'user_id': ctx.actor_user_id, 'org_id': None, 'conversation_id': r.conversation_id,
         'input_message_id': current['id'], 'base_context_revision': 1, 'request_params': {}}
    ])
    h.db = mock_db
    h._actor_runtime = r
    r.task_image_resource_resolver = create_resolver(h, q, ctx)
    r.task_operation_store = SimpleNamespace(registry=build_capability_catalog())
    p.core_tools.append(build_tool_catalog().require('generate_image').to_schema())
    p.messages.append({'role': 'user', 'content': current['content'][0]['text']})
    collected = []

    async def submit(calls, *args, **kwargs):
        assert len(calls) == 1
        tool_call = calls[0]
        bound = json.loads(tool_call['arguments'])
        assert bound['source_prompt']['content_object']['source'] == ref(row, 2)
        assert bound['references'] == [
            dict(m['reference'], role=m['role']) for m in incomplete['items'][1]['materials'] if m['usage'] == 'input'
        ]
        recipe, = [json.loads(a.inline_text) for a in r.task_execution_state.artifacts
                   if 'image.direct.recipe' in a.validations]
        original_ref, = recipe['resolved']
        assert original_ref['message_id'] == source['id']
        assert original_ref['content_sha256'] == hashlib.sha256(raw).hexdigest()
        assert [original_ref['width'], original_ref['height']] == data['original']['size']
        frozen = freeze_image_request(recipe['normalized'], recipe['resolved'], origin={},
                                     max_requests=1, max_credits=300)
        verify_frozen_request(frozen)
        image_url = 'https://local.invalid/verified-reference'
        payload = KieImageAdapter(client=None, model=frozen['model'])._build_input_params(
            frozen['prompt'], [image_url], frozen['aspect_ratio'], frozen['output_format'], frozen['resolution'])
        assert shown.count(bound['prompt']) == 1
        assert bound['prompt'] == second.body == frozen['prompt'] == payload['prompt']
        assert frozen['mode'] == 'image_to_image' and frozen['num_images'] == 1
        assert frozen['aspect_ratio'] == '1:1'
        assert (frozen['output_format'], frozen['resolution']) == ('png', '1K')
        assert payload.get('aspect_ratio', payload.get('image_size')) == '1:1'
        assert [image_url] in payload.values()
        collected.append(frozen)
        return [(tool_call, AgentResult('本地序列化，未派发'), False, '本地序列化')]

    h._execute_tool_calls.side_effect = submit
    selected = False

    async def replay_selection(**kwargs):
        nonlocal selected
        if not selected:
            selected = True
            facts = json.loads(context_message(r)['content'].split('\n')[-1])
            assert facts['content_objects'] == [row]
            tool = next(t['function'] for t in kwargs['tools'] if t['function']['name'] == 'generate_image')
            assert 'content_source' in tool['parameters']['properties']
            yield StreamChunk(tool_calls=[ToolCallDelta(
                index=0, id='image-second', name='generate_image', arguments_delta=json.dumps({
                    'content_source': ref(facts['content_objects'][0], 2)
                }))])
        else:
            yield StreamChunk(content='本地参数已就绪。')

    await run_content_loop(r, p, h, q, replay_selection)
    assert len(collected) == 1


@pytest.mark.asyncio
async def test_recorded_content_reference_from_another_turn_acquires_original_task(monkeypatch):
    from services.task_execution.contracts import TaskState
    from services.task_execution.followup import FollowupRestart
    from services.task_execution.direct_loop import context_message

    path = os.getenv('CHAT_CONTENT_TWO_SKILL_FAILURES')
    if not path:
        pytest.skip('Private conversation record required')
    records = json.loads(Path(path).read_text())['conversations']
    history = next(c for c in records if c['states'])
    failed, original = history['tasks']
    recorded = next(c['function'] for m in failed['checkpoint']['messages']
                    for c in m.get('tool_calls') or () if c['function']['name'] == 'generate_image')
    args = json.loads(recorded['arguments'])
    reference = args['content_source']
    r, p, h, q = case()
    p.content_controls_available = True
    previous = original['checkpoint']['_task_execution']
    previous_state = TaskState.model_validate(previous)
    _, _, first = co.load(previous_state, reference)
    question = failed['question'] if isinstance(failed['question'], list) else json.loads(failed['question'])
    message = question[0]['text']
    r.task_followup_candidate = {'state': previous, 'checkpoint': original['checkpoint'],
                                 'message': message, 'input_message_id': failed['input_message_id']}
    p.messages = [{'role': 'user', 'content': message}]
    facts = json.loads(context_message(r)['content'].split('\n')[-1])
    assert not facts['content_objects'] and facts['previous_content_objects'][0]['sha256'] == reference['sha256']

    async def transfer(source, observation, merged):
        assert source is r.task_followup_candidate
        assert observation == {'relation': 'continue', 'evidence': [message]}
        assert not merged['content_blocks']
        restored = TaskState.model_validate(merged['_task_execution'])
        assert restored.root_task_id == previous_state.root_task_id
        assert restored.artifacts == previous_state.artifacts
        assert restored.operations == previous_state.operations
        params, _ = co.compile_input(restored, 'generate_image', {}, reference)
        assert params['prompt'] == first.body
        assert params['references']
        return {'outcome': 'saved', 'delivery_context': {'logical_task_id': str(restored.root_task_id)}}

    r.task_followup_adopt = AsyncMock(side_effect=transfer)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    with pytest.raises(FollowupRestart):
        await execute(r, p, h, q, [call('generate_image', args, 'real-first')])
    h._execute_tool_calls.assert_not_awaited()
    r.task_followup_adopt.assert_awaited_once()
    assert json.loads(p.messages[-1]['content'])['accepted'] is False


@pytest.mark.asyncio
async def test_recorded_malformed_save_does_not_pass_draft_or_enable_execution_skill():
    from services.adapters.types import StreamChunk, ToolCallDelta
    from tests.test_content_objects import run_content_loop
    from tests.test_skill_runtime import Source, item, state

    path = os.getenv('CHAT_CONTENT_TWO_SKILL_FAILURES')
    if not path:
        pytest.skip('Private conversation record required')
    records = json.loads(Path(path).read_text())['conversations']
    history = next(c for c in records if not c['states'])
    followup, original = history['tasks']
    malformed = [c['function']['arguments'] for m in original['checkpoint']['messages']
                 for c in m.get('tool_calls') or () if c['function']['name'] == co.SAVE]
    assert len(malformed) == 3 and all(isinstance(json.loads(m)['document'], str) for m in malformed)
    def answer(task):
        return json.loads(task['answer']) if isinstance(task['answer'], str) else task['answer']
    draft = next(part['text'] for part in answer(original) if part['type'] == 'text')
    from dataclasses import replace
    from pydantic import TypeAdapter
    from schemas.message import ContentPart
    from services.handlers.chat_context.image_sources import content_parts
    r, p, h, q = case()
    q = replace(q, content=TypeAdapter(list[ContentPart]).validate_python(content_parts(original['question'])))
    assert any(part.type == 'image' for part in q.content)
    rounds = 0

    async def write(**kwargs):
        nonlocal rounds
        rounds += 1
        params = next(t['function']['parameters'] for t in kwargs['tools'] if t['function']['name'] == co.SAVE)
        assert params['properties']['document']['type'] == 'object'
        if rounds <= len(malformed):
            if rounds > 1:
                previous_failure = co.INVALID_OBJECT if rounds == 2 else co.ATTACHMENT_USAGE_REQUIRED
                assert any(previous_failure in (m.get('content') or '') for m in kwargs['messages']
                           if m['role'] == 'tool')
            yield StreamChunk(tool_calls=[ToolCallDelta(index=0, id=f'save-{rounds}', name=co.SAVE,
                                                        arguments_delta=malformed[rounds-1])])
        else:
            yield StreamChunk(content=draft)

    sink, blocks, _ = await run_content_loop(r, p, h, q, write)
    assert rounds == 4 and not co.objects(r.task_execution_state)
    assert sink.text == co.SAVE_FAILED_TEXT
    assert [b['text'] for b in blocks if b['type'] == 'text'] == [co.SAVE_FAILED_TEXT]
    h._execute_tool_calls.assert_not_awaited()

    skill = next(part for part in answer(followup) if part['type'] == 'skill_step')
    key = 'chat-image-orchestration'
    r, p, h, q = case()
    database_source = Source([item(key, tools=('generate_image', 'file_search'), content_protocol=co.PROTOCOL)],
                             body='Only execute content_source.')
    r.skill_runtime = state(database_source, platform_tool_names={'generate_image', 'file_search'})
    await r.skill_runtime.initialize()
    r.skill_runtime.directory[key] = r.skill_runtime.directory[key].model_copy(update={'revision': skill['revision']})
    activation = call('activate_skill', {'skill_id': key}, 'real-activate')
    blocks = await execute(r, p, h, q, [activation])
    assert not r.skill_runtime.active
    database_source.load.assert_not_awaited()
    assert not any(b.get('type') == 'skill_step' for b in blocks)
    assert 'SKILL_CONTENT_OBJECT_REQUIRED' in str(p.messages[-1])
    from services.task_execution.direct_loop import schemas
    assert co.SCHEMA in schemas(r)
