"""BackgroundTaskWorker 的跨进程周期任务。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from services.periodic_job_gate import (
    PeriodicJobClaim,
    claim_periodic_job,
    finish_periodic_job,
    renew_periodic_job,
)


class BackgroundPeriodicTasksMixin:
    """模型评分与企微巡检的周期租约执行逻辑。"""

    db: Any
    _last_wecom_dup_check: datetime | None
    _last_scoring_aggregation: datetime | None

    async def _get_active_org_ids(self) -> list[str]:
        raise NotImplementedError

    async def _finish_failed_periodic_claim(
        self,
        job_name: str,
        claim: PeriodicJobClaim | None,
    ) -> None:
        if claim is None or claim.lease_token is None:
            return
        try:
            await finish_periodic_job(
                self.db,
                job_name,
                claim.lease_token,
                succeeded=False,
            )
        except Exception as exc:
            logger.error(
                f"{job_name} lease finish failed | error={exc}"
            )

    async def _renew_periodic_lease(
        self,
        job_name: str,
        lease_token: str,
    ) -> None:
        while True:
            await asyncio.sleep(60)
            await renew_periodic_job(self.db, job_name, lease_token)

    async def _stop_periodic_heartbeat(
        self,
        heartbeat: asyncio.Task[None],
    ) -> None:
        if heartbeat.done():
            heartbeat.result()
            return
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat

    async def _cancel_periodic_heartbeat(
        self,
        heartbeat: asyncio.Task[None] | None,
    ) -> None:
        if heartbeat is None:
            return
        if not heartbeat.done():
            heartbeat.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await heartbeat

    async def check_wecom_duplicates(self) -> None:
        """每天检查企微孤儿用户和重复身份。"""
        now = datetime.now(timezone.utc)
        if (
            self._last_wecom_dup_check is not None
            and (now - self._last_wecom_dup_check).total_seconds() < 86400
        ):
            return

        claim = None
        heartbeat = None
        try:
            claim = await claim_periodic_job(self.db, "wecom_dup_monitor")
            if claim.outcome == "completed":
                self._last_wecom_dup_check = now
                return
            if claim.outcome == "busy":
                return
            if claim.lease_token is None:
                raise RuntimeError("WECOM_DUP_MONITOR_LEASE_TOKEN_MISSING")
            heartbeat = asyncio.create_task(
                self._renew_periodic_lease(
                    "wecom_dup_monitor",
                    claim.lease_token,
                )
            )
            from services.wecom_dup_monitor import WecomDuplicateMonitor

            await WecomDuplicateMonitor(self.db).check_and_alert()
            await self._stop_periodic_heartbeat(heartbeat)
            await finish_periodic_job(
                self.db,
                "wecom_dup_monitor",
                claim.lease_token,
                succeeded=True,
            )
            self._last_wecom_dup_check = now
        except Exception as exc:
            await self._cancel_periodic_heartbeat(heartbeat)
            await self._finish_failed_periodic_claim(
                "wecom_dup_monitor",
                claim,
            )
            logger.error(
                f"Wecom dup check failed | error={exc}",
                exc_info=True,
            )

    async def _run_model_scoring(self) -> None:
        """每小时执行一次全局模型评分聚合。"""
        now = datetime.now(timezone.utc)
        if (
            self._last_scoring_aggregation is not None
            and (now - self._last_scoring_aggregation).total_seconds() < 3600
        ):
            return

        claim = None
        heartbeat = None
        try:
            claim = await claim_periodic_job(self.db, "model_scoring")
            if claim.outcome == "completed":
                self._last_scoring_aggregation = now
                return
            if claim.outcome == "busy":
                return
            if claim.lease_token is None:
                raise RuntimeError("MODEL_SCORING_LEASE_TOKEN_MISSING")
            heartbeat = asyncio.create_task(
                self._renew_periodic_lease(
                    "model_scoring",
                    claim.lease_token,
                )
            )
            from services.model_scorer import aggregate_model_scores

            succeeded = True
            for org_id in await self._get_active_org_ids():
                succeeded = (
                    await aggregate_model_scores(
                        org_id=org_id,
                        db_source=self.db,
                    )
                    and succeeded
                )
            succeeded = (
                await aggregate_model_scores(
                    org_id=None,
                    db_source=self.db,
                )
                and succeeded
            )
            if not succeeded:
                raise RuntimeError("MODEL_SCORING_PARTIAL_FAILURE")
            await self._stop_periodic_heartbeat(heartbeat)
            await finish_periodic_job(
                self.db,
                "model_scoring",
                claim.lease_token,
                succeeded=True,
            )
            self._last_scoring_aggregation = now
        except Exception as exc:
            await self._cancel_periodic_heartbeat(heartbeat)
            await self._finish_failed_periodic_claim(
                "model_scoring",
                claim,
            )
            logger.error(
                f"Model scoring aggregation failed | error={exc}"
            )
