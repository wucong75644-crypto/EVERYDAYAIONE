"""Explicit paid local acceptance; never collected by the default test pattern.

Run only with RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE=1 and a test-owned Redis socket.
Original incident files remain private; production application databases are never used.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from dotenv import dotenv_values
from PIL import Image
from psycopg.types.json import Jsonb
from redis.asyncio import Redis

pytest_plugins = ['tests.test_content_objects_postgres']


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_real_incidents_and_two_consecutive_edits(content_db, monkeypatch):
    await _real_supplier_acceptance(content_db, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_real_canvas_keeps_both_explicit_materials(content_db, monkeypatch):
    await _real_supplier_acceptance(content_db, monkeypatch, canvas_only=True)


async def _real_supplier_acceptance(content_db, monkeypatch, *, canvas_only=False, suite=None):
    assert os.environ.get('RUN_SKILL_REAL_SUPPLIER_ACCEPTANCE') == '1'
    root = Path(os.environ['SKILL_SUPPLIER_EVIDENCE']).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    socket = os.environ['CHAT_IMAGE_TEST_REDIS_SOCKET']
    assert socket.startswith('/private/tmp/skill-supplier-') and socket.endswith('.sock')
    from core.config import get_settings
    from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
    from core.local_db import LocalDBClient
    from core.redis import RedisClient
    from schemas.message import TextPart, ImagePart
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from services.adapters.kie.client import KieClient
    from services.adapters.kie.image_adapter import KieImageAdapter
    from services.adapters.types import StreamChunk
    from services.file_executor import FileExecutor
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.handlers.chat.stream_setup import _prepare_permission_and_tools
    from services.handlers.chat_context.image_sources import image_sources
    from services.handlers.chat_image_lifecycle import ChatImageLifecycle
    from services.handlers.chat_image_request import REQUEST_KEY, LIFECYCLE_KEY
    from services.handlers.image_dimensions import read_image_dimensions
    from services.media_tool_executor import MediaToolMixin
    from services.model_gateway import ModelGateway, ModelCallRequest
    from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
    from services.task_execution import content_objects as co
    from services.task_execution.contracts import TaskState
    from services.task_execution.direct_loop import end_candidate
    from services.task_execution.followup import FollowupRestart
    from services.task_execution.image_resources import create_resolver
    from services.task_execution.image_continuation import continuation_payload
    from services.task_execution.user_changes import restore
    from services.task_limit_service import TaskLimitService
    from services.tools import ToolCall, ToolResult
    from services.tools.dispatcher import _dispatch_call_id
    from services.tools.policy import ToolPolicy
    from tests.test_model_directed_loop import case
    from tests.test_skill_composition_postgres import seed
    from tests.test_task_execution_postgres import save
    from tests.test_task_execution_followup_reuse_postgres import adopt
    from services.agent.execution_budget import ExecutionBudget

    def connect(facts):
        connection = psycopg.connect(psycopg.conninfo.make_conninfo(facts['dsn'], user='everydayai'))
        connection.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true),set_config('app.access_kind','runtime',true)",
                           (facts['user'], facts.get('org') or ''))
        return connection

    def operation_store(facts):
        from services.task_execution.operation_store import TaskOperationStore
        from services.tools.catalog import build_capability_catalog
        from psycopg import sql
        class DB:
            def rpc(self, name, parameters):
                async def execute():
                    query = sql.SQL('SELECT {}({})').format(sql.Identifier(name), sql.SQL(',').join(
                        sql.SQL('{} => %s').format(sql.Identifier(key)) for key in parameters))
                    with connect(facts) as c:
                        result = c.execute(query, tuple(parameters.values())).fetchone()[0]
                    return SimpleNamespace(data=result)
                return SimpleNamespace(execute=execute)
        return TaskOperationStore(DB(), task_id=facts['task'], execution_token=facts['token'],
                                  turn_id=facts['turn'], registry=build_capability_catalog())

    # Match the tracked production schema's function owner. The older minimal
    # fixture omitted renewal ownership; do not add grants to the application.
    with psycopg.connect(content_db) as c:
        c.execute('ALTER FUNCTION renew_generation_lease(uuid,uuid,integer) OWNER TO everydayai')
        c.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS placeholder_created_at timestamptz')
    settings = get_settings()
    secrets = dotenv_values(os.environ['SKILL_SUPPLIER_SETTINGS_FILE'])
    for name in ('kie_api_key', 'dashscope_api_key', 'oss_access_key_id', 'oss_access_key_secret',
                 'oss_bucket_name', 'oss_endpoint', 'oss_region', 'oss_cdn_domain'):
        if name != 'oss_region':
            assert secrets.get(name.upper()), f'Missing configured {name}'
        if secrets.get(name.upper()):
            monkeypatch.setattr(settings, name, secrets[name.upper()])
    monkeypatch.setattr(settings, 'oss_internal_endpoint', None)
    workspace = Path(os.environ.get('SKILL_SUPPLIER_WORKSPACE', str(root / 'workspace'))).resolve()
    monkeypatch.setattr(settings, 'file_workspace_root', str(workspace))
    monkeypatch.setattr(settings, 'chat_image_async_enabled', True)
    monkeypatch.setattr(settings, 'agent_task_execution_enabled', True)
    redis = Redis(unix_socket_path=socket, decode_responses=True)
    monkeypatch.setattr(RedisClient, '_instance', redis)
    limiter = TaskLimitService(redis)
    async def get_limiter(): return limiter
    monkeypatch.setattr('api.deps.get_task_limit_service', get_limiter)
    events, evidence, wire = [], [], []
    def persist():
        file = root / 'evidence.json'
        file.write_text(json.dumps({'cases': evidence, 'events': events, 'wire': wire}, ensure_ascii=False, default=str, indent=2))
        file.chmod(0o600)
    async def deliver(*args, **kwargs):
        events.append(copy.deepcopy(args[2])); persist()
    monkeypatch.setattr('services.websocket_manager.ws_manager.send_to_task_or_user', deliver)
    paid_posts = 0
    resume_file = os.environ.get('SKILL_SUPPLIER_RESUME_EVIDENCE')
    resume_input = json.loads(Path(resume_file).read_text()) if resume_file else None
    assert not (canvas_only and resume_input), 'A fresh material-binding check cannot reuse an old submission'
    skip_canvas=os.environ.get('SKILL_SUPPLIER_SKIP_CANVAS')=='1'
    maximum = 64 if suite is not None else 1 if canvas_only else 4 if skip_canvas else 5
    max_new_supplier_posts = maximum - (len(resume_input['cases']) if resume_input else 0)
    assert 0 <= max_new_supplier_posts <= maximum
    phase = 'setup'
    async def capture_request(request):
        nonlocal paid_posts
        if request.method == 'POST' and request.url.path == '/api/v1/jobs/createTask':
            assert paid_posts < max_new_supplier_posts, 'Explicit supplier acceptance budget exhausted'
            paid_posts += 1
            wire.append({'kind': 'supplier_submit', 'phase': phase, 'body': json.loads(request.content), 'at': time.time()})
            persist()
    async def capture_response(response):
        if response.request.url.path == '/api/v1/jobs/createTask':
            await response.aread()
            wire.append({'kind': 'supplier_ack', 'phase': phase, 'status': response.status_code, 'body': response.json()})
            persist()
    def adapter(model, **kwargs):
        client = KieClient(settings.kie_api_key, **kwargs)
        client._client = httpx.AsyncClient(base_url=client.BASE_URL, headers=client.headers, timeout=90,
            trust_env=False, event_hooks={'request': [capture_request], 'response': [capture_response]})
        return KieImageAdapter(client, model)
    # Actual application adapter and actual HTTP transport, with private evidence hooks.
    monkeypatch.setattr('services.adapters.factory.create_image_adapter', adapter)
    database = LocalDBClient(psycopg.conninfo.make_conninfo(content_db, user='everydayai'), min_size=1, max_size=6)
    worker = ChatImageLifecycle(database, settings)
    f = None
    original_plan = json.loads(Path(__file__).with_name('fixtures').joinpath('chat_skill_failures_20261122.json').read_text())
    continuation_configure = None
    provider_ids = {}
    def configure_continuations(callback):
        nonlocal continuation_configure
        continuation_configure = callback

    def checkpoint_for(facts):
        async def checkpoint(point, payload):
            with connect(facts) as connection:
                if '_task_execution' in payload:
                    return save(connection, facts, payload, expected=payload.get('_task_execution_expected_revision', 0))
                return connection.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                    (facts['task'], facts['token'], point.value, Jsonb({**payload, 'safe_point': point.value}))).fetchone()[0]
        return checkpoint

    def setup(facts, content, *, followup=None, update_input=True):
        r, p, h, q = case(checkpoint_for(facts))
        text = content[0].text
        r.task_bootstrap = replace(r.task_bootstrap, task_id=UUID(facts['task']), conversation_id=UUID(facts['conv']),
            actor_user_id=UUID(facts['user']), input_message_id=UUID(facts['message']), original_goal=text,
            org_id=UUID(facts['org']) if facts.get('org') else None,
            execution_identity=r.task_bootstrap.execution_identity.model_copy(update={'workspace_owner_id': facts['user']}))
        r.task_id, r.turn_id, r.conversation_id = facts['task'], facts['turn'], facts['conv']
        p.execution_context = replace(p.execution_context, actor_user_id=facts['user'], workspace_owner_id=facts['user'],
            task_id=facts['task'], conversation_id=facts['conv'], org_id=facts.get('org'))
        with connect(facts) as c:
            base = c.execute('SELECT base_context_revision FROM tasks WHERE id=%s', (facts['task'],)).fetchone()[0]
            if update_input:
                c.execute("UPDATE messages SET content=%s,status='completed',context_revision=NULL,turn_id=%s WHERE id=%s",
                    (Jsonb([part.model_dump(mode='json') for part in content]), facts['turn'], facts['message']))
            rows = c.execute('SELECT to_jsonb(m) FROM messages m WHERE conversation_id=%s AND id<>%s AND status=\'completed\' ORDER BY context_revision,created_at',
                             (facts['conv'], facts['message'])).fetchall()
        q = replace(q, task_id=facts['task'], user_id=facts['user'], conversation_id=facts['conv'], content=content,
            model_id='qwen3.8-max', thinking_mode='disabled', thinking_effort='low',
            context_anchor=SimpleNamespace(input_message_id=facts['message'], base_revision=base))
        h.org_id = facts.get('org')
        h.db = ScopedDatabaseClient(database, DatabaseScope(facts['user'], facts.get('org'), DatabaseAccessKind.RUNTIME))
        h._actor_runtime = r
        r.task_image_resource_resolver = create_resolver(h, q, p.execution_context)
        r.task_operation_store = operation_store(facts)
        p.permission, p.core_tools = _prepare_permission_and_tools('auto', facts.get('org'), True, execution_context=p.execution_context)
        p.budget = ExecutionBudget(max_turns=8, max_wall_time=240)
        sources, urls, history = [], [], []
        fs = FileExecutor(settings.file_workspace_root, facts['user'], facts.get('org'))
        for row in [x[0] for x in rows] + [{'id': facts['message'], 'role': 'user', 'content': [x.model_dump(mode='json') for x in content]}]:
            discovered = image_sources(row, visible_indices=range(20))
            for source in discovered:
                if source['available']:
                    part = row['content']
                    part = json.loads(part) if isinstance(part, str) else part
                    path = part[source['content_index']]['workspace_path']
                    dimensions = read_image_dimensions(fs.resolve_safe_path(path))
                    source['canvas'] = {k: dimensions[k] for k in ('width', 'height', 'aspect_ratio')}
                    urls.append(fs.get_cdn_url(path)); sources.append(source)
            if row['id'] != facts['message']:
                parts = json.loads(row['content']) if isinstance(row['content'], str) else row['content']
                history.append({'role': row['role'], 'content': '\n'.join(x.get('text', '已生成图片' if x.get('type') == 'image' else '') for x in parts)})
        from services.prompt_builder.layers.static_layer import render_static_system
        system=render_static_system() if os.environ.get('SKILL_SUPPLIER_LIVE_AUTHORING')=='1' else '按用户要求完成。写作交付完整正文；明确执行已有方案时引用原条目，不重写，不虚构受理。'
        p.messages = [{'role': 'system', 'content': system}, *history,
            UserLayer.render(UserMessageInput(text, image_sources=sources, image_urls=urls)).user_message]
        if followup:
            r.task_followup_candidate = followup
            async def transfer(candidate, observation, payload):
                with connect(facts) as c: return adopt(c, facts, candidate, observation, payload)
            r.task_followup_adopt = transfer
        receipts = []
        async def dispatch(calls, *args, task_image_operations, **kwargs):
            results = []
            for raw in calls:
                assert raw['name'] == 'generate_image', 'Unexpected external effect during image acceptance'
                native = json.loads(raw['arguments']); parsed = ToolCall(raw['id'], raw['name'], native)
                binding = task_image_operations.bindings[raw['id']]
                began = await r.task_operation_store.begin(r.task_execution_state, step_id=binding.step_id,
                    effect_key=binding.effect_key, effect_version=binding.effect_version, call=parsed)
                assert began.outcome == 'execute'
                task_image_operations.adopt(began)
                owner = SimpleNamespace(db=h.db, task_id=facts['task'], conversation_id=facts['conv'], user_id=facts['user'],
                    workspace_user_id=facts['user'], context_scope='user', personal_context_allowed=True, org_id=facts.get('org'),
                    execution_mode='interactive', resource_manifest=None, image_execution_token=facts['token'],
                    cancellation_event=r.cancellation_event, _task_execution_state=r.task_execution_state)
                context = replace(p.execution_context, call_id=parsed.call_id)
                decision = ToolPolicy(r.task_operation_store.registry).decide(parsed.name, context, native)
                assert decision.outcome == 'allow'
                token = _dispatch_call_id.set(parsed.call_id)
                try: result = await MediaToolMixin._generate_image(owner, native)
                finally: _dispatch_call_id.reset(token)
                receipt = result.metadata
                wire.append({'kind':'native_image_acceptance','receipt':receipt,'result':str(result)})
                persist()
                assert receipt['accepted'] is True, str(result)
                receipts.append({'receipt': copy.deepcopy(receipt), 'native': native})
                wrapped = ToolResult.wrap(result, call=parsed, context=context, decision=decision)
                committed = await r.task_operation_store.complete_invocation(r.task_execution_state,
                    operation_id=began.operation.id, result=wrapped, status='accepted', receipt_ref='task:' + receipt['task_id'])
                task_image_operations.adopt(committed)
                results.append((raw, wrapped, False, '已受理'))
            return results
        h._execute_tool_calls.side_effect = dispatch
        return r, p, h, q, receipts

    async def run(facts, content, *, followup=None, exact_reply=None, continuation=None, configure=None):
        r, p, h, q, receipts = setup(facts, content, followup=followup, update_input=continuation is None)
        if configure is not None:
            replacement = await configure(r, p, h, q, receipts)
            if replacement is not None:
                r, p, h, q = replacement
        if continuation is not None:
            from services.task_execution.contracts import ContinuationInput
            r.task_execution_state = TaskState.model_validate(continuation['_task_execution'])
            r.committed_task_execution_revision = r.task_execution_state.revision
            r.checkpoint_base = copy.deepcopy(continuation)
            r.task_image_completion = ContinuationInput.model_validate(continuation['_continuation_input'])
            r.task_user_changes = restore(continuation, root_task_id=r.task_execution_state.root_task_id, owner_task_id=facts['task'])
            p.messages = copy.deepcopy(continuation['messages'])
            if continuation_configure is not None:
                h = await continuation_configure(facts, r, p, h, q, receipts, continuation)
            async def no_new_effect(*_, **__):
                raise AssertionError('Image completion must not create a new business effect')
            if continuation_configure is None:
                h._execute_tool_calls.side_effect = no_new_effect
        sink, blocks, totals = CollectingExecutionSink(), [], StreamTotals()
        if exact_reply is not None:
            async def reply(**kwargs):
                yield StreamChunk(content=exact_reply); yield StreamChunk(finish_reason='stop')
            p.adapter.stream_chat = reply
            gateway = None
        else:
            ad = DashScopeChatAdapter(settings.dashscope_api_key, 'qwen3.8-max', session_cache_enabled=False)
            async def capture(req):
                wire.append({'kind': 'model', 'phase': phase, 'body': json.loads(req.content)}); persist()
            ad._client = httpx.AsyncClient(base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
                headers={'Authorization': 'Bearer ' + settings.dashscope_api_key}, timeout=150, trust_env=False,
                event_hooks={'request': [capture]})
            gateway = ModelGateway(adapter_factory=lambda *_, **__: ad, event_publisher=SimpleNamespace(publish=lambda *_, **__: None))
            p.model_gateway = gateway.open_chat(ModelCallRequest(model_id='qwen3.8-max', task_id=facts['task'], timeout=150))
        p.stream_kwargs = {'max_tokens': 8192, 'enable_builtin_tools': True}
        try:
            for _ in range(3):
                try:
                    await _run_loop(handler=h, request=q, prepared=p, cancellation_event=r.cancellation_event,
                        sink=sink, totals=totals, blocks=blocks, runtime=r)
                    break
                except FollowupRestart as restart:
                    # The real RPC returns delivery metadata separately from
                    # its atomically persisted replay checkpoint.
                    with connect(facts) as c:
                        payload = c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s', (facts['task'],)).fetchone()[0]
                    r.task_execution_state = TaskState.model_validate(payload['_task_execution'])
                    r.committed_task_execution_revision = r.task_execution_state.revision
                    r.checkpoint_base = payload; r.task_followup_candidate = None
                    r.task_user_changes = restore(payload, root_task_id=r.task_execution_state.root_task_id, owner_task_id=facts['task'])
                    r._steer_messages.extend(i.message for i in r.task_user_changes.items if not i.delivered)
                    p.messages = copy.deepcopy(payload['messages']); blocks.clear()
                    if configure is not None and hasattr(configure, 'restore'):
                        await configure.restore(r, p, payload)
            else: raise AssertionError('Followup restart limit exceeded')
        finally:
            if gateway: await gateway.close()
        wire.append({'kind':'actor_reply','phase':phase,'blocks':copy.deepcopy(blocks)})
        persist()
        return r, p, h, q, receipts, blocks

    async def upload(facts, source, relative):
        from services.oss_service import get_oss_service
        fs = FileExecutor(settings.file_workspace_root, facts['user'], facts.get('org'))
        path = fs.resolve_safe_path(relative); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(source).read_bytes())
        url = await get_oss_service().sync_workspace_file(path, str(path.relative_to(Path(settings.file_workspace_root))))
        assert url
        async with httpx.AsyncClient(trust_env=False, timeout=45) as client:
            response = await client.get(url); response.raise_for_status()
            assert hashlib.sha256(response.content).digest() == hashlib.sha256(path.read_bytes()).digest()
        return ImagePart(url=url, workspace_path=relative, name=path.name)

    async def generate_all(facts, receipts, label, *, parent_terminal=False):
        nonlocal phase
        completed = []
        for index, entry in enumerate(receipts):
            phase = f'{label}-{index + 1}'
            child = {'id': entry['receipt']['task_id'], 'user_id': facts['user'], 'org_id': facts.get('org')}
            task = await worker.refresh(child); snapshot = task['request_params'][REQUEST_KEY]
            row = {'label': phase, 'snapshot': snapshot, 'receipt': entry['receipt'], 'native': entry['native']}
            evidence.append(row); persist()
            print(json.dumps({'phase': phase, 'stage': 'submitting', 'ratio': snapshot['aspect_ratio'], 'resolution': snapshot['resolution'], 'reference_count': len(snapshot['references'])}), flush=True)
            started = time.monotonic()
            was_completed = task['status'] == 'completed'
            if not was_completed:
                await worker.submit(task)
            task = await worker.refresh(child)
            external_id = task.get('external_task_id') or provider_ids.get(child['id'])
            row['external_task_id'] = external_id
            row['reused_completed_child'] = was_completed
            persist()
            assert external_id, task['request_params'][LIFECYCLE_KEY]
            ad = adapter(snapshot['model'])
            try:
                while time.monotonic() - started < 600:
                    result = await ad.query_task(external_id)
                    row['provider_status'] = result.status.value; persist()
                    if result.status.value in {'success', 'failed'}:
                        row['provider_urls'] = result.image_urls; row['provider_error'] = result.fail_msg; persist()
                        if not was_completed:
                            await worker.record_provider_result(task, result)
                        else:
                            assert result.status.value == 'success'
                        break
                    with connect(facts) as c:
                        renewed = c.execute('SELECT renew_generation_lease(%s,%s,300)', (facts['task'], facts['token'])).fetchone()[0]
                        assert renewed['outcome'] == 'renewed' or (parent_terminal and renewed['outcome']=='terminal'), renewed
                    await asyncio.sleep(4)
                else: raise AssertionError('Supplier still pending; preserve original task ID, do not resubmit')
            finally: await ad.close()
            final = await worker.refresh(child)
            # A competing worker may own result storage after the provider has
            # completed. Wait for its durable terminal write, never resubmit IO
            # or treat a transient `running` row as a terminal provider failure.
            deadline = time.monotonic() + 45
            while final['status'] not in {'completed', 'failed', 'cancelled'} and time.monotonic() < deadline:
                await asyncio.sleep(.25)
                final = await worker.refresh(child)
            row['elapsed_s'] = round(time.monotonic() - started, 3)
            row['status'] = final['status']; row['result'] = final.get('result_data'); persist()
            assert final['status'] == 'completed', row
            with connect(facts) as c:
                message_status, published = c.execute('SELECT status,content FROM messages WHERE id=%s', (entry['receipt']['message_id'],)).fetchone()
            assert message_status == 'completed'
            published = json.loads(published) if isinstance(published, str) else published
            part, = published
            assert part['quality_checks']['size_matches'] is True
            row['published_part'] = part
            path = FileExecutor(settings.file_workspace_root, facts['user'], facts.get('org')).resolve_safe_path(part['workspace_path'])
            with Image.open(path) as im: row['actual_dimensions'] = list(im.size)
            row['local_file'] = str(path); row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            # Read-only audit under the isolated fixture owner. Application
            # registration remains on its original scoped RPC; no grants change.
            with psycopg.connect(content_db) as c:
                assert c.execute('SELECT count(*) FROM user_assets WHERE id=%s', (part['asset_id'],)).fetchone()[0] == 1
                charges = c.execute('SELECT amount,status FROM credit_transactions WHERE task_id=%s', (child['id'],)).fetchall()
                assert charges == [(snapshot['estimated_credits'], 'confirmed')]
            row['local_settlement'] = charges
            if suite is not None:
                for journal_file in root.glob('parallel*.json'):
                    events.extend(json.loads(journal_file.read_text()).get('events', []))
            assert any(e.get('type') == 'message_done' and e.get('task_id') == child['id'] for e in events)
            persist(); completed.append(row)
            print(json.dumps({'phase': phase, 'stage': 'completed', 'seconds': row['elapsed_s'], 'dimensions': row['actual_dimensions'], 'local_file': row['local_file']}), flush=True)
        return completed

    async def close_outer(facts, parts):
        # The fixture starts inside the Actor loop. Complete its outer turn
        # through the real terminal RPC before asking the dependency worker to resume.
        with psycopg.connect(content_db) as c:
            owner = c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s', (facts['task'],)).fetchone()[0]
            output = owner['assistant_message_id']
            if output is None:
                output = str(uuid4())
                c.execute("INSERT INTO messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id) VALUES(%s,%s,%s,'assistant','[]','pending',%s,%s)",
                    (output, facts['conv'], facts.get('org'), facts['turn'], facts['message']))
                c.execute('UPDATE tasks SET assistant_message_id=%s WHERE id=%s', (output, facts['task']))
        with connect(facts) as c:
            closed = c.execute('SELECT commit_generation_turn(%s,%s,%s,%s,%s,%s,%s)',
                (facts['task'], facts['token'], output, Jsonb(parts), Jsonb({}), 0, None)).fetchone()[0]
            assert closed['outcome'] in {'committed', 'already_committed'}, closed

    async def next_user(facts, text):
        new = {**facts, 'task': str(uuid4()), 'message': str(uuid4()), 'turn': str(uuid4())}
        output = str(uuid4())
        with psycopg.connect(content_db) as c:
            c.execute("INSERT INTO messages(id,conversation_id,org_id,role,content,status,turn_id) VALUES(%s,%s,%s,'user',%s,'pending',%s)",
                (new['message'], facts['conv'], facts.get('org'), Jsonb([{'type': 'text', 'text': text}]), new['turn']))
            c.execute("INSERT INTO messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id) VALUES(%s,%s,%s,'assistant','[]','pending',%s,%s)",
                (output, facts['conv'], facts.get('org'), new['turn'], new['message']))
            task_data = {'id': new['task'], 'user_id': new['user'], 'conversation_id': new['conv'],
                         'assistant_message_id': output, 'org_id': facts.get('org'), 'model_id': 'qwen3.8-max', 'request_params': {'thinking_mode': 'disabled'}}
            c.execute('SELECT enqueue_generation_turn(%s,%s,%s,%s,%s)',
                (Jsonb(task_data), new['message'], new['turn'], 'serial', Jsonb({'actor': True})))
        with connect(new) as c:
            claimed = c.execute('SELECT claim_next_serial_generation_turn(%s,300,3)', (new['conv'],)).fetchone()[0]
            assert claimed['outcome'] == 'claimed' and claimed['task_id'] == new['task'], claimed
            new['token'] = claimed['execution_token']
        return new

    async def complete_parent(facts, runtime, *, expected_status='completed'):
        # Mirror the outer Actor's terminal reduction before committing the
        # physical turn. The in-loop fixture otherwise leaves `active` behind.
        with connect(facts) as c:
            state=c.execute('SELECT state FROM agent_task_states WHERE owner_task_id=%s',(facts['task'],)).fetchone()
            task_status=c.execute('SELECT status FROM tasks WHERE id=%s',(facts['task'],)).fetchone()[0]
            if state and state[0]['status']=='active' and task_status=='running':
                from services.task_execution.direct_loop import end_candidate
                before=TaskState.model_validate(state[0]);closed=end_candidate(before)
                saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(facts['task'],)).fetchone()[0]
                saved.update(_task_execution=closed.model_dump(mode='json'),_task_execution_expected_revision=before.revision)
                save(c,facts,saved,expected=before.revision)
        await close_outer(facts, [])
        with connect(facts) as c:
            saved_state = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=(SELECT root_task_id FROM agent_task_events WHERE child_task_id=%s)',
                (next(row['receipt']['task_id'] for row in reversed(evidence) if row['snapshot']['origin']['parent_task_id'] == facts['task']),)).fetchone()[0]
            if saved_state['status'] == expected_status:
                return
            last_child = saved_state['operations'][-1]['receipt_ref'].removeprefix('task:')
        from core.local_db import AsyncLocalDBClient
        from services.task_execution.continuation_consumer import ImageContinuationConsumer
        client = AsyncLocalDBClient(psycopg.conninfo.make_conninfo(content_db, user='everydayai'), min_size=1, max_size=2)
        await client.open()
        try:
            consumer=ImageContinuationConsumer(client)
            for _ in range(32):
                await consumer.scan_once()
                with connect(facts) as c:
                    status=c.execute('SELECT status FROM agent_task_events WHERE child_task_id=%s',(last_child,)).fetchone()[0]
                if status=='queued':break
                if consumer.cursor is None:break
        finally:
            await client.close()
        with connect(facts) as c:
            event_status, continuation_task = c.execute('SELECT status,continuation_task_id FROM agent_task_events WHERE child_task_id=%s', (last_child,)).fetchone()
            assert event_status == 'queued' and continuation_task
            assert c.execute("SELECT count(*) FROM tasks WHERE delivery_context->>'logical_task_id'=%s AND status='pending'", (saved_state['root_task_id'],)).fetchone()[0] == 1
        queued = {'task_id': str(continuation_task)}
        from services.conversation_execution import GenerationClaim
        with connect(facts) as c:
            claim_result = c.execute('SELECT claim_next_serial_generation_turn(%s,300,3)', (facts['conv'],)).fetchone()[0]
            task = c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s', (queued['task_id'],)).fetchone()[0]
        claim = GenerationClaim.from_rpc(claim_result, facts['conv'], 'serial')
        assert claim is not None and claim.task_id == queued['task_id']
        with connect(facts) as c:
            bundle = c.execute('SELECT load_agent_image_continuation(%s,%s)', (claim.task_id, claim.execution_token)).fetchone()[0]
        scope = SimpleNamespace(actor_user_id=facts['user'], **runtime.task_bootstrap.execution_identity.model_dump())
        payload, candidate = continuation_payload(bundle, task=task, claim=claim, execution_scope=scope)
        assert candidate.status in {'active', 'completed', 'partial'}
        with connect(facts) as c:
            applied = c.execute('SELECT apply_agent_image_continuation(%s,%s,%s,%s)',
                (claim.task_id, claim.execution_token, saved_state['revision'], Jsonb(payload))).fetchone()[0]
            assert applied['outcome'] == 'saved'
        current = {**facts, 'task': claim.task_id, 'turn': claim.turn_id,
                   'message': claim.input_message_id, 'token': claim.execution_token}
        resumed = await run(current, [TextPart(text=candidate.original_goal)], continuation=payload)
        wire.append({'kind':'actor_continuation_reply','blocks':resumed[5],
                     'state_status':resumed[0].task_execution_state.status})
        persist()
        if resumed[4] and suite is not None:
            await generate_all(current, resumed[4], 'remaining-original-plan')
            await complete_parent(current, resumed[0], expected_status=expected_status)
            return
        assert not resumed[4]
        if expected_status == 'partial':
            from services.task_execution.direct_loop import end_candidate
            before=resumed[0].task_execution_state
            closed=end_candidate(before)
            with connect(current) as c:
                saved=copy.deepcopy(resumed[0].checkpoint_base)
                saved.update(_task_execution=closed.model_dump(mode='json'),_task_execution_expected_revision=before.revision)
                save(c,current,saved,expected=before.revision)
            resumed[0].task_execution_state=closed
        assert resumed[0].task_execution_state.status == expected_status
        await close_outer(current, [])
        wire.append({'kind': 'actor_continuation_completed', 'phase': phase,
                     'image_artifacts': len([a for a in candidate.artifacts if a.kind == 'image'])})
        persist()

    try:
        if suite is not None:
            async def set_phase(value):
                nonlocal phase
                phase = value
            return await suite(locals())
        if resume_file:
            phase = 'recorded-completion-recovery'
            resume = resume_input
            assert resume and all(row['status'] == 'completed' for row in resume['cases'])
            prior_case = resume['cases'][-1]
            info = psycopg.conninfo.conninfo_to_dict(content_db)
            assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
            dump = Path(os.environ['SKILL_SUPPLIER_RESUME_DUMP']).read_text()
            copies = re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n', dump, re.M | re.S)
            assert copies
            with psycopg.connect(content_db) as c:
                c.execute('SET session_replication_role=replica')
                for command, rows in copies:
                    if rows:
                        with c.cursor().copy(command.removesuffix(';')) as target:
                            target.write(rows)
                c.execute('SET session_replication_role=origin')
                c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
                parent = c.execute('SELECT to_jsonb(t) FROM tasks t WHERE id=%s', (prior_case['snapshot']['origin']['parent_task_id'],)).fetchone()[0]
                state = c.execute('SELECT state FROM agent_task_states WHERE root_task_id=(SELECT root_task_id FROM agent_task_events WHERE child_task_id=%s)', (prior_case['receipt']['task_id'],)).fetchone()[0]
            f = dict(dsn=content_db, user=parent['user_id'], conv=parent['conversation_id'], task=parent['id'],
                     turn=parent['turn_id'], token=parent['execution_token'], message=parent['input_message_id'])
            originals = [next(workspace.rglob('reference-1.png')), next(workspace.rglob('reference-2.jpg'))]
            for saved_case in resume['cases']:
                row = copy.deepcopy(saved_case)
                child = {'id': row['receipt']['task_id'], 'user_id': row['snapshot']['origin']['actor_user_id'], 'org_id': None}
                task = await worker.refresh(child)
                assert task['external_task_id'] == row['external_task_id']
                assert task['request_params'][REQUEST_KEY] == row['snapshot']
                with psycopg.connect(content_db) as c:
                    published = c.execute('SELECT content FROM messages WHERE id=%s', (row['receipt']['message_id'],)).fetchone()[0]
                    part, = json.loads(published) if isinstance(published, str) else published
                    assert c.execute('SELECT count(*) FROM user_assets WHERE id=%s', (part['asset_id'],)).fetchone()[0] == 1
                    assert c.execute('SELECT amount,status FROM credit_transactions WHERE task_id=%s', (child['id'],)).fetchall() == [(row['snapshot']['estimated_credits'], 'confirmed')]
                assert part['quality_checks']['size_matches'] is True
                path = FileExecutor(settings.file_workspace_root, child['user_id'], None).resolve_safe_path(part['workspace_path'])
                row.update(published_part=part, local_file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                with Image.open(path) as image: row['actual_dimensions'] = list(image.size)
                query = adapter(row['snapshot']['model'])
                try:
                    result = await query.query_task(row['external_task_id'])
                    assert result.status.value == 'success'
                finally: await query.close()
                evidence.append(row)
            wire.extend(resume['wire'])
            events.extend(resume['events'])
            persist()
            with psycopg.connect(content_db) as c:
                c.execute('UPDATE messages SET context_revision=NULL WHERE id=%s AND context_revision=0', (f['message'],))
            runtime = SimpleNamespace(task_bootstrap=SimpleNamespace(execution_identity=TaskState.model_validate(state).execution_identity))
            if parent['status'] == 'running':
                with connect(f) as c:
                    renewed = c.execute('SELECT renew_generation_lease(%s,%s,300)', (f['task'], f['token'])).fetchone()[0]
                    assert renewed['outcome'] == 'renewed'
            await complete_parent(f, runtime)
            assert paid_posts == 0
            print('CANVAS_RECOVERED_FROM_REAL_PAID_RESULT_WITHOUT_RESUBMISSION', flush=True)
        else:
            # Scenario 1: exact real user request and original image bytes, fresh model.
            phase = 'canvas-model'
            f = seed(content_db)
            with psycopg.connect(content_db) as c: c.execute('UPDATE users SET credits=10000 WHERE id=%s', (f['user'],))
            prior = json.loads((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT']) / 'fixed-live-image-off-3.json').read_text())
            refs = prior['native'][0]['recipes'][0]['resolved']
            # Locate exact verified originals by the captured relative workspace paths.
            originals = [next((Path(os.environ['SKILL_FAILURE_CAPTURE_ROOT']) / 'workspace').rglob(Path(ref['workspace_path']).name)) for ref in sorted(refs, key=lambda x: x['content_index'])]
            for path, reference in zip(originals, sorted(refs, key=lambda x: x['content_index'])):
                assert hashlib.sha256(path.read_bytes()).hexdigest() == reference['content_sha256']
            if not skip_canvas:
                attachments = [await upload(f, path, f'验收原图/reference-{i}{path.suffix}') for i, path in enumerate(originals, 1)]
                canvas = await run(f, [TextPart(text=original_plan['canvas']['user_text']), *attachments])
                assert len(canvas[4]) == 1
                first = (await generate_all(f, canvas[4], 'canvas'))[0]
                assert first['snapshot']['aspect_ratio'] == '3:4' and first['snapshot']['resolution'] == '1K'
                target = first['snapshot']['size_requirement']
                assert first['snapshot']['references'][target['reference_index']]['content_index'] == 2
                assert {ref['content_index'] for ref in first['snapshot']['references']} == {1, 2}
                posted = next(entry for entry in wire if entry['kind'] == 'supplier_submit' and entry['phase'] == 'canvas-1')
                assert len(posted['body']['input']['input_urls']) == 2
    
                await complete_parent(f, canvas[0])
                if canvas_only:
                    assert paid_posts == max_new_supplier_posts == 1
                    print('REAL_CANVAS_TWO_MATERIALS_COMPLETE: one actual job, exact source hashes and target size', flush=True)
                    return

        if resume_input and len(resume_input['cases']) >= 3:
            chosen = [row for row in evidence if row['label'].startswith('plan-')]
            assert len(chosen) == 2
        else:
            # Scenario 2: replay the user's original five-plan reply verbatim, then select 1 and 3.
            phase = 'original-plan-registration'
            f = seed(content_db)
            with psycopg.connect(content_db) as c: c.execute('UPDATE users SET credits=10000 WHERE id=%s', (f['user'],))
            original = await upload(f, originals[0], '验收原图/product.png')
            live_authoring=os.environ.get('SKILL_SUPPLIER_LIVE_AUTHORING')=='1'
            author = await run(f, [TextPart(text=('这是卡册。帮我写5个提示词，参考这个产品做新品封面，只设计封面配色、装饰和拍摄背景。不增加内页书写用途、规格、性能、价格或赠品，没有资料的材质不要当成既有事实。先只写完整文字方案，不要生成图片。' if live_authoring else original_plan['plan']['user_text'])), original],
                exact_reply=None if live_authoring else original_plan['plan']['response'])
            r, p, h, q, _, blocks = author
            row, = co.catalog(r.task_execution_state); assert len(row['items']) == 5
            authored_text=blocks[-1]['text']
            if not live_authoring:
                assert authored_text == original_plan['plan']['response']
            expected_bodies=[co.load(r.task_execution_state,{'artifact_id':row['artifact_id'],'item_index':i})[2].body for i in range(1,6)]
            if live_authoring:
                assert not author[4], 'Writing-only authoring must not create an image job'
                for index in range(1,6):
                    item=co.load(r.task_execution_state,{'artifact_id':row['artifact_id'],'item_index':index})[2]
                    assert len(item.materials)==1 and item.materials[0].usage=='input', 'Original product reference must survive host registration'
            before = r.task_execution_state; closed = end_candidate(before)
            payload = copy.deepcopy(r.checkpoint_base); payload.update(_task_execution=closed.model_dump(mode='json'), _task_execution_expected_revision=before.revision)
            with connect(f) as c: assert save(c, f, payload, expected=before.revision)['outcome'] == 'saved'
            await close_outer(f, [{'type': 'text', 'text': authored_text, 'content_artifact_id': row['artifact_id']}])
            f = await next_user(f, original_plan['plan']['selection'])
            with connect(f) as c:
                source = c.execute('SELECT read_agent_task_followup(%s,%s)', (f['task'], f['token'])).fetchone()[0]['source']
            phase = 'plan-selection'
            selected = await run(f, [TextPart(text=original_plan['plan']['selection'])], followup=source)
            assert len(selected[4]) == 2
            for entry, index in zip(selected[4], (1, 3)):
                native = entry['native']; assert native['source_prompt']['content_object']['source']['item_index'] == index
                assert native['prompt'] == expected_bodies[index - 1]
                assert len(native['references'])==1
                accepted=await worker.refresh({'id':entry['receipt']['task_id'],'user_id':f['user'],'org_id':f.get('org')})
                frozen=accepted['request_params'][REQUEST_KEY]
                assert frozen['references'][0]['content_sha256']==hashlib.sha256(originals[0].read_bytes()).hexdigest()
            chosen = await generate_all(f, selected[4], 'plan')
            assert all(x['snapshot']['aspect_ratio'] == '1:1' and x['snapshot']['resolution'] == '1K' for x in chosen)

            await complete_parent(f, selected[0])

        # Consecutive real edits: target the last actual generated image, then its output.
        previous = evidence[-1]
        resumed_edits = len([row for row in evidence if row['label'].startswith('edit-')])
        for edit_number, instruction in enumerate((
                '把你刚生成的那张图的桌面背景改为浅蓝色，产品的形态、封面图案和构图都保持不变。直接生成一张。',
                '把你刚生成的那张图的桌面背景改为浅米灰色，产品的形态、封面图案和构图都保持不变。直接生成一张。'), 1):
            if edit_number <= resumed_edits:
                continue
            f = await next_user(f, instruction)
            phase = f'edit-{edit_number}-model'
            edit = await run(f, [TextPart(text=instruction)])
            assert len(edit[4]) == 1
            native = edit[4][0]['native']
            assert len(native['references']) == 1
            assert native['references'][0]['message_id'] == previous['receipt']['message_id']
            result = (await generate_all(f, edit[4], f'edit-{edit_number}'))[0]
            assert result['snapshot']['references'][0]['content_sha256'] == previous['sha256']
            assert result['snapshot']['aspect_ratio'] == previous['snapshot']['aspect_ratio']
            assert result['snapshot']['resolution'] == previous['snapshot']['resolution']
            await complete_parent(f, edit[0])
            previous = result
        assert paid_posts == max_new_supplier_posts
        print(f'REAL_SUPPLIER_ACCEPTANCE_COMPLETE: {maximum} actual jobs, numbered plans, two consecutive edits', flush=True)
    finally:
        persist()
        # Preserve the isolated state for evidence/recovery before fixture teardown.
        dump = root / 'isolated-database.sql'
        with dump.open('wb') as output:
            subprocess.run(['pg_dump', '--no-owner', '--no-privileges', content_db], stdout=output, check=False)
        dump.chmod(0o600)
        if suite is not None:
            # Text-only turns can have a bootstrap checkpoint without a task
            # projection. Preserve evidence above, then remove all fixture-owned
            # checkpoints before the fixture attempts protected schema rollback.
            info = psycopg.conninfo.conninfo_to_dict(content_db)
            assert info['dbname'].startswith('chat_image_test_') and info['host'].startswith('/')
            with psycopg.connect(content_db) as connection:
                connection.execute('DELETE FROM agent_task_events')
                connection.execute('DELETE FROM agent_task_states')
                connection.execute('DELETE FROM conversation_turn_checkpoints')
                connection.execute('SET CONSTRAINTS ALL IMMEDIATE')
        await worker.close()
        database.close()
        await redis.aclose()
