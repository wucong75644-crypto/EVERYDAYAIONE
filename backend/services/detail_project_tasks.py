"""Bounded page task summaries. Uses the same current-run and latest-version rules as detail/downloads."""
import base64
import json
from datetime import datetime, timezone
from uuid import UUID

from psycopg.rows import dict_row
from core.db_scope import SET_DATABASE_SCOPE_SQL, DatabaseScope, DatabaseAccessKind
from core.exceptions import AppException
from services.detail_page_recovery import latest_tasks, recovery_projection


def execution_state(plan):
    expires = plan.get('lease_expires_at')
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires.replace('Z', '+00:00'))
    return 'running' if plan['status']=='planning' and expires and expires>datetime.now(timezone.utc) else 'waiting'


def summarize(project, plans, tasks):
    run = str(project.get('run_state', {}).get('run_id', ''))
    plans = [p for p in plans if str(p['generation_run_id'])==run]
    tasks = [t for t in tasks if str(t['request_params']['_media_request_v1']['origin'].get('generation_run_id'))==run]
    latest = latest_tasks(tasks)
    expected = sum(p['image_count'] for p in plans) or project['image_count']
    completed = sum(t['status']=='completed' for t in latest.values())
    recoveries = [recovery_projection(p, project, [t for t in tasks if
        str(t['request_params']['_media_request_v1']['origin']['plan_source']['plan_id'])==str(p['id'])]) for p in plans]
    recovery_waiting = any(r and r['status']=='waiting' for r in recoveries)
    stage = None
    if project['status']=='draft': display='draft'
    elif completed==expected and expected>0: display='completed'
    elif recovery_waiting: display='recovering'
    elif project['status']=='failed' or any(r and r['status']=='blocked' for r in recoveries): display='needs_attention'
    elif any(p['status']=='planning' and execution_state(p)=='running' for p in plans):
        stage=min(p['current_stage'] for p in plans if p['status']=='planning' and execution_state(p)=='running')
        display={1:'selling_points',2:'visual_direction',3:'prompts'}[stage]
    elif any(t['status'] not in ('completed','failed','cancelled') for t in latest.values()): display='generating'
    elif any(p['status']=='planning' for p in plans): display='queued'
    elif plans and all(p['status']=='ready' for p in plans): display='generating'
    else: display='needs_attention'
    title=project.get('title') or next((p.get('product_name') for p in plans if p.get('product_name')), '')
    if not title:
        kind={'default':'主图与详情','main_image':'主图','detail_page':'详情图'}[project['content_type']]
        date=str(project['created_at'])[:16].replace('T',' ')
        title=f'{kind} · {date}'
    return {key:project[key] for key in ('id','created_at','content_type','status')} | {
        'title':title[:120], 'display_status':display,'stage':stage,'expected_count':expected,
        'completed_count':completed,'recovery_waiting':recovery_waiting,
        'thumbnail_url':project.get('thumbnail_url')}


class DetailProjectTasks:
    def __init__(self, projects):
        self.projects=projects

    def list(self, cursor=None, limit=30):
        params=[]; after=''
        if cursor:
            try:
                timestamp,identity=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                if not isinstance(timestamp,str) or not isinstance(identity,str):
                    raise ValueError('Invalid cursor fields')
                datetime.fromisoformat(timestamp.replace('Z','+00:00')); UUID(identity)
                params=[timestamp,identity]; after='AND (created_at,id)<(%s::timestamptz,%s::uuid)'
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                raise AppException('DETAIL_CURSOR_INVALID','任务分页位置无效',400) from exc
        rows=self._read(after+' ORDER BY created_at DESC,id DESC LIMIT %s',params+[limit+1])
        visible=rows[:limit]; next_cursor=None
        if len(rows)>limit:
            last=visible[-1]
            next_cursor=base64.urlsafe_b64encode(json.dumps([str(last['created_at']),str(last['id'])]).encode()).decode().rstrip('=')
        return {'items':self._summaries(visible),'next_cursor':next_cursor}

    def status(self, ids):
        return self._summaries(self._read('AND id=ANY(%s::uuid[]) ORDER BY created_at DESC,id DESC',[ids]))

    def _read(self, suffix, params):
        with self.projects.db.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT id,title,created_at,content_type,status,image_count,run_state FROM public.detail_projects '
                'WHERE user_id=%s AND org_id IS NOT DISTINCT FROM %s AND status<>\'archived\' '+suffix,
                [self.projects.user_id,self.projects.org_id,*params])
            return list(cur.fetchall())

    def _summaries(self, rows):
        if not rows: return []
        ids=[str(p['id']) for p in rows]
        scope=DatabaseScope(self.projects.user_id,self.projects.org_id,DatabaseAccessKind.RUNTIME)
        with self.projects.db.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SET_DATABASE_SCOPE_SQL,scope.settings)
            cur.execute('''SELECT p.id,p.project_id,p.generation_run_id,p.status,p.current_stage,p.image_count,
                p.lease_expires_at,p.model_settings,p.recovery_state,p.delivery_recovery,
                jsonb_array_length(p.stage_attempts) AS attempt_count,
                p.stage_outputs->'1'->'product'->>'name' AS product_name
                FROM public.ecom_image_plans p JOIN public.detail_projects d ON d.id=p.project_id
                WHERE d.id=ANY(%s::uuid[]) AND d.user_id=%s AND d.org_id IS NOT DISTINCT FROM %s
                AND p.generation_run_id::text=d.run_state->>'run_id' ''',(ids,self.projects.user_id,self.projects.org_id))
            plans=list(cur.fetchall())
            # Latest version first at SQL boundary; recovery and counts never include old runs/retries.
            cur.execute('''SELECT DISTINCT ON (o->>'project_id',o->'plan_source'->>'item_id')
                t.id,t.status,t.created_at,
                jsonb_build_object('_media_request_v1',jsonb_build_object('origin',o)) AS request_params,
                t.error_message,jsonb_build_object('fail_code',t.result->>'fail_code') AS result
                FROM public.tasks t CROSS JOIN LATERAL (SELECT t.request_params->'_media_request_v1'->'origin' AS o) x
                JOIN public.detail_projects d ON d.id::text=o->>'project_id'
                WHERE d.id=ANY(%s::uuid[]) AND d.user_id=%s AND d.org_id IS NOT DISTINCT FROM %s
                AND t.user_id=d.user_id AND t.org_id IS NOT DISTINCT FROM d.org_id
                AND o->>'generation_run_id'=d.run_state->>'run_id'
                ORDER BY o->>'project_id',o->'plan_source'->>'item_id',t.created_at DESC,t.id DESC''',
                (ids,self.projects.user_id,self.projects.org_id))
            tasks=list(cur.fetchall())
            cur.execute('''SELECT DISTINCT ON (project_id) * FROM public.detail_project_images
                WHERE project_id=ANY(%s::uuid[]) AND user_id=%s AND org_id IS NOT DISTINCT FROM %s
                ORDER BY project_id,(category='product') DESC,sort_order''',(ids,self.projects.user_id,self.projects.org_id))
            images={str(i['project_id']):i for i in cur.fetchall()}
        for p in plans: p['stage_attempts']=[None]*p.pop('attempt_count')
        for row in rows:
            image=images.get(str(row['id']))
            row['thumbnail_url']=self.projects._serialize_image(image)['thumbnail_url'] if image else None
        return [summarize(p,[r for r in plans if str(r['project_id'])==str(p['id'])],
            [t for t in tasks if t['request_params']['_media_request_v1']['origin']['project_id']==str(p['id'])]) for p in rows]
