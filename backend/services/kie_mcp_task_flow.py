"""Personal MCP orchestration: callbacks wake reconciliation; provider reads prove results."""
import asyncio
import json
from uuid import NAMESPACE_URL, uuid5

from loguru import logger
from services.kie_mcp_task_store import TaskStore

store = TaskStore()


def task_view(row):
    urls = row['archived_urls'] or row['source_urls']
    result = {'local_task_id': str(row['id']), 'task_id': row['provider_task_id'] or str(row['id']),
              'state': row['state'], 'video_urls': urls, 'failed': row['state'] == 'fail',
              'credits_consumed': float(row['credits_consumed']) if row.get('credits_consumed') is not None else None,
              'storage_state': row['storage_state'], 'model': row['payload']['model'],
              'created_at': row['created_at'].isoformat(), 'request_key': row['request_key'],
              'prompt_preview': row['payload']['input'].get('prompt', '')[:120], 'error_code': row.get('error_code'),
              'continue_waiting': row['state'] not in ('success','fail')}
    if row['state'] == 'success' and row['storage_state'] != 'stored':
        result['warning'] = 'Video generated; permanent storage pending. Current URLs may be temporary; storage retries do not regenerate video.'
    if row['state'] == 'submission_unknown':
        result['warning'] = 'Submission outcome unknown; keep waiting by local_task_id. Never submit again with a new request_key.'
    return result


def verified_unknown(row, record):
    """Only bind a response-lost create when provider's saved callback and inputs match."""
    try:
        params = record.get('param')
        params = json.loads(params) if isinstance(params, str) else params
        inputs = params.get('input')
        inputs = json.loads(inputs) if isinstance(inputs, str) else inputs
        return (record.get('model') == row['payload']['model']
                and params.get('callBackUrl') == row['payload'].get('callBackUrl')
                and bool(params.get('callBackUrl'))
                and isinstance(inputs, dict)
                and all(inputs.get(k) == v for k, v in row['payload']['input'].items()))
    except (ValueError, AttributeError, TypeError):
        return False


async def archive(row, urls):
    from services.oss_service import get_oss_service
    from services.handlers.native_video_storage import persist
    oss = get_oss_service()
    owner = str(uuid5(NAMESPACE_URL, 'everydayai:personal-kie-mcp'))
    outputs = []
    for index, url in enumerate(urls):
        task = {'id': str(uuid5(row['id'], str(index))), 'user_id': owner, 'org_id': None}
        saved = await persist(oss, task, url)
        outputs.append(saved['url'])
    return outputs


async def advance(row, request=None, archiver=None):
    if request is None:
        from services.kie_video_mcp import kie_request
        request = kie_request
    archiver = archiver or archive
    try:
        provider_id = row['provider_task_id'] or row['callback_candidate']
        if row['state'] == 'success':
            record = None
            urls = row['source_urls']
        else:
            record = await request('GET', 'recordInfo', params={'taskId': provider_id})
            if record.get('taskId') != provider_id:
                await store.finish(row, callback_candidate=None, error_code='PROVIDER_TASK_ID_MISMATCH')
                return
            if not row['provider_task_id']:
                if not verified_unknown(row, record):
                    await store.finish(row, callback_candidate=None, error_code='CALLBACK_ASSOCIATION_UNVERIFIED')
                    return
            state = record.get('state')
            if state == 'fail':
                await store.finish(row, provider_task_id=provider_id, state='fail', error_code='PROVIDER_GENERATION_FAILED',
                                   credits_consumed=record.get('creditsConsumed'))
                return
            if state != 'success':
                await store.finish(row, provider_task_id=provider_id, state='running' if state=='generating' else 'waiting', error_code=None)
                return
            raw = record.get('resultJson') or '{}'
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            urls = parsed.get('resultUrls') if isinstance(parsed, dict) else None
            if not isinstance(urls, list) or not urls or not all(isinstance(u,str) and u.startswith('https://') for u in urls):
                await store.finish(row, provider_task_id=provider_id, error_code='PROVIDER_RESULT_UNAVAILABLE')
                return
        changes = {'state': 'success', 'provider_task_id': provider_id, 'source_urls': urls}
        if record:
            changes['credits_consumed'] = record.get('creditsConsumed')
        try:
            # Leave time for lease release and DB writes before another worker may claim it.
            async with asyncio.timeout(100):
                changes['archived_urls'] = await archiver(row, urls)
            changes.update(storage_state='stored', error_code=None)
        except Exception as error:
            logger.warning('kie_mcp_archive_retry | task={} error_type={}', str(row['id']), type(error).__name__)
            changes.update(storage_state='retry', error_code='VIDEO_ARCHIVE_PENDING')
        await store.finish(row, **changes)
    except Exception as error:
        logger.warning('kie_mcp_poll_retry | task={} error_type={}', str(row['id']), type(error).__name__)
        await store.finish(row, error_code='PROVIDER_QUERY_RETRY')


async def worker_loop():
    async def worker():
        while True:
            try:
                row = await store.claim()
                if row:
                    async with asyncio.timeout(150):
                        await advance(row)
                else:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning('kie_mcp_worker_retry | error_type={}', type(error).__name__)
                await asyncio.sleep(10)
    jobs = [asyncio.create_task(worker()) for _ in range(2)]
    try:
        await asyncio.gather(*jobs)
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
