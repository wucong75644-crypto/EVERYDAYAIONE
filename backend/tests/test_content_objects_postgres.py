"""Registered objects use the existing fenced save and followup transfer."""
import copy
from dataclasses import replace
from uuid import UUID
import psycopg
import pytest
from services.task_execution import content_objects as co
from services.task_execution.direct_loop import end_candidate
from services.task_execution.contracts import TaskState
from tests.test_content_objects import document, ref
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call
from tests.test_task_execution_postgres import save
from tests.test_model_directed_dispatch_postgres import dispatch_db, controls_db
from tests.test_skill_composition_postgres import seed, connect
from tests.test_task_execution_followup_reuse_postgres import (followup_db,new_turn,adopt,continuation_db,native_result_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db)

@pytest.fixture(scope='module')
def content_db(dispatch_db):
    root=__import__('pathlib').Path(__file__).parents[1]
    with psycopg.connect(dispatch_db) as c:
        for migration in ('376_agent_task_image_method_handoff','377_agent_task_content_objects'):
            c.execute((root/f'migrations/{migration}.sql').read_text())
    yield dispatch_db
    with psycopg.connect(dispatch_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_events');c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for migration in ('377_agent_task_content_objects','376_agent_task_image_method_handoff'):
            c.execute((root/f'migrations/rollback/{migration}_rollback.sql').read_text())

@pytest.mark.asyncio
async def test_registered_object_survives_real_new_turn_and_patch_without_new_review(content_db):
    followup_db=content_db
    f=seed(followup_db)
    async def checkpoint(point,payload):
        with connect(f) as c:return save(c,f,payload,expected=payload.get('_task_execution_expected_revision',0))
    r,p,h,q=case(checkpoint)
    r.task_bootstrap=replace(r.task_bootstrap,task_id=UUID(f['task']),conversation_id=UUID(f['conv']),actor_user_id=UUID(f['user']),
        input_message_id=UUID(f['message']),execution_identity=r.task_bootstrap.execution_identity.model_copy(update={'workspace_owner_id':f['user']}))
    r.task_id=f['task'];r.conversation_id=f['conv'];r.turn_id=f['turn']
    p.execution_context=replace(p.execution_context,actor_user_id=f['user'],workspace_owner_id=f['user'],conversation_id=f['conv'],task_id=f['task'])
    q=replace(q,user_id=f['user'],conversation_id=f['conv'],task_id=f['task']);p.content_controls_available=True
    await execute(r,p,h,q,[call(co.SAVE,{'document':document()},'first-document')])
    original=co.catalog(r.task_execution_state)[0]
    # The actual end-of-turn reducer closes saved work before the next user turn.
    before=r.task_execution_state
    closed=end_candidate(before)
    cp=copy.deepcopy(r.checkpoint_base);cp['_task_execution']=closed.model_dump(mode='json')
    cp['_task_execution_expected_revision']=before.revision
    with connect(f) as c:assert save(c,f,cp,expected=before.revision)['outcome']=='saved'
    old,new,source,obs,merged=new_turn(followup_db,old=f,text='继续原任务，修改第二条')
    assert co.catalog(TaskState.model_validate(source['state']))==[original]
    with connect(new) as c:
        reply=adopt(c,new,source,obs,merged)
        assert reply['outcome']=='saved'
        state=TaskState.model_validate(reply['payload']['_task_execution'])
        assert co.catalog(state)==[original]
        assert co.load(state,ref(original,2))[2].body==document()['items'][1]['body']
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(new['task'],)).fetchone()[0]==0
        assert str(c.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()[0])==new['task']
    async def next_checkpoint(point,payload):
        with connect(new) as c:return save(c,new,payload,expected=payload.get('_task_execution_expected_revision',0))
    r2,p2,h2,q2=case(next_checkpoint)
    r2.task_bootstrap=replace(r.task_bootstrap,task_id=UUID(new['task']),input_message_id=UUID(new['message']))
    r2.task_id=new['task'];r2.conversation_id=new['conv'];r2.turn_id=new['turn']
    r2.task_execution_state=state;r2.committed_task_execution_revision=state.revision
    r2.checkpoint_base=copy.deepcopy(reply['payload']);p2.messages=copy.deepcopy(reply['payload']['messages'])
    p2.execution_context=replace(p.execution_context,task_id=new['task']);p2.content_controls_available=True
    q2=replace(q,task_id=new['task'])
    await execute(r2,p2,h2,q2,[call(co.SAVE,{'base':ref(original,2),
        'updates':[{'item_index':2,'item':{'body':'修订正文\n修订后的完整限制'}}]},'second-revision')])
    updated=co.catalog(r2.task_execution_state)[0]
    assert updated['revision']==2
    assert co.load(r2.task_execution_state,ref(updated,2))[2].materials==co.load(state,ref(original,2))[2].materials
    from psycopg.types.json import Jsonb
    with connect(new) as c:
        raw=r2.task_execution_state.model_dump(mode='json')
        assert c.execute('SELECT agent_task_direct_records_valid(%s)',(Jsonb(raw),)).fetchone()[0]
        forged=copy.deepcopy(raw);forged['steps'][0]['capability']='platform.erp_execute'
        assert not c.execute('SELECT agent_task_direct_records_valid(%s)',(Jsonb(forged),)).fetchone()[0]
        forged=copy.deepcopy(raw);forged['artifacts'][0]['validations']=['artifact.exists']
        assert not c.execute('SELECT agent_task_direct_records_valid(%s)',(Jsonb(forged),)).fetchone()[0]
    h._execute_tool_calls.assert_not_awaited();h2._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_content_selection_adopts_saved_task_before_business_dispatch(content_db):
    from psycopg.types.json import Jsonb
    from services.task_execution.followup import FollowupRestart

    source_turn = seed(content_db)
    r, p, h, q = case()
    old_task = source_turn['task']
    b = r.task_bootstrap
    b = replace(b, task_id=UUID(old_task), conversation_id=UUID(source_turn['conv']),
                actor_user_id=UUID(source_turn['user']), input_message_id=UUID(source_turn['message']),
                execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id': source_turn['user']}))
    r.task_bootstrap = b
    r.task_id, r.turn_id, r.conversation_id = old_task, source_turn['turn'], source_turn['conv']
    p.content_controls_available = True
    await execute(r, p, h, q, [call(co.SAVE, {'document': document()}, 'registered')])
    original = r.task_execution_state
    row, = co.catalog(original)
    with connect(source_turn) as connection:
        assert save(connection, source_turn, r.checkpoint_base, expected=0)['outcome'] == 'saved'

    text = '把上一份方案的第一条生成图片'
    _, new, source, _, _ = new_turn(content_db, old=source_turn, text=text)

    async def checkpoint(point, payload):
        with connect(new) as connection:
            return connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                (new['task'], new['token'], point.value,
                 Jsonb({**payload, 'safe_point': point.value}))).fetchone()[0]

    runtime, prepared, handler, request = case(checkpoint)
    runtime.task_id, runtime.turn_id, runtime.conversation_id = new['task'], new['turn'], new['conv']
    runtime.task_bootstrap = replace(b, task_id=UUID(new['task']), input_message_id=UUID(new['message']))
    runtime.task_followup_candidate = source
    prepared.content_controls_available = True
    prepared.messages = [{'role': 'user', 'content': text}]
    prepared.execution_context = replace(prepared.execution_context, task_id=new['task'],
        conversation_id=new['conv'], actor_user_id=new['user'], workspace_owner_id=new['user'])

    async def transfer(selected, observation, payload):
        assert selected == source
        assert observation == {'relation': 'continue', 'evidence': [text]}
        with connect(new) as connection:
            return adopt(connection, new, selected, observation, payload)

    runtime.task_followup_adopt = transfer
    with pytest.raises(FollowupRestart):
        await execute(runtime, prepared, handler, request, [
            call('generate_image', {'content_source': ref(row)}, 'select-saved-first'),
        ])
    handler._execute_tool_calls.assert_not_awaited()
    with connect(new) as connection:
        saved = connection.execute(
            'SELECT owner_task_id, state FROM agent_task_states WHERE root_task_id=%s',
            (old_task,)).fetchone()
        assert str(saved[0]) == new['task']
        assert connection.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',
                                  (new['task'],)).fetchone()[0] == 0
    recovered = TaskState.model_validate(saved[1])
    assert recovered.root_task_id == original.root_task_id and recovered.artifacts == original.artifacts
    assert co.compile_input(recovered, 'generate_image', {}, ref(row))[0]['prompt'] == document()['items'][0]['body']


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['text-to-image', 'image-to-image', 'recorded-first', 'recorded-fifth'])
async def test_chat_executes_from_registration_to_page_receipt(content_db, monkeypatch, tmp_path, scenario):
    import base64
    import hashlib
    import json
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4
    import httpx
    from PIL import Image
    from psycopg.types.json import Jsonb

    from core.config import get_settings
    from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
    from core.local_db import LocalDBClient
    from schemas.message import ImagePart, TextPart
    from services.adapters.kie.client import KieClient
    from services.adapters.kie.image_adapter import KieImageAdapter
    from services.adapters.types import StreamChunk, ToolCallDelta
    from services.file_executor import FileExecutor
    from services.handlers.chat_image_lifecycle import ChatImageLifecycle
    from services.handlers.chat_image_request import REQUEST_KEY, ChatImageNotAcceptedError
    from services.handlers.image_handler import ImageHandler
    from services.media_tool_executor import MediaToolMixin
    from services.task_execution.direct_loop import restore_plan
    from services.task_execution.followup import FollowupRestart
    from services.task_execution.image_resources import create_resolver
    from services.task_execution.user_changes import restore as restore_changes
    from services.tools import ToolCall, ToolResult
    from services.tools.catalog import build_tool_catalog
    from services.tools.dispatcher import _dispatch_call_id
    from services.tools.policy import ToolPolicy
    from tests.test_content_objects import run_content_loop
    from tests.test_task_execution_operations_postgres import typed_store

    f = seed(content_db)
    settings = get_settings().model_copy(update={
        'file_workspace_root': str(tmp_path), 'chat_image_async_enabled': True,
        'agent_task_execution_enabled': True,
    })
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    ws = AsyncMock()
    monkeypatch.setattr('services.websocket_manager.ws_manager.send_to_task_or_user', ws)
    limiter = SimpleNamespace(acquire_image_slot=AsyncMock(), release=AsyncMock())
    monkeypatch.setattr('api.deps.get_task_limit_service', AsyncMock(return_value=limiter))
    monkeypatch.setattr(FileExecutor, 'get_cdn_url', lambda self, path: 'https://local.invalid/' + path)

    with_original = scenario != 'text-to-image'
    recorded = scenario.startswith('recorded-')
    fifth = scenario == 'recorded-fifth'
    item_index = 5 if fifth else 1
    body = '  纸品封面，保留细纹理。\n避免改变装订孔及商标。  '
    ratio = '3:4' if scenario == 'image-to-image' else '1:1'
    question = '参考这张原图修改封面，只生成一张' if with_original else '帮我直接生成一张纸品封面图'
    first_document = None
    failed_saves = []
    if recorded:
        path = os.getenv('CHAT_CONTENT_TWO_SKILL_FAILURES')
        if not path:
            pytest.skip('Private failure records required')
        history = json.loads(Path(path).read_text())['conversations']
        conversation = next(c for c in history if bool(c['states']) != fifth)
        followup, original_task = conversation['tasks']
        def input_text(task):
            parts = task['question'] if isinstance(task['question'], list) else json.loads(task['question'])
            return next(part['text'] for part in parts if part['type'] == 'text')
        question, later_question = input_text(original_task), input_text(followup)
        model_calls = [c['function']['arguments'] for m in original_task['checkpoint']['messages']
                       for c in m.get('tool_calls') or () if c['function']['name'] == co.SAVE]
        if fifth:
            failed_saves = model_calls
            first_document = json.loads(json.loads(model_calls[1])['document'])
        else:
            first_document = json.loads(model_calls[0])['document']
        body = first_document['items'][item_index-1]['body']
    parts = [TextPart(text=question)]
    materials = []
    input_sha = None
    file_path = 'original/product.png'
    if with_original:
        evidence = os.getenv('REAL_INPUT_RETEST_DIR') if fifth else os.getenv('CHAT_CONTENT_DUPLICATE_RECORD')
        if not evidence:
            pytest.skip('Original image for private replay is unavailable')
        if fifth:
            originals = json.loads((Path(evidence) / 'live-originals.json').read_text())['originals']
            source = next(asset for asset in originals if 'IMG_20261110_131009_f527d6_001.png' in asset['workspace_path'])
        else:
            source = json.loads(Path(evidence).read_text())['original']
        original = base64.b64decode(source['raw_b64'])
        input_sha = hashlib.sha256(original).hexdigest()
        original_path = FileExecutor(str(tmp_path), f['user'], None).resolve_safe_path(file_path)
        original_path.parent.mkdir(parents=True)
        original_path.write_bytes(original)
        parts.append(ImagePart(url='https://local.invalid/'+file_path, workspace_path=file_path))
        materials.append({'usage':'input', 'role':'原图', 'reference':{'message_id':f['message'], 'content_index':1}})
    original_message = f['message']
    with psycopg.connect(content_db) as connection:
        connection.execute("UPDATE messages SET status='completed', context_revision=0, turn_id=%s, content=%s WHERE id=%s",
            (f['turn'], json.dumps([p.model_dump(mode='json', exclude_none=True) for p in parts]), f['message']))

    async def checkpoint(point, payload):
        with connect(f) as connection:
            if '_task_execution' in payload:
                return save(connection, f, payload, expected=payload['_task_execution_expected_revision'])
            return connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                (f['task'], f['token'], point.value, Jsonb({**payload, 'safe_point': point.value}))).fetchone()[0]

    def runtime_case(request_parts):
        r, p, h, q = case(checkpoint)
        b = r.task_bootstrap
        r.task_bootstrap = replace(b, task_id=UUID(f['task']), conversation_id=UUID(f['conv']),
            actor_user_id=UUID(f['user']), input_message_id=UUID(f['message']),
            execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id': f['user']}))
        r.task_id, r.turn_id, r.conversation_id = f['task'], f['turn'], f['conv']
        p.execution_context = replace(p.execution_context, task_id=f['task'], conversation_id=f['conv'],
            actor_user_id=f['user'], workspace_owner_id=f['user'])
        q = replace(q, task_id=f['task'], conversation_id=f['conv'], user_id=f['user'], content=request_parts)
        p.messages = [{'role':'user','content':request_parts[0].text}]
        p.core_tools.append(build_tool_catalog().require('generate_image').to_schema())
        return r, p, h, q

    r, p, h, q = runtime_case(parts)
    def tool_chunk(name, *, arguments):
        return StreamChunk(tool_calls=[ToolCallDelta(index=0,id=name,name=name,arguments_delta=arguments)])

    if recorded:
        for item in first_document['items']:
            if fifth:
                # The recorded response omitted the original's use. Supply
                # the corrected model response explicitly in this test.
                item['materials'] = materials
            else:
                for material in item['materials']:
                    material['reference']['message_id'] = original_message
        corrections = iter(failed_saves + [json.dumps({'document':first_document},ensure_ascii=False)])

        async def register(**kwargs):
            yield tool_chunk(co.SAVE, arguments=next(corrections))

        registered_sink, registered_blocks, _ = await run_content_loop(r, p, h, q, register)
        saved_block, = [block for block in registered_blocks if block['type'] == 'text']
        assert saved_block['text'].count(body) == 1 and not registered_sink.text
        row, = co.catalog(r.task_execution_state)
        selection = ref(row, item_index)
        old_task_id = f['task']
        _old, f, previous, _obs, _merged = new_turn(content_db, old=f, text=later_question)

        async def transfer(source, observation, payload):
            with connect(f) as connection:
                return adopt(connection, f, source, observation, payload)

        r, p, h, q = runtime_case([TextPart(text=later_question)])
        r.task_followup_candidate = previous
        r.task_followup_adopt = transfer

        async def select(**kwargs):
            yield tool_chunk('generate_image', arguments=json.dumps({'content_source':selection}))

        with pytest.raises(FollowupRestart):
            await run_content_loop(r, p, h, q, select)
        h._execute_tool_calls.assert_not_awaited()
        with connect(f) as connection:
            checkpoint_state = connection.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
                (f['task'],)).fetchone()[0]
        r, p, h, q = runtime_case([TextPart(text=later_question)])
        r.checkpoint_base = checkpoint_state
        r.task_execution_state = TaskState.model_validate(checkpoint_state['_task_execution'])
        assert str(r.task_execution_state.root_task_id) == old_task_id
        r.committed_task_execution_revision = r.task_execution_state.revision
        r.task_user_changes = restore_changes(checkpoint_state, root_task_id=old_task_id, owner_task_id=f['task'])
        r._steer_messages.extend(item.message for item in r.task_user_changes.items if not item.delivered)
        restore_plan(r, checkpoint_state)
        p.messages = copy.deepcopy(checkpoint_state['messages'])

    database = LocalDBClient(psycopg.conninfo.make_conninfo(content_db, user='everydayai'), min_size=1, max_size=4)
    scoped = ScopedDatabaseClient(database, DatabaseScope(f['user'], None, DatabaseAccessKind.RUNTIME))
    h.db = scoped
    h._actor_runtime = r
    r.task_image_resource_resolver = create_resolver(h, q, p.execution_context)
    store = typed_store(f)
    r.task_operation_store = store
    receipts = []

    async def dispatch(calls, *args, task_image_operations, **kwargs):
        assert len(calls) == 1
        raw = calls[0]
        native = json.loads(raw['arguments'])
        assert native['prompt'] == body
        if 'aspect_ratio' in native:
            assert native['aspect_ratio'] == ratio
        if recorded:
            assert native['source_prompt']['content_object']['source'] == selection
            assert raw['id'].endswith(':continued')
        parsed = ToolCall(raw['id'], raw['name'], native)
        binding = task_image_operations.bindings[raw['id']]
        result = await store.begin(r.task_execution_state, step_id=binding.step_id, effect_key=binding.effect_key,
                                   effect_version=binding.effect_version, call=parsed)
        assert result.outcome == 'execute'
        task_image_operations.adopt(result)
        owner = SimpleNamespace(db=scoped, task_id=f['task'], conversation_id=f['conv'], user_id=f['user'],
            workspace_user_id=f['user'], context_scope='user', personal_context_allowed=True, org_id=None,
            execution_mode='interactive', resource_manifest=None, image_execution_token=f['token'],
            cancellation_event=r.cancellation_event, _task_execution_state=r.task_execution_state)
        context = replace(p.execution_context, call_id=parsed.call_id)
        decision = ToolPolicy(store.registry).decide(parsed.name, context, native)
        assert decision.outcome == 'allow'
        token = _dispatch_call_id.set(parsed.call_id)
        try:
            if with_original:
                with pytest.raises(ChatImageNotAcceptedError):
                    await ImageHandler(scoped).accept_chat_image(owner, {**native, 'prompt': '不完整'})
            result_from_tool = await MediaToolMixin._generate_image(owner, native)
            receipt = result_from_tool.metadata
            repeated = await ImageHandler(scoped).accept_chat_image(owner, native)
        finally:
            _dispatch_call_id.reset(token)
        assert receipt['task_id'] == repeated['task_id']
        receipts.append(receipt)
        wrapped = ToolResult.wrap(result_from_tool, call=parsed, context=context, decision=decision)
        committed = await store.complete_invocation(r.task_execution_state, operation_id=result.operation.id,
            result=wrapped, status='accepted', receipt_ref='task:'+receipt['task_id'])
        task_image_operations.adopt(committed)
        return [(raw, wrapped, False, '已受理')]

    h._execute_tool_calls.side_effect = dispatch
    doc = {'title':'本轮生成输入','items':[{'title':'第一个结果','body':body,'materials':materials,
           'options':{'aspect_ratio':ratio, 'resolution':'1K', 'output_format':'png'}}]}
    seen = 0

    async def model(**kwargs):
        nonlocal seen
        seen += 1
        if recorded:
            assert receipts and seen == 1, 'A checked selection should resume before asking the model again'
            yield StreamChunk(content='图片任务已受理。')
        elif seen == 1:
            args = {'document':doc,'continue_execution':True}
            yield StreamChunk(tool_calls=[ToolCallDelta(index=0,id='save',name=co.SAVE,
                                                        arguments_delta=json.dumps(args, ensure_ascii=False))])
        elif seen == 2:
            row, = co.catalog(r.task_execution_state)
            assert co.load(r.task_execution_state, ref(row))[2].body == body
            assert any(t['function']['name'] == 'generate_image' for t in kwargs['tools'])
            yield StreamChunk(tool_calls=[ToolCallDelta(index=0,id='run',name='generate_image',
                                                        arguments_delta=json.dumps({'content_source':ref(row)}))])
        else:
            assert receipts and seen == 3
            yield StreamChunk(content='图片任务已受理。')

    try:
        sink, blocks, _ = await run_content_loop(r, p, h, q, model)
        assert seen == (1 if recorded else 3) and len(receipts) == 1
        assert body not in sink.text and not any(body in block.get('text','') for block in blocks)
        receipt = receipts[0]
        assert receipt['accepted'] is True and receipt['completed'] is False
        assert all(event.args[2]['type'] == 'message_pending' for event in ws.await_args_list)
        child = {'id':receipt['task_id'],'user_id':f['user'],'org_id':None}
        worker = ChatImageLifecycle(database, settings)
        queued = await worker.refresh(child)
        snapshot = queued['request_params'][REQUEST_KEY]
        assert snapshot['mode'] == ('image_to_image' if with_original else 'text_to_image')
        assert snapshot['prompt_sha256'] == hashlib.sha256(body.encode()).hexdigest()
        assert [(x['workspace_path'],x['content_sha256']) for x in snapshot['references']] == (
            [(file_path, input_sha)] if with_original else [])
        assert (snapshot['aspect_ratio'],snapshot['resolution'],snapshot['output_format']) == (ratio,'1K','png')
        assert snapshot['origin']['input_message_id'] == f['message']
        if with_original:
            assert snapshot['references'][0]['width'] == snapshot['references'][0]['height'] == (1024 if fifth else 1254)

        requests = []
        external_id = 'local-' + str(uuid4())

        def provider_http(request):
            assert request.url.path == '/api/v1/jobs/createTask'
            serialized = json.loads(request.content)['input']
            assert serialized['prompt'] == body
            assert serialized.get('aspect_ratio', serialized.get('image_size')) == ratio
            assert (['https://local.invalid/'+file_path] in serialized.values()) == with_original
            requests.append(serialized)
            return httpx.Response(200, json={'code':200, 'msg':'success', 'data':{'taskId':external_id}})

        client = KieClient('isolated-unused')
        client._client = httpx.AsyncClient(base_url='https://local.invalid',
            transport=httpx.MockTransport(provider_http), trust_env=False)
        monkeypatch.setattr(client, '_schedule_shadow_upload', Mock())
        monkeypatch.setattr('services.adapters.factory.create_image_adapter',
            lambda model, **kwargs:KieImageAdapter(client, model))
        # Once the placeholder exists, submit through the real worker and then
        # inject a local fixture as the supplier's completed result.
        await worker.submit(queued)
        saved = await worker.refresh(child)
        assert saved['external_task_id'] == external_id
        out_path = 'generated/local-replay.png'
        image_path = FileExecutor(str(tmp_path), f['user'], None).resolve_safe_path(out_path)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB',(30,40) if ratio == '3:4' else (30,30)).save(image_path)
        owner_hash = hashlib.md5(f['user'].encode(), usedforsecurity=False).hexdigest()[:8]
        output_url = f'https://local.invalid/workspace/personal/{owner_hash}/{out_path}'
        monkeypatch.setattr('services.assets.asset_identity.configured_asset_hosts',
            lambda:frozenset({'local.invalid'}))
        monkeypatch.setattr('services.file_upload.persist_media_urls_to_workspace',
            AsyncMock(return_value=[{'url':output_url,'workspace_path':out_path}]))
        await worker.rpc(child,'record_chat_image_provider_result',
            p_result={'status':'success','image_urls':['https://local.invalid/provider-result']})
        await worker.finish_recorded(await worker.refresh(child))
        await worker.advance(await worker.refresh(child))
        assert len(requests) == 1
        final = ws.await_args_list[-1].args[2]
        assert final['type'] == 'message_done' and final['task_id'] == receipt['task_id']
        assert final['message_id'] == receipt['message_id']
        displayed, = final['payload']['message']['content']
        assert displayed['url'] == output_url and UUID(displayed['asset_id'])
        assert displayed['quality_checks']['size_matches'] is True
        if replay_dir := os.getenv('CHAT_EXECUTION_WS_REPLAY_DIR'):
            pending = ws.await_args_list[0].args[2]
            Path(replay_dir, scenario + '.json').write_text(json.dumps([pending, final], default=str))
        with psycopg.connect(content_db) as connection:
            status, content = connection.execute('SELECT status,content FROM messages WHERE id=%s',
                (receipt['message_id'],)).fetchone()
            assert status == 'completed' and json.loads(content)[0]['url'] == output_url
            assert connection.execute('SELECT count(*) FROM user_assets WHERE id=%s',
                (displayed['asset_id'],)).fetchone()[0] == 1
            prompt, metadata = connection.execute('SELECT prompt,metadata FROM user_asset_refs WHERE source_task_id=%s',
                (receipt['task_id'],)).fetchone()
            assert prompt == body and metadata['request_hash'] == snapshot['request_hash']
            assert connection.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',
                (receipt['task_id'],)).fetchone()[0] == 1
    finally:
        database.pool.close()
