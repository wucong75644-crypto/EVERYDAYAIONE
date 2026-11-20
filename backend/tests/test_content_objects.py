"""Single registered source for display, revision and native execution."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from services.task_execution import content_objects as co
from services.task_execution.contracts import TaskError, TaskState
from services.task_execution.direct_loop import context_message, schemas, image_plan_item
from services.task_execution.reducer import digest
from services.tools.catalog import build_capability_catalog
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


def document():
    return {'title':'产品新方案','notes':'分析：这是摄影参考，判断模式B。',
        'items':[{'title':f'主题{i}','body':f'完整正文{i}，文字 ORIGINAL-{i}\n保持原产品结构，禁止水印。\n不能变形，条目限制{i}。',
            'materials':[{'reference':{'message_id':'original','content_index':1},'role':'原产品','usage':'input'},
                         {'reference':{'message_id':'analysis','content_index':1},'role':'仅构图分析','usage':'analysis'}],
            'options':{'aspect_ratio':'1:1','resolution':'1K','output_format':'png'}} for i in range(1,4)]}

async def registered():
    r,p,h,q=case(); p.content_controls_available=True
    blocks=await execute(r,p,h,q,[call(co.SAVE,{'document':document()},'write')])
    assert len(blocks)==1 and blocks[0]['type']=='text' and blocks[0]['content_delivery_final']
    h._execute_tool_calls.assert_not_awaited()
    row=co.catalog(r.task_execution_state)[0]
    return r,p,h,q,row


def ref(row,index=1):
    return {k:row[k] for k in ('artifact_id','sha256')} | {'item_index':index}

@pytest.mark.asyncio
async def test_authored_display_markdown_survives_revision_without_entering_tool_input():
    r, p, h, q = case()
    intro = '# 成品说明\n\n- 阅读顺序'
    prefix = '## 修订稿\n\n- 编辑记录'
    original = '原始正文。'
    revised = '修订正文。\n\n## 限制\n\n保留原标识。'
    draft = {'title': '稿件', 'display_prefix': intro, 'items': [
        {'title': '修订稿', 'display_prefix': prefix, 'body': original},
    ]}
    first, = await execute(r, p, h, q, [call(co.SAVE, {'document': draft}, 'write')])
    assert first['text'] == '\n\n'.join((intro, prefix, original))
    row, = co.catalog(r.task_execution_state)
    revised_block, = await execute(r, p, h, q, [call(co.SAVE, {
        'base': {'artifact_id': row['artifact_id'], 'item_index': 1},
        'updates': [{'item_index': 1, 'item': {'body': revised}}],
    }, 'revise')])
    assert revised_block['text'] == '\n\n'.join((intro, prefix, revised))
    current, = co.catalog(r.task_execution_state)
    args, _ = co.compile_input(r.task_execution_state, 'generate_video', {}, ref(current))
    assert args['prompt'] == revised


@pytest.mark.asyncio
async def test_writing_records_one_shared_origin_and_only_explicit_execution_selects_it():
    from dataclasses import replace
    from schemas.message import ImagePart, TextPart

    r, p, h, q = case()
    source = ImagePart(name='原图.png', workspace_path='uploads/original.png')
    q = replace(q, content=[TextPart(text='参考这张图写两条'), source],
                context_anchor=SimpleNamespace(input_message_id=str(r.task_bootstrap.input_message_id)))
    draft = {'title': '两条方案', 'items': [
        {'title': '第一条', 'body': '正文一。'}, {'title': '第二条', 'body': '正文二。'},
    ]}

    direct = call(co.SAVE, {'document': draft, 'continue_execution': True}, 'direct')
    assert not await execute(r, p, h, q, [direct])
    assert json.loads(p.messages[-1]['content'])['code'] == co.ATTACHMENT_USAGE_REQUIRED
    assert not co.objects(r.task_execution_state)

    saved, = await execute(r, p, h, q, [call(co.SAVE, {'document': draft}, 'save')])
    assert saved['content_delivery_final'] and saved['text'].count('正文二。') == 1
    row, = co.catalog(r.task_execution_state)
    assert row['source_images'] == [{'index': 1, 'name': '原图.png'}]
    assert co.has_selectable_source_images(r.task_execution_state)
    reference = ref(row, 2)
    artifact, _, item = co.load(r.task_execution_state, reference)
    assert not item.materials and not item.options
    original, = co.ContentRecord.model_validate_json(artifact.inline_text).source_images

    with pytest.raises(TaskError, match='CONTENT_SOURCE_IMAGE_SELECTION_REQUIRED'):
        co.compile_input(r.task_execution_state, 'generate_image', {}, reference)
    target, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, {**reference, 'without_reference': True})
    assert target['mode'] == 'text_to_image' and target['references'] == []
    select_original = {**reference, 'source_image_indices': [1]}
    target, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, select_original)
    assert target['mode'] == 'image_to_image' and target['prompt'] == '正文二。'
    assert image_plan_item(r.task_execution_state, set(), target) is None
    assert target['references'] == [
        {'message_id': str(r.task_bootstrap.input_message_id), 'content_index': 1, 'role': '原图参考'},
    ]
    for indices in ([2], [1, 1]):
        with pytest.raises(TaskError, match='CONTENT_SOURCE_IMAGE_UNAVAILABLE'):
            co.compile_input(r.task_execution_state, 'generate_image', {}, {**reference, 'source_image_indices': indices})

    await execute(r, p, h, q, [call(co.SAVE, {'base': reference,
        'updates': [{'item_index': 2, 'item': {'body': '修订版原文。'}}]}, 'revise')])
    revised, = co.catalog(r.task_execution_state)
    version, _, item = co.load(r.task_execution_state, ref(revised, 2))
    assert item.body == '修订版原文。'
    assert co.ContentRecord.model_validate_json(version.inline_text).source_images == (original,)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_reference_is_canonical_and_a_different_selection_is_rejected():
    from services.handlers.chat.content_tools import model_tools
    from services.tools.catalog import build_tool_catalog

    r, _, _, _, row = await registered()
    state = r.task_execution_state
    assert not co.has_selectable_source_images(state)
    image = build_tool_catalog().require('generate_image').to_schema()
    tool, = model_tools([image], selectable_source_images=co.has_selectable_source_images(state))
    selector = tool['function']['parameters']['properties']['content_source']['properties']
    assert 'source_image_indices' not in selector

    reference = ref(row, 3)
    canonical = co.compile_input(state, 'generate_image', {}, reference)
    assert co.compile_input(state, 'generate_image', {}, {**reference, 'source_image_indices': [1]}) == canonical
    with pytest.raises(TaskError, match='CONTENT_SOURCE_IMAGE_UNAVAILABLE'):
        co.compile_input(state, 'generate_image', {}, {**reference, 'source_image_indices': [2]})

    from dataclasses import replace
    from schemas.message import ImagePart, TextPart

    r, p, h, q = case()
    q = replace(q, content=[TextPart(text='分别用这两张图写方案'),
                           ImagePart(name='A.png', workspace_path='A.png'),
                           ImagePart(name='B.png', workspace_path='B.png')],
                context_anchor=SimpleNamespace(input_message_id=str(r.task_bootstrap.input_message_id)))
    draft = document()
    draft['items'][0]['materials'][0]['reference'] = {'message_id': str(r.task_bootstrap.input_message_id), 'content_index': 1}
    await execute(r, p, h, q, [call(co.SAVE, {'document': draft}, 'write')])
    saved, = co.catalog(r.task_execution_state)
    reference = ref(saved)
    canonical = co.compile_input(r.task_execution_state, 'generate_image', {}, reference)
    assert co.compile_input(r.task_execution_state, 'generate_image', {}, {**reference, 'source_image_indices': [1]}) == canonical
    with pytest.raises(TaskError, match='CONTENT_ARGUMENT_CONFLICT'):
        co.compile_input(r.task_execution_state, 'generate_image', {}, {**reference, 'source_image_indices': [2]})

@pytest.mark.asyncio
async def test_explicit_image_override_changes_only_execution_references():
    runtime, _, _, _, row = await registered()
    reference = ref(row)
    state = runtime.task_execution_state
    before, _ = co.compile_input(state, 'generate_image', {}, reference)
    replacement = {'message_id': str(uuid4()), 'content_index': 1}
    arguments, binding = co.compile_input(state, 'generate_image', {}, {**reference, 'image_override': [replacement]})
    assert arguments['references'] == [{**replacement, 'role': '原图参考'}]
    assert all(arguments[key] == before[key] for key in ('prompt', 'aspect_ratio', 'resolution', 'output_format'))
    assert binding['reference']['image_override'] == [replacement]
    assert co.compile_input(state, 'generate_image', {}, reference)[0] == before
    with pytest.raises(TaskError, match='CONTENT_IMAGE_SELECTION_CONFLICT'):
        co.compile_input(state, 'generate_image', {}, {**reference, 'image_override': [replacement, replacement]})


@pytest.mark.asyncio
async def test_registration_renders_same_source_and_roundtrips_checkpoint():
    r,p,h,q,row=await registered()
    state=TaskState.model_validate_json(r.task_execution_state.model_dump_json())
    a,doc,item=co.load(state,ref(row))
    assert '完整正文1' in co.render(doc) and '条目限制3' in co.render(doc)
    assert doc.model_dump(mode='json')==co.ContentDocument.model_validate(document()).model_dump(mode='json')
    assert hashlib.sha256(a.inline_text.encode()).hexdigest()==a.sha256
    facts=json.loads(context_message(r)['content'].split('\n')[-1])
    assert facts['content_objects'] == co.catalog(state, for_model=True)
    assert 'sha256' not in facts['content_objects'][0] and 'body_sha256' not in facts['content_objects'][0]['items'][0]
    # The model receives an index, not every full plan for each execution.
    assert '完整正文' not in str(facts['content_objects'])
    assert row['artifact_id'] not in {artifact['id'] for artifact in facts['artifacts']}
    assert all(item['has_bound_inputs'] and 'materials' not in item for item in facts['content_objects'][0]['items'])

@pytest.mark.asyncio
async def test_patch_only_second_preserves_other_items_and_all_shared_fields():
    r,p,h,q,row=await registered(); old=r.task_execution_state
    changed={**document()['items'][1],'body':'修订第二条：SAKURA FUJI'}
    await execute(r,p,h,q,[call(co.SAVE,{'base':ref(row,2),'updates':[{'item_index':2,'item':changed}]},'edit')])
    current=co.catalog(r.task_execution_state)[0]
    assert current['revision']==2 and current['object_key']==row['object_key']
    _,before,_=co.load(old,ref(row)); _,after,item=co.load(r.task_execution_state,ref(current,2))
    assert before.items[0]==after.items[0] and before.items[2]==after.items[2]
    assert before.notes==after.notes
    assert item.body==changed['body'] and item.materials==before.items[1].materials
    with pytest.raises(TaskError,match='CONTENT_BASE_VERSION_CHANGED'):
        co.compile_input(r.task_execution_state,'generate_image',{},ref(row,2))
    # Frozen accepted work may still verify its pinned previous object.
    args,binding=co.compile_input(r.task_execution_state,'generate_image',{},ref(row,2),allow_old=True)
    assert binding['allow_old'] is True
    assert '完整正文2' in args['prompt']

@pytest.mark.asyncio
async def test_single_and_all_compile_every_execution_field_without_analysis():
    r,p,h,q,row=await registered()
    for i in (2,1,2,3):
        args,binding=co.compile_input(r.task_execution_state,'generate_image',{},ref(row,i))
        assert args['prompt']==document()['items'][i-1]['body']
        assert '判断模式' not in args['prompt']
        assert args['references']==[{'message_id':'original','content_index':1,'role':'原产品'}]
        assert args['mode']=='image_to_image' and args['resolution']=='1K'
        assert digest({k:v for k,v in args.items() if k!='source_prompt'})==binding['proof']['arguments_sha256']
        assert image_plan_item(r.task_execution_state,set(),args) is None
    for extra in ({'prompt':'删减'}, {'references':[]}, {'resolution':'2K'}):
        with pytest.raises(TaskError,match='CONTENT_ARGUMENT_CONFLICT'):
            co.compile_input(r.task_execution_state,'generate_image',extra,ref(row))
    with pytest.raises(TaskError,match='CONTENT_OBJECT_UNAVAILABLE_OR_CHANGED'):
        co.compile_input(r.task_execution_state,'generate_image',{},dict(ref(row),sha256='0'*64))

@pytest.mark.asyncio
async def test_native_proof_enforces_scope_and_argument_integrity():
    from services.handlers.chat_image_request import ChatImageInputResolver
    r,p,h,q,row=await registered(); state=r.task_execution_state
    owner=SimpleNamespace(_task_execution_state=state,user_id=str(state.actor_user_id),org_id=None,
        conversation_id=str(state.conversation_id),personal_context_allowed=True,context_scope='user',
        workspace_user_id=str(state.actor_user_id))
    resolver=ChatImageInputResolver(owner,base_revision=0,input_message_id='input')
    args,_=co.compile_input(state,'generate_image',{},ref(row))
    assert resolver.source_prompt(args['source_prompt'],args['prompt'])==args['source_prompt']
    with pytest.raises(ValueError): resolver.source_prompt(args['source_prompt'],'删减')
    owner.workspace_user_id='different'
    with pytest.raises(PermissionError,match='CONTENT_SOURCE_DENIED'):
        resolver.verify_content_object(args['source_prompt']['content_object'])

@pytest.mark.asyncio
async def test_save_failure_is_atomic_and_never_dispatches():
    r,p,h,q=case(); p.content_controls_available=True
    async def failed(*args): raise RuntimeError('checkpoint failure')
    r,p,h,q=case(failed); p.content_controls_available=True
    before=copy.deepcopy(p.messages)
    with pytest.raises(RuntimeError,match='checkpoint failure'):
        await execute(r,p,h,q,[call(co.SAVE,{'document':document()},'save')])
    assert r.task_execution_state is None and p.messages==before
    h._execute_tool_calls.assert_not_awaited()


def test_skill_v3_is_validated_through_existing_publication_contract():
    from services.skills.contracts import PackageCreate, PublishRevision, SkillCatalogMetadata
    from services.skills.storage import SkillStorage
    path=Path(__file__).resolve().parents[2]/'examples/skills/catalog/platform/chat-image-orchestration/v3/SKILL.md'
    raw=path.read_bytes(); body=raw.split(b'---\n',2)[2]
    publication=PublishRevision(revision='v3',content_sha256=hashlib.sha256(raw).hexdigest(),body_sha256=hashlib.sha256(body).hexdigest())
    result=SkillStorage.validate_bytes(PackageCreate(skill_key='chat-image-orchestration',source='platform',scope_kind='platform'),publication,raw)
    assert result.catalog_metadata.content_protocol==co.PROTOCOL
    assert 'content_protocol' not in SkillCatalogMetadata().model_dump(mode='json')

@pytest.mark.asyncio
async def test_fresh_upload_revision_single_and_all_reach_native_snapshot(monkeypatch,mock_db,tmp_path):
    """No supplier IO: real input resolution, recipe and provider serialization."""
    from unittest.mock import AsyncMock
    from core.config import get_settings
    from services.agent.agent_result import AgentResult
    from services.file_executor import FileExecutor
    from services.handlers.chat_image_request import freeze_image_request, verify_frozen_request
    from services.task_execution.image_resources import create_resolver
    from PIL import Image
    settings=get_settings()
    monkeypatch.setattr(settings,'file_workspace_root',str(tmp_path))
    monkeypatch.setattr(settings,'chat_image_async_enabled',True)
    r,p,h,q=case();p.content_controls_available=True
    context=p.execution_context
    files=FileExecutor(str(tmp_path),context.actor_user_id,None)
    path=files.resolve_safe_path('upload.png');Image.new('RGB',(32,32),'blue').save(path)
    original_sha=hashlib.sha256(path.read_bytes()).hexdigest()
    from uuid import uuid4
    source_id=str(uuid4());input_id=str(uuid4())
    rows=[{'id':source_id,'role':'user','status':'completed','conversation_id':r.conversation_id,'org_id':None,'context_revision':1,
        'content':[{'type':'text','text':'按原产品写三条方案，结构不变，1:1'}, {'type':'image','workspace_path':'upload.png','url':'https://test.invalid/upload.png'}]},
        {'id':input_id,'role':'user','status':'completed','conversation_id':r.conversation_id,'org_id':None,'context_revision':None,
        'content':[{'type':'text','text':'把刚改的第二条生成图片'}]}]
    mock_db.set_table_data('messages',rows)
    mock_db.set_table_data('conversations',[{'id':r.conversation_id,'user_id':context.actor_user_id,'org_id':None,'source':'web'}])
    mock_db.set_table_data('tasks',[{'id':r.task_id,'user_id':context.actor_user_id,'org_id':None,'conversation_id':r.conversation_id,
        'input_message_id':input_id,'base_context_revision':1,'request_params':{}}])
    h.db=mock_db;h._actor_runtime=r
    r.task_image_resource_resolver=create_resolver(h,q,context)
    r.task_operation_store=SimpleNamespace(registry=build_capability_catalog())
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    doc=document()
    for item in doc['items']: item['materials']=[{'reference':{'message_id':source_id,'content_index':1},'role':'原产品','usage':'input'}]
    await execute(r,p,h,q,[call(co.SAVE,{'document':doc},'write')])
    row=co.catalog(r.task_execution_state)[0]
    edited={**doc['items'][1],'body':'刚改的第二条：樱花主视觉，FUJI-3776m'}
    await execute(r,p,h,q,[call(co.SAVE,{'base':ref(row,2),'updates':[{'item_index':2,'item':edited}]},'edit')])
    row=co.catalog(r.task_execution_state)[0]; snapshots=[]
    async def submit(calls,*args,**kwargs):
        results=[]
        for raw in calls:
            arguments=json.loads(raw['arguments'])
            recipes=[json.loads(a.inline_text) for a in r.task_execution_state.artifacts if 'image.direct.recipe' in a.validations]
            recipe=next(v for v in recipes if v['normalized']['prompt']==arguments['prompt'])
            assert recipe['resolved'][0]['content_sha256']==original_sha
            snapshot=freeze_image_request(recipe['normalized'],recipe['resolved'],origin={},max_requests=1,max_credits=300)
            verify_frozen_request(snapshot)
            from services.adapters.kie.image_adapter import KieImageAdapter
            payload=KieImageAdapter(client=None,model=snapshot['model'])._build_input_params(snapshot['prompt'],['https://local.invalid/original.png'],'1:1','png','1K')
            assert payload['prompt']==arguments['prompt']
            snapshots.append(snapshot)
            results.append((raw,AgentResult('本地替身：仅验证序列化，未提交供应商'),False,'本地替身'))
        return results
    h._execute_tool_calls.side_effect=submit
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,2)},'single')])
    assert len(snapshots)==1, p.messages[-2:]
    assert snapshots[0]['prompt']==edited['body']
    await execute(r,p,h,q,[call('generate_image',{'content_source':ref(row,i)},f'all-{i}') for i in range(1,4)])
    # A prior submitted identity cannot be recreated by merely changing call_id.
    assert len(snapshots) in (3,4)
    assert {v['prompt'] for v in snapshots}=={doc['items'][0]['body'],edited['body'],doc['items'][2]['body']}

@pytest.mark.asyncio
async def test_retired_skill_does_not_hide_shared_tools_or_load_its_body():
    from tests.test_skill_runtime import Source, item, state
    r,p,h,q,row=await registered()
    source = Source([item('chat-image-orchestration', tools=('generate_image','file_search'), content_protocol=co.PROTOCOL)])
    skills = state(source, platform_tool_names={'generate_image','file_search'})
    await skills.initialize()
    r.skill_runtime=skills
    assert not skills.model_candidates()
    assert co.SAVE in {s['function']['name'] for s in schemas(r)}
    await execute(r, p, h, q, [call('activate_skill', {'skill_id': 'chat-image-orchestration'}, 'retired')])
    assert not skills.active and 'SKILL_NOT_AVAILABLE' in p.messages[-1]['content']
    source.load.assert_not_awaited()
    await execute(r,p,h,q,[call('generate_image',{'mode':'text_to_image','prompt':'copied'},'legacy')])
    h._execute_tool_calls.assert_not_awaited()
    assert 'CONTENT_SOURCE_REQUIRED' in str(p.messages[-1])

@pytest.mark.asyncio
async def test_revision_replaces_one_complete_text_without_old_conditions():
    r,p,h,q,row=await registered();old=r.task_execution_state
    replacement='  新版完整原文\r\n新版限制：全部包含在这里。\n禁止项：A，B。  '
    await execute(r,p,h,q,[call(co.SAVE,{'base':ref(row,2),
        'updates':[{'item_index':2,'item':{'body':replacement}}]},'complete-revision')])
    current=co.catalog(r.task_execution_state)[0];_,doc,item=co.load(r.task_execution_state,ref(current,2))
    assert item.materials==co.load(old,ref(row,2))[2].materials
    assert item.options==co.load(old,ref(row,2))[2].options
    args,_=co.compile_input(r.task_execution_state,'generate_image',{},ref(current,2))
    assert args['prompt']==replacement and args['prompt'].encode()==replacement.encode()
    assert '条目限制2' not in args['prompt']
    for extra in ({'shared_conditions':['旧共同要求']},{'updates':[{'item_index':2,'item':{'body':'新版','conditions':['旧限制']}}]}):
        state=r.task_execution_state
        proposal={'base':ref(current,2),'updates':[{'item_index':2,'item':{'body':replacement}}],**extra}
        await execute(r,p,h,q,[call(co.SAVE,proposal,'retired-'+str(len(p.messages)))])
        assert r.task_execution_state==state
        assert 'CONTENT_OBJECT_INVALID' in p.messages[-1]['content']

@pytest.mark.asyncio
async def test_generic_video_and_file_adapters_keep_all_execution_text_and_reject_unsupported_inputs():
    r,p,h,q=case();p.content_controls_available=True
    d={'title':'同一份修订稿','notes':'讨论过程，仅说明',
       'items':[{'title':'修订第二稿','body':'第一段完整文字\n第二段完整文字\n共同交付要求：逐段保留', 'materials':[],'options':{}}]}
    await execute(r,p,h,q,[call(co.SAVE,{'document':d},'script')]);row=co.catalog(r.task_execution_state)[0]
    selection = {'artifact_id': row['artifact_id'], 'item_index': 1}
    video, video_binding = co.compile_input(r.task_execution_state, 'generate_video', {}, selection)
    code, file_binding = co.compile_input(r.task_execution_state, 'code_execute',
                                         {'code':'print(chat_source_text)','description':'导出Word'}, selection)
    assert video_binding['reference']['sha256'] == file_binding['reference']['sha256'] == row['sha256']
    namespace={};exec(code['code'],namespace)
    assert namespace['chat_source_text']==video['prompt']
    assert video['prompt']==d['items'][0]['body']
    assert '讨论过程' not in video['prompt']
    from services.task_execution.video_operations import frozen_request
    parent = {'id':r.task_id, 'turn_id':r.turn_id,
              'input_message_id':r.task_bootstrap.input_message_id, 'base_context_revision':0}
    padded = '  ' + video['prompt'] + '\n'
    snapshot = frozen_request(SimpleNamespace(_task_execution_state=r.task_execution_state),
                              {'prompt':padded}, parent)
    assert snapshot['prompt'].encode() == padded.encode()
    r,p,h,q,row=await registered()
    with pytest.raises(TaskError,match='CONTENT_MATERIAL_NOT_SUPPORTED_BY_TOOL'):
        co.compile_input(r.task_execution_state,'generate_video',{},ref(row))

@pytest.mark.asyncio
async def test_background_registration_has_no_streamed_tool_card():
    from services.handlers.chat.execution_engine import _read_turn
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.adapters.types import StreamChunk, ToolCallDelta
    r,p,h,q=case();p.stream_kwargs={}
    async def stream_chat(**kwargs):
        yield StreamChunk(tool_calls=[ToolCallDelta(index=0,id='provider-call',name=co.SAVE,arguments_delta=json.dumps({'document':document()},ensure_ascii=False))])
        yield StreamChunk(prompt_tokens=1,completion_tokens=1,finish_reason='tool_calls')
    p.adapter.stream_chat=stream_chat;sink=CollectingExecutionSink();blocks=[]
    _,_,calls,previewed=await _read_turn(p,[co.SCHEMA],r.cancellation_event,sink,StreamTotals(),blocks,r)
    assert calls[0]['name']==co.SAVE and not previewed and not blocks and not sink.blocks
    await execute(r,p,h,q,calls,blocks)
    assert len(blocks)==1 and blocks[0]['type']=='text' and not any(b.get('tool_name')==co.SAVE for b in blocks)

@pytest.mark.asyncio
async def test_model_cannot_inject_host_proof_to_bypass_reference_version_selection():
    r,p,h,q,row=await registered()
    canonical,_=co.compile_input(r.task_execution_state,'generate_image',{},ref(row))
    await execute(r,p,h,q,[call('generate_image',canonical,'untrusted-canonical')])
    h._execute_tool_calls.assert_not_awaited()
    assert 'CONTENT_SOURCE_REQUIRED' in str(p.messages[-1])

@pytest.mark.asyncio
@pytest.mark.parametrize('name',['generate_image','generate_video','image_agent'])
async def test_no_parallel_fresh_generation_path_can_bypass_saved_text(name,monkeypatch):
    from unittest.mock import AsyncMock
    r,p,h,q=case();p.content_controls_available=True
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    await execute(r,p,h,q,[call(name,{'mode':'text_to_image','prompt':'另抄的文字'},'unbound')])
    h._execute_tool_calls.assert_not_awaited()
    expected='CONTENT_INPUT_PATH_RETIRED' if name=='image_agent' else 'CONTENT_SOURCE_REQUIRED'
    assert expected in str(p.messages[-1])


@pytest.mark.asyncio
async def test_image_entry_follows_the_bound_workflow_before_native_dispatch(monkeypatch):
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from services.agent.agent_result import AgentResult

    runtime, prepared, handler, request, row = await registered()
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    plan_source = {'plan_id': str(uuid4()), 'revision': 1, 'item_id': str(uuid4())}
    selection = ref(row, 2)
    state = runtime.task_execution_state
    await execute(runtime, prepared, handler, request,
                  [call('generate_image', {'plan_source': plan_source}, 'ordinary-plan')])
    assert 'CONTENT_INPUT_PATH_RETIRED' in str(prepared.messages[-1])
    await execute(runtime, prepared, handler, request,
                  [call('generate_image', {'content_source': selection, 'mode': 'image_to_image'}, 'mixed-fields')])
    assert 'CONTENT_ARGUMENT_CONFLICT' in str(prepared.messages[-1])

    prepared.ecom_workflow = SimpleNamespace(value=SimpleNamespace(kind='main_images'),
        server_selected=lambda: True, barrier=AsyncMock(return_value=None))
    await execute(runtime, prepared, handler, request,
                  [call('generate_image', {'content_source': selection}, 'server-content')])
    assert 'ECOM_PLAN_SOURCE_REQUIRED' in str(prepared.messages[-1])
    assert runtime.task_execution_state == state
    handler._execute_tool_calls.assert_not_awaited()

    prepare = AsyncMock(return_value=({'mode': 'image_to_image', 'prompt': '服务器策划原文'}, [], plan_source))
    runtime.task_image_resource_resolver = SimpleNamespace(prepare_request=prepare)
    runtime.task_operation_store = SimpleNamespace(registry=build_capability_catalog())
    submitted = call('generate_image', {'plan_source': plan_source}, 'server-plan')
    handler._execute_tool_calls.return_value = [(submitted, AgentResult('本地替身'), False, '本地替身')]
    await execute(runtime, prepared, handler, request, [submitted])
    prepare.assert_awaited_once()
    handler._execute_tool_calls.assert_awaited_once()
    assert handler._execute_tool_calls.await_args.args[0] == [submitted]


async def run_content_loop(r, p, h, q, stream, blocks=None, *, sink=None):
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals

    sink = sink if sink is not None else CollectingExecutionSink()
    blocks = [] if blocks is None else blocks
    totals = StreamTotals()
    p.stream_kwargs = {}
    p.adapter.stream_chat = stream
    await _run_loop(handler=h, request=q, prepared=p, cancellation_event=r.cancellation_event,
                    sink=sink, totals=totals, blocks=blocks, runtime=r)
    return sink, blocks, totals


@pytest.mark.parametrize(('request_text', 'image_fields'), [
    ('生成一张米白色产品主图。', {'content_source', 'draft'}),
    ('用上面的第2条提示词出图。', {'content_source'}),
])
async def test_model_request_assembles_one_current_contract_per_tool(monkeypatch, request_text, image_fields):
    from dataclasses import replace

    from schemas.message import TextPart
    from services.adapters.types import StreamChunk
    from services.prompt_builder.layers.static_layer import StaticLayer
    from services.tools import build_legacy_catalog

    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "chat_inline_image_enabled", True)
    runtime, prepared, handler, request = case()
    request = replace(request, content=[TextPart(text=request_text)])
    prepared.messages = [
        {'role': 'system', 'content': StaticLayer.render()},
        {'role': 'user', 'content': request_text},
    ]
    prepared.core_tools = [
        build_legacy_catalog().require(name).to_schema()
        for name in ('generate_image', 'web_search', 'file_search')
    ]
    prepared.tool_context.discovered_tools.add('generate_video')
    requests = []

    async def stream(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        yield StreamChunk(content='请指定要处理的图片？', finish_reason='stop')

    await run_content_loop(runtime, prepared, handler, request, stream)

    assert len(requests) == 1
    sent = requests[0]
    offered = {tool['function']['name']: tool['function'] for tool in sent['tools']}
    assert len(offered) == len(sent['tools'])
    assert set(offered['generate_image']['parameters']['properties']) == image_fields
    assert set(offered['generate_video']['parameters']['properties']) == {'content_source'}
    assert '仅接受文字脚本' in offered['generate_video']['description']
    assert '保留用户给出的来源、时间' in offered['web_search']['parameters']['properties']['query']['description']
    assert 'keyword' in offered['file_search']['parameters']['properties']
    assert {'find_available_tools', 'read_conversation_text', 'save_task_text'} <= offered.keys()
    system = ' '.join(message['content'] for message in sent['messages'] if message['role'] == 'system')
    assert '目录匹配不等于必须使用' in system
    assert '有匹配的可用图片 Skill 时先激活' not in system
    assert 'repair_arguments' in system
    assert '步骤依赖本身不要求向用户确认方案' in system
    handler._execute_tool_calls.assert_not_awaited()


def model_save_chunk(doc, *, call_id='provider-save'):
    from services.adapters.types import StreamChunk, ToolCallDelta

    return StreamChunk(tool_calls=[
        ToolCallDelta(index=0, id=call_id, name=co.SAVE,
                      arguments_delta=json.dumps({'document': doc}, ensure_ascii=False))
    ])


def model_content_output(title, items, *, introduction=''):
    lines = [co.OUTPUT_START, title]
    if introduction:
        lines.append(introduction)
    for item in items:
        lines.extend((co.OUTPUT_ITEM, item['title']))
        if item.get('display_prefix'):
            lines.append(item['display_prefix'])
        lines.extend((co.OUTPUT_BODY, item['body']))
    return '\n'.join([*lines, co.OUTPUT_END])


@pytest.mark.asyncio
@pytest.mark.parametrize('complete', [True, False])
async def test_authored_output_commits_before_showing_once_and_recovers_without_sampling(complete):
    from services.adapters.types import StreamChunk
    from services.handlers.chat.outcome_builder import build_content_parts

    saved = []

    async def checkpoint(point, payload):
        saved.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}

    runtime, prepared, handler, request = case(checkpoint)
    body = '  保留首尾空白和内部行尾。\r\n最后一句。\n  '
    frame = model_content_output('手稿', [{'title': '第一条', 'body': body, 'display_prefix': '### 一、成品'}])
    if not complete:
        frame = frame.removesuffix(co.OUTPUT_END)
    samples = 0

    async def model(**kwargs):
        nonlocal samples
        samples += 1
        assert co.OUTPUT_START not in str(kwargs['messages'][-1]['content'])
        for i in range(0, len(frame), 7):
            yield StreamChunk(content=frame[i:i + 7])
        yield StreamChunk(finish_reason='stop')

    sink, blocks, totals = await run_content_loop(runtime, prepared, handler, request, model)
    assert samples == 1 and not sink.text and len(blocks) == 1
    handler._execute_tool_calls.assert_not_awaited()
    part, = build_content_parts(blocks, fallback_text=totals.text)
    assert co.OUTPUT_START not in part.text
    if not complete:
        assert body in part.text and part.text.count(body) == 1
        assert not co.catalog(runtime.task_execution_state)
        return
    row, = co.catalog(runtime.task_execution_state)
    assert part.content_artifact_id == row['artifact_id']
    assert part.text.count(body) == 1 and part.text.count('### 一、成品') == 1
    assert co.load(runtime.task_execution_state, ref(row))[2].body.encode() == body.encode()
    persisted = saved[-1]['content_blocks']
    assert persisted == blocks == sink.blocks
    await run_content_loop(runtime, prepared, handler, request, model, blocks=persisted)
    assert samples == 1 and len(co.objects(runtime.task_execution_state)) == 1


@pytest.mark.asyncio
async def test_mixed_draft_preserves_visible_text_without_saving_second_version():
    from services.adapters.types import StreamChunk
    from services.handlers.chat.outcome_builder import build_content_parts

    r, p, h, q = case()
    complete = document()
    incomplete = copy.deepcopy(complete)
    incomplete['items'][1]['body'] = '另一份草稿'
    body = complete['items'][1]['body']
    rounds = 0

    async def stream(**kwargs):
        nonlocal rounds
        rounds += 1
        yield StreamChunk(content=body)
        yield model_save_chunk(incomplete)

    sink, blocks, totals = await run_content_loop(r, p, h, q, stream)
    part, = build_content_parts(blocks, fallback_text=totals.text)
    assert rounds == 1 and part.text == sink.text == body
    assert not co.catalog(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_truncated_output_preserves_text_but_cannot_be_used_as_input():
    from services.adapters.types import StreamChunk
    from services.handlers.chat.outcome_builder import build_content_parts

    r, p, h, q = case()
    body = '模型已经写出的内容'

    async def stream(**kwargs):
        yield StreamChunk(content=body)
        yield StreamChunk(finish_reason='length')

    sink, blocks, totals = await run_content_loop(r, p, h, q, stream)
    part, = build_content_parts(blocks, fallback_text=totals.text)
    assert sink.text == body and part.text.startswith(body)
    assert '本轮输出未写完' in part.text and part.content_incomplete is True
    assert not co.catalog(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_answer_still_delivered_when_registration_tool_is_available():
    from services.adapters.types import StreamChunk
    from services.handlers.chat.execution_sink import CollectingExecutionSink

    r, p, h, q = case()
    delivery = CollectingExecutionSink()

    async def stream(**kwargs):
        assert co.SCHEMA in kwargs['tools']
        yield StreamChunk(content='普通')
        assert delivery.text == '普通'
        yield StreamChunk(content='回答')

    sink, blocks, totals = await run_content_loop(r, p, h, q, stream, sink=delivery)
    assert sink.text == totals.text == '普通回答'
    assert blocks == [{'type': 'text', 'text': '普通回答'}]
    assert not co.catalog(r.task_execution_state)


@pytest.mark.asyncio
async def test_image_submission_narration_requires_receipt():
    from dataclasses import replace
    from schemas.message import TextPart
    from services.adapters.types import StreamChunk
    r, p, h, q = case()
    q = replace(q, content=[TextPart(text='用第四个方案做成图片')])

    async def promise(**kwargs):
        yield StreamChunk(content='我来登记第四条，然后')
        yield StreamChunk(content='提交生图。图片任务已提交。')

    sink, blocks, totals = await run_content_loop(r, p, h, q, promise)
    shown, = [block['text'] for block in blocks if block['type'] == 'text']
    assert shown == totals.text == sink.text == '本轮尚未收到图片任务的受理回执。'
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_item_execution_rejects_a_rewritten_new_document_before_dispatch():
    from dataclasses import replace
    from schemas.message import TextPart
    from services.handlers.chat import content_tools

    runtime, prepared, handler, request, row = await registered()
    request = replace(request, content=[TextPart(text='第一个方案帮我做成图片')])
    assert content_tools.selects_previous_item(request)
    schema, = content_tools.model_tools([co.SCHEMA], reuse_source=True)
    assert 'document' not in schema['function']['parameters']['properties']
    await execute(runtime, prepared, handler, request, [call(co.SAVE, {
        'document': {'title': '新名称', 'items': [{'title': '转写', 'body': '模型重写了已选原文'}]},
        'new_content': True, 'continue_execution': True,
    }, 'relabel')])
    assert json.loads(prepared.messages[-1]['content'])['code'] == 'CONTENT_EXECUTION_SOURCE_REQUIRED'
    assert co.catalog(runtime.task_execution_state) == [row]
    handler._execute_tool_calls.assert_not_awaited()
    for independent in ('画一张有第二条鱼的图片', '写四条方案，再把第二条做成图片', '只改第二条文案'):
        assert not content_tools.selects_previous_item(replace(request, content=[TextPart(text=independent)]))


@pytest.mark.asyncio
async def test_author_contract_keeps_user_format_and_original_when_model_omits_optional_fields():
    from dataclasses import replace
    from schemas.message import TextPart, ImagePart
    from services.adapters.types import StreamChunk

    runtime, prepared, handler, request = case()
    source = str(runtime.task_bootstrap.input_message_id)
    request = replace(request, content=[TextPart(text='参考这张图写两条提示词，不要改变原图结构'),
                                        ImagePart(name='产品.png', workspace_path='product.png')],
                      context_anchor=SimpleNamespace(base_revision=0, input_message_id=source))
    original = '正向\r\n 保留封面。\n负面\n禁止透视变形。'
    doc = {'title': '设计', 'items': [{'title': '甲', 'body': '第一份原文。'}, {'title': '乙', 'body': original}]}

    async def model(**kwargs):
        offered = next(t['function'] for t in kwargs['tools'] if t['function']['name'] == co.SAVE)
        assert offered['parameters']['required'] == ['document']
        assert 'message_source' not in offered['parameters']['properties']
        yield model_save_chunk(doc)
        yield StreamChunk(finish_reason='tool_calls')

    _, blocks, _ = await run_content_loop(runtime, prepared, handler, request, model)
    part, = [b for b in blocks if b['type'] == 'text']
    assert part['text'].count(original) == 1
    row, = co.catalog(runtime.task_execution_state)
    args, _ = co.compile_input(runtime.task_execution_state, 'generate_image', {}, ref(row, 2))
    assert args['prompt'].encode() == original.encode() and args['mode'] == 'image_to_image'
    assert args['references'] == [{'message_id': source, 'content_index': 1, 'role': '原图参考'}]
    with pytest.raises(TaskError, match='CONTENT_ARGUMENT_CONFLICT'):
        co.compile_input(runtime.task_execution_state, 'generate_image', {}, {**ref(row, 2), 'without_reference': True})


def test_quoted_source_never_omits_an_additional_unscoped_constraint():
    from services.task_execution.content_groups import index_document

    for suffix in ('\n禁止出现水印。', '\n---\n负面提示词：禁止出现水印。', '\n> 另一个候选'):
        assert index_document('### 方案 1\n\n> 执行原文。' + suffix) is None


@pytest.mark.asyncio
async def test_unresolved_product_image_cannot_be_silently_omitted_by_mode_option():
    from dataclasses import replace
    from schemas.message import TextPart, ImagePart

    r, p, h, q = case()
    q = replace(q, content=[TextPart(text='写两个提示词'), ImagePart(name='A.png', workspace_path='a.png'),
                            ImagePart(name='B.png', workspace_path='b.png')],
                context_anchor=SimpleNamespace(input_message_id=str(r.task_bootstrap.input_message_id)))
    draft = {'title': '方案', 'items': [{'title': '一', 'body': '保持构图',
        'materials': [{'reference': {'message_id': str(r.task_bootstrap.input_message_id), 'content_index': 1},
                       'usage': 'analysis', 'role': '分析 A'}], 'options': {'mode': 'text_to_image'}}]}
    await execute(r, p, h, q, [call(co.SAVE, {'document': draft}, 'save')])
    row, = co.catalog(r.task_execution_state)
    with pytest.raises(TaskError, match='CONTENT_SOURCE_IMAGE_SELECTION_REQUIRED'):
        co.compile_input(r.task_execution_state, 'generate_image', {}, ref(row))
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_reference_by_title_and_last_edit_resolves_same_saved_input():
    r, p, h, q, _ = await registered()
    def visible_items():
        facts = json.loads(context_message(r)['content'].split('\n')[-1])
        group, = facts['content_objects']
        return group['items']

    second = visible_items()[1]
    await execute(r, p, h, q, [call(co.SAVE, {
        'base': second['content_source'],
        'updates': [{'item_index': 2, 'item': {'body': '修订后的完整原文。\n负面提示词：保留装订孔。'}}],
    }, 'revise-second')])
    items = visible_items()
    named, = [item for item in items if item['title'] == second['title']]
    changed, = [item for item in items if item.get('changed_in_revision') == 2]
    assert named == changed == items[1]
    assert named['content_source'] != second['content_source']
    args, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, named['content_source'])
    assert args['prompt'] == '修订后的完整原文。\n负面提示词：保留装订孔。'
    assert args['references'][0]['message_id'] == 'original' and args['aspect_ratio'] == '1:1'
    with pytest.raises(TaskError, match='CONTENT_BASE_VERSION_CHANGED'):
        co.compile_input(r.task_execution_state, 'generate_image', {}, second['content_source'])


@pytest.mark.asyncio
@pytest.mark.parametrize('instruction', [
    '保留这张图里的红色福字，把背景换成棕红色实木桌子',
    '参考这张图，保留红色福字，把背景换成棕红色实木桌子',
])
async def test_explicit_current_image_binding_survives_different_wording(instruction):
    from dataclasses import replace
    from schemas.message import ImagePart, TextPart

    r, p, h, q = case()
    source_id = str(r.task_bootstrap.input_message_id)
    q = replace(q, content=[TextPart(text=instruction), ImagePart(name='fu.png', workspace_path='fu.png')],
                context_anchor=SimpleNamespace(input_message_id=source_id))
    draft = {'title': '换背景', 'items': [{'title': '福字', 'body': instruction}]}

    await execute(r, p, h, q, [call(co.SAVE, {'document': draft, 'continue_execution': True}, 'invalid')])
    failure = json.loads(p.messages[-1]['content'])
    assert failure['code'] == co.ATTACHMENT_USAGE_REQUIRED
    assert failure['issues'][0]['path'] == '$.document.items[0].materials'
    assert failure['material_example']['reference'] == {'message_id': source_id, 'content_index': 1}
    assert not co.objects(r.task_execution_state)

    draft['items'][0]['materials'] = [failure['material_example']]
    await execute(r, p, h, q, [call(co.SAVE, {'document': draft, 'continue_execution': True}, 'corrected')])
    row, = co.catalog(r.task_execution_state)
    native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(row))
    assert native['mode'] == 'image_to_image'
    assert native['prompt'] == instruction
    assert native['references'] == [{'message_id': source_id, 'content_index': 1, 'role': '编辑底图'}]


@pytest.mark.asyncio
async def test_save_repair_stops_after_three_failures_even_when_invalid_keys_change():
    from services.adapters.types import StreamChunk

    r, p, h, q = case()
    offers = []

    async def stream(**kwargs):
        key = ['attachments', 'image_usage', 'attachment_usage'][len(offers)]
        offers.append(key)
        yield model_save_chunk({'title': '稿件', 'items': [{'title': '正文', 'body': '完整正文', key: []}]})
        yield StreamChunk(finish_reason='tool_calls')

    _, blocks, _ = await run_content_loop(r, p, h, q, stream)
    assert len(offers) == 3
    assert [b['text'] for b in blocks if b.get('content_delivery_final')] == [co.SAVE_FAILED_TEXT]
    assert not co.objects(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_discovery_reads_visible_save_contract_without_registering_a_business_tool(monkeypatch):
    from services.handlers.chat import content_tools

    monkeypatch.setattr('services.tools.runtime_context._check_identity', lambda *_: None)
    r, p, h, q = case()
    p.content_controls_available = True
    h.db = object()
    projected, = content_tools.model_tools([co.SCHEMA], first_delivery=True)
    p.content_visible_tools = {co.SAVE: projected['function']}
    await execute(r, p, h, q, [call(content_tools.DISCOVER, {'query': co.SAVE}, 'discover')])
    result = json.loads(p.messages[-1]['content'])
    assert result == {'ok': True, 'tools': [projected['function']], 'status': 'ready'}
    assert content_tools.checkpoint_state(r)['names'] == []
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_web_search_invalid_filter_cannot_be_dropped_before_chat_dispatch():
    from services.agent.tool_args_validator import validate_tool_args
    from services.tools.catalog import build_tool_catalog

    r, p, h, q = case()
    offer = build_tool_catalog().require('web_search').to_schema()
    p.content_visible_tools = {'web_search': offer['function']}
    raw = {'query': '查询产品', 'domain_filter': 'example.com'}
    untouched, error = validate_tool_args('web_search', raw, [offer])
    assert untouched == raw and json.loads(error)['issues'][0]['path'] == '$.domain_filter'
    await execute(r, p, h, q, [call('web_search', raw, 'search')])
    h._execute_tool_calls.assert_not_awaited()
    assert 'MODEL_TOOL_ARGUMENTS_INVALID' in json.dumps(p.messages[-1]['content'])


@pytest.mark.asyncio
async def test_explicit_analysis_material_never_becomes_native_reference():
    from dataclasses import replace
    from schemas.message import ImagePart, TextPart

    r, p, h, q = case()
    source_id = str(r.task_bootstrap.input_message_id)
    q = replace(q, content=[TextPart(text='参考这张图片的结构作分析，做一张新的图'), ImagePart(name='input.png', workspace_path='input.png')],
                context_anchor=SimpleNamespace(input_message_id=source_id))
    draft = {'title': '新图', 'items': [{'title': '方案', 'body': '绘制全新图案。', 'materials': [
        {'reference': {'message_id': source_id, 'content_index': 1}, 'role': '构图分析', 'usage': 'analysis'},
    ]}]}
    await execute(r, p, h, q, [call(co.SAVE, {'document': draft, 'continue_execution': True}, 'save')])
    row, = co.catalog(r.task_execution_state)
    native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(row))
    assert native['mode'] == 'text_to_image' and native['references'] == []


@pytest.mark.asyncio
async def test_read_exact_saved_item_then_edit_text_without_generating(monkeypatch):
    from services.handlers.chat import content_tools

    r, p, h, q, row = await registered()
    h.db = object()
    monkeypatch.setattr(content_tools, '_check_identity', lambda *_: None)
    before = r.task_execution_state
    await execute(r, p, h, q, [call(content_tools.READ, {'content_source': ref(row, 2)}, 'inspect')])
    result = json.loads(p.messages[-1]['content'])
    assert result['ok'] and result['content_source'] == ref(row, 2)
    assert result['item']['body'] == document()['items'][1]['body']
    assert result['item']['materials'] == document()['items'][1]['materials']
    assert r.task_execution_state is before

    edited_body = result['item']['body'] + '\n背景再浅一点。'
    await execute(r, p, h, q, [call(co.SAVE, {
        'base': result['content_source'], 'updates': [{'item_index': 2, 'item': {'body': edited_body}}],
    }, 'edit')])
    latest, = co.catalog(r.task_execution_state)
    _, revised, _ = co.load(r.task_execution_state, ref(latest))
    _, original, _ = co.load(before, ref(row))
    assert latest['revision'] == row['revision'] + 1
    assert revised.items[0] == original.items[0] and revised.items[2] == original.items[2]
    assert revised.items[1].body == edited_body
    assert revised.items[1].materials == original.items[1].materials
    h._execute_tool_calls.assert_not_awaited()

    await execute(r, p, h, q, [call(content_tools.READ, {'content_source': ref(row, 2)}, 'outdated')])
    assert json.loads(p.messages[-1]['content'])['code'] == 'CHAT_CONTENT_VERSION_UNAVAILABLE'


@pytest.mark.asyncio
async def test_read_visual_delivers_one_image_with_original_tool_result_pair(monkeypatch):
    from services.handlers.chat import content_tools
    from services.task_execution.pending_batch import pending_calls
    from unittest.mock import Mock

    r, p, h, q = case()
    h.db = object()
    p.content_controls_available = True
    reference = {'message_id': str(uuid4()), 'content_index': 3}
    preview = 'https://images.invalid/selected.png'
    view = Mock(return_value=(
        {'reference': reference, 'visually_present': True}, preview))
    monkeypatch.setattr(content_tools, 'read_image_view', view)
    monkeypatch.setattr(content_tools, 'visual_budget_remaining', lambda _: 1)
    await execute(r, p, h, q, [
        call(content_tools.READ, {'view_image': reference}, 'selected'),
        call(content_tools.READ, {'view_image': reference}, 'over-budget'),
    ])
    answers = {m['tool_call_id']: json.loads(m['content']) for m in p.messages if m['role'] == 'tool'}
    assert answers['selected'] == {'ok': True, 'reference': reference, 'visually_present': True}
    assert answers['over-budget']['code'] == 'CHAT_IMAGE_VISUAL_BUDGET_EXHAUSTED'
    assert p.messages[-1]['content'][-1] == {'type': 'image_url', 'image_url': {'url': preview}}
    assert not pending_calls(p.messages)[0]
    assert view.call_count == 1
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_edit_uses_historical_output_instead_of_previous_prompt_input():
    from dataclasses import replace
    from schemas.message import TextPart

    r, p, h, q, prior = await registered()
    # Chosen output and the original prompt's input are different occurrences.
    image_ref = {'message_id': str(uuid4()), 'content_index': 3}
    q = replace(q, content=[TextPart(text='第二张再浅一点')])
    draft = {'title': '第二张调浅', 'items': [{
        'title': '修改已生成图', 'body': '在这张成图上只把背景调浅，保留其他部分。',
        'materials': [{'reference': image_ref, 'role': '所选成图', 'usage': 'input'}],
    }]}
    await execute(r, p, h, q, [call(co.SAVE, {
        'document': draft, 'new_content': True, 'continue_execution': True,
    }, 'edit-output')])

    edit = next(row for row in co.catalog(r.task_execution_state) if row['artifact_id'] != prior['artifact_id'])
    native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(edit))
    original, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(prior, 2))
    assert native['mode'] == 'image_to_image'
    assert native['references'] == [{**image_ref, 'role': '所选成图'}]
    assert native['references'] != original['references']
    assert native['prompt'] == draft['items'][0]['body']


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed', [False, True])
async def test_inline_image_failures_leave_no_original_or_native_operation(mixed):
    from unittest.mock import AsyncMock
    from services.handlers.chat import content_tools
    from services.task_execution.inline_image_action import KEY
    from services.tools.catalog import build_tool_catalog

    r, p, h, q = case()
    p.content_controls_available = True
    image = build_tool_catalog().require('generate_image').to_schema()
    offered, = content_tools.model_tools([image], inline_image=True)
    p.content_visible_tools = {'generate_image': offered['function']}
    verify = AsyncMock(side_effect=ValueError('IMAGE_ORIGINAL_UNAVAILABLE'))
    r.task_image_resource_resolver = SimpleNamespace(prepare_request=verify)
    calls = [call('generate_image', {'draft': {'title': '即时图片', 'body': '完整图像要求'}}, 'inline')]
    if mixed:
        calls.append(call('web_search', {'query': '独立请求'}, 'sibling'))

    await execute(r, p, h, q, calls)
    outputs = [json.dumps(m['content'], ensure_ascii=False) for m in p.messages if m['role'] == 'tool']
    assert len(outputs) == len(calls)
    assert all(('CHAT_INLINE_IMAGE_BATCH_REQUIRED' if mixed else 'CHAT_INLINE_IMAGE_INPUT_INVALID') in entry
               for entry in outputs)
    assert KEY not in r.checkpoint_base
    assert not co.objects(r.task_execution_state)
    assert verify.await_count == (0 if mixed else 1)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_inline_call_without_prepared_checkpoint_stops_before_dispatch():
    from services.conversation_state import ConversationStopRequested
    from services.handlers.chat.execution_engine import _assistant_tool_message, _execute_tools
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.pending_batch import recover_pending_batch

    r, p, h, q = case()
    original = call('generate_image', {'draft': {'title': '即时图', 'body': '原始需求'}}, 'inline')
    p.messages.append(_assistant_tool_message('', [original]))
    with pytest.raises(ConversationStopRequested, match='inline_image_checkpoint_unconfirmed'):
        await recover_pending_batch(
            prepared=p, runtime=r, request=q, handler=h, sink=CollectingExecutionSink(),
            totals=StreamTotals(), blocks=[], cancellation_event=r.cancellation_event, model_round=1,
            execute_tools=_execute_tools, repeated_tool_call_guard=None)
    h._execute_tool_calls.assert_not_awaited()
    assert not co.objects(r.task_execution_state)


@pytest.mark.asyncio
@pytest.mark.parametrize('question', [
    '把第二张图的背景改成浅黄色，主体保持原样。请只写一段修改提示词，不要出图。',
    '只写一段改图提示词，先不要实际生成图片。',
])
async def test_prompt_only_request_delivers_answer_without_image_receipt(question):
    from dataclasses import replace
    from schemas.message import TextPart
    from services.adapters.types import StreamChunk

    runtime, prepared, handler, request = case()
    request = replace(request, content=[TextPart(text=question)])
    reply = '保留蓝色正方形的形状、位置和颜色，仅把背景换成浅黄色。'

    async def stream(**kwargs):
        yield StreamChunk(content=reply)
        yield StreamChunk(finish_reason='stop')

    sink, blocks, totals = await run_content_loop(runtime, prepared, handler, request, stream)
    assert blocks[-1]['text'] == sink.text == totals.text == reply
    handler._execute_tool_calls.assert_not_awaited()


def test_prompt_authoring_can_be_followed_by_explicit_image_execution():
    from types import SimpleNamespace
    from schemas.message import TextPart
    from services.handlers.chat.content_tools import image_execution_request

    assert image_execution_request(SimpleNamespace(content=[TextPart(
        text='先写一段提示词，再用它生成一张图片。',
    )]))
    assert image_execution_request(SimpleNamespace(content=[TextPart(
        text='不要生成第一张图片，只用第二张图出图。',
    )]))
