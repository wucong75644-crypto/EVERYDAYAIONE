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
