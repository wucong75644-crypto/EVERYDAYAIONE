"""Explicit paid employee replay. Private inputs, isolated SQL, actual model/media IO.

Not default CI collection. Never connects to an employee application database.
Run only under the existing supplier acceptance gate and owned local fixtures.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
from pydantic import TypeAdapter

from tests.manual_skill_supplier_acceptance import _real_supplier_acceptance

pytest_plugins = ['tests.test_content_objects_postgres']


@pytest.mark.asyncio
@pytest.mark.parametrize('task_db', ['everydayai'], indirect=True, scope='module')
async def test_twenty_real_employee_tasks(content_db, monkeypatch):
    await _real_supplier_acceptance(content_db, monkeypatch, suite=employee_suite)


def parts(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return [{'type': 'text', 'text': raw}]
    return raw or []


async def employee_suite(env):
    from core.config import get_settings
    from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
    from schemas.message import ContentPart, TextPart
    from services.file_executor import FileExecutor
    from services.handlers.chat_handler import ChatHandler
    from services.handlers.chat_context.image_sources import image_sources
    from services.prompt_builder.layers.static_layer import render_static_system
    from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
    from services.skills.runtime import SkillRuntime
    from services.skills.resolver import SkillCandidate
    from services.skills.contracts import ValidatedSkill
    from services.skills.assets import SkillResources
    from services.skills.capability_state import available_tool_names
    from services.tools.catalog import build_capability_catalog
    from services.agent.execution_budget import ExecutionBudget
    from services.agent.file_id import compute_fid
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    from services.task_execution import content_objects as co
    from services.task_execution.direct_loop import end_candidate
    from services.task_execution.contracts import TaskState
    from services.model_gateway import ModelGateway
    from services.adapters.factory import create_chat_adapter
    from dotenv import dotenv_values

    assert os.environ.get('RUN_EMPLOYEE_REAL_ACCEPTANCE') == '1'
    capture = Path(os.environ['EMPLOYEE_PRIVATE_CAPTURE']).resolve()
    data = json.loads((capture / 'groups-private.json').read_text())['groups']
    assert len(data) == 20
    provenance = json.loads((capture / 'provenance-private.json').read_text())
    resources = json.loads((capture / 'resources-private.json').read_text())
    dependencies = json.loads((capture / 'closed-plans-private.json').read_text())
    assets = {x['id']: x for x in provenance['assets']}
    assets.update({x['id']: x for x in json.loads((capture / 'asset-keys-private.json').read_text())['assets']})
    validated = {x['package_id']: x['value'] for x in json.loads((capture / 'skills-private.json').read_text())['validated']}
    settings, root, database = env['settings'], env['root'], env['database']
    monkeypatch, dsn, wire = env['monkeypatch'], env['content_db'], env['wire']
    run_id = uuid4()
    results = []
    schema_root=Path(__file__).parents[1]/'migrations'
    with psycopg.connect(dsn) as c:
        c.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS features jsonb NOT NULL DEFAULT '{}'")
        for name in ('060_org_departments.sql','061_org_positions.sql','064_org_member_assignments.sql'):
            c.execute((schema_root/name).read_text())
        audit=(schema_root/'046_tool_audit_log.sql').read_text()
        c.execute(audit[:audit.index('-- 2. 索引')])
        c.execute("CREATE TABLE tool_audit_log_current PARTITION OF tool_audit_log DEFAULT")
        c.execute((schema_root/'088_extend_tool_audit_log.sql').read_text())
        c.execute('ALTER TABLE tool_audit_log OWNER TO everydayai')
        c.execute('ALTER TABLE tool_audit_log_current OWNER TO everydayai')
        c.execute((schema_root/'267_mcp_tool_audit_columns.sql').read_text())
        c.execute('RESET ROLE')
        c.execute('GRANT SELECT ON org_departments,org_positions,org_member_assignments TO everydayai')
        c.execute('GRANT INSERT,SELECT ON tool_audit_log TO everydayai')
        c.execute('GRANT SELECT ON user_assets,user_asset_refs TO everydayai')
        c.execute("CREATE POLICY employee_test_asset_read ON user_assets FOR SELECT TO everydayai USING (org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND storage_scope='user' AND storage_owner_key=current_setting('app.actor_user_id',true))")
        # The original minimal fixture only modeled single-org runtime reads.
        # Native worker discovery intentionally spans orgs before scoped writes.
        c.execute("CREATE POLICY employee_test_worker_hints ON tasks FOR SELECT TO everydayai USING (current_setting('app.access_kind',true)='worker')")
    public = dotenv_values(os.environ['SKILL_SUPPLIER_SETTINGS_FILE'])
    if public.get('APP_OPENROUTER_API_KEY'):
        monkeypatch.setattr(settings, 'openrouter_api_key', public['APP_OPENROUTER_API_KEY'])
    for key in ('ecom_image_planning_enabled',):
        monkeypatch.setattr(settings, key, True)
    for key in ('ecom_image_planning_input_credits_per_million','ecom_image_planning_output_credits_per_million'):
        if public.get(key.upper()):monkeypatch.setattr(settings, key, float(public[key.upper()]))
    if os.environ.get('EMPLOYEE_PLANNER_MODEL'):
        monkeypatch.setattr(settings,'ecom_image_planning_model',os.environ['EMPLOYEE_PLANNER_MODEL'])
    if os.environ.get('EMPLOYEE_NON_THINKING_MODEL'):
        monkeypatch.setattr(settings,'ecom_image_planning_non_thinking_model',os.environ['EMPLOYEE_NON_THINKING_MODEL'])
    model_adapters = []
    def factory(model_id, **kwargs):
        adapter = create_chat_adapter(model_id, **kwargs)
        get_client = adapter._get_client
        async def client():
            actual = await get_client()
            if not getattr(actual, '_employee_evidence_hook', False):
                async def record(request):
                    wire.append({'kind': 'planner_model', 'phase': 'employee-planner', 'body': json.loads(request.content)})
                    env['persist']()
                actual.event_hooks['request'].append(record)
                actual._employee_evidence_hook = True
            return actual
        adapter._get_client = client
        model_adapters.append(adapter)
        return adapter
    gateway = ModelGateway(adapter_factory=factory, event_publisher=SimpleNamespace(publish=lambda *a, **k: None))
    monkeypatch.setattr('services.model_gateway._MODEL_GATEWAY', gateway)

    def record():
        p = root / 'employee-results.json'
        p.write_text(json.dumps({'run_id': str(run_id), 'results': results}, ensure_ascii=False, default=str, indent=2))
        p.chmod(0o600)

    def insert(c, table, row):
        columns = {x[0]: x[1] for x in c.execute('SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=\'public\' AND table_name=%s', (table,))}
        value = {k: Jsonb(v) if columns[k] in {'jsonb','json'} and v is not None else json.dumps(v,ensure_ascii=False) if columns[k]=='text' and isinstance(v,(dict,list)) else v
                 for k,v in row.items() if k in columns}
        c.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (id) DO NOTHING').format(
            sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,value)),sql.SQL(',').join(sql.Placeholder() for _ in value)), tuple(value.values()))

    async def transplant(g, ordinal):
        """Copy only required histories/resources with new identities and fresh byte proofs."""
        namespace = uuid5(run_id, str(g['index']))
        idmap = {}
        def identity(value):
            if value in validated:return value  # Published package IDs are immutable contract identities.
            if value not in idmap:idmap[value] = str(uuid5(namespace, value))
            return idmap[value]
        original_task = g['task']
        org, user = identity(original_task['org_id']), identity(original_task['user_id'])
        # Select immutable input/closed history plus explicit quoted source rows.
        all_messages = {m['id']: m for m in [*dependencies.get('messages',[]), *resources['messages'], *g['messages'], *original_task['request_params'].get('_image_sources_v1',{}).get('messages',[]), g['input']]}
        messages = {k: copy.deepcopy(m) for k,m in all_messages.items() if m['conversation_id']==original_task['conversation_id'] and (m['id']==g['input']['id'] or datetime.fromisoformat(m['created_at'])<=datetime.fromisoformat(g['input']['created_at']))}
        for _ in range(3):
            for m in list(messages.values()):
                for p in parts(m.get('content')):
                    quote = p.get('quoted_message_id') if isinstance(p,dict) else None
                    if quote and quote in all_messages:messages[quote]=copy.deepcopy(all_messages[quote])
        for _ in range(100):
            absent={m.get('reply_to_message_id') for m in messages.values()}-{None}-set(messages)
            if not absent:break
            assert absent<=set(all_messages), 'Capture must include actual reply ancestors'
            messages.update({key:copy.deepcopy(all_messages[key]) for key in absent})
        fs = FileExecutor(settings.file_workspace_root, user, org)
        paths, urls, fids = {}, {}, {}
        imports = {}
        for m in messages.values():
            for p in parts(m.get('content')):
                if not isinstance(p,dict) or p.get('type')!='image' or p.get('failed'):continue
                a = assets.get(p.get('asset_id')) or {}
                original_path = a.get('workspace_path') or p.get('workspace_path')
                if not original_path:continue
                if original_path not in paths:
                    candidates = list((capture/'workspace').rglob(Path(original_path).name))
                    digest = a.get('content_sha256') or p.get('source_content_sha256')
                    good = [f for f in candidates if f.is_file() and (not digest or hashlib.sha256(f.read_bytes()).hexdigest()==digest)]
                    if not good:
                        import httpx
                        from urllib.parse import urlparse
                        url=a.get('original_url') or a.get('download_url') or p.get('original_url') or p.get('download_url') or p.get('url')
                        assert urlparse(url).hostname=='cdn.everydayai.com.cn'
                        async with httpx.AsyncClient(trust_env=False, timeout=40) as client:
                            response=await client.get(url);response.raise_for_status()
                            assert len(response.content)<30_000_000
                            assert not digest or hashlib.sha256(response.content).hexdigest()==digest
                        saved=capture/'workspace'/'verified-extra'/Path(original_path).name
                        saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(response.content);saved.chmod(0o600)
                        good=[saved]
                    assert good, f'Fixture original unavailable: {Path(original_path).name}'
                    paths[original_path] = f'员工回放/{ordinal:02d}/{Path(original_path).name}'
                    uploaded = await env['upload']({'user':user,'org':org},good[0],paths[original_path])
                    urls[p.get('original_url') or p.get('download_url') or p.get('url')] = uploaded.url
                    for u in (a.get('original_url'),a.get('download_url'),p.get('url'),p.get('original_url'),p.get('download_url')):
                        if u:urls[u]=uploaded.url
                    fids[compute_fid(original_task['org_id'],original_path)]=compute_fid(org,paths[original_path])
                if a:imports[a['id']]=copy.deepcopy(a)
        def transform(value):
            if isinstance(value,dict):return {k:transform(v) for k,v in value.items()}
            if isinstance(value,list):return [transform(v) for v in value]
            if not isinstance(value,str):return value
            protected = {}
            for a,b in [*urls.items(), *fids.items(), *paths.items()]:
                token = f'__LOCAL_RESOURCE_{len(protected)}__'
                if a in value:
                    value = value.replace(a, token); protected[token] = b
            value = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',lambda m:identity(m[0]),value)
            for a,b in protected.items():value=value.replace(a,b)
            return value
        # Generated fresh CDN paths also contain the new actor IDs: do not
        # transform those twice. Identity mappings are fixed points.
        for mapped in list(idmap.values()):idmap[mapped]=mapped
        transformed = transform(copy.deepcopy(g))
        facts = {'dsn':dsn,'org':org,'user':user,'task':identity(original_task['id']),'conv':identity(original_task['conversation_id']),
                 'turn':identity(g['checkpoints'][0]['turn_id']),'message':identity(original_task['input_message_id']),'token':str(uuid4())}
        # Capture CDN URLs exactly as generated, not re-hashed identities.
        transformed_messages = []
        for m in messages.values():
            row = transform(m)
            row['content'] = parts(row.get('content'))
            for old_part, new_part in zip(parts(m.get('content')),row['content']):
                if old_part.get('type')=='image':
                    original = (assets.get(old_part.get('asset_id')) or {}).get('workspace_path') or old_part.get('workspace_path')
                    if original in paths:
                        new_part['workspace_path']=paths[original]
                        current_url=urls.get(old_part.get('original_url') or old_part.get('download_url') or old_part.get('url'))
                        if current_url:
                            new_part.update(url=current_url,original_url=current_url)
                            if old_part.get('download_url'):new_part['download_url']=current_url
            row['content']=parts(row['content']);transformed_messages.append(row)
        # Minimal test schema grants are fixture-owned; actual application writes
        # below always use the non-superuser scoped client and unchanged RPCs.
        with psycopg.connect(dsn) as c:
            c.execute('INSERT INTO organizations(id,status,features) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(org,dependencies['organization']['status'],Jsonb(dependencies['organization']['features'])))
            c.execute('INSERT INTO users(id,credits) VALUES(%s,100000) ON CONFLICT DO NOTHING',(user,))
            member=next(x for x in dependencies['members'] if x['user_id']==original_task['user_id'])
            c.execute('INSERT INTO org_members(org_id,user_id,status,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(org,user,member['status'],member['role']))
            for position in dependencies['positions']:
                insert(c,'org_positions',transform(position))
            departments=list(dependencies['departments']); inserted_depts=set()
            while departments:
                ready=[x for x in departments if not x.get('parent_id') or x['parent_id'] in inserted_depts]
                assert ready
                for department in ready:
                    insert(c,'org_departments',transform(department)); inserted_depts.add(department['id']); departments.remove(department)
            for assignment in dependencies['assignments']:
                if assignment['user_id']==original_task['user_id']:
                    insert(c,'org_member_assignments',transform(assignment))
            conversations={x['conversation_id'] for x in transformed_messages}|{facts['conv']}
            for conv in conversations:insert(c,'conversations',{'id':conv,'user_id':user,'org_id':org,'context_revision':original_task['base_context_revision'] or 0})
            pending=list(transformed_messages); inserted=set()
            while pending:
                ready=[m for m in pending if not m.get('reply_to_message_id') or m['reply_to_message_id'] in inserted]
                assert ready, 'Fixture missing original reply ancestry'
                for row in ready:
                    row['org_id']=org
                    insert(c,'messages',row); inserted.add(row['id']); pending.remove(row)
            task_rows={x['id']:x for x in provenance['tasks']}
            wanted_tasks={p.get('task_id') for m in messages.values() for p in parts(m.get('content')) if isinstance(p,dict)}
            wanted_tasks|={p.get('quoted_task_id') for m in messages.values() for p in parts(m.get('content')) if isinstance(p,dict)}
            for tid in wanted_tasks-{None,original_task['id']}:
                if tid in task_rows:
                    row=transform(task_rows[tid]);row.update(org_id=org,user_id=user);row['credit_transaction_id']=None;row['context_through_message_id']=None
                    insert(c,'tasks',row)
            for old_a in imports.values():
                a=transform(old_a);relative=paths[old_a['workspace_path']];path=fs.resolve_safe_path(relative)
                a.update(org_id=org,workspace_path=relative,
                         storage_key=str(path.relative_to(Path(settings.file_workspace_root))),content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size,
                         original_url=fs.get_cdn_url(relative),download_url=fs.get_cdn_url(relative))
                insert(c,'user_assets',a)
            row=transform(original_task);row.update(id=facts['task'],user_id=user,org_id=org,conversation_id=facts['conv'],status='running',execution_token=facts['token'],
                turn_id=facts['turn'],input_message_id=facts['message'],delivery_context={'actor':True},credits_locked=0,credits_used=0,credit_transaction_id=None,queue_sequence=0,assistant_message_id=None,placeholder_message_id=None,context_through_message_id=None,lease_expires_at=None)
            # Current request starts with original source inventory but no stale
            # operation or workflow from a prior paid production execution.
            row['request_params']={**{k:v for k,v in row['request_params'].items() if k in {'_image_sources_v1'}},'thinking_mode':'disabled'}
            insert(c,'tasks',row)
            c.execute("UPDATE tasks SET lease_expires_at=now()+interval '30 minutes' WHERE id=%s",(facts['task'],))
            c.execute('UPDATE messages SET status=\'pending\',turn_id=%s,context_revision=NULL WHERE id=%s',(facts['turn'],facts['message']))
        snapshot = transform(g['checkpoints'][0]['state'].get('skill_runtime'))
        verified = {identity(k):transform(v) for k,v in validated.items()}
        # Preserve original closed-history prompt, replacing only locator identities.
        history=transform(copy.deepcopy(g['checkpoints'][0]['state']['messages']))
        last=max(i for i,m in enumerate(history) if m.get('role')=='user');history=history[:last+1]
        history=[m for m in history if not(m.get('role')=='system' and isinstance(m.get('content'),str) and m['content'].startswith('[Model-directed execution]'))]
        history[0]={'role':'system','content':render_static_system()}
        input_row=next(m for m in transformed_messages if m['id']==facts['message'])
        content=TypeAdapter(list[ContentPart]).validate_python(input_row['content'])
        return facts,content,snapshot,verified,history

    async def employee_handler(facts,r,p,q,receipts):
        scoped=ScopedDatabaseClient(database,DatabaseScope(facts['user'],facts['org'],DatabaseAccessKind.RUNTIME))
        actual=ChatHandler(scoped);actual._adapter=create_chat_adapter('qwen3.8-max');actual.org_id=facts['org'];actual._actor_execution_token=facts['token'];actual._actor_turn_id=facts['turn'];actual._actor_enabled=True;actual._actor_runtime=r
        from services.tool_invocation_store import DatabaseToolInvocationStore
        actual._actor_invocation_store=DatabaseToolInvocationStore(scoped)
        actual._workspace_user_id=facts['user'];actual._personal_context_allowed=True
        dispatcher=actual._execute_tool_calls
        async def captured(calls,*args,**kwargs):
            answer=await dispatcher(calls,*args,**kwargs)
            for call,result,is_error,display in answer:
                wire.append({'kind':'tool_result','phase':'employee','call':call,'error':is_error,'display':display,'metadata':getattr(result,'metadata',{})});env['persist']()
                if call['name']=='generate_image' and not is_error and getattr(result,'metadata',{}).get('accepted'):
                    receipts.append({'receipt':copy.deepcopy(result.metadata),'native':json.loads(call['arguments'])})
            return answer
        actual._execute_tool_calls=captured
        r.task_image_resource_resolver=__import__('services.task_execution.image_resources',fromlist=['create_resolver']).create_resolver(actual,q,p.execution_context)
        p.ecom_workflow=EcommerceWorkflow(actual,q,r,p)
        await p.ecom_workflow.initialize(recover_previous=False)
        if r.skill_runtime:
            await p.ecom_workflow.coordinate(r.skill_runtime)
        return actual

    def configure_for(facts, snapshot, verified, history):
        async def configure(r,p,h,q,receipts):
            with psycopg.connect(dsn) as c:
                output=c.execute('SELECT assistant_message_id FROM tasks WHERE id=%s',(facts['task'],)).fetchone()[0]
                if output is None:
                    output = str(uuid4())
                    c.execute("INSERT INTO messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id) VALUES(%s,%s,%s,'assistant','[]','pending',%s,%s)",
                              (output,facts['conv'],facts['org'],facts['turn'],facts['message']))
                    c.execute('UPDATE tasks SET assistant_message_id=%s WHERE id=%s',(output,facts['task']))
                output=str(output)
            q=replace(q,message_id=output)
            p.budget=ExecutionBudget(max_turns=12,max_wall_time=650)
            p.messages=copy.deepcopy(history)
            sources=image_sources({'id':facts['message'],'content':[x.model_dump(mode='json') for x in q.content]},facts['org'],visible_indices=range(30))
            p.messages[-1]=UserLayer.render(UserMessageInput(text='\n'.join(x.text for x in q.content if x.type=='text'),
                image_urls=[x.original_url or x.url for x in q.content if x.type=='image'],image_sources=sources,org_id=facts['org'])).user_message
            registry=build_capability_catalog();allowed=available_tool_names(registry,p.execution_context)
            if snapshot:
                candidates=[SkillCandidate.model_validate(x) for x in snapshot['directory']]
                class Source:
                    async def discover(self):return candidates
                    async def session_bindings(self):return []
                    async def load(self,candidate,*,restoring=False):
                        raw=verified[str(candidate.package_id)]
                        assert raw['revision']==candidate.revision and raw['skill_key']==candidate.skill_key
                        assert raw['resources']=='assets=() template_variables={}'
                        return ValidatedSkill(**{k:v for k,v in raw.items() if k not in {'catalog_metadata','resources'}},
                            catalog_metadata=candidate.catalog_metadata,resources=SkillResources())
                sr=SkillRuntime(turn_id=r.turn_id,source=Source(),platform_tool_names=[v.name for v in registry.specs()],authorized_tool_names=allowed,
                    cancellation_event=r.cancellation_event,template_context={'actor_user_id':q.user_id,'org_id':facts['org'],'conversation_scope':'user','agent_domain':'general','execution_mode':'interactive','is_channel':False})
                try:
                    await sr.initialize(checkpoint=snapshot)
                except Exception:
                    try:recomputed=sr._compose().model_dump(mode='json')
                    except Exception as e:recomputed={'error':str(e)}
                    diagnostic={'snapshot':snapshot,'recomputed':recomputed,'runtime_turn_id':r.turn_id}
                    path=root/'skill-restore-diagnostic.json';path.write_text(json.dumps(diagnostic,ensure_ascii=False,default=str));path.chmod(0o600)
                    raise
                r.skill_runtime=sr;sr.ensure_messages(p.messages)
                async def on_restore(runtime, prepared, payload):
                    if not payload.get('skill_runtime'):return
                    restored=SkillRuntime(turn_id=runtime.turn_id,source=Source(),platform_tool_names=[v.name for v in registry.specs()],authorized_tool_names=allowed,
                        cancellation_event=runtime.cancellation_event,task_execution_state=runtime.task_execution_state,
                        template_context={'actor_user_id':q.user_id,'org_id':facts['org'],'conversation_scope':'user','agent_domain':'general','execution_mode':'interactive','is_channel':False})
                    await restored.initialize(checkpoint=payload['skill_runtime'])
                    runtime.skill_runtime=restored;restored.ensure_messages(prepared.messages)
                configure.restore=on_restore
            actual=await employee_handler(facts,r,p,q,receipts)
            return r,p,actual,q
        return configure

    async def restore_completion_skills(facts,r,p,h,q,receipts,payload):
        snapshot=payload.get('skill_runtime')
        if not snapshot:return await employee_handler(facts,r,p,q,receipts)
        candidates=[SkillCandidate.model_validate(x) for x in snapshot['directory']]
        class CompletionSource:
            async def discover(self):return candidates
            async def session_bindings(self):return []
            async def load(self,candidate,*,restoring=False):
                raw=validated[str(candidate.package_id)]
                assert (raw['revision'],raw['skill_key'])==(candidate.revision,candidate.skill_key)
                return ValidatedSkill(**{k:v for k,v in raw.items() if k not in {'catalog_metadata','resources'}},
                    catalog_metadata=candidate.catalog_metadata,resources=SkillResources())
        registry=build_capability_catalog()
        sr=SkillRuntime(turn_id=r.turn_id,source=CompletionSource(),platform_tool_names=[x.name for x in registry.specs()],
            authorized_tool_names=available_tool_names(registry,p.execution_context),cancellation_event=r.cancellation_event,
            task_execution_state=r.task_execution_state,template_context={'actor_user_id':facts['user'],'org_id':facts['org'],
                'conversation_scope':'user','agent_domain':'general','execution_mode':'interactive','is_channel':False})
        await sr.initialize(checkpoint=snapshot);r.skill_runtime=sr
        r.committed_composition_revision=(payload.get('_skill_composition') or {}).get('revision',0)
        sr.ensure_messages(p.messages)
        return await employee_handler(facts,r,p,q,receipts)
    env['configure_continuations'](restore_completion_skills)
    restored_input=None
    resume=os.environ.get('EMPLOYEE_RESUME')
    if resume:
        original_root=Path(resume).resolve()
        old=json.loads((original_root/'evidence.json').read_text())
        summary=json.loads(Path(os.environ.get('EMPLOYEE_RESUME_SUMMARY',original_root/'employee-results.json')).read_text())
        dump=(original_root/'isolated-database.sql').read_text()
        copies=re.findall(r'^(COPY public\.[^\n]+ FROM stdin;)\n(.*?)^\\\.\n',dump,re.M|re.S)
        assert copies
        with psycopg.connect(dsn) as c:
            c.execute('SET session_replication_role=replica')
            for command, rows in copies:
                if rows:
                    with c.cursor().copy(command.removesuffix(';')) as target:target.write(rows)
            c.execute('SET session_replication_role=origin')
            c.execute("SELECT setval('task_queue_sequence_seq',GREATEST(COALESCE((SELECT max(queue_sequence) FROM tasks),1),1),true)")
            owner=summary['results'][0]['facts'];facts={**owner,'dsn':dsn}
            if os.environ.get('EMPLOYEE_RESUME_GROUP')=='35':
                c.execute('UPDATE tasks SET queue_sequence=0 WHERE id=%s',(facts['task'],))
                task=c.execute("SELECT to_jsonb(t) FROM tasks t WHERE conversation_id=%s AND type='chat' AND status='running' ORDER BY queue_sequence DESC LIMIT 1",(facts['conv'],)).fetchone()[0]
                facts={**facts,'task':task['id'],'turn':task['turn_id'],'message':task['input_message_id'],'token':task['execution_token']}
            c.execute("UPDATE tasks SET lease_expires_at=now()+interval '30 minutes' WHERE id=%s AND status='running'",(facts['task'],))
        env['evidence'].extend(old['cases']);env['events'].extend(old['events']);wire.extend(old['wire']);env['persist']()
        for recorded in old['cases']:
            if recorded.get('external_task_id'):
                env['provider_ids'][recorded['receipt']['task_id']]=recorded['external_task_id']
        for file in original_root.glob('parallel*.json'):
            journal=json.loads(file.read_text())
            env['events'].extend(journal.get('events',[]))
            for child, value in journal.get('tasks',{}).items():
                if value.get('external_task_id'):
                    env['provider_ids'][child]=value['external_task_id']
        if os.environ.get('EMPLOYEE_RECOVER_REMAINING')=='1':
            saved=next(row for row in summary['results'] if row['group']==17)
            facts={**saved['continuation_facts'],'dsn':dsn}
            with psycopg.connect(dsn) as c:
                c.execute("UPDATE tasks SET lease_expires_at=now()+interval '30 minutes' WHERE id=%s AND status='running'",(facts['task'],))
                raw=c.execute('SELECT state FROM agent_task_states WHERE owner_task_id=%s',(facts['task'],)).fetchone()[0]
            images=await env['generate_all'](facts,saved['continuation_receipts'],'employee-17-final-five')
            assert len(images)==5
            runtime=SimpleNamespace(task_bootstrap=SimpleNamespace(execution_identity=TaskState.model_validate(raw).execution_identity))
            await env['complete_parent'](facts,runtime,expected_status='partial')
            results.append({**saved,'status':'image_completed','remaining_images':images,'total_final_images':10,
                'original_attempt':'partial','explicit_retry_and_continuation':True})
            record();await gateway.close();return
        if os.environ.get('EMPLOYEE_RECOVER_RETRY')=='1':
            saved=next(row for row in summary['results'] if row['group']==17)
            facts={**saved['facts'],'dsn':dsn}
            paid=next(row for row in reversed(old['cases']) if 'explicit-retry' in row['label'])
            images=await env['generate_all'](facts,[{'receipt':paid['receipt'],'native':paid['native']}],
                'employee-17-existing-retry',parent_terminal=True)
            results.append({**saved,'facts':facts,'status':'partial','recovered_retry_images':images})
            record()
            # Explicit user-style continuation of the original ten-item plan,
            # not an automatic retry or a fabricated employee response.
            text='继续执行第6至第10个方案，已完成的第1至第5张不要重做。'
            current=await env['next_user'](facts,text)
            with env['connect'](current) as c:
                followup=c.execute('SELECT read_agent_task_followup(%s,%s)',(current['task'],current['token'])).fetchone()[0]['source']
            g=next(g for g in data if g['index']==81)
            snapshot=copy.deepcopy(g['checkpoints'][0]['state']['skill_runtime'])
            if snapshot:
                snapshot['turn_id']=current['turn'];snapshot['composition']['turn_id']=current['turn']
            output=await env['run'](current,[TextPart(text=text)],followup=followup,
                configure=configure_for(current,snapshot,validated,[{'role':'system','content':render_static_system()},{'role':'user','content':text}]))
            r,p,h,q,receipts,blocks=output
            results[-1].update(continuation_facts=current,continuation_receipts=receipts,continuation_blocks=blocks)
            record()
            assert len(receipts)==5,'The remaining five immutable plan items must be accepted once'
            assert {entry['native']['source_prompt']['content_object']['source']['item_index'] for entry in receipts}=={6,7,8,9,10}
            finished=await env['generate_all'](current,receipts,'employee-17-remaining')
            await env['complete_parent'](current,r)
            results[-1].update(status='image_completed',remaining_images=finished,explicit_user_style_continuation=text)
            record();await gateway.close();return
        if os.environ.get('EMPLOYEE_RECOVER_COMPLETION'):
            groups={int(x) for x in os.environ['EMPLOYEE_RECOVER_COMPLETION'].split(',')}
            for saved in summary['results']:
                if saved['group'] not in groups:continue
                facts={**saved['facts'],'dsn':dsn}
                with psycopg.connect(dsn) as c:
                    c.execute("UPDATE tasks SET lease_expires_at=now()+interval '30 minutes' WHERE id=%s AND status='running'",(facts['task'],))
                    raw=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(facts['task'],)).fetchone()[0]
                receipts=saved['receipts'];failed=[]
                if os.environ.get('EMPLOYEE_RETRY_UNSENT')=='1':
                    from services.handlers.chat_image_request import LIFECYCLE_KEY, REQUEST_KEY
                    from services.handlers.chat_image_controls import ChatImageControls
                    for entry in receipts:
                        task=await env['worker'].refresh({'id':entry['receipt']['task_id'],'user_id':facts['user'],'org_id':facts['org']})
                        if task['status']=='failed':
                            assert not task.get('external_task_id') and task['request_params'][LIFECYCLE_KEY]['phase']=='published'
                            assert '尚未提交' in json.dumps(task['result_data'],ensure_ascii=False)
                            failed.append((entry,task))
                    assert len(failed)==1,'Only the recorded single pre-send failure may be retried'
                    receipts=[entry for entry in receipts if entry is not failed[0][0]]
                images=await env['generate_all'](facts,receipts,f'employee-{saved["group"]:02d}-recovered')
                assert len(images)+len(failed)==saved['accepted']
                runtime=SimpleNamespace(task_bootstrap=SimpleNamespace(execution_identity=TaskState.model_validate(raw).execution_identity))
                await env['complete_parent'](facts,runtime,expected_status='partial' if failed else 'completed')
                if failed:
                    entry,task=failed[0]
                    scoped=ScopedDatabaseClient(database,DatabaseScope(facts['user'],facts['org'],DatabaseAccessKind.RUNTIME))
                    controls=ChatImageControls(scoped,facts['user'],facts['org'])
                    request_id=str(uuid4())
                    receipt=await controls.replay(task['id'],request_id)
                    assert (await controls.replay(task['id'],request_id))['task_id']==receipt['task_id']
                    retry=await env['worker'].refresh({'id':receipt['task_id'],'user_id':facts['user'],'org_id':facts['org']})
                    original=task['request_params'][REQUEST_KEY];frozen=retry['request_params'][REQUEST_KEY]
                    for key in ('prompt','references','aspect_ratio','resolution','model'):
                        assert original[key]==frozen[key]
                    images+=await env['generate_all'](facts,[{'receipt':receipt,'native':entry['native']}],f'employee-{saved["group"]:02d}-explicit-retry',parent_terminal=True)
                results.append({**saved,'status':'image_completed','images':images,'facts':facts,'recovered_publication':True})
                if failed:results[-1].update(initial_attempt='partial',explicit_unsent_retry=True)
                record()
            assert len(results)==len(groups)
            await gateway.close();return

        if os.environ.get('EMPLOYEE_DIAGNOSE'):
            target=next(x for x in summary['results'] if x['group']==int(os.environ['EMPLOYEE_DIAGNOSE']))
            facts={**target['facts'],'dsn':dsn}
            with env['connect'](facts) as c:
                raw=c.execute('SELECT content FROM messages WHERE id=%s',(facts['message'],)).fetchone()[0]
            content=TypeAdapter(list[ContentPart]).validate_python(parts(raw))
            r,p,h,q,_=env['setup'](facts,content,update_input=False)
            block=next(b for b in target['blocks'] if b['type']=='tool_step')
            args=json.loads(block['input'])
            from services.task_execution import inline_image_action
            try:
                await inline_image_action.prepare(r,h,q,p,{'id':'offline-original-arguments','name':'generate_image'},args,build_capability_catalog(),version=inline_image_action.input_version(r,q))
                diagnostic={'status':'prepared'}
            except Exception as e:diagnostic={'status':'failed','type':type(e).__name__,'error':str(e),'trace':traceback.format_exc()}
            path=root/'native-diagnostic.json';path.write_text(json.dumps(diagnostic,ensure_ascii=False));path.chmod(0o600)
            print(json.dumps(diagnostic,ensure_ascii=False),flush=True)
            await gateway.close();return
        if os.environ.get('EMPLOYEE_RESUME_GROUP')=='35':
            g=next(g for g in data if g['index']==35)
            goal='\n'.join(x.get('text','') for x in parts(g['input']['content']) if x['type']=='text')
            with env['connect'](facts) as c:
                found=c.execute('SELECT read_agent_task_followup(%s,%s)',(facts['task'],facts['token'])).fetchone()[0]
            assert found['outcome']=='found',found
            snapshot=copy.deepcopy(g['checkpoints'][0]['state']['skill_runtime'])
            snapshot['turn_id']=facts['turn'];snapshot['composition']['turn_id']=facts['turn']
            restored_input=(facts,[TextPart(text=goal)],snapshot,validated,[{'role':'system','content':render_static_system()},{'role':'user','content':goal}],found['source'])
        else:
            with env['connect'](facts) as c:state=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(facts['task'],)).fetchone()[0]
            runtime=SimpleNamespace(task_bootstrap=SimpleNamespace(execution_identity=TaskState.model_validate(state).execution_identity))
            await env['complete_parent'](facts,runtime)
            results.append({**summary['results'][0],'status':'image_completed','recovered_without_supplier_resubmission':True})
            record();await gateway.close();return

    only={int(x) for x in os.environ.get('EMPLOYEE_CASES','').split(',') if x}
    try:
        authors={}
        ordinals={g['index']:number for number,g in enumerate(data,1)}
        ordered=sorted(data,key=lambda g:(g['index']!=37,ordinals[g['index']]))
        for g in ordered:
            ordinal=ordinals[g['index']]
            if only and g['index'] not in only:continue
            row={'group':ordinal,'index':g['index'],'status':'running','started_at':time.time()};results.append(row);record()
            print(json.dumps({'group':ordinal,'index':g['index'],'stage':'starting'}),flush=True)
            started=time.monotonic()
            try:
                followup=None
                if g['index']==35 and restored_input:
                    facts,content,snapshot,verified,history,followup=restored_input
                    row['source_group']=9
                elif g['index']==35 and 37 in authors:
                    previous=authors[37]
                    goal='\n'.join(x.get('text','') for x in parts(g['input']['content']) if x['type']=='text')
                    facts=await env['next_user'](previous['facts'],goal)
                    with env['connect'](facts) as c:
                        followup=c.execute('SELECT read_agent_task_followup(%s,%s)',(facts['task'],facts['token'])).fetchone()[0]['source']
                    assert followup and len(co.catalog(TaskState.model_validate(followup['state']))[0]['items'])==6
                    content=[TextPart(text=goal)]
                    # Initialize this turn's empty method state; adoption restores
                    # the actual prior phase checkpoint under the new turn ID.
                    snapshot=copy.deepcopy(g['checkpoints'][0]['state']['skill_runtime']);verified=previous['verified']
                    assert not snapshot.get('active') and not snapshot.get('resident')
                    snapshot['turn_id']=facts['turn'];snapshot['composition']['turn_id']=facts['turn']
                    history=[{'role':'system','content':render_static_system()},{'role':'user','content':goal}]
                    row['source_group']=9
                else:
                    facts,content,snapshot,verified,history=await transplant(g,ordinal)
                extra=os.environ.get('EMPLOYEE_CASE18_EXTRA_REQUIREMENT') if g['index']==84 else None
                if extra:
                    original_text='\n'.join(part.text for part in content if part.type=='text')
                    content=[TextPart(text=original_text+'\n\n补充要求：'+extra),*(part for part in content if part.type!='text')]
                    for message in reversed(history):
                        if message.get('role')=='user':
                            if isinstance(message['content'],list):
                                message['content'].append({'type':'text','text':'补充要求：'+extra})
                            else:message['content']+='\n\n补充要求：'+extra
                            break
                    row['simulated_additional_requirement']=extra
                row['facts']=facts;record()
                await env['set_phase'](f'employee-{ordinal:02d}-model')
                output=await env['run'](facts,content,followup=followup,configure=configure_for(facts,snapshot,verified,history))
                r,p,h,q,receipts,blocks=output
                row.update(blocks=blocks,catalog=co.catalog(r.task_execution_state),state=r.task_execution_state.model_dump(mode='json') if r.task_execution_state else None,
                    accepted=len(receipts),model_done_s=round(time.monotonic()-started,2),receipts=receipts)
                if os.environ.get('EMPLOYEE_EXPECT_NATIVE_PLAN_TEXT')=='1' and g['index']==84:
                    with env['connect'](facts) as c:
                        plans=[record[0] for record in c.execute('SELECT to_jsonb(p) FROM ecom_image_plans p WHERE parent_task_id=%s',(facts['task'],)).fetchall()]
                    ready=[plan for plan in plans if plan['status']=='ready']
                    assert len(ready)==1,'Clarified writing-only task must deliver a complete ready plan'
                    plan=ready[0];text='\n'.join(block.get('text','') for block in blocks if block['type']=='text')
                    assert len(plan['items'])==10 and all(item['request_text'] in text for item in plan['items'])
                    assert not receipts and not any(block.get('tool_name')=='save_task_text' for block in blocks)
                    row['native_plan_delivery']={'plan_id':plan['id'],'items':10,'all_original_bodies_visible':True,
                        'model_settings':plan['model_settings'],'all_content_hashes_valid':all(hashlib.sha256(item['request_text'].encode()).hexdigest()==item['request_text_sha256'] for item in plan['items'])}
                    assert row['native_plan_delivery']['all_content_hashes_valid']
                    assert not any(entry.get('kind')=='supplier_submit' for entry in wire)
                    model_controls=[entry['body'] for entry in wire if entry.get('kind')=='planner_model']
                    assert model_controls and all(body.get('enable_thinking') is False
                        or (body.get('reasoning') or {}).get('effort')=='none' for body in model_controls)
                    if os.environ.get('EMPLOYEE_EXPECT_JSON_WIRE')=='1':
                        stages=[]
                        for body in model_controls:
                            for message in body.get('messages',[]):
                                for part in message.get('content',[]) if isinstance(message.get('content'),list) else []:
                                    if part.get('type')!='text':continue
                                    try:value=json.loads(part.get('text',''))
                                    except (ValueError,TypeError):continue
                                    if isinstance(value,dict) and value.get('stage') in {1,2,3}:
                                        stage=value['stage'];stages.append(stage)
                                        assert body.get('response_format')==({'type':'json_object'} if stage in {1,3} else None)
                        assert {1,2,3}<=set(stages)
                        row['native_plan_delivery']['structured_wire_verified']=True
                record()
                if receipts:
                    completed=await env['generate_all'](facts,receipts,f'employee-{ordinal:02d}')
                    row['images']=completed;record()
                    await env['complete_parent'](facts,r)
                    row['status']='image_completed'
                else:
                    if r.task_execution_state:
                        before=r.task_execution_state;closed=end_candidate(before)
                        if closed!=before:
                            payload=copy.deepcopy(r.checkpoint_base);payload.update(_task_execution=closed.model_dump(mode='json'),_task_execution_expected_revision=before.revision)
                            with env['connect'](facts) as c:env['save'](c,facts,payload,expected=before.revision)
                    await env['close_outer'](facts,blocks)
                    if g['index']==37:
                        assert len(row['catalog'])==1 and len(row['catalog'][0]['items'])==6
                        authors[37]={'facts':facts,'snapshot':snapshot,'verified':verified}
                    row['status']='reply_completed'
                    if g['index']==61 and os.environ.get('EMPLOYEE_FOLLOW_SIZE')=='1':
                        later=[m for m in g['messages'] if m['role']=='user' and datetime.fromisoformat(m['created_at'])>datetime.fromisoformat(g['input']['created_at'])]
                        answer=next('\n'.join(x.get('text','') for x in parts(m['content']) if x['type']=='text') for m in later
                                    if re.fullmatch(r'\s*1[kK]\s*','\n'.join(x.get('text','') for x in parts(m['content']) if x['type']=='text')))
                        row['clarification']=copy.deepcopy(blocks);row['original_facts']=facts
                        new_facts=await env['next_user'](facts,answer)
                        snapshot=copy.deepcopy(snapshot);snapshot['turn_id']=new_facts['turn'];snapshot['composition']['turn_id']=new_facts['turn']
                        next_history=[*copy.deepcopy(p.messages),{'role':'user','content':answer}]
                        await env['set_phase']('employee-15-real-resolution-answer')
                        continued=await env['run'](new_facts,[TextPart(text=answer)],configure=configure_for(new_facts,snapshot,verified,next_history))
                        assert len(continued[4])==1,'Original employee resolution confirmation must produce one image'
                        finished=await env['generate_all'](new_facts,continued[4],'employee-15-confirmed')
                        assert finished[0]['snapshot']['resolution']=='1K' and finished[0]['snapshot']['aspect_ratio']=='1:1'
                        await env['complete_parent'](new_facts,continued[0])
                        row.update(status='image_completed',facts=new_facts,accepted=1,receipts=continued[4],images=finished,
                                   blocks=continued[5],employee_resolution_answer=answer)
                row['seconds']=round(time.monotonic()-started,2)
            except Exception as error:
                row.update(status='failed',error_type=type(error).__name__,error=str(error)[:1500],trace=traceback.format_exc()[-5000:],seconds=round(time.monotonic()-started,2));record()
                if os.environ.get('EMPLOYEE_EXPECT_NATIVE_PLAN_TEXT')=='1':
                    raise  # Acceptance mode must not turn collected business failures into pytest success.
            record();print(json.dumps({k:row[k] for k in ('group','index','status','seconds','accepted','error_type','error') if k in row},ensure_ascii=False),flush=True)
        assert len(results)==(len(only) if only else 20)
    finally:
        record();await gateway.close()
