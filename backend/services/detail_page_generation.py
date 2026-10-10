"""Durable page entry and bounded worker over the shared ecommerce planner/media lifecycle."""
import asyncio
import time
from datetime import datetime, timezone
from contextlib import AsyncExitStack, suppress
from types import SimpleNamespace
from uuid import uuid4

from core.config import get_settings
from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from core.exceptions import AppException
from services.agent.image.ecommerce_planner.contracts import source_id
from services.agent.image.ecommerce_planner.inputs import FixedSettings, source_bindings
from services.agent.image.ecommerce_planner.page_profile import profile
from services.agent.image.ecommerce_planner.prompt_resources import resources, resource_versions
from services.agent.image.ecommerce_planner.format_delivery import PROMPTS_VERSION
from services.agent.image.ecommerce_planner.service import EcommerceImagePlanner, PlannerStreamBudget, _digest
from services.detail_project_service import DetailProjectService
from services.detail_project_tasks import execution_state
from services.detail_page_recovery import POLICY, DetailDeliveryRecovery, recovery_projection, latest_tasks
from services.handlers.chat_image_request import freeze_image_request, chat_image_acceptance_allowed, validate_single_image_request


def page_error(error):
    """Translate expected database guards without exposing SQL or private inputs."""
    if isinstance(error, AppException):
        return error
    guards = {
        'DETAIL_PROJECT_VERSION_CONFLICT': ('草稿已更新，请刷新后重试', 409),
        'DETAIL_RUN_ACTIVE': ('已有任务正在运行，请等待完成', 409),
        'ECOM_PLAN_EXECUTION_UNCERTAIN': ('正在核实模型调用结果，暂时不能重新调用', 409),
        'DETAIL_PLAN_NOT_RETRYABLE': ('当前阶段不能恢复，请刷新查看任务状态', 409),
        'DETAIL_IMAGE_BUDGET_EXCEEDED': ('图片数量或积分预算超出当前限制', 400),
        'IMAGE_REFERENCE_CHANGED': ('原始图片已改变，请重新上传后开始', 409),
        'DETAIL_SCOPE_DENIED': ('无权访问该任务', 403),
        'DETAIL_PROJECT_DENIED': ('无权访问该项目', 403),
    }
    for code, (message, status) in guards.items():
        if code in str(error):
            return AppException(code, message, status)
    return AppException('DETAIL_GENERATION_FAILED', '任务处理失败，请稍后重试', 500)


def page_scope(db, user_id, org_id):
    # OrgScopedDB already wraps a scoped base; don't carry its request filters into a worker.
    base = getattr(db, '_db', db)
    return ScopedDatabaseClient(base, DatabaseScope(user_id, org_id, DatabaseAccessKind.RUNTIME))


def page_owner(db, user_id, org_id, project_id):
    return SimpleNamespace(db=db, user_id=user_id, org_id=org_id, workspace_user_id=user_id,
        context_scope='user', conversation_id=None, resource_manifest=None, execution_mode='interactive',
        project_id=project_id)


def plan_error(row):
    from services.agent.image.ecommerce_planner.recovery import failure_summary
    error = row.get('recovery_state', {}).get('last_error')
    if not error:
        return None
    if error.get('code') == 'MODEL_TIMEOUT' and can_resume_plan(row):
        return {**error, 'message': '本地模型请求已超时结束，有效阶段已保存，可以从失败阶段重新尝试。'}
    return {**error, 'message': error.get('message') or failure_summary(error.get('code',''),error.get('category','business'))}


def closed_timeout(attempt):
    """Match migration 284; legacy failure is written after _call's finally closes."""
    usage = attempt.get('usage') or {}
    return (attempt.get('outcome') == 'uncertain' and bool(attempt.get('completed_at'))
        and usage.get('error_type') == 'ModelGatewayTimeoutError' and usage.get('error_code') == 'MODEL_TIMEOUT'
        and ('local_request_closed' not in usage or usage['local_request_closed'] is True))


def can_resume_plan(row):
    expires = row.get('lease_expires_at')
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires.replace('Z', '+00:00'))
    if row['status'] != 'failed' or (expires and expires > datetime.now(timezone.utc)):
        return False
    return not any(a.get('outcome') == 'started' or (a.get('outcome') == 'uncertain' and not closed_timeout(a))
        for a in (row.get('recovery_state', {}).get('attempts') or {}).values())


def acceptance_diagnostics(error):
    """Whitelist database identifiers, never DETAIL, failed rows or SQL text."""
    import re
    diag = getattr(error, 'diag', None)
    fields = {'sqlstate': getattr(error, 'sqlstate', None)}
    fields.update({key: getattr(diag, key, None) for key in ('table_name', 'column_name', 'constraint_name')})
    return {key: value for key, value in fields.items()
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{1,128}', value)}


class PageImageInputResolver:
    """Exact project paths use the existing file guard, versions, digests and CDN client."""
    def __init__(self, owner):
        from services.file_resources import FileTargetResolver
        self.files = FileTargetResolver(owner)
        self.owner = owner

    def _target(self, reference, digest=True):
        from services.agent.file_id import compute_fid
        from services.file_resources import FileTarget, content_digest
        if reference.get('file_id') != compute_fid(self.owner.org_id, reference['workspace_path']):
            raise ValueError('IMAGE_REFERENCE_CHANGED')
        target = FileTarget(self.files.guarded(reference['workspace_path']))
        if list(target.validate()) != reference['file_version'] or (digest and
            content_digest(target.path, self.files.check) != reference['content_sha256']):
            raise ValueError('IMAGE_REFERENCE_CHANGED')
        return target

    def verify(self, references):
        for reference in references:
            self._target(reference)

    def preview(self, reference):
        self._target(reference, digest=False)
        return self.files.files.get_cdn_url(reference['workspace_path'])

    def bind(self, images):
        from services.agent.file_id import compute_fid
        from services.file_resources import FileTarget, FileTargetError, file_version
        from services.handlers.image_dimensions import read_image_dimensions
        from services.tools.resource_access import ResourceAccessError
        from services.agent.image.analysis_media import AnalysisMediaError, MAX_FILE_BYTES
        bound = []
        for position, image in enumerate(images, 1):
            try:
                target = FileTarget(self.files.guarded(image['workspace_path']))
                version = target.validate()
                if version[1] > MAX_FILE_BYTES:
                    raise AnalysisMediaError('ANALYSIS_IMAGE_TOO_LARGE',f'图片{position}超过10MiB，请减小文件后重试')
                dimensions = read_image_dimensions(target.path)
                if file_version(target.path) != version:
                    raise ValueError('IMAGE_REFERENCE_CHANGED')
            except ResourceAccessError as exc:
                raise AnalysisMediaError(exc.code,'无权读取分析图片，请重新选择获准图片',status=403) from exc
            except (FileTargetError, OSError, ValueError) as exc:
                raise AnalysisMediaError('ANALYSIS_IMAGE_INVALID',f'图片{position}已缺失、变化或无法解码，请重新选择图片') from exc
            ref = {'file_id': compute_fid(self.owner.org_id, image['workspace_path']),
                'role': image['category'], 'workspace_path': image['workspace_path'],
                'file_version': list(version), 'content_sha256': dimensions['content_sha256'], 'size': version[1]}
            ref['source_id'] = source_id(ref)
            bound.append(ref)
        return bound


def image_resolver(owner, origin):
    if origin.get('destination') == 'detail_project':
        return PageImageInputResolver(owner)
    from services.handlers.chat_image_request import ChatImageInputResolver
    return ChatImageInputResolver(owner, base_revision=origin['base_context_revision'], input_message_id=origin['input_message_id'])


class DetailPageGeneration:
    def __init__(self, db, user_id, org_id):
        self.db = page_scope(db, user_id, org_id)
        self.user_id, self.org_id = user_id, org_id
        self.projects = DetailProjectService(db, user_id, org_id)

    def read(self, project_id):
        project = self.projects.get_by_id(project_id)
        plans = self.db.table('ecom_image_plans').select('*').eq('project_id', project_id).order('created_at').order('id').execute().data or []
        current_run = project.get('run_state', {}).get('run_id')
        tasks = self.db.table('tasks').select('id,status,result,result_data,error_message,credits_used,request_params,created_at').eq('user_id', self.user_id).eq(
            "request_params->'_media_request_v1'->'origin'->>'project_id'", project_id).order('created_at').order('id').execute().data or []
        groups = [{
            'plan_id': row['id'], 'kind': row['input_snapshot']['task_type'], 'status': row['status'],
            'execution_state': execution_state(row),
            'stage': row['current_stage'], 'count': row['image_count'], 'items': row['items'],
            'error': plan_error(row),
            'acceptance_error': row.get('recovery_state', {}).get('acceptance_error'),
            'resume_request_id': row.get('recovery_state', {}).get('resume_request_id'),
            'can_resume': str(row['generation_run_id']) == current_run and can_resume_plan(row),
            'auto_recovery': recovery_projection(row, project, [task for task in tasks if
                task['request_params']['_media_request_v1']['origin']['plan_source']['plan_id'] == row['id']]),
            'retry_may_have_provider_cost': can_resume_plan(row) and any(closed_timeout(a)
                for a in (row.get('recovery_state', {}).get('attempts') or {}).values()),
            'questions': (row.get('stage_outputs', {}).get(str(row['current_stage'])) or {}).get('questions', [])
                if isinstance(row.get('stage_outputs', {}).get(str(row['current_stage'])), dict) else [],
            'tasks': [{key: task.get(key) for key in ('id','status','result_data','error_message','credits_used','created_at')} |
                {'item_id': task['request_params']['_media_request_v1']['origin']['plan_source']['item_id'],
                 'submission_state': task['request_params']['_media_lifecycle_v1']['phase'],
                 'retry_of_task_id': task['request_params']['_media_request_v1']['origin'].get('retry_of_task_id')}
                for task in tasks if task['request_params']['_media_request_v1']['origin']['plan_source']['plan_id'] == row['id']]
        } for row in plans]
        runs = {}
        for row, group in zip(plans, groups):
            run_id = str(row['generation_run_id'])
            if run_id != current_run:
                group['auto_recovery'] = None
            run = runs.setdefault(run_id, {'run_id': run_id, 'created_at': row['created_at'],
                'requirement': '\n'.join(part.get('text', '') for message in row['input_snapshot'].get('messages', [])
                    for part in message.get('parts', [])), 'groups': []})
            run['groups'].append(group)
        project['runs'] = list(runs.values())
        for run in project['runs']:
            run['groups'].sort(key=lambda group: group['kind'] == 'detail_page')
        project['groups'] = runs.get(current_run, {}).get('groups', [])
        return project

    def start(self, project_id, version, request_id):
        settings = get_settings()
        project = self.projects.get_by_id(project_id)
        if project.get('run_state', {}).get('request_id') == request_id:
            return self.read(project_id)
        if not settings.detail_page_generation_enabled or not chat_image_acceptance_allowed(settings, self.user_id):
            raise AppException('DETAIL_GENERATION_DISABLED', '主图详情生成尚未开放', 409)
        selected = profile(settings, project['prompt_model'])
        if selected.ecom_analysis_transport == 'kie_upload':
            import os
            from services.adapters.kie.client import KieClient
            if not os.getenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV):
                raise AppException('ANALYSIS_UPLOAD_CONFIG','海外图片上传配置不可用，请联系管理员',503)
        project = self.projects.get_ai_input_project(project_id)
        if project['version'] != version:
            raise AppException('DETAIL_PROJECT_VERSION_CONFLICT', '草稿已更新，请刷新后重试', 409)
        refs = PageImageInputResolver(page_owner(self.db,self.user_id,self.org_id,project_id)).bind(project['images'])
        image_config=validate_single_image_request({'mode':'image_to_image','prompt':'输入配置校验',
            'aspect_ratio':project['aspect_ratio'],'resolution':project['quality'].upper()},len(refs))
        total=14 if project['content_type']=='default' else project['image_count']
        image_budget={'max_requests':settings.chat_image_max_requests,'max_credits':settings.chat_image_max_credits}
        if total>image_budget['max_requests'] or image_config['estimated_credits']*total>image_budget['max_credits']:
            raise AppException('DETAIL_IMAGE_BUDGET_EXCEEDED','图片数量或积分预算超出当前限制',400)
        selectors = [{key: ref[key] for key in ('file_id','role','source_id')} for ref in refs]
        messages = [{'source_id': f'project:{project_id}:v{version}', 'parts': [{'content_index':0, 'text':project['requirement']}]}]
        kinds = ['main_images','detail_page'] if project['content_type']=='default' else [
            'main_images' if project['content_type']=='main_image' else 'detail_page']
        rows=[]
        for kind in kinds:
            count = 7 if project['content_type']=='default' else project['image_count']
            fixed = FixedSettings(task_type=kind,platform=project['platform'],language=project['language'],
                aspect_ratio=project['aspect_ratio'],resolution=project['quality'].upper(),image_count=count)
            snapshot = {'messages':messages,'references':selectors,'resolved_references':refs,
                'image_count':count,'task_type':kind,'platform':fixed.platform,'language':fixed.language,
                'target_size':{'aspect_ratio':fixed.aspect_ratio,'resolution':fixed.resolution}}
            snapshot['source_bindings']=source_bindings(snapshot)
            rows.append({'id':str(uuid4()),'project_id':project_id,'generation_run_id':request_id,
                'user_id':self.user_id,'org_id':self.org_id,'invocation_key':kind,'input_digest':_digest(snapshot),
                'image_count':count,'input_snapshot':snapshot,'target_size':snapshot['target_size'],
                'prompt_versions':resource_versions(PROMPTS_VERSION),
                'model_settings':{'model':project['prompt_model'],'profile':vars(selected),'image_budget':image_budget,
                    'delivery_policy':POLICY}})
        try:
            self.db.rpc('start_detail_page_run', {'p_project_id':project_id,'p_version':version,
                'p_request_id':request_id,'p_plans':rows}).execute()
        except Exception as error:
            raise page_error(error) from error
        return self.read(project_id)

    def resume(self, project_id, plan_id, request_id):
        try:
            self.db.rpc('resume_detail_page_plan', {'p_project_id':project_id,
                'p_plan_id':plan_id,'p_request_id':request_id}).execute()
        except Exception as error:
            raise page_error(error) from error
        return self.read(project_id)

    def stop_recovery(self, project_id):
        try:
            self.db.rpc('stop_detail_delivery_recovery', {'p_project_id':project_id}).execute()
        except Exception as error:
            raise page_error(error) from error
        return self.read(project_id)

    async def accept(self, plan):
        # Group acceptance is atomic. Once all items have tasks, the scanner must
        # not revalidate mutable source files or resubmit an already accepted group.
        tasks = await asyncio.to_thread(lambda:self.db.table('tasks').select('request_params').eq('user_id',self.user_id).eq(
            "request_params->'_media_request_v1'->'origin'->'plan_source'->>'plan_id'",plan['id']).execute().data or [])
        accepted = {task['request_params']['_media_request_v1']['origin']['plan_source']['item_id'] for task in tasks}
        if plan['items'] and {item['item_id'] for item in plan['items']} <= accepted:
            return {'outcome':'replay'}
        refs=plan['input_snapshot']['resolved_references']
        budget=plan['model_settings']['image_budget']
        resolver=PageImageInputResolver(page_owner(self.db,self.user_id,self.org_id,plan['project_id']))
        await asyncio.to_thread(resolver.verify,refs)
        snapshots=[]
        for item in plan['items']:
            origin={'destination':'detail_project','project_id':plan['project_id'],
                'generation_run_id':plan['generation_run_id'],'actor_user_id':self.user_id,'org_id':self.org_id,
                'workspace_owner_id':self.user_id,'context_scope':'user',
                'plan_source':{'plan_id':plan['id'],'revision':plan['plan_revision'],'item_id':item['item_id'],
                    'request_text_sha256':item['request_text_sha256']}}
            snapshots.append({'item_id':item['item_id'],'snapshot':freeze_image_request(
                {'mode':'image_to_image','prompt':item['request_text'],'aspect_ratio':item['aspect_ratio'],
                 'resolution':plan['target_size']['resolution']},refs,origin=origin,
                 max_requests=budget['max_requests'],max_credits=budget['max_credits'])})
        return await asyncio.to_thread(lambda:self.db.rpc('accept_detail_page_group',{
            'p_project_id':plan['project_id'],'p_plan_id':plan['id'],'p_snapshots':snapshots,'p_org_id':self.org_id}).execute().data)


class DetailPageWorker:
    def __init__(self, db):
        self.db=db
        self.jobs={}

    @staticmethod
    def renew(service, plan_id, lease):
        return service.db.rpc('renew_detail_page_plan',{
            'p_plan_id':plan_id,'p_lease_token':lease}).execute().data

    async def close(self):
        jobs=list(self.jobs.values())
        for job in jobs: job.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)
        self.jobs.clear()

    async def scan(self):
        organizations=await asyncio.to_thread(lambda:self.db.table('organizations').select('id').execute().data)
        candidates=[]
        for org in [None,*[str(row['id']) for row in organizations or []]]:
            scoped=ScopedDatabaseClient(self.db,DatabaseScope(None,org,DatabaseAccessKind.WORKER))
            projects=await asyncio.to_thread(lambda:scoped.rpc('scan_detail_page_work',{'p_org_id':org,'p_limit':20}).execute().data)
            for project in projects:
                service=DetailPageGeneration(self.db,project['user_id'],org)
                plans=await asyncio.to_thread(lambda:service.db.table('ecom_image_plans').select('*').eq('project_id',project['id']).execute().data)
                plans=[plan for plan in plans if str(plan['generation_run_id'])==project['run_state']['run_id']]
                tasks=await asyncio.to_thread(lambda:service.db.table('tasks').select('*').eq('user_id',project['user_id']).eq(
                    "request_params->'_media_request_v1'->'origin'->>'generation_run_id'",project['run_state']['run_id']).execute().data or [])
                for plan in plans:
                    if plan['status']=='planning' and plan['id'] not in self.jobs:
                        candidates.append((service,plan))
                    elif plan['status']=='ready' and not plan.get('recovery_state',{}).get('acceptance_error'):
                        try: await service.accept(plan)
                        except Exception as error:
                            from loguru import logger
                            logger.warning('detail_image_acceptance_pending | plan={} error_type={} db={}',
                                plan['id'],type(error).__name__,acceptance_diagnostics(error))
                            public_error=page_error(error)
                            await asyncio.to_thread(lambda:service.db.table('ecom_image_plans').update({'recovery_state':{
                                **plan.get('recovery_state',{}),'acceptance_error':{'code':public_error.code,
                                    'attempt_id':str(uuid4())}}}).eq('id',plan['id']).execute())
                    if plan['id'] not in self.jobs:
                        try:
                            await DetailDeliveryRecovery(service).advance(plan,project,[task for task in tasks if
                                task['request_params']['_media_request_v1']['origin']['plan_source']['plan_id']==plan['id']])
                        except Exception as error:
                            from loguru import logger
                            logger.warning('detail_delivery_recovery_pending | plan={} error_type={}',plan['id'],type(error).__name__)
                await asyncio.to_thread(self.aggregate,service,project,plans)

        # Round-robin owners as well as projects; one user's batch cannot fill every opportunity.
        from collections import defaultdict, deque
        owners=defaultdict(deque)
        for service,plan in candidates:
            owners[(service.org_id,service.user_id)].append((service,plan))
        while owners and len(self.jobs)<get_settings().detail_page_planning_concurrency:
            for key in list(owners):
                if len(self.jobs)>=get_settings().detail_page_planning_concurrency: break
                service,plan=owners[key].popleft()
                if not owners[key]: del owners[key]
                job=asyncio.create_task(self.run(service,plan),name=f"detail-plan:{plan['id']}")
                self.jobs[plan['id']]=job
                job.add_done_callback(lambda _job,key=plan['id']:self.jobs.pop(key,None))

    @staticmethod
    def aggregate(service, project, plans):
        if not plans: return
        tasks=service.db.table('tasks').select('id,status,result,error_message,request_params,created_at').eq('user_id',project['user_id']).eq(
            "request_params->'_media_request_v1'->'origin'->>'generation_run_id'",project['run_state']['run_id']).order('created_at').order('id').execute().data or []
        latest=latest_tasks(tasks)
        expected=sum(plan['image_count'] for plan in plans)
        recovering=any((recovery_projection(plan,project,[task for task in tasks if
            task['request_params']['_media_request_v1']['origin']['plan_source']['plan_id']==plan['id']]) or {}).get('status')=='waiting'
            for plan in plans)
        if recovering:
            status='analyzing' if any(plan['status']!='ready' for plan in plans) else 'generating'
        elif len(latest)==expected and all(t['status'] in ('completed','failed','cancelled') for t in latest.values()):
            status='completed' if all(t['status']=='completed' for t in latest.values()) else 'failed'
        elif any(p['status']=='planning' for p in plans): status='analyzing'
        elif any(p['status'] in ('failed','cancelled','needs_input','insufficient') or p.get('recovery_state',{}).get('acceptance_error') for p in plans):
            # Already accepted image work must settle even when another group failed.
            status='generating' if any(t['status'] not in ('completed','failed','cancelled') for t in latest.values()) else 'failed'
        else: status='generating' if tasks else 'plan_ready'
        updates={'status':status,'updated_at':datetime.now(timezone.utc).isoformat()}
        if not project.get('title'):
            for plan in plans:
                stage=plan.get('stage_outputs',{}).get('1')
                name=stage.get('product',{}).get('name') if isinstance(stage,dict) else None
                if isinstance(name,str) and name.strip():
                    updates['title']=name.strip()[:120]
                    break
        # Rotate bounded scans so delayed recovery cannot starve newer projects.
        service.db.table('detail_projects').update(updates).eq(
            'id',project['id']).eq('user_id',project['user_id']).eq("run_state->>'run_id'",project['run_state']['run_id']).execute()

    async def run(self, service, row):
        owner=page_owner(service.db,service.user_id,service.org_id,row['project_id'])
        owner.task_id=row['id']
        owner.cancellation_event=asyncio.Event()
        frozen=SimpleNamespace(**row['model_settings']['profile'])
        remaining=frozen.wall_seconds
        saved_deadline=row.get('recovery_state',{}).get('deadline')
        if saved_deadline:
            remaining=min(remaining,max(0,(datetime.fromisoformat(saved_deadline.replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()))
        owner.execution_budget=PlannerStreamBudget(None,remaining+5)
        planner=EcommerceImagePlanner(owner,execution_profile=frozen)
        refs=row['input_snapshot']['resolved_references']
        resolver=PageImageInputResolver(owner)
        try:
            urls=[]
            version=(row.get('prompt_versions') or {}).get('stage_three_delivery_version')
            bodies,schema=resources(row['input_snapshot']['task_type'],version)
            expected=resource_versions(version)
            if any((version==PROMPTS_VERSION or key in row.get('prompt_versions',{})) and row.get('prompt_versions',{}).get(key)!=value
                    for key,value in expected.items()):
                raise ValueError('PLANNER_RESOURCE_VERSION_MISMATCH')
            # Core owns the claim. Renewal retains its exact token and never resets attempt deadlines.
            async def heartbeat():
                while True:
                    await asyncio.sleep(30)
                    lease=getattr(planner,'active_lease',None)
                    if not lease: return
                    try:
                        renewed=await asyncio.to_thread(self.renew,service,row['id'],lease)
                    except Exception:
                        owner.cancellation_event.set()
                        raise
                    if not renewed:
                        owner.cancellation_event.set()
                        return
            renewal=asyncio.create_task(heartbeat())
            async with AsyncExitStack() as media_stack:
                async def preflight():
                    from services.agent.image.analysis_media import prepare_analysis_media
                    prepared=await media_stack.enter_async_context(prepare_analysis_media(refs,resolver,
                        model=frozen.ecom_image_planning_model,
                        transport=getattr(frozen,'ecom_analysis_transport','cdn'),
                        deadline=time.monotonic()+owner.execution_budget.remaining,
                        memory_mb=getattr(frozen,'ecom_analysis_memory_mb',384),api_key=get_settings().kie_api_key))
                    urls.extend(prepared)
                try: await planner.execute(row,refs,urls,row['input_snapshot']['messages'],schema,bodies,preflight=preflight)
                finally:
                    urls.clear()
                    renewal.cancel()
                    with suppress(asyncio.CancelledError): await renewal
        except asyncio.CancelledError: raise
        except Exception as error:
            from loguru import logger
            logger.warning('detail_planning_failed | plan={} error_type={}',row['id'],type(error).__name__)
