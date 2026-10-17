"""Spawn-only test entrypoint: fresh DB/Redis clients and an actual worker per PID."""
import asyncio
import json
import os
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch


def run_worker_process(dsn, socket, root, identities, role, order, barrier, entered, action_done, callback_done, calls, reports):
    """No inherited clients, fake IO only at supplier/storage/asset boundaries."""
    async def run():
        from core.config import Settings
        from core.db_scope import DatabaseScope, DatabaseAccessKind, ScopedDatabaseClient
        from core.local_db import LocalDBClient
        from core.redis import RedisClient
        from redis.asyncio import Redis
        from services.adapters.base import ImageGenerateResult, TaskStatus
        from services.handlers.chat_image_lifecycle import ChatImageLifecycle
        from services.task_limit_service import TaskLimitService
        from api.routes.webhook import handle_webhook, _processing_tasks
        from starlette.requests import Request
        from loguru import logger
        logger.remove()
        settings = Settings(_env_file=None, database_url='postgresql://invalid/test',
            jwt_secret_key='isolated-test-key', file_workspace_root=root,
            callback_token='isolated-callback', chat_image_processing_concurrency=20 if order=='capacity' else 2)
        raw = LocalDBClient(dsn, min_size=1, max_size=4)
        redis = Redis(unix_socket_path=socket, decode_responses=True)
        limiter = TaskLimitService(redis)
        worker = ChatImageLifecycle(raw, settings)
        first_storage = True

        async def generate(**kwargs):
            with calls.get_lock():
                calls.value += 1
                index = calls.value
            return ImageGenerateResult(task_id=f'spawn-{os.getpid()}-{index}', status=TaskStatus.PENDING)

        async def storage(*args, **kwargs):
            nonlocal first_storage
            if role=='recovery' and order=='processing_timeout' and first_storage:
                first_storage = False
                entered.set()
                await asyncio.Event().wait()  # Actual pool deadline cancels this.
            return [{'url':'https://local.invalid/saved.png',
                'workspace_path':f"generated/{identities[0]['id']}.png"}]

        async def wait(event):
            if not await asyncio.to_thread(event.wait, 10):
                raise TimeoutError('Process handoff did not arrive')

        async def callback():
            if order in ('recovery_first', 'processing_timeout'):
                await wait(action_done if order=='recovery_first' else entered)
            external = (await worker.refresh(identities[0]))['external_task_id']
            payload = {'code':200,'data':{'taskId':external, 'state':'success',
                'resultJson':json.dumps({'resultUrls':['https://provider.invalid/result.png']})}}
            scoped = ScopedDatabaseClient(raw, DatabaseScope(identities[0]['user_id'], None, DatabaseAccessKind.WORKER))
            async def send_one():
                body = json.dumps(payload).encode()
                async def receive(): return {'type':'http.request','body':body,'more_body':False}
                request = Request({'type':'http','method':'POST','path':'/api/webhook/kie',
                    'query_string':b'token=isolated-callback','headers':[]}, receive)
                response = await handle_webhook('kie', request, scoped)
                assert response.status_code==200
                pending = list(_processing_tasks)
                if pending: await asyncio.gather(*pending)
            # Concurrent duplicate HTTP callbacks, not only terminal-state replay.
            await asyncio.gather(*(send_one() for _ in range(3)))
            callback_done.set()

        async def recover():
            if order=='callback_first': await wait(callback_done)
            if order=='processing_timeout': worker._work.timeout = .5
            identity = identities[0]
            # Use the actual pool and lifecycle; retain a stale read for the race.
            task = await worker.refresh(identity)
            worker._work.dispatch([(identity['id'],identity['user_id'],'project',lambda:worker.advance(task))])
            await asyncio.gather(*list(worker._work.jobs.values()))
            if order=='processing_timeout': assert callback_done.is_set()
            action_done.set()
            async with asyncio.timeout(10):
                while True:
                    row = await worker.refresh(identity)
                    if row['request_params']['_media_lifecycle_v1']['phase']=='published': break
                    await worker.scan()
                    await asyncio.gather(*list(worker._work.jobs.values()))
                    await asyncio.sleep(.005)
            await wait(callback_done)

        with ExitStack() as stack:
            for target, value in [
                ('core.config.get_settings',lambda:settings),
                ('api.routes.webhook.get_settings',lambda:settings),
                ('core.redis.RedisClient._instance',redis),
                ('api.deps.get_task_limit_service',AsyncMock(return_value=limiter)),
                ('services.adapters.factory.create_image_adapter',lambda *a,**kw:NS(generate=generate,close=AsyncMock())),
                ('services.file_upload.persist_media_urls_to_workspace',storage),
                ('services.assets.asset_registry.register_task_media_best_effort',
                    lambda *a,**kw:[{'asset':{'id':identities[0]['id']}}]),
            ]:
                stack.enter_context(patch(target,value))
            try:
                await asyncio.to_thread(barrier.wait, 15)
                if role=='scan':
                    async with asyncio.timeout(20 if order=='capacity' else 10):
                        while True:
                            await worker.scan()
                            await asyncio.gather(*list(worker._work.jobs.values()))
                            rows = [await worker.refresh(identity) for identity in identities]
                            if all(row['request_params']['_media_lifecycle_v1']['phase']=='accepted' for row in rows): break
                            await asyncio.sleep(.005)
                elif role=='callback': await callback()
                else: await recover()
                reports.put({'pid':os.getpid(),'role':role,'ok':True})
            finally:
                await worker.close()
                await redis.aclose()
                raw.pool.close()
    try:
        asyncio.run(run())
    except BaseException as error:
        reports.put({'pid':os.getpid(),'role':role,'ok':False,'error_type':type(error).__name__,'error':str(error)})
        raise
