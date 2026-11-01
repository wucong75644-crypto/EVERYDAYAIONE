"""Route durable child completions into the existing Actor queue.

The original worker DB authority discovers minimal native task hints. Each
logical-task read/write is then scoped to that DB task's real actor/org. Redis
and an in-memory cursor are only accelerators; events/queue dedupe are in SQL.
"""
from types import SimpleNamespace

from loguru import logger
from core.db_scope import AsyncScopedDatabaseClient, DatabaseScope, DatabaseAccessKind, database_scope_from_client
from core.local_db import AsyncLocalDBClient

from .database_scope import task_database


def actor_database(db,row):
    scope = database_scope_from_client(db)
    if isinstance(db, AsyncScopedDatabaseClient) and scope.access_kind == DatabaseAccessKind.WORKER:
        # The worker discovered this native task under its existing
        # authority; each logical write still requires its actual
        # actor/org. Never rebind a user/runtime scoped client.
        return AsyncScopedDatabaseClient(db, DatabaseScope(
            str(row['user_id']), str(row['org_id']) if row['org_id'] else None,
            DatabaseAccessKind.RUNTIME))
    else:
        return task_database(db,SimpleNamespace(actor_user_id=row['user_id'],org_id=row['org_id']))

class ImageContinuationConsumer:
    def __init__(self, db, *, batch_size=100):
        self.db, self.batch_size, self.cursor = db, min(max(batch_size,1),100), None
        self.routing_db = (AsyncScopedDatabaseClient(db, DatabaseScope(None,None,DatabaseAccessKind.WORKER))
            if isinstance(db, AsyncLocalDBClient) else db)

    async def scan_once(self):
        result = await self.routing_db.rpc('list_agent_task_child_hints',{'p_after':self.cursor,'p_limit':self.batch_size}).execute()
        rows = result.data if result else None
        if not isinstance(rows,list):
            raise RuntimeError('TASK_CONTINUATION_HINTS_INVALID')
        self.cursor = rows[-1]['id'] if rows else None
        queued = 0
        for row in rows:
            try:
                scoped = actor_database(self.db,row)
                found = await (scoped.table('agent_task_events').select('id,root_task_id,status')
                    .eq('child_task_id',row['id']).maybe_single().execute())
                event = found.data if found else None
                if not event:
                    continue
                if event['status']=='pending':
                    found = await (scoped.table('agent_task_states').select('revision')
                        .eq('root_task_id',event['root_task_id']).maybe_single().execute())
                    state = found.data if found else None
                    if not state:
                        continue
                    result = await scoped.rpc('enqueue_agent_image_continuation',{
                        'p_root_task_id':event['root_task_id'],'p_expected_revision':state['revision'],
                        'p_event_id':event['id']}).execute()
                    outcome = result.data if result else None
                    if not isinstance(outcome,dict):
                        raise RuntimeError('TASK_CONTINUATION_ENQUEUE_INVALID')
                    if outcome.get('outcome') not in {'queued','already_queued','obsolete'}:
                        continue
                    queued += outcome['outcome']=='queued'
                # Separate transaction: losing this cache update never loses
                # the event or causes another task, and avoids child/conv locks.
                await scoped.rpc('mark_agent_task_child_hint',{'p_child_task_id':row['id']}).execute()
            except Exception as error:
                logger.warning('task_continuation_deferred | error={}',type(error).__name__)
        return queued


class ReceiptContinuationConsumer(ImageContinuationConsumer):
    hint_rpc='list_agent_task_receipt_hints'
    refresh_rpc='refresh_agent_task_receipt_events'
    async def scan_once(self):
        result=await self.routing_db.rpc(self.hint_rpc,{'p_after':self.cursor,'p_limit':self.batch_size}).execute()
        rows=result.data if result else None
        if not isinstance(rows,list):raise RuntimeError('TASK_RECEIPT_HINTS_INVALID')
        self.cursor=rows[-1]['id'] if rows else None
        queued=0
        for row in rows:
            try:
                scoped=actor_database(self.db,row)
                result=await scoped.rpc(self.refresh_rpc,{'p_source_task_id':row['id']}).execute()
                outcome=result.data if result else None
                if not isinstance(outcome,dict):raise RuntimeError('TASK_RECEIPT_REFRESH_INVALID')
                queued+=outcome.get('outcome')=='queued'
            except Exception as error:
                logger.warning('task_receipt_continuation_deferred | error={}',type(error).__name__)
        return queued


class VideoContinuationConsumer(ReceiptContinuationConsumer):
    hint_rpc='list_agent_task_video_hints'
    refresh_rpc='refresh_agent_task_video_events'


class TaskContinuationConsumer:
    def __init__(self,db,*,batch_size=100):
        self.sources=(ImageContinuationConsumer(db,batch_size=batch_size),ReceiptContinuationConsumer(db,batch_size=batch_size),VideoContinuationConsumer(db,batch_size=batch_size))

    async def scan_once(self):
        queued=0
        for source in self.sources:
            try:queued+=await source.scan_once()
            except Exception as error:
                logger.warning('task_continuation_source_deferred | source={} | error={}',type(source).__name__,type(error).__name__)
        return queued
