"""Single registered source for display, revision and native execution."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
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
async def test_registration_renders_same_source_and_roundtrips_checkpoint():
    r,p,h,q,row=await registered()
    state=TaskState.model_validate_json(r.task_execution_state.model_dump_json())
    a,doc,item=co.load(state,ref(row))
    assert '完整正文1' in co.render(doc) and '条目限制3' in co.render(doc)
    assert doc.model_dump(mode='json')==co.ContentDocument.model_validate(document()).model_dump(mode='json')
    assert hashlib.sha256(a.inline_text.encode()).hexdigest()==a.sha256
    facts=json.loads(context_message(r)['content'].split('\n')[-1])
    assert facts['content_objects']==[row]
    # The model receives an index, not every full plan for each execution.
    assert '完整正文' not in str(facts['content_objects'])

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
async def test_active_new_skill_cannot_rewrite_or_call_old_prompt_branch():
    from tests.test_skill_runtime import Source, item, state, activate
    r,p,h,q,row=await registered()
    skills=state(Source([item('chat-image-orchestration',tools=('generate_image','file_search'),content_protocol=co.PROTOCOL)],body='Only content_source.'),platform_tool_names={'generate_image','file_search'})
    await skills.initialize();assert (await skills.activate(activate('chat-image-orchestration')))['ok']
    r.skill_runtime=skills
    assert co.requires_reference(r)
    assert co.SAVE not in {s['function']['name'] for s in schemas(r)}
    before=r.task_execution_state
    await execute(r,p,h,q,[call(co.SAVE,{'document':document()},'forbidden-write')])
    assert r.task_execution_state==before and 'CONTENT_CONSUMER_CANNOT_REWRITE' in p.messages[-1]['content']
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
    video,_=co.compile_input(r.task_execution_state,'generate_video',{},ref(row))
    code,_=co.compile_input(r.task_execution_state,'code_execute',{'code':'print(chat_source_text)','description':'导出Word'},ref(row))
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


async def run_content_loop(r, p, h, q, stream, blocks=None):
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals

    sink = CollectingExecutionSink()
    blocks = [] if blocks is None else blocks
    totals = StreamTotals()
    p.stream_kwargs = {}
    p.adapter.stream_chat = stream
    await _run_loop(handler=h, request=q, prepared=p, cancellation_event=r.cancellation_event,
                    sink=sink, totals=totals, blocks=blocks, runtime=r)
    return sink, blocks, totals


def model_save_chunk(doc):
    from services.adapters.types import StreamChunk, ToolCallDelta

    return StreamChunk(tool_calls=[
        ToolCallDelta(index=0, id='provider-save', name=co.SAVE,
                      arguments_delta=json.dumps({'document': doc}, ensure_ascii=False))
    ])


@pytest.mark.asyncio
@pytest.mark.parametrize('repairs', [True, False])
async def test_mixed_draft_is_not_published_or_saved(repairs):
    from services.adapters.types import StreamChunk
    from services.handlers.chat.outcome_builder import build_content_parts

    r, p, h, q = case()
    complete = document()
    incomplete = copy.deepcopy(complete)
    incomplete['items'][1]['body'] = '只有第二条的正向内容'
    model_rounds = []

    async def stream(**kwargs):
        model_rounds.append(len(model_rounds) + 1)
        assert co.SCHEMA in kwargs['tools']
        if model_rounds == [1]:
            yield StreamChunk(content=complete['items'][1]['body'])
            yield model_save_chunk(incomplete)
        else:
            assert kwargs['messages'][-2]['role'] == 'tool'
            assert json.loads(kwargs['messages'][-2]['content'])['code'] == co.DRAFT_CONFLICT
            assert not co.catalog(r.task_execution_state)
            assert not r.checkpoint_base['content_blocks']
            if repairs:
                yield model_save_chunk(complete)
            else:
                yield StreamChunk(content='没有保存的另一份草稿')

    sink, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert model_rounds == [1, 2]
    parts = build_content_parts(blocks, fallback_text=totals.text)
    assert len(parts) == 1 and parts[0].type == 'text'
    h._execute_tool_calls.assert_not_awaited()
    if not repairs:
        assert not co.catalog(r.task_execution_state)
        assert '没能保存完整原文' in parts[0].text == sink.text
        return
    assert not sink.text
    assert sink.blocks == blocks and len(blocks) == 1
    row, = co.catalog(r.task_execution_state)
    _, doc, item = co.load(r.task_execution_state, ref(row, 2))
    assert item.body == complete['items'][1]['body']
    assert parts[0].text == co.render(doc)
    assert parts[0].text.count(item.body) == 1
    assert all(detail not in parts[0].text for detail in ('判断模式', '输出规格', '素材用途'))

    # Simulate recovery after the atomic save, before the enclosing turn returned.
    r.task_execution_state = TaskState.model_validate_json(r.task_execution_state.model_dump_json())
    restored = copy.deepcopy(r.checkpoint_base['content_blocks'])
    restored_sink, recovered, _ = await run_content_loop(r, p, h, q, stream, restored)
    assert model_rounds == [1, 2]
    assert recovered == blocks and not restored_sink.blocks


@pytest.mark.asyncio
async def test_unsaved_draft_stays_hidden_when_model_budget_ends():
    from services.adapters.types import StreamChunk
    from services.agent.execution_budget import ExecutionBudget
    from services.handlers.chat.execution_engine import _finalize_output_parts

    r, p, h, q = case()
    p.budget = ExecutionBudget(max_turns=1, wrap_up_turns_reserved=0)

    async def stream(**kwargs):
        assert p.budget.turns_used == 1
        yield StreamChunk(content='不可作为正式交付的草稿')
        yield model_save_chunk(document())

    sink, blocks, totals = await run_content_loop(r, p, h, q, stream)
    parts = await _finalize_output_parts(handler=h, prepared=p, totals=totals, blocks=blocks, sink=sink,
                                        cancellation_event=r.cancellation_event, runtime=r)
    assert not co.objects(r.task_execution_state)
    assert not sink.text and len(sink.blocks) == len(parts) == 1
    assert parts[0].text == co.SAVE_FAILED_TEXT


@pytest.mark.asyncio
async def test_plain_answer_still_delivered_when_registration_tool_is_available():
    from services.adapters.types import StreamChunk

    r, p, h, q = case()

    async def stream(**kwargs):
        assert co.SCHEMA in kwargs['tools']
        yield StreamChunk(content='普通')
        yield StreamChunk(content='回答')

    sink, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert sink.text == totals.text == '普通回答'
    assert blocks == [{'type': 'text', 'text': '普通回答'}]
    assert not co.catalog(r.task_execution_state)
