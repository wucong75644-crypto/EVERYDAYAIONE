"""Social ticks share the existing background host, with bounded concurrency."""

import asyncio
from loguru import logger
from psycopg.rows import dict_row
from core.db_scope import DatabaseScope, DatabaseAccessKind, SET_DATABASE_SCOPE_SQL
from core.config import get_settings
from .research import ResearchWorker
from .production import ProductionWorker


class SocialOpsWorker:
    def __init__(self, db):
        self.db = db
        self.pending = set()
        self.publish_pending = set()

    async def poll_publish(self):
        if not get_settings().social_ops_enabled:
            return
        await self.notify_publish()
        rows = await asyncio.to_thread(self.discover_publish)
        if (
            get_settings().social_ops_scheduled_publish_enabled
            and not self.publish_pending
            and rows
        ):
            row = rows[0]
            task = asyncio.create_task(self.tick_publish(row))
            self.publish_pending.add(task)
            task.add_done_callback(self.publish_pending.discard)

    async def poll(self):
        if not get_settings().social_ops_enabled:
            return
        if self.pending:
            return
        rows = await asyncio.to_thread(self.discover)
        research = [r for r in rows if not r["run_id"]]
        production = [r for r in rows if r["run_id"]]
        selected = (research[:2] + production[:1]) if production else research[:3]
        if not research:
            selected = production[:3]
        for row in selected:
            task = asyncio.create_task(self.tick(row))
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)

    async def tick_publish(self, row):
        from .publish_worker import PublishWorker

        scope = DatabaseScope(
            actor_user_id=str(row["owner_id"]),
            org_id=str(row["org_id"]),
            access_kind=DatabaseAccessKind.RUNTIME,
        )
        try:
            await PublishWorker(self.db, scope).tick(str(row["intent_id"]))
        except Exception as error:
            logger.warning(
                "Social publish tick stopped | kind={}", type(error).__name__
            )

    async def notify_publish(self):
        from services.scheduler.push_dispatcher import push_dispatcher

        def claim():
            with self.db.pool.connection() as conn, conn.transaction(), conn.cursor(
                row_factory=dict_row
            ) as c:
                scope = DatabaseScope(
                    actor_user_id=None,
                    org_id=None,
                    access_kind=DatabaseAccessKind.WORKER,
                )
                c.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
                c.execute("SELECT * FROM social_publish_notifications()")
                return c.fetchall()

        for row in await asyncio.to_thread(claim):
            await push_dispatcher.dispatch(
                str(row["org_id"]),
                {"type": "web", "user_id": str(row["owner_id"])},
                f"社媒发布排期 {row['intent_id']}：{row['state']} {row['reason']}。请在社媒运营查看详情。",
                [],
            )

    def discover_publish(self):
        with self.db.pool.connection() as conn, conn.transaction(), conn.cursor(
            row_factory=dict_row
        ) as c:
            scope = DatabaseScope(
                actor_user_id=None, org_id=None, access_kind=DatabaseAccessKind.WORKER
            )
            c.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
            c.execute("SELECT * FROM social_publish_due()")
            return c.fetchall()

    def discover(self):
        with self.db.pool.connection() as conn, conn.transaction(), conn.cursor(
            row_factory=dict_row
        ) as c:
            scope = DatabaseScope(
                actor_user_id=None, org_id=None, access_kind=DatabaseAccessKind.WORKER
            )
            c.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
            c.execute("SELECT * FROM social_ops_due()")
            return c.fetchall()

    async def tick(self, row):
        scope = DatabaseScope(
            actor_user_id=str(row["owner_id"]),
            org_id=str(row["org_id"]),
            access_kind=DatabaseAccessKind.RUNTIME,
        )
        try:
            if row["run_id"]:
                await ProductionWorker(self.db, scope).tick(row["run_id"])
            else:
                await ResearchWorker(self.db, scope).tick(row["job_id"])
        except Exception as error:
            logger.warning("SocialOps tick stopped | kind={}", type(error).__name__)

    async def stop(self):
        for task in list(self.pending | self.publish_pending):
            task.cancel()
        await asyncio.gather(
            *(self.pending | self.publish_pending), return_exceptions=True
        )
