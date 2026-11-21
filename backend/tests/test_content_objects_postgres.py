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
        for migration in ('347_agent_task_image_reconciliation','376_agent_task_image_method_handoff','377_agent_task_content_objects','378_agent_content_followup_groups','381_agent_media_confirmation_slots'):
            c.execute((root/f'migrations/{migration}.sql').read_text())
    yield dispatch_db
    with psycopg.connect(dispatch_db) as c:
        c.execute("DELETE FROM conversation_turn_checkpoints WHERE state ? '_task_execution'")
        c.execute('DELETE FROM agent_task_events');c.execute('DELETE FROM agent_task_states');c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        for migration in ('381_agent_media_confirmation_slots','378_agent_content_followup_groups','377_agent_task_content_objects','376_agent_task_image_method_handoff','347_agent_task_image_reconciliation'):
            c.execute((root/f'migrations/rollback/{migration}_rollback.sql').read_text())

@pytest.mark.asyncio
async def test_registered_object_survives_real_new_turn_and_patch_without_new_review(content_db):
    from psycopg.types.json import Jsonb
    from services.task_execution.followup import FollowupRestart
    from services.task_execution.user_changes import restore
    from tests.test_content_objects import run_content_loop

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
    user_request = '继续原任务，修改第二条'
    old,new,source,_,_=new_turn(followup_db,old=f,text=user_request)
    assert co.catalog(TaskState.model_validate(source['state']))==[original]
    async def next_checkpoint(point,payload):
        with connect(new) as c:
            if '_task_execution' in payload:
                return save(c,new,payload,expected=payload['_task_execution_expected_revision'])
            return c.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                (new['task'],new['token'],point.value,Jsonb({**payload,'safe_point':point.value}))).fetchone()[0]
    def revision_runtime():
        actor, prepared, handler, request = case(next_checkpoint)
        actor.task_bootstrap=replace(r.task_bootstrap,task_id=UUID(new['task']),input_message_id=UUID(new['message']))
        actor.task_id,actor.conversation_id,actor.turn_id=new['task'],new['conv'],new['turn']
        prepared.execution_context=replace(p.execution_context,task_id=new['task'])
        prepared.messages=[{'role':'user','content':user_request}]
        prepared.content_controls_available=True
        return actor, prepared, handler, replace(q,task_id=new['task'])

    async def transfer(candidate, observation, payload):
        assert observation == {'relation':'amend','evidence':[user_request]}
        with connect(new) as c:
            return adopt(c,new,candidate,observation,payload)

    r2,p2,h2,q2=revision_runtime()
    r2.task_followup_candidate=source
    r2.task_followup_adopt=transfer
    selection = {'artifact_id': original['artifact_id'], 'item_index': 2}
    changes = {'base': selection, 'updates':[{'item_index':2,'item':{'body':'修订正文\n修订后的完整限制'}}]}
    with pytest.raises(FollowupRestart):
        await execute(r2,p2,h2,q2,[call(co.SAVE,changes,'second-revision')])
    with connect(new) as c:
        checkpoint = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
                               (new['task'],)).fetchone()[0]
        assert str(c.execute('SELECT owner_task_id FROM agent_task_states WHERE root_task_id=%s',(old['task'],)).fetchone()[0])==new['task']
        assert c.execute('SELECT count(*) FROM credit_transactions WHERE task_id=%s',(new['task'],)).fetchone()[0]==0
    state=TaskState.model_validate(checkpoint['_task_execution'])
    assert co.catalog(state)==[original]
    r2,p2,h2,q2=revision_runtime()
    r2.task_execution_state=state;r2.committed_task_execution_revision=state.revision
    r2.checkpoint_base=checkpoint;p2.messages=copy.deepcopy(checkpoint['messages'])
    r2.task_user_changes=restore(checkpoint,root_task_id=old['task'],owner_task_id=new['task'])
    r2._steer_messages.extend(item.message for item in r2.task_user_changes.items if not item.delivered)

    async def unexpected_model(**kwargs):
        raise AssertionError('The original revision was selected before the owner transfer')
        yield
    _, blocks, _ = await run_content_loop(r2,p2,h2,q2,unexpected_model)
    displayed, = [part for part in blocks if part['type']=='text']
    assert displayed['content_delivery_final']
    updated=co.catalog(r2.task_execution_state)[0]
    assert updated['revision']==2
    assert co.load(r2.task_execution_state,ref(updated,2))[2].materials==co.load(state,ref(original,2))[2].materials
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
@pytest.mark.parametrize('scenario', [
    'text-to-image', 'image-to-image', 'inline-text-to-image', 'inline-image-to-image', 'inline-first-turn-upload', 'inline-edit-selected-output', 'inline-repair-then-image', 'inline-resume-before-reserve', 'inline-resume-after-acceptance', 'recorded-first', 'recorded-fifth', 'recorded-sixth-size', 'recorded-write-once', 'recorded-retired',
    'recorded-third-output', 'new-third-output', 'new-regeneration-output', 'recorded-quoted-output',
    'recorded-third-history', 'new-third-history',
])
async def test_chat_executes_from_registration_to_page_receipt(content_db, monkeypatch, tmp_path, scenario):
    import asyncio
    import base64
    import hashlib
    import json
    import os
    import re
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
    from tests.test_content_objects import model_content_output, run_content_loop
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

    inline = scenario.startswith('inline-')
    image_repair = scenario == 'inline-repair-then-image'
    interrupted = scenario.startswith('inline-resume-')
    crashed = False
    with_original = scenario not in {'text-to-image', 'inline-text-to-image'} and not interrupted
    recorded = scenario.startswith('recorded-')
    history_import = scenario.endswith('-history')
    recorded_output = scenario in {'recorded-third-output', 'recorded-third-history'}
    regeneration = scenario == 'new-regeneration-output'
    tool_omission = regeneration or scenario == 'new-third-output'
    new_output = scenario in {'new-third-output', 'new-third-history'} or regeneration
    quoted = scenario == 'recorded-quoted-output'
    automatic_output = recorded_output or new_output or quoted
    followup = recorded or automatic_output
    sixth = scenario == 'recorded-sixth-size'
    first_upload = scenario == 'inline-first-turn-upload'
    edit_selected = scenario == 'inline-edit-selected-output'
    repairs_arguments = image_repair or edit_selected
    fifth = scenario == 'recorded-fifth'
    write_once = scenario == 'recorded-write-once'
    retired = scenario == 'recorded-retired'
    item_index = 3 if recorded_output or new_output else 2 if write_once else 6 if sixth else 5 if fifth else 1
    body = '  纸品封面，保留细纹理。\n避免改变装订孔及商标。  '
    synthetic_original = scenario in {'image-to-image', 'inline-image-to-image', 'inline-first-turn-upload'} or image_repair or sixth or edit_selected
    ratio = 'auto' if edit_selected else '3:4' if synthetic_original or new_output else '1:1'
    resolution = '2K' if new_output or edit_selected else '1K'
    question = '参考这张原图修改封面，只生成一张' if with_original else '帮我直接生成一张纸品封面图'
    if scenario == 'inline-image-to-image' or image_repair:
        question += '，输出比例3:4。'
    if first_upload:
        question = '这个是图片。你用你写的提示词帮我生成新的图片'
    if edit_selected:
        question = '把你刚生成的那张图的桌面颜色再调浅一点，福字主体不动，比例你定。直接生成一张。'
    first_document = None
    first_arguments = None
    if quoted:
        path, archive = os.getenv('CHAT_IMAGE_REFERENCE_INCIDENT'), os.getenv('CHAT_THIRD_PLAN_RECORD')
        if not path or not archive:
            pytest.skip('Private numbered reply and local product image required')
        author, reply = [json.loads(line) for line in Path(path).read_text().splitlines()]
        question = author['content'][0]['text']
        first_output = reply['content'][0]['text']
        later_question = '第一个方案帮我做成图片'
        # Expected results only. The unmodified reply is streamed below.
        output_items = [{'body': line[2:]} for line in first_output.splitlines() if line.startswith('> ')]
        assert len(output_items) == 4
        body = output_items[item_index-1]['body']
        archived = json.loads((Path(archive) / 'record.json').read_text())
        image = json.loads(archived['messages'][0]['content'])[1]
        assert image['name'] == author['content'][1]['name']
        original = (Path(archive) / 'original.png').read_bytes()
    elif recorded_output:
        evidence = os.getenv('CHAT_THIRD_PLAN_RECORD')
        if not evidence:
            pytest.skip('Private third-plan conversation and original image required')
        capture = Path(evidence)
        source_message, displayed, followup_message, failed = json.loads((capture / 'record.json').read_text())['messages']
        question = json.loads(source_message['content'])[0]['text']
        later_question = json.loads(followup_message['content'])[0]['text']
        visible = next(p['text'] for p in json.loads(displayed['content']) if p['type'] == 'text')
        # Fixture only: feed the old prose using the new explicit output boundaries.
        captures = list(re.finditer(r'^\*\*(新品 \d+[^\n]+)\*\*\n+\x60\x60\x60text\n(.*?)\n\x60\x60\x60', visible, re.S | re.M))
        assert len(captures) == 5
        output_items = [{'title': match[1], 'display_prefix': f'**{match[1]}**', 'body': match[2]} for match in captures]
        first_output = model_content_output('新品提示词', output_items, introduction=visible[:captures[0].start()].rstrip())
        body = output_items[2]['body']
        checkpoint = json.loads((capture / 'state.json').read_text())['checkpoints'][1]['state']
        original_artifact = json.loads(checkpoint['_task_execution']['artifacts'][0]['inline_text'])
        assert body == original_artifact['document']['items'][0]['body']
        assert original_artifact['document']['items'][0]['options']['aspect_ratio'] == ratio
        assert 'CONTENT_ARGUMENT_CONFLICT' in failed['content']
        original = (capture / 'original.png').read_bytes()
        if history_import:
            first_output = visible
            first_line = visible.count('\n', 0, captures[2].start(2)) + 1
            last_line = visible.count('\n', 0, captures[2].end(2)) + 1
            body += '\n'
    elif new_output:
        question = '参考上传图写五套产品封面提示词。每条输出比例为3:4，输出分辨率为2K。'
        later_question = ('第三个方案 你不能再帮我生成一次？' if regeneration else '用第三条和原图生成一张图片。')
        body = '  保留纸品的标识和边缘。\n输出比例：3:4\n输出分辨率：2K\n不要添加水印。  '
        output_items = [{'title': f'封面方向{i}', 'body': body if i == 3 else f'第{i}套独立正文。'} for i in range(1, 6)]
        first_output = model_content_output('五套封面', output_items)
        if history_import:
            first_output = '\n\n'.join(f"### {item['title']}\n\n{item['body']}" for item in output_items)
            start = first_output.index(body)
            first_line = first_output.count('\n', 0, start) + 1
            last_line = first_output.count('\n', 0, start + len(body)) + 1
            body += '\n'
        from io import BytesIO
        fixture = BytesIO()
        Image.new('RGB', (1024, 1024), 'white').save(fixture, format='PNG')
        original = fixture.getvalue()
    elif write_once:
        evidence = os.getenv('CHAT_CONTENT_ONE_SAVE_DIR')
        if not evidence:
            pytest.skip('Private latest prompt request required')
        record = json.loads((Path(evidence) / 'latest-record.json').read_text())
        exact = json.loads((Path(evidence) / 'first-attempt.json').read_text())
        assert record['verified_latest'] and [entry['ok'] for entry in record['saves']] == [False, False, True]
        first_arguments = exact['first_arguments']
        first_document = co.SaveContent.model_validate_json(first_arguments).document.model_dump(mode='json', exclude_unset=True)
        assert first_document == record['saves'][0]['document']
        assert all(set(item) == {'title', 'body'} for item in first_document['items'])
        question = record['question'][0]['text']
        later_question = '用这张原图把第二条提示词做成一张图片'
        body = first_document['items'][1]['body']
    elif retired:
        path = os.getenv('CHAT_CONTENT_RETIRED_SKILL_RECORD')
        if not path:
            pytest.skip('Private online orchestration failure required')
        record = json.loads(Path(path).read_text())
        execution, writing = record['tasks']
        assert record['observed_task_id'] == execution['id'] and record['child_count'] == 0
        execution_checkpoint = execution['cp'].get('payload') or execution['cp']
        writing_checkpoint = writing['cp'].get('payload') or writing['cp']
        failed_call = next(c['function'] for m in execution_checkpoint['messages'] for c in m.get('tool_calls') or []
                           if c['function']['name'] == 'generate_image')
        submitted = json.loads(failed_call['arguments'])['content_source']
        artifact = next(a for a in writing_checkpoint['_task_execution']['artifacts'] if a['id'] == submitted['artifact_id'])
        first_document = json.loads(artifact['inline_text'])['document']
        item_index = submitted['item_index']
        body = first_document['items'][item_index-1]['body']
        assert submitted['sha256'] == hashlib.sha256(body.encode()).hexdigest()
        assert submitted['sha256'] != artifact['sha256']
        def first_text(turn):
            parts = json.loads(turn['question']) if isinstance(turn['question'],str) else turn['question']
            return next(part['text'] for part in parts if part['type'] == 'text')
        question, later_question = first_text(writing), first_text(execution)
    elif sixth:
        question = '参考这张商品图，写六套图片提示词。'
        later_question = '第六个方案帮我做成图片'
        body = '保留产品结构。画布尺寸保持1024×1024像素。输出比例1:1。'
        first_document = {'title': '六套封面', 'items': [
            {'title': f'第{i}个方案', 'body': body if i == 6 else f'方案{i}正文',
             'options': {'width': 1024, 'height': 1024, 'aspect_ratio': '1:1'},
             'materials': []}
            for i in range(1, 7)]}
    elif recorded:
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
            first_document = json.loads(json.loads(model_calls[1])['document'])
        else:
            first_document = json.loads(model_calls[0])['document']
        body = first_document['items'][item_index-1]['body']
    parts = [TextPart(text=question)]
    materials = []
    input_sha = None
    file_path = 'original/product.png'
    if with_original:
        source_name = 'product.png'
        if synthetic_original:
            from io import BytesIO
            fixture = BytesIO()
            Image.new('RGB', (739, 981) if first_upload or sixth or edit_selected else (1254, 1254), 'white').save(fixture, format='PNG')
            original = fixture.getvalue()
        elif not automatic_output:
            evidence = os.getenv('REAL_INPUT_RETEST_DIR') if fifth or write_once else os.getenv('CHAT_CONTENT_DUPLICATE_RECORD')
            if not evidence:
                pytest.skip('Original image for private replay is unavailable')
            if fifth or write_once:
                originals = json.loads((Path(evidence) / 'live-originals.json').read_text())['originals']
                source = next(asset for asset in originals if 'IMG_20261110_131009_f527d6_001.png' in asset['workspace_path'])
            else:
                source = json.loads(Path(evidence).read_text())['original']
            original = base64.b64decode(source['raw_b64'])
            source_name = Path(source.get('workspace_path', file_path)).name
        input_sha = hashlib.sha256(original).hexdigest()
        if write_once:
            assert source['workspace_path'] == record['attachment']['workspace_path']
            assert input_sha == exact['original_sha256']
        original_path = FileExecutor(str(tmp_path), f['user'], None).resolve_safe_path(file_path)
        original_path.parent.mkdir(parents=True)
        original_path.write_bytes(original)
        parts.append(ImagePart(url='https://local.invalid/'+file_path, workspace_path=file_path, name=source_name))
        materials.append({'usage':'input', 'role':'原图', 'reference':{'message_id':f['message'], 'content_index':1}})
    original_message = f['message']
    with psycopg.connect(content_db) as connection:
        initial_credits, = connection.execute('SELECT credits FROM users WHERE id=%s',
                                             (f['user'],)).fetchone()
        connection.execute("UPDATE messages SET status='completed', context_revision=0, turn_id=%s, content=%s WHERE id=%s",
            (f['turn'], json.dumps([p.model_dump(mode='json', exclude_none=True) for p in parts]), f['message']))
        if edit_selected:
            selected_message = str(uuid4())
            for message, revision, tier in ((selected_message, 0, '2K'), (str(uuid4()), 1, '4K')):
                image_task = str(uuid4())
                result_part = {'type': 'image', 'workspace_path': file_path,
                               'url': 'https://local.invalid/' + file_path, 'task_id': image_task}
                facts = {'task_id': image_task, 'size_requirement': {'resolution': tier, 'aspect_ratio': '3:4'}}
                connection.execute(
                    "INSERT INTO messages(id,conversation_id,role,content,status,context_revision,generation_params) "
                    "VALUES (%s,%s,'assistant',%s,'completed',%s,%s)",
                    (message, f['conv'], json.dumps([result_part]), revision, Jsonb(facts)))
            connection.execute("UPDATE messages SET content=%s, context_revision=2 WHERE id=%s",
                               (json.dumps([parts[0].model_dump()]), f['message']))
            connection.execute("UPDATE tasks SET base_context_revision=1 WHERE id=%s", (f['task'],))
            parts = [parts[0]]
            materials[0]['reference'] = {'message_id': selected_message, 'content_index': 0}

    class RestartTest(Exception):
        pass

    async def checkpoint(point, payload):
        nonlocal crashed
        with connect(f) as connection:
            if '_task_execution' in payload:
                result = save(connection, f, payload, expected=payload['_task_execution_expected_revision'])
            else:
                result = connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                    (f['task'], f['token'], point.value, Jsonb({**payload, 'safe_point': point.value}))).fetchone()[0]
        if scenario == 'inline-resume-before-reserve' and payload.get('_inline_media_action') and not crashed:
            assert result['outcome'] == 'saved' and not payload['_task_execution']['operations']
            crashed = True
            raise RestartTest
        return result

    def runtime_case(request_parts):
        r, p, h, q = case(checkpoint)
        b = r.task_bootstrap
        r.task_bootstrap = replace(b, task_id=UUID(f['task']), conversation_id=UUID(f['conv']),
            actor_user_id=UUID(f['user']), input_message_id=UUID(f['message']),
            original_goal=request_parts[0].text,
            execution_identity=b.execution_identity.model_copy(update={'workspace_owner_id': f['user']}))
        r.task_id, r.turn_id, r.conversation_id = f['task'], f['turn'], f['conv']
        p.execution_context = replace(p.execution_context, task_id=f['task'], conversation_id=f['conv'],
            actor_user_id=f['user'], workspace_owner_id=f['user'])
        q = replace(q, task_id=f['task'], conversation_id=f['conv'], user_id=f['user'], content=request_parts,
                    context_anchor=SimpleNamespace(input_message_id=f['message'],
                        base_revision=1 if edit_selected else 0 if first_upload else None))
        p.messages = [{'role':'user','content':request_parts[0].text}]
        p.core_tools.append(build_tool_catalog().require('generate_image').to_schema())
        return r, p, h, q

    r, p, h, q = runtime_case(parts)
    def tool_chunk(name, *, arguments):
        return StreamChunk(tool_calls=[ToolCallDelta(index=0,id=name,name=name,arguments_delta=arguments)])

    if followup:
        if write_once or automatic_output:
            from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
            from services.handlers.chat_context.image_sources import image_sources

            discovered, = image_sources({'id': original_message, 'role': 'user',
                                         'content': [part.model_dump(mode='json') for part in parts]},
                                        visible_indices=[1])
            discovered['canvas'] = {'width': 1024, 'height': 1024, 'aspect_ratio': '1:1'}
            p.messages = [UserLayer.render(UserMessageInput(
                question, image_sources=[discovered], image_urls=[parts[1].url])).user_message]
        elif not automatic_output:
            for item in first_document['items']:
                if fifth or sixth:
                    item['materials'] = materials
                else:
                    for material in item['materials']:
                        material['reference']['message_id'] = original_message
            first_arguments = json.dumps({'document':first_document},ensure_ascii=False)
        registration_rounds = 0

        async def register(**kwargs):
            nonlocal registration_rounds
            registration_rounds += 1
            assert registration_rounds == 1
            if write_once:
                assert '服务器目标尺寸预检' not in str(kwargs['messages'])
                assert 'inherit_reference' not in str(kwargs['messages'])
                parameters = next(tool['function']['parameters'] for tool in kwargs['tools']
                                  if tool['function']['name'] == co.SAVE)
                assert parameters['$defs']['ContentItem']['required'] == ['title', 'body']
            if automatic_output:
                assert co.OUTPUT_START not in str(kwargs['messages'][-1]['content'])
                assert any(part.get('type') == 'image_url' for message in kwargs['messages']
                           if message['role'] == 'user' and isinstance(message['content'], list)
                           for part in message['content'])
                for index in range(0, len(first_output), 101):
                    yield StreamChunk(content=first_output[index:index + 101])
                yield StreamChunk(finish_reason='stop')
            else:
                yield tool_chunk(co.SAVE, arguments=first_arguments)

        registered_sink, registered_blocks, _ = await run_content_loop(r, p, h, q, register)
        saved_block, = [block for block in registered_blocks if block['type'] == 'text']
        if history_import:
            from services.handlers.chat.outcome_builder import build_content_parts

            assert saved_block['text'] == registered_sink.text == first_output
            assert not co.catalog(r.task_execution_state) and registration_rounds == 1
            h._execute_tool_calls.assert_not_awaited()
            reply = [part.model_dump(mode='json', exclude_none=True)
                     for part in build_content_parts(registered_blocks, fallback_text='')]
            _old, f, history_message, _, _ = new_turn(content_db, old=f, text=later_question, reply=reply)
            r, p, h, q = runtime_case([TextPart(text=later_question)])
            row = selection = None
        else:
            assert saved_block['text'].count(body) == 1
            assert registered_sink.text == (first_output if quoted else '')
            assert saved_block['content_delivery_final'] and registration_rounds == 1
            if automatic_output:
                assert p.messages[-1] == {'role': 'assistant', 'content': saved_block['text']}
            else:
                assert json.loads(p.messages[-1]['content'])['ok'] is True
            h._execute_tool_calls.assert_not_awaited()
            row, = co.catalog(r.task_execution_state)
            selection = ref(row, item_index)
            if retired or write_once or automatic_output:
                del selection['sha256']
            if write_once or automatic_output:
                from services.handlers.chat.outcome_builder import build_content_parts

                artifact, saved_doc, saved_item = co.load(r.task_execution_state, selection)
                saved = co.ContentRecord.model_validate_json(artifact.inline_text)
                assert saved_block['content_artifact_id'] == artifact.id
                assert saved_block['text'] == co.render(saved_doc, saved.display_gaps)
                assert saved_item.body == body
                if saved_item.materials:
                    assert all(m.reference.message_id == original_message and m.usage == 'input' for m in saved_item.materials)
                delivered, = [part for part in build_content_parts(registered_blocks, fallback_text='') if part.type == 'text']
                assert delivered.content_artifact_id == artifact.id and delivered.text == saved_block['text']
                source, = saved.source_images
                assert source.name == source_name
                assert source.reference.model_dump(exclude_none=True) == {'message_id': original_message, 'content_index': 1}
                if automatic_output:
                    assert [i.body for i in saved_doc.items] == [i['body'] for i in output_items]
                    assert all(saved_block['text'].count(i['body']) == 1 for i in output_items)
                    assert co.OUTPUT_START not in saved_block['text']
                if not saved_item.materials:
                    selection['source_image_indices'] = [1]
            old_task_id = f['task']
            _old, f, previous, _obs, _merged = new_turn(content_db, old=f, text=later_question)

            async def transfer(source, observation, payload):
                with connect(f) as connection:
                    return adopt(connection, f, source, observation, payload)

            r, p, h, q = runtime_case([TextPart(text=later_question)])
            r.task_followup_candidate = previous
            r.task_followup_adopt = transfer

            selection_rounds = 0

            async def select(**kwargs):
                from services.handlers.chat.content_tools import IMAGE_CALL_REPAIR

                nonlocal selection_rounds
                selection_rounds += 1
                if tool_omission and selection_rounds == 1:
                    yield StreamChunk(content='图片已提交。', finish_reason='stop')
                    return
                if tool_omission:
                    assert any(m.get('content') == IMAGE_CALL_REPAIR for m in kwargs['messages'])
                tool = next(tool['function'] for tool in kwargs['tools'] if tool['function']['name'] == 'generate_image')
                selector = tool['parameters']['properties']['content_source']
                assert selector['required'] == ['artifact_id','item_index'] and 'sha256' not in selector['properties']
                assert 'activate_skill' not in {tool['function']['name'] for tool in kwargs['tools']}
                if write_once or automatic_output:
                    context = next(m for m in kwargs['messages'] if m['role'] == 'system' and
                                   str(m.get('content', '')).startswith('[Model-directed execution]'))
                    facts = json.loads(context['content'].split('\n')[-1])
                    offered, = facts['previous_content_objects']
                    if saved_item.materials:
                        assert offered['items'][item_index-1]['has_bound_inputs']
                    else:
                        assert offered['source_images'] == [{'index': 1, 'name': source.name}]
                        assert 'source_image_indices' in selector['properties']
                selected = {**selection, 'regenerate': later_question} if regeneration else selection
                if regeneration and selection_rounds == 2:
                    selected = {**selected, 'sha256': '0' * 64}
                if regeneration and selection_rounds == 3:
                    error = next(m['content'] for m in reversed(kwargs['messages']) if m['role'] == 'tool')
                    result = json.loads(error.splitlines()[0])
                    assert result['recovery'] == {'action': 'reload_source'}
                    assert result['submission_state'] == 'not_dispatched'
                yield tool_chunk('generate_image', arguments=json.dumps({'content_source': selected}))

            with pytest.raises(FollowupRestart):
                await run_content_loop(r, p, h, q, select)
            assert selection_rounds == (3 if regeneration else 2 if tool_omission else 1)
            if tool_omission:
                from services.handlers.chat import content_tools
                assert content_tools.checkpoint_state(r)['image_call_repair_input'] == f['message']
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
        nonlocal crashed
        assert len(calls) == 1
        raw = calls[0]
        native = json.loads(raw['arguments'])
        assert native['prompt'] == body
        if 'aspect_ratio' in native:
            assert native['aspect_ratio'] == ratio
        if followup:
            saved_source = native['source_prompt']['content_object']['source']
            assert saved_source == {**selection, 'sha256': row['sha256']}
            assert raw['id'].endswith(':continued') != history_import
        parsed = ToolCall(raw['id'], raw['name'], native)
        binding = task_image_operations.bindings[raw['id']]
        if kwargs.get('recovery_only'):
            assert scenario == 'inline-resume-after-acceptance' and crashed
            context = replace(p.execution_context, call_id=parsed.call_id)
            actor = SimpleNamespace(_actor_enabled=True, _actor_invocation_store=object(),
                                    _actor_turn_id=f['turn'], _actor_execution_token=f['token'])
            lifecycle = task_image_operations.lifecycle(actor, context, parsed.call_id, recovery_only=True)
            replay = await lifecycle.replay(parsed, context, ToolPolicy(store.registry).decide(parsed.name, context, native))
            assert replay.metadata.get('reconciled') and replay.execution.replayed
            receipts.append(replay.metadata)
            return [(raw, replay, False, '原任务已受理')]
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
            if scenario == 'inline-resume-after-acceptance' and not crashed:
                assert receipt['accepted'] is True
                crashed = True
                raise RestartTest
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
    if first_upload or edit_selected:
        doc['items'][0]['options'] = {
            'size_requirement': {'mode': 'inherit_reference'}, 'resolution': '4K',
        }
    execution_body = ('保留产品结构。画布尺寸按平台输出规格适配。输出比例以平台输出规格为准。'
                      if sixth else body)
    seen = 0
    if not followup:
        row = selection = None

    async def model(**kwargs):
        nonlocal seen, row, selection
        seen += 1
        if history_import:
            if seen == 1:
                yield tool_chunk('read_conversation_text', arguments=json.dumps({'message_id': history_message}))
            elif seen == 2:
                result = json.loads(next(m['content'] for m in reversed(kwargs['messages']) if m['role'] == 'tool'))
                original_reply, = result['messages']
                assert original_reply['text'] == first_output
                arguments = {'continue_execution': True, 'message_source': {
                    'message_id': history_message, 'content_index': original_reply['content_index'],
                    'title': '封面提示词', 'items': [
                        {'title': '第三条', 'first_line': first_line, 'last_line': last_line},
                    ],
                }}
                from services.handlers.chat.content_tools import prepare_message_content

                source = co.MessageContentSource.model_validate(arguments['message_source'])
                with pytest.raises(ValueError, match='CONTENT_MESSAGE_SOURCE_UNAVAILABLE_OR_CHANGED'):
                    await asyncio.to_thread(prepare_message_content, scoped, q.task_id, p.execution_context,
                        source, {f"{history_message}:{original_reply['content_index']}": {
                            'sha256': '0' * 64, 'context_revision': original_reply['context_revision'],
                        }})
                yield tool_chunk(co.SAVE, arguments=json.dumps(arguments))
            elif seen == 3:
                row, = co.catalog(r.task_execution_state)
                selection = {'artifact_id': row['artifact_id'], 'item_index': 1}
                artifact, selected_document, item = co.load(r.task_execution_state, ref(row))
                assert item.body == body
                record = co.ContentRecord.model_validate_json(artifact.inline_text)
                proof = json.loads(selected_document.notes)['message_source']
                assert proof['message_id'] == history_message
                assert proof['sha256'] == hashlib.sha256(first_output.encode()).hexdigest()
                assert record.source_images[0].reference.message_id == original_message
                assert [material.reference for material in item.materials if material.usage == 'input'] == [
                    record.source_images[0].reference]
                yield tool_chunk('generate_image', arguments=json.dumps({'content_source': selection}))
            else:
                assert receipts and seen == 4
                yield StreamChunk(content='图片任务已受理。')
        elif followup:
            assert receipts and seen == 1, 'A checked selection should resume before asking the model again'
            yield StreamChunk(content='图片任务已受理。')
        elif inline:
            if repairs_arguments and seen == 1:
                if edit_selected:
                    yield tool_chunk('generate_image', arguments=json.dumps({'content_source': {
                        'artifact_id': 'old-plan', 'item_index': 1, 'regenerate': question,
                        'image_override': [materials[0]['reference']],
                    }}))
                else:
                    yield tool_chunk('generate_image', arguments='{"draft": <invalid>}')
                return
            if seen == (2 if repairs_arguments else 1):
                if repairs_arguments:
                    rejection = next(m['content'] for m in reversed(kwargs['messages']) if m['role'] == 'tool')
                    assert json.loads(rejection.splitlines()[0])['recovery']['action'] == 'repair_arguments'
                    assert not receipts and not co.objects(r.task_execution_state)
                offered = next(tool['function']['parameters'] for tool in kwargs['tools']
                               if tool['function']['name'] == 'generate_image')
                assert 'draft' in offered['properties']
                if edit_selected:
                    assert list(offered['properties']) == ['draft']
                assert offered['$defs']
                yield tool_chunk('generate_image', arguments=json.dumps({'draft': doc['items'][0]}, ensure_ascii=False))
            else:
                assert receipts and seen == (3 if repairs_arguments else 2), kwargs['messages'][-4:]
                yield StreamChunk(content='图片任务已受理。')
        elif seen == 1:
            args = {'document':copy.deepcopy(doc),'continue_execution':True}
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
        if interrupted:
            with pytest.raises(RestartTest):
                await run_content_loop(r, p, h, q, model)
            assert crashed and seen == 1 and not receipts
            with psycopg.connect(content_db) as connection:
                restored = connection.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
                    (f['task'],)).fetchone()[0]
            r, p, h, q = runtime_case(parts)
            r.checkpoint_base = copy.deepcopy(restored)
            r.task_execution_state = TaskState.model_validate(restored['_task_execution'])
            r.committed_task_execution_revision = r.task_execution_state.revision
            p.messages = copy.deepcopy(restored['messages'])
            h.db = scoped
            h._actor_runtime = r
            r.task_image_resource_resolver = create_resolver(h, q, p.execution_context)
            r.task_operation_store = store
            h._execute_tool_calls.side_effect = dispatch
            monkeypatch.setattr(settings, 'chat_inline_image_enabled', False)
        else:
            restored = {}
        sink, blocks, _ = await run_content_loop(r, p, h, q, model, blocks=copy.deepcopy(restored.get('content_blocks', [])))
        expected_rounds = 4 if history_import else 1 if followup else 2 if inline and not repairs_arguments else 3
        assert seen == expected_rounds and len(receipts) == 1, blocks
        if inline:
            from services.task_execution.inline_image_action import read_record
            from services.task_execution.pending_batch import pending_calls
            record = read_record(r)
            saved, = co.objects(r.task_execution_state)
            assert saved.id == record.reference.artifact_id and saved.source_call_id == record.save_id
            assert not pending_calls(p.messages)[0]
            external_calls = [c for msg in p.messages for c in msg.get('tool_calls') or []]
            assert [item['function']['name'] for item in external_calls] == ['generate_image'] * (2 if repairs_arguments else 1)
            assert json.loads(external_calls[-1]['function']['arguments']) == {'draft': doc['items'][0]}
        assert body not in sink.text and not any(body in block.get('text','') for block in blocks)
        receipt = receipts[0]
        assert receipt['accepted'] is True and receipt['completed'] is False
        assert all(event.args[2]['type'] == 'message_pending' for event in ws.await_args_list)
        child = {'id':receipt['task_id'],'user_id':f['user'],'org_id':None}
        worker = ChatImageLifecycle(database, settings)
        queued = await worker.refresh(child)
        snapshot = queued['request_params'][REQUEST_KEY]
        assert snapshot['mode'] == ('image_to_image' if with_original else 'text_to_image')
        assert snapshot['prompt_sha256'] == hashlib.sha256(execution_body.encode()).hexdigest()
        assert [(x['workspace_path'],x['content_sha256']) for x in snapshot['references']] == (
            [(file_path, input_sha)] if with_original else [])
        assert (snapshot['aspect_ratio'],snapshot['resolution'],snapshot['output_format']) == (ratio,resolution,'png')
        assert snapshot['origin']['input_message_id'] == f['message']
        if regeneration:
            assert snapshot['variant_id'] == f"regenerate:{f['message']}"
        if with_original:
            reference = snapshot['references'][0]
            edge = 1024 if fifth or write_once or automatic_output else 1254
            assert (reference['width'], reference['height']) == ((739, 981) if first_upload or sixth or edit_selected else (edge, edge))

        requests = []
        external_id = 'local-' + str(uuid4())

        def provider_http(request):
            assert request.url.path == '/api/v1/jobs/createTask'
            serialized = json.loads(request.content)['input']
            assert serialized['prompt'] == execution_body
            assert serialized.get('aspect_ratio', serialized.get('image_size')) == ratio
            assert serialized['resolution'] == resolution
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
            assert prompt == execution_body and metadata['request_hash'] == snapshot['request_hash']
            assert connection.execute('SELECT amount,status FROM credit_transactions WHERE task_id=%s',
                (receipt['task_id'],)).fetchall() == [(snapshot['estimated_credits'], 'confirmed')]
            remaining_credits, = connection.execute('SELECT credits FROM users WHERE id=%s',
                                                   (f['user'],)).fetchone()
            assert initial_credits - remaining_credits == snapshot['estimated_credits']
            if inline:
                assert connection.execute(
                    "SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",
                    (f['user'],),
                ).fetchone()[0] == 1
    finally:
        database.pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('sample', ['ordinary', 'skill'])
async def test_real_remaining_plans_keep_original_and_new_upload_can_replace_it(content_db, monkeypatch, tmp_path, sample):
    """Real text and original bytes, local model decisions and worker transport."""
    import hashlib
    import io
    import json
    import os
    import re
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
    from services.handlers.chat.outcome_builder import build_content_parts
    from services.handlers.chat_image_lifecycle import ChatImageLifecycle
    from services.handlers.chat_image_request import REQUEST_KEY
    from services.media_tool_executor import MediaToolMixin
    from services.task_execution.artifact_reader import TaskArtifactReader
    from services.task_execution.direct_loop import restore_plan
    from services.task_execution.followup import FollowupRestart
    from services.task_execution.image_resources import create_resolver
    from services.task_execution.user_changes import restore
    from services.tools import ToolCall, ToolResult
    from services.tools.catalog import build_tool_catalog
    from services.tools.dispatcher import _dispatch_call_id
    from services.tools.policy import ToolPolicy
    from tests.test_content_objects import run_content_loop
    from tests.test_task_execution_operations_postgres import typed_store

    evidence = os.getenv('CHAT_IMAGE_CONTINUITY_REPLAY')
    if not evidence:
        pytest.skip('Private conversation and original image required')
    capture = json.loads((Path(evidence) / 'replay.json').read_text())
    initial, visible, turns = capture['initial'], capture['draft']['content'][0]['text'], capture['turns']
    original = initial['content'][1]
    raw = (Path(os.environ['CHAT_THIRD_PLAN_RECORD']) / 'original.png').read_bytes()
    archived = json.loads((Path(os.environ['CHAT_THIRD_PLAN_RECORD']) / 'record.json').read_text())
    archived_image = json.loads(archived['messages'][0]['content'])[1]
    assert archived_image['original_url'] == original['original_url']
    assert archived_image['workspace_path'] == original['workspace_path'] and len(raw) == original['size']
    # The offline model uses the recorded visible Markdown, including exact newlines.
    mixed = None
    if sample == 'skill':
        import base64
        path = os.getenv('CHAT_CONTENT_DUPLICATE_RECORD')
        if not path:
            pytest.skip('Private original Skill conversation required')
        record = json.loads(Path(path).read_text())
        initial = record['messages'][1]
        original = initial['content'][1]
        visible = record['mixed']['content']
        mixed = record['mixed']['tool_calls']
        raw = base64.b64decode(record['original']['raw_b64'])
        assert original['workspace_path'] == record['original']['path']
    expected_fourth = {
        'ordinary': '7635318a2ad3f8d42ea117579481c8037be7dd510a746c6cbd6b62ac8ae1f1a7',
        'skill': '68fc87a916ddf18372a81b615773bccb0ca1736f1aee51dfe435b5a70a67d907',
    }[sample]

    f = seed(content_db)
    settings = get_settings().model_copy(update={'file_workspace_root': str(tmp_path),
        'chat_image_async_enabled': True, 'agent_task_execution_enabled': True})
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    ws = AsyncMock()
    monkeypatch.setattr('services.websocket_manager.ws_manager.send_to_task_or_user', ws)
    monkeypatch.setattr('api.deps.get_task_limit_service', AsyncMock(return_value=SimpleNamespace(
        acquire_image_slot=AsyncMock(), release=AsyncMock())))
    monkeypatch.setattr(FileExecutor, 'get_cdn_url', lambda _, path: 'https://local.invalid/' + path)
    files = FileExecutor(str(tmp_path), f['user'], None)
    replacement = io.BytesIO()
    Image.new('RGB', (300, 400), 'navy').save(replacement, format='PNG')
    replacement_bytes = replacement.getvalue()
    old_path, new_path = original['workspace_path'], 'new-task/product.png'
    image_bytes = {old_path: raw, new_path: replacement_bytes}
    for path, data in image_bytes.items():
        local = files.resolve_safe_path(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    database = LocalDBClient(psycopg.conninfo.make_conninfo(content_db, user='everydayai'), min_size=1, max_size=4)
    scoped = ScopedDatabaseClient(database, DatabaseScope(f['user'], None, DatabaseAccessKind.RUNTIME))
    provider_requests, submitted = [], []

    def create_task(request):
        assert request.url.path == '/api/v1/jobs/createTask'
        data = json.loads(request.content)['input']
        provider_requests.append(data)
        return httpx.Response(200, json={'code': 200, 'msg': 'success', 'data': {'taskId': str(uuid4())}})

    client = KieClient('isolated-replay')
    client._client = httpx.AsyncClient(base_url='https://local.invalid', transport=httpx.MockTransport(create_task), trust_env=False)
    monkeypatch.setattr(client, '_schedule_shadow_upload', Mock())
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', lambda model, **_: KieImageAdapter(client, model))
    monkeypatch.setattr('services.assets.asset_identity.configured_asset_hosts', lambda: frozenset({'local.invalid'}))
    worker = ChatImageLifecycle(database, settings)

    def upload(turn, text, *, image=None):
        parts = [TextPart(text=text)] + ([image] if image else [])
        with psycopg.connect(content_db) as connection:
            connection.execute("""UPDATE messages SET status='completed', context_revision=COALESCE(context_revision,0),
                turn_id=%s,content=%s WHERE id=%s""", (turn['turn'], json.dumps(
                    [part.model_dump(mode='json', exclude_none=True) for part in parts]), turn['message']))
        return parts

    def context(turn, parts):
        async def checkpoint(point, payload):
            with connect(turn) as connection:
                if '_task_execution' in payload:
                    return save(connection, turn, payload, expected=payload['_task_execution_expected_revision'])
                return connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                    (turn['task'], turn['token'], point.value, Jsonb({**payload, 'safe_point': point.value}))).fetchone()[0]
        runtime, prepared, handler, request = case(checkpoint)
        bootstrap = runtime.task_bootstrap
        runtime.task_bootstrap = replace(bootstrap, task_id=UUID(turn['task']), conversation_id=UUID(turn['conv']),
            actor_user_id=UUID(turn['user']), input_message_id=UUID(turn['message']),
            execution_identity=bootstrap.execution_identity.model_copy(update={'workspace_owner_id': turn['user']}))
        runtime.task_id, runtime.turn_id, runtime.conversation_id = turn['task'], turn['turn'], turn['conv']
        prepared.execution_context = replace(prepared.execution_context, task_id=turn['task'], conversation_id=turn['conv'],
            actor_user_id=turn['user'], workspace_owner_id=turn['user'])
        prepared.messages = [{'role': 'user', 'content': parts[0].text}]
        prepared.content_controls_available = True
        prepared.core_tools.append(build_tool_catalog().require('generate_image').to_schema())
        handler.db, handler._actor_runtime = scoped, runtime
        request = replace(request, task_id=turn['task'], conversation_id=turn['conv'], user_id=turn['user'], content=parts,
                          context_anchor=SimpleNamespace(input_message_id=turn['message'], base_revision=0))
        runtime.task_image_resource_resolver = create_resolver(handler, request, prepared.execution_context)
        runtime.task_operation_store = store = typed_store(turn)
        runtime.task_artifact_reader = TaskArtifactReader(store.db, task_id=turn['task'], execution_token=turn['token'])

        async def dispatch(calls, *args, task_image_operations, **kwargs):
            outcomes = []
            for raw_call in calls:
                args = json.loads(raw_call['arguments'])
                parsed = ToolCall(raw_call['id'], raw_call['name'], args)
                binding = task_image_operations.bindings[raw_call['id']]
                result = await store.begin(runtime.task_execution_state, step_id=binding.step_id, effect_key=binding.effect_key,
                    effect_version=binding.effect_version, call=parsed)
                task_image_operations.adopt(result)
                decision_context = replace(prepared.execution_context, call_id=parsed.call_id)
                decision = ToolPolicy(store.registry).decide(parsed.name, decision_context, parsed.arguments)
                if result.outcome == 'replay':
                    lifecycle = task_image_operations.lifecycle(handler, decision_context, parsed.call_id)
                    replayed = lifecycle.replay_result(result, parsed, decision_context, decision)
                    outcomes.append((raw_call, replayed, False, '已复用'))
                    continue
                assert result.outcome == 'execute'
                owner = SimpleNamespace(db=scoped, task_id=turn['task'], conversation_id=turn['conv'], user_id=turn['user'],
                    workspace_user_id=turn['user'], context_scope='user', personal_context_allowed=True, org_id=None,
                    execution_mode='interactive', resource_manifest=None, image_execution_token=turn['token'],
                    image_composition=kwargs.get('image_composition'), image_skill_snapshot=kwargs.get('image_skill_snapshot', ()),
                    cancellation_event=runtime.cancellation_event, _task_execution_state=runtime.task_execution_state)
                token = _dispatch_call_id.set(parsed.call_id)
                try:
                    output = await MediaToolMixin._generate_image(owner, args)
                finally:
                    _dispatch_call_id.reset(token)
                receipt = output.metadata
                assert receipt['accepted'] is True and receipt['completed'] is False, (receipt, str(output)[:1200])
                wrapped = ToolResult.wrap(output, call=parsed, context=decision_context, decision=decision)
                committed = await store.complete_invocation(runtime.task_execution_state, operation_id=result.operation.id,
                    result=wrapped, status='accepted', receipt_ref='task:' + receipt['task_id'])
                task_image_operations.adopt(committed)
                submitted.append(receipt)
                outcomes.append((raw_call, wrapped, False, '已受理'))
            return outcomes

        handler._execute_tool_calls.side_effect = dispatch
        return runtime, prepared, handler, request

    async def publish(turn, receipts, expected_bodies, expected_path, expected_ratio):
        for receipt, body in zip(receipts, expected_bodies, strict=True):
            child = {'id': receipt['task_id'], 'user_id': turn['user'], 'org_id': None}
            queued = await worker.refresh(child)
            snapshot = queued['request_params'][REQUEST_KEY]
            assert snapshot['mode'] == 'image_to_image' and snapshot['prompt'] == body
            assert (snapshot['aspect_ratio'], snapshot['resolution'], snapshot['output_format']) == (expected_ratio, '1K', 'png')
            reference, = snapshot['references']
            assert reference['workspace_path'] == expected_path
            assert reference['content_sha256'] == hashlib.sha256(image_bytes[expected_path]).hexdigest()
            await worker.submit(queued)
            external = provider_requests[-1]
            assert external['prompt'] == body and ['https://local.invalid/' + expected_path] in external.values()
            assert external.get('aspect_ratio', external.get('image_size')) == expected_ratio
            assert external['resolution'] == '1K'
            path = 'generated/' + receipt['task_id'] + '.png'
            local = files.resolve_safe_path(path)
            local.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (30, 40) if expected_ratio == '3:4' else (30, 30)).save(local)
            user_hash = hashlib.md5(turn['user'].encode(), usedforsecurity=False).hexdigest()[:8]
            url = f'https://local.invalid/workspace/personal/{user_hash}/{path}'
            monkeypatch.setattr('services.file_upload.persist_media_urls_to_workspace',
                AsyncMock(return_value=[{'url': url, 'workspace_path': path}]))
            await worker.rpc(child, 'record_chat_image_provider_result',
                p_result={'status': 'success', 'image_urls': ['https://local.invalid/replay']})
            await worker.finish_recorded(await worker.refresh(child))
            await worker.advance(await worker.refresh(child))
            events = [event.args[2] for event in ws.await_args_list if event.args[2]['task_id'] == receipt['task_id']]
            assert events[0]['type'] == 'message_pending'
            assert events[-1]['type'] == 'message_done'
            displayed, = events[-1]['payload']['message']['content']
            assert displayed['url'] == url and displayed['quality_checks']['size_matches'] is True
            if directory := os.getenv('CHAT_EXECUTION_WS_REPLAY_DIR'):
                key = f'continuity-{sample}-{len(provider_requests)}'
                Path(directory, key + '.json').write_text(json.dumps([events[0], events[-1]], default=str))
                Path(directory, key + '-request.json').write_text(json.dumps({
                    'provider': 'local MockTransport', 'prompt_sha256': hashlib.sha256(external['prompt'].encode()).hexdigest(),
                    'prompt_characters': len(external['prompt']), 'includes_negative_section': '## 负面提示词' in external['prompt'],
                    'reference_sha256': reference['content_sha256'], 'aspect_ratio': snapshot['aspect_ratio'],
                    'resolution': snapshot['resolution'], 'output_format': snapshot['output_format'],
                    'receipt': receipt['accepted'], 'first_event': events[0]['type'], 'last_event': events[-1]['type'],
                }, indent=2))

    def attach_history(turn, runtime):
        with connect(turn) as connection:
            history = connection.execute('SELECT read_agent_task_followup(%s,%s)',
                (turn['task'], turn['token'])).fetchone()[0]
        runtime.task_followup_candidate = history.get('source')
        runtime.task_content_groups = history.get('other_groups', [])
        async def read_root(root):
            with connect(turn) as connection:
                return connection.execute('SELECT read_agent_content_followup(%s,%s,%s)',
                    (turn['task'], turn['token'], root)).fetchone()[0]['source']
        runtime.read_content_root = read_root
        async def transfer(candidate, observation, payload):
            with connect(turn) as connection:
                return adopt(connection, turn, candidate, observation, payload)
        runtime.task_followup_adopt = transfer

    async def assistant_reply(turn, parts, text, tool_calls=None):
        async def model(**kwargs):
            yield StreamChunk(content=text[:19])
            yield StreamChunk(content=text[19:])
            if tool_calls:
                yield StreamChunk(tool_calls=[ToolCallDelta(index=i, id=c['id'], name=c['function']['name'],
                    arguments_delta=c['function']['arguments']) for i,c in enumerate(tool_calls)])
        runtime, prepared, handler, request = context(turn, parts)
        attach_history(turn, runtime)
        if tool_calls:
            from services.skills.selection import SkillSelection
            from tests.test_task_execution_original_skills import OriginalSource, original_runtime
            skill, = [part for part in record['messages'][0]['content'] if part['type'] == 'skill_step']
            if not os.getenv('TASK_ORIGINAL_SKILLS_EXPORT'):
                pytest.skip('Local copy of pinned original Skill is required')
            pinned = SkillSelection(skill_id='reference-image-prompts', revision=skill['revision'])
            runtime.skill_runtime = original_runtime(OriginalSource(), turn_id=runtime.turn_id)
            await runtime.skill_runtime.initialize(selection=pinned)
            assert (await runtime.skill_runtime.activate_manual(pinned))['ok']
        sink, blocks, _ = await run_content_loop(runtime, prepared, handler, request, model)
        group, = co.catalog(runtime.task_execution_state)
        shown, = [block for block in blocks if block['type'] == 'text']
        assert shown['content_artifact_id'] == group['artifact_id']
        assert shown['text'] == sink.text == text
        handler._execute_tool_calls.assert_not_awaited()
        return ([part.model_dump(mode='json', exclude_none=True)
                 for part in build_content_parts(blocks, fallback_text='')], group, runtime.task_execution_state)

    async def choose(turn, parts):
        runtime, prepared, handler, request = context(turn, parts)
        attach_history(turn, runtime)

        async def model(**kwargs):
            # A local stand-in chooses only from the catalog actually exposed this round.
            # Neither the original tool calls nor manually prepared source ranges are inputs.
            facts = json.loads(kwargs['messages'][-1]['content'].split('\n')[-1])
            question = parts[0].text
            override = None
            if '旧第四' in question:
                group, = facts['older_content_objects']
                new_group, = facts['previous_content_objects']
                image, = new_group['uploads']
                override = image['reference']
            elif '笔袋' in question:
                group, = [row for row in facts['previous_content_objects'] + facts.get('older_content_objects', [])
                          if '笔袋' in row['title']]
            else:
                group, = facts['previous_content_objects']
            index = 2 if '第二' in question else 4
            indices = [item['item_index'] for item in group['items'] if not item.get('image_status')
                       ] if '剩' in question or '其他三个' in question else [index]
            actions = []
            for idx in indices:
                selected = dict(group['items'][idx - 1]['content_source'])
                if override:
                    selected['image_override'] = [override]
                if '只改第二' in question:
                    tool = co.SAVE
                    args = {'base': selected, 'updates': [{'item_index': idx, 'item': {'body': revised_body}}]}
                else:
                    tool = 'generate_image'
                    args = {'content_source': selected}
                actions.append(ToolCallDelta(index=len(actions), id=f'select-{idx}', name=tool, arguments_delta=json.dumps(args)))
            yield StreamChunk(tool_calls=actions)
        with pytest.raises(FollowupRestart):
            await run_content_loop(runtime, prepared, handler, request, model)
        with connect(turn) as connection:
            checkpoint = connection.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',
                (turn['task'],)).fetchone()[0]
        runtime, prepared, handler, request = context(turn, parts)
        runtime.checkpoint_base = checkpoint
        runtime.task_execution_state = state = TaskState.model_validate(checkpoint['_task_execution'])
        runtime.committed_task_execution_revision = state.revision
        if 'skill_runtime' in checkpoint:
            from tests.test_task_execution_original_skills import OriginalSource, original_runtime
            runtime.skill_runtime = original_runtime(OriginalSource(), turn_id=runtime.turn_id, task_state=state)
            await runtime.skill_runtime.initialize(checkpoint['skill_runtime'])
        runtime.task_user_changes = restore(checkpoint, root_task_id=str(state.root_task_id), owner_task_id=turn['task'])
        runtime._steer_messages.extend(item.message for item in runtime.task_user_changes.items if not item.delivered)
        restore_plan(runtime, checkpoint)
        prepared.messages = copy.deepcopy(checkpoint['messages'])

        async def done(**kwargs):
            yield StreamChunk(content='图片任务已受理。')

        await run_content_loop(runtime, prepared, handler, request, done)
        return runtime, prepared

    try:
        def advance(old, text, reply=None):
            return new_turn(content_db, old=old, text=text, reply=reply or [], preserve_checkpoint=True)[1:3]

        # Re-open the archived, unindexed answer as a separate conversation. Only its
        # actual message id enters the fallback; no ranges, slots or materials are supplied.
        unindexed = seed(content_db)
        upload(unindexed, initial['content'][0]['text'], image=ImagePart.model_validate(original))
        archived_parts = (record['messages'][0]['content'] if sample == 'skill' else capture['draft']['content'])
        historical, historical_id = advance(unindexed, '用第四条做图', archived_parts)
        rr, pp, hh, qq = context(historical, [TextPart(text='用第四条做图')])
        hh.db = ScopedDatabaseClient(database, DatabaseScope(historical['user'], None, DatabaseAccessKind.RUNTIME))
        await execute(rr, pp, hh, qq, [call(co.SAVE, {'continue_execution': True,
            'message_source': {'message_id': historical_id}}, 'select-original-reply')])
        assert json.loads(pp.messages[-1]['content'])['ok'], pp.messages[-1]['content']
        recovered, = co.catalog(rr.task_execution_state)
        recovered_item = co.load(rr.task_execution_state, ref(recovered, 4))[2]
        assert hashlib.sha256(recovered_item.body.encode()).hexdigest() == expected_fourth
        assert recovered_item.materials[0].reference.message_id == unindexed['message']
        hh._execute_tool_calls.assert_not_awaited()
        original_reply, original_group, original_state = await assistant_reply(f, upload(
            f, initial['content'][0]['text'], image=ImagePart.model_validate(original)), visible, mixed)
        bodies = [co.load(original_state, ref(original_group, i))[2].body
                  for i in range(1, len(original_group['items']) + 1)]
        assert len(bodies) == (6 if sample == 'skill' else 4)
        assert hashlib.sha256(bodies[3].encode()).hexdigest() == expected_fourth
        assert ('## 负面提示词' in bodies[3]) == (sample == 'skill')
        assert all(item['materials'][0]['reference']['message_id'] == f['message'] for item in original_group['items'])

        question = turns[0]['input']['content'][0]['text']
        fourth, _ = advance(f, question, original_reply)
        _, after = await choose(fourth, [TextPart(text=question)])
        assert submitted, [m.get('content','')[:400] for m in after.messages if m['role'] == 'tool'][-5:]
        await publish(fourth, submitted, [bodies[3]], old_path, '1:1')
        change = '只改第二条，主色改成深蓝，保留原有配图和规格。'
        edit_turn, _ = advance(fourth, change)
        revised_body = bodies[1].replace('粉', '蓝') if '粉' in bodies[1] else bodies[1] + '\n主色改为深蓝。'
        revised, _ = await choose(edit_turn, [TextPart(text=change)])
        changed, = co.catalog(revised.task_execution_state)
        assert changed['object_key'] == original_group['object_key'] and changed['revision'] == 2
        updated = [co.load(revised.task_execution_state, ref(changed, i))[2] for i in range(1,len(bodies)+1)]
        assert [item.body for item in updated] == [bodies[0], revised_body, *bodies[2:]]
        assert [item['materials'] for item in changed['items']] == [item['materials'] for item in original_group['items']]
        last_turn = edit_turn
        if sample == 'ordinary':
            remaining = turns[1]['input']['content'][0]['text']
            last_turn, _ = advance(edit_turn, remaining)
            await choose(last_turn, [TextPart(text=remaining)])
            await publish(last_turn, submitted[1:], [updated[i].body for i in (0,1,2)], old_path, '1:1')

        new_question = '参考这次上传的新图，写四条全新的笔袋方案。'
        writer, _ = advance(last_turn, new_question)
        new_image = ImagePart(name='新产品.png', workspace_path=new_path, url='https://local.invalid/' + new_path)
        fence = chr(96)*3
        new_text = '\n'.join(f'### 笔袋 {i}｜新款\n{fence}text\n竖版笔袋 {i}，输出比例：3:4。\n{fence}' for i in range(1,5))
        new_reply, new_group, _ = await assistant_reply(writer, upload(writer, new_question, image=new_image), new_text)
        assert new_group['object_key'] != changed['object_key']
        assert all(item['materials'][0]['reference']['message_id'] == writer['message'] for item in new_group['items'])
        assert new_group['items'][3]['options']['aspect_ratio'] == '3:4'
        swap = '用新图做旧第四条，规格照旧。'
        replacement_turn, _ = advance(writer, swap, new_reply)
        before = len(submitted)
        resumed, _ = await choose(replacement_turn, [TextPart(text=swap)])
        assert str(resumed.task_execution_state.root_task_id) == f['task']
        assert co.catalog(resumed.task_execution_state) == [changed]
        await publish(replacement_turn, submitted[before:], [bodies[3]], new_path, '1:1')
        assert len(submitted) == len(provider_requests) == before + 1
        new_command = '把笔袋第四条做成图片。'
        fresh, _ = advance(replacement_turn, new_command)
        fresh_runtime, _ = await choose(fresh, [TextPart(text=new_command)])
        assert str(fresh_runtime.task_execution_state.root_task_id) == writer['task']
        await publish(fresh, submitted[before+1:], ['竖版笔袋 4，输出比例：3:4。'], new_path, '3:4')
        assert len(provider_requests) == before + 2
    finally:
        await client._client.aclose()
        database.pool.close()


@pytest.mark.asyncio
async def test_tool_argument_failure_checkpoint_survives_actor_replacement(content_db, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from psycopg.types.json import Jsonb
    from services.conversation_commands import SafePoint
    from services.handlers.chat import tool_argument_repairs as repairs
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.pending_batch import pending_calls
    from tests.test_chat_tool_argument_repairs import error_code, setup

    facts = seed(content_db)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())

    class Interrupted(Exception):
        pass

    crash_at_next_validated_batch = False

    async def checkpoint(point, payload):
        with connect(facts) as connection:
            result = connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                (facts['task'], facts['token'], point.value, Jsonb(payload))).fetchone()[0]
        assert result['outcome'] == 'saved'
        if crash_at_next_validated_batch and point == SafePoint.BEFORE_TOOL:
            raise Interrupted
        return result

    def owner():
        runtime, prepared, handler, request = setup(checkpoint)
        runtime.task_id, runtime.turn_id, runtime.conversation_id = facts['task'], facts['turn'], facts['conv']
        runtime.task_bootstrap = None
        request = replace(request, task_id=facts['task'], conversation_id=facts['conv'], user_id=facts['user'],
                          context_anchor=SimpleNamespace(input_message_id=UUID(facts['message'])))
        return runtime, prepared, handler, request

    runtime, prepared, handler, request = owner()
    for number in range(2):
        await execute(runtime, prepared, handler, request, [call('web_search', {f'invalid{number}': True},
                                                                 f'miss-{number}')])
    crash_at_next_validated_batch = True
    final = call('web_search', {'query': 42}, 'last-miss')
    with pytest.raises(Interrupted):
        await execute(runtime, prepared, handler, request, [final])
    handler._execute_tool_calls.assert_not_awaited()

    previous_token = facts['token']
    facts['token'] = str(uuid4())
    with psycopg.connect(content_db) as connection:
        connection.execute('UPDATE tasks SET execution_token=%s WHERE id=%s', (facts['token'], facts['task']))
    with connect(facts) as connection:
        lost = connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
            (facts['task'], previous_token, 'before_model', Jsonb({'messages': []}))).fetchone()[0]
        assert lost['outcome'] == 'ownership_lost'
        loaded = connection.execute('SELECT load_generation_checkpoint(%s,%s)',
            (facts['task'], facts['token'])).fetchone()[0]
        assert loaded['outcome'] == 'loaded' and loaded['safe_point'] == 'before_tool'
        saved = loaded['state']
        assert error_code(saved['messages'], 'last-miss') == repairs.EXHAUSTED
        assert not pending_calls(saved['messages'])[0]

    crash_at_next_validated_batch = False
    restarted, prepared, handler, request = owner()
    restarted.checkpoint_base = saved
    prepared.messages = copy.deepcopy(saved['messages'])
    resumed_blocks = copy.deepcopy(saved['content_blocks'])
    sample = AsyncMock(side_effect=AssertionError('exhausted target must not sample again'))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', sample)
    await _run_loop(
        prepared=prepared, runtime=restarted, request=request, handler=handler,
        sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=resumed_blocks,
        cancellation_event=restarted.cancellation_event,
    )
    assert resumed_blocks[-1]['text'] == repairs.STOP
    sample.assert_not_awaited()
    assert error_code(prepared.messages, 'last-miss') == repairs.EXHAUSTED
    assert repairs.stopped(restarted, request)
    assert list(repairs.read(restarted).counts.values()) == [3]
    handler._execute_tool_calls.assert_not_awaited()


async def test_confirmed_sibling_has_an_independent_slot_and_preserves_previous_dispatch_fence(content_db):
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4
    from psycopg.types.json import Jsonb

    from schemas.message import TextPart
    from services.task_execution.item_confirmation import slot
    from services.task_execution.model_directed import record_calls
    from services.task_execution.reducer import digest
    from services.tools import ToolCall
    from services.tools.catalog import build_capability_catalog

    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    user_text = '写三条视频脚本。第1条直接生成视频，第2条和第3条分别等我确认。'
    request = replace(request, content=[TextPart(text=user_text)])
    draft = {'title': '三条短片', 'items': [{'title': str(i), 'body': f'视频脚本{i}'} for i in range(1, 4)]}
    await execute(runtime, prepared, handler, request, [call(co.SAVE, {'document': draft}, 'save')])
    state = runtime.task_execution_state
    row, = co.catalog(state)
    registry = build_capability_catalog()
    first = ToolCall('first', 'generate_video', {'prompt': '视频脚本1'})
    second = ToolCall('second', 'generate_video', {'prompt': '视频脚本2'})
    first_slot = slot(state, first.name, ref(row, 1))
    second_slot = slot(state, second.name, ref(row, 2))
    state = record_calls(state, runtime.task_bootstrap, [first], registry, media_slots={first.call_id: first_slot})
    from services.task_execution.reducer import reserve_operation, record_operation
    first_binding = state.call_bindings[-1]
    state, operation, _ = reserve_operation(state, expected_revision=state.revision,
        step_id=first_binding.step_id, capability='platform.generate_video', effect_key='video:first',
        effect_version=1, arguments={'prompt': '视频脚本1'}, owner_task_id=runtime.task_id, invocation_id=first.call_id)
    state = state.model_copy(update={'operations': (operation.model_copy(update={'tool_name': first.name}),)})
    state = record_operation(state, operation.id, owner_task_id=runtime.task_id,
                             status='accepted', receipt_ref=f'task:{uuid4()}')
    approved_id = str(uuid4())
    next_state = record_calls(state, runtime.task_bootstrap, [second], registry,
        input_sha256=digest([approved_id]), media_slots={second.call_id: second_slot})
    assert next_state.call_bindings[-1].step_id != first_binding.step_id
    assert len(next_state.operations) == 1
    persisted = next_state.model_dump(mode='json')
    checkpoint = {
        '_task_direct_seen': [approved_id],
        '_task_user_changes': {'items': [{'event_id': approved_id, 'message': '确认第2条', 'delivered': True}]},
    }
    with psycopg.connect(content_db) as db:
        def current(checkpoint):
            return db.execute('SELECT agent_task_direct_operation_current(%s,%s,%s)',
                (Jsonb(persisted), Jsonb(checkpoint), operation.id)).fetchone()[0]

        assert db.execute('SELECT agent_task_direct_records_valid(%s)', (Jsonb(persisted),)).fetchone()[0]
        assert db.execute("SELECT agent_media_confirmed_index('确认第2条')").fetchone()[0] == 2
        assert db.execute('SELECT agent_task_direct_input_hash(%s)', (Jsonb(checkpoint),)).fetchone()[0] == digest([approved_id])
        assert current(checkpoint)
        changed = copy.deepcopy(checkpoint)
        changed['_task_user_changes']['items'][0]['message'] = '取消上一条，改用新图'
        assert not current(changed)
        changed = copy.deepcopy(checkpoint)
        changed['_task_user_changes']['items'][0]['message'] = '确认第3条'
        assert not current(changed)
        changed = copy.deepcopy(checkpoint)
        changed['_task_user_changes']['items'][0]['attachments'] = [{}]
        assert not current(changed)
        tampered = copy.deepcopy(persisted)
        tampered['call_bindings'][-1]['media_slot'] = 'object-' + '0' * 32 + ':2'
        assert not db.execute('SELECT agent_task_direct_records_valid(%s)', (Jsonb(tampered),)).fetchone()[0]
