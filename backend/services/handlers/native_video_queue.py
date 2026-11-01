"""Native video jobs reuse the existing task worker, ledger, assets and WS.

Only a dispatch marker permits one create. Unknown creates retain their credit
hold and never invoke legacy Smart retry/refund or create another provider job.
"""
import asyncio
from contextlib import suppress
from datetime import datetime,timezone
import json
from uuid import uuid4

from loguru import logger
from core.db_scope import DatabaseScope,DatabaseAccessKind,ScopedDatabaseClient
from services.background_jobs import FairBackgroundJobs
from services.task_execution.video_operations import REQUEST,LIFECYCLE


def is_native_video(task):return (task.get('type')=='video' and (task.get('delivery_context') or {}).get('agent_video') is True
    and REQUEST in (task.get('request_params') or {}))


def expired(value):return bool(value and datetime.fromisoformat(str(value).replace('Z','+00:00'))<=datetime.now(timezone.utc))


class NativeVideoQueue:
    def __init__(self,db,settings):
        self.db=db;self.settings=settings;self.cursors={}
        self.jobs=FairBackgroundJobs('native-video',2,300)

    def scoped(self,task):return ScopedDatabaseClient(self.db,DatabaseScope(str(task['user_id']),task.get('org_id'),DatabaseAccessKind.WORKER))

    async def rpc(self,task,name,**parameters):
        return await asyncio.to_thread(lambda:self.scoped(task).rpc(name,{'p_task_id':str(task['id']),
            'p_org_id':task.get('org_id'),**parameters}).execute().data)

    async def refresh(self,task):
        return await asyncio.to_thread(lambda:self.scoped(task).table('tasks').select('*').eq('id',str(task['id'])).single().execute().data)

    async def close(self):await self.jobs.close()

    async def scan(self,limit=20):
        rows=await asyncio.to_thread(lambda:self.db.table('organizations').select('id').execute().data)
        candidates=[]
        for org in [None,*[str(row['id']) for row in rows or []]]:
            cursor=self.cursors.get(org)
            scoped=ScopedDatabaseClient(self.db,DatabaseScope(None,org,DatabaseAccessKind.WORKER))
            found=await asyncio.to_thread(lambda:scoped.rpc('scan_agent_video_work',{'p_org_id':org,
                'p_after_created':cursor[0] if cursor else None,'p_after_id':cursor[1] if cursor else None,'p_limit':limit}).execute().data)
            self.cursors[org]=(found[-1]['created_at'],found[-1]['id']) if found else None
            for task in found or []:
                candidates.append((task['id'],(org,task['user_id']),task['conversation_id'],lambda task=task:self.advance(task)))
        self.jobs.dispatch(candidates)

    async def advance(self,task):
        task=await self.refresh(task);state=task['request_params'][LIFECYCLE];phase=state['phase']
        if phase=='queued' or (phase=='submitting' and expired(state.get('lease_expires_at')) and not state.get('dispatch_started_at')):
            await self.submit(task)
        elif phase=='submitting' and expired(state.get('lease_expires_at')):
            await self.mark_uncertain(task,state)
        elif phase=='accepted':
            from core.task_config import VIDEO_TASK_TIMEOUT_MINUTES
            accepted=datetime.fromisoformat(state['accepted_at'].replace('Z','+00:00'))
            if (datetime.now(timezone.utc)-accepted).total_seconds()>VIDEO_TASK_TIMEOUT_MINUTES*60:
                await self.mark_uncertain(task,state)
        elif phase=='uncertain' and expired(state.get('uncertain_deadline')):
            await self.rpc(task,'publish_agent_video',p_status='failed',p_platform_pays=True,
                p_content=[{'type':'text','text':'无法确认视频生成结果，积分已退还；可能发生的生成费用由平台承担'}],p_error='供应商受理未确认，平台承担')
            await self.deliver(await self.refresh(task))
        elif phase=='settling':await self.finish_with_lock(task)
        elif phase=='published' and state.get('delivery_pending'):await self.deliver(task)
        # accepted/uncertain use the original callback/fallback poller. No create.

    async def mark_uncertain(self,task,state):
        await self.rpc(task,'mark_agent_video_uncertain',p_claim_token=state['claim_token'],
            p_timeout_seconds=self.settings.chat_image_uncertain_timeout_seconds)

    async def submit(self,task):
        from config.kie_models import calculate_video_cost
        from services.handlers.chat_image_request import canonical_hash
        from services.adapters.factory import create_video_adapter,VIDEO_MODEL_REGISTRY
        from services.handlers.video_handler import VideoHandler
        from api.deps import get_task_limit_service
        from core.exceptions import TaskQueueFullError
        snapshot=task['request_params'][REQUEST];state=task['request_params'][LIFECYCLE]
        gate=await self.rpc(task,'agent_video_submission_gate')
        if gate['outcome']=='paused':return
        if gate['outcome']!='ready':
            await self.rpc(task,'fail_agent_video_before_dispatch',p_claim_token=state.get('claim_token'),p_error='任务已停止，未提交供应商')
            return
        adapter=None;token=None
        try:
            if snapshot.get('request_hash')!=canonical_hash({k:v for k,v in snapshot.items() if k!='request_hash'}):raise ValueError('VIDEO_SNAPSHOT_CHANGED')
            current=calculate_video_cost(model_name=snapshot['model'],duration_seconds=snapshot['duration_seconds'])
            if current['user_credits']!=snapshot['estimated_credits'] or current['kie_cost']!=snapshot['estimated_provider_credits']:raise ValueError('VIDEO_PRICE_CHANGED')
            if snapshot['model'] not in VIDEO_MODEL_REGISTRY:raise ValueError('VIDEO_MODEL_UNAVAILABLE')
            adapter=create_video_adapter(snapshot['model'])
            if adapter.model!=VIDEO_MODEL_REGISTRY[snapshot['model']]['provider_model']:raise ValueError('VIDEO_MODEL_CHANGED')
            # Validate before reserving credits or emitting the dispatch marker.
            adapter.validate_prompt(snapshot['prompt']);adapter.validate_frames(str(snapshot['duration_seconds']))
            adapter.validate_aspect_ratio(snapshot['aspect_ratio']);adapter.validate_image_urls(None)
        except Exception as error:
            await self.rpc(task,'fail_agent_video_before_dispatch',p_claim_token=state.get('claim_token'),p_error=str(error))
            if adapter is not None:await adapter.close()
            return
        try:
            limiter=await get_task_limit_service()
            await limiter.acquire_image_slot(task['user_id'],task['conversation_id'],str(task['id']),task.get('org_id'))
        except TaskQueueFullError:
            await adapter.close();return
        except BaseException:
            await adapter.close();raise
        token=str(uuid4())
        try:
            claim=await self.rpc(task,'claim_agent_video_submission',p_claim_token=token,p_lease_seconds=120)
            if claim.get('outcome')!='claimed':
                if claim.get('outcome') in {'identity_denied','insufficient_credits'} or (claim.get('outcome')=='not_active' and claim.get('reason')!='paused'):
                    await self.rpc(task,'fail_agent_video_before_dispatch',p_claim_token=state.get('claim_token'),p_error=claim['outcome'])
                if claim.get('outcome')=='not_active' and claim.get('reason')=='paused':
                    await limiter.release(str(task['user_id']),str(task['conversation_id']),org_id=task.get('org_id'),slot_id=str(task['id']),strict=True)
                return
            handler=VideoHandler(self.scoped(task));handler.org_id=task.get('org_id')
            async def observe(phase,*,request,external_task_id=None):
                if phase=='dispatch':
                    reply=await self.rpc(task,'mark_agent_video_dispatch',p_claim_token=token)
                    if reply.get('outcome')!='dispatch':raise RuntimeError('VIDEO_DISPATCH_NOT_OWNED')
                else:
                    reply=await self.rpc(task,'bind_agent_video_submission',p_claim_token=token,p_external_task_id=external_task_id)
                    if reply.get('outcome')!='accepted':raise RuntimeError('VIDEO_ACCEPTANCE_UNCONFIRMED')
            callback=handler._build_callback_url(adapter.provider.value)
            if callback:callback+='&video_task='+str(task['id'])+'&video_claim='+token
            await adapter.generate(prompt=snapshot['prompt'],duration_seconds=snapshot['duration_seconds'],
                aspect_ratio=snapshot['aspect_ratio'],remove_watermark=snapshot['remove_watermark'],
                callback_url=callback,wait_for_result=False,
                _chat_video_single_submit=True,_native_video_observer=observe)
        except asyncio.CancelledError:raise
        except Exception:
            current_task=await self.refresh(task);current_state=current_task['request_params'][LIFECYCLE]
            if current_state.get('dispatch_started_at'):
                await self.mark_uncertain(task,current_state)
            elif current_state.get('claim_token')==token:
                gate=await self.rpc(task,'agent_video_submission_gate')
                if gate['outcome']=='paused':
                    # Preserve the same pending ledger for a later resume. No
                    # send marker exists, and this claim still owns the slot.
                    await limiter.release(str(task['user_id']),str(task['conversation_id']),org_id=task.get('org_id'),slot_id=str(task['id']),strict=True)
                else:
                    await self.rpc(task,'fail_agent_video_before_dispatch',p_claim_token=token,p_error='视频提交前中断，未发送供应商请求')
        finally:await adapter.close()

    async def record_result(self,task,result):
        from services.adapters.base import TaskStatus
        state=task['request_params'][LIFECYCLE]
        if result.status==TaskStatus.FAILED and result.fail_code=='TIMEOUT':
            await self.mark_uncertain(task,state);return
        payload={'task_id':result.task_id,'status':'success' if result.status==TaskStatus.SUCCESS else 'failed',
            'video_url':getattr(result,'video_url',None),'error':getattr(result,'fail_msg',None),
            'fail_code':getattr(result,'fail_code',None)}
        await self.rpc(task,'record_agent_video_result',p_external_task_id=task['external_task_id'],p_result=payload)
        await self.finish_with_lock(await self.refresh(task))

    async def finish_with_lock(self,task):
        from core.redis import RedisClient
        from services.task_completion_service import TaskCompletionService,_COMPLETION_LOCK_TTL_SECONDS
        key='task_complete_lock:'+str(task['id']);token=await RedisClient.acquire_lock(key,timeout=_COMPLETION_LOCK_TTL_SECONDS)
        if not token:return
        renewal=asyncio.create_task(TaskCompletionService(self.db)._renew_completion_lock(key,token))
        try:await self.finish(await self.refresh(task))
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError,Exception):await renewal
            await RedisClient.release_lock(key,token)

    async def finish(self,task):
        result=task.get('result') or {}
        if task['request_params'][LIFECYCLE]['phase']=='published':await self.deliver(task);return
        if result.get('status')!='success':
            error=result.get('error') or '视频未完成'
            await self.rpc(task,'publish_agent_video',p_content=[{'type':'text','text':'视频未完成：'+error}],p_status='failed',p_error=error)
            await self.deliver(await self.refresh(task));return
        from services.oss_service import get_oss_service
        from services.handlers.native_video_storage import persist,verify
        from services.assets.asset_registry import AssetRegistryService,ReadyAssetDraft,AssetRefDraft
        oss=get_oss_service();output=task['request_params'].get('_agent_video_output_v1')
        if output is None:
            output=await persist(oss,task,result['video_url'])
            asset=await asyncio.to_thread(AssetRegistryService(self.scoped(task)).register_ready_asset,
                ReadyAssetDraft(org_id=task.get('org_id'),storage_scope='user',storage_owner_key=str(task['user_id']),media_type='video',
                    original_url=output['url'],download_url=output['url'],name=output['name'],mime_type=output['mime_type'],
                    size=output['size'],content_sha256=output['content_sha256'],metadata={'native_task_id':str(task['id']),'etag':output['etag']}),
                AssetRefDraft(ref_key='task:'+str(task['id'])+':0',actor_user_id=str(task['user_id']),source_type='generated',
                    source_kind='video_task',ref_kind='task',conversation_id=str(task['conversation_id']),source_message_id=str(task['assistant_message_id']),
                    source_task_id=str(task['id']),content_index=0,model_id=task['model_id']))
            output={**output,'asset_id':str(asset['asset']['id'])}
            await self.rpc(task,'record_agent_video_output',p_output=output)
        await verify(oss,task,output)
        snapshot=task['request_params'][REQUEST]
        content={'type':'video','url':output['url'],'duration':snapshot['duration_seconds'],'asset_id':output['asset_id'],
            'task_id':str(task['id']),'content_sha256':output['content_sha256']}
        await self.rpc(task,'publish_agent_video',p_content=[content],p_status='completed')
        await self.deliver(await self.refresh(task))

    async def deliver(self,task):
        from api.deps import get_task_limit_service
        from services.websocket_manager import ws_manager
        from schemas.websocket import build_message_done
        message=await asyncio.to_thread(lambda:self.scoped(task).table('messages').select('*').eq('id',str(task['assistant_message_id'])).single().execute().data)
        if isinstance(message.get('content'),str):message['content']=json.loads(message['content'])
        await ws_manager.send_to_task_or_user(str(task['id']),str(task['user_id']),
            build_message_done(str(task['id']),str(task['conversation_id']),message,task.get('credits_used',0)),org_id=task.get('org_id'))
        limiter=await get_task_limit_service()
        await limiter.release(str(task['user_id']),str(task['conversation_id']),org_id=task.get('org_id'),slot_id=str(task['id']),strict=True)
        await self.rpc(task,'ack_agent_video_delivery')
