"""Durable manual Kuaimai synchronization consumer for the Sync service."""

from __future__ import annotations

import asyncio
from datetime import date

from loguru import logger

from core.database import get_async_db
from services.configuration.sync_resolver import SyncConfigurationResolver
from services.kuaimai_external import thinktank_sync, viperp_sync


LEASE_SECONDS = 300
RENEW_SECONDS = 60


async def external_manual_sync_loop() -> None:
    db = await get_async_db()
    logger.info("External manual sync consumer started")
    while True:
        try:
            response = await db.rpc(
                "sync_claim_external_sync",
                {"p_lease_seconds": LEASE_SECONDS},
            ).execute()
            request = response.data
            if not isinstance(request, dict):
                await asyncio.sleep(2)
                continue
            await _execute_claim(db, request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                f"External manual sync consumer failed | error={error}",
                exc_info=True,
            )
            await asyncio.sleep(2)


async def _execute_claim(db, request: dict) -> None:
    request_id = str(request["id"])
    execution_token = str(request["execution_token"])
    sync_task = asyncio.create_task(_run_sync(db, request))
    renew_task = asyncio.create_task(
        _renew_lease(db, request_id, execution_token)
    )
    success = False
    error_message = None
    try:
        done, _ = await asyncio.wait(
            {sync_task, renew_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
        await sync_task
        success = True
    except Exception as error:
        error_message = str(error)
        sync_task.cancel()
        await asyncio.gather(sync_task, return_exceptions=True)
    finally:
        renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)
    result = await db.rpc("sync_finish_external_sync", {
        "p_request_id": request_id,
        "p_execution_token": execution_token,
        "p_success": success,
        "p_error_message": error_message,
    }).execute()
    if result.data is not True:
        logger.warning(
            "External manual sync finish lost fencing token | "
            f"request_id={request_id}"
        )


async def _renew_lease(db, request_id: str, execution_token: str) -> None:
    while True:
        await asyncio.sleep(RENEW_SECONDS)
        response = await db.rpc("sync_renew_external_sync", {
            "p_request_id": request_id,
            "p_execution_token": execution_token,
            "p_lease_seconds": LEASE_SECONDS,
        }).execute()
        if response.data is not True:
            raise RuntimeError("EXTERNAL_SYNC_LEASE_LOST")


async def _run_sync(db, request: dict) -> None:
    org_id = str(request["org_id"])
    source = str(request["source"])
    credentials = await SyncConfigurationResolver(db).external_credentials(
        org_id,
        source,
    )
    common = {
        "org_id": org_id,
        "sync_type": str(request["sync_type"]),
        "start_date": _date(request.get("start_date")),
        "end_date": _date(request.get("end_date")),
        "credentials": credentials,
    }
    if source == "thinktank":
        result = await thinktank_sync.sync_thinktank(db, **common)
    else:
        result = await viperp_sync.sync_viperp(
            db,
            **common,
            dimension=str(request["dimension"]),
        )
    if not result.success:
        raise RuntimeError(result.error or "EXTERNAL_SYNC_FAILED")


def _date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
