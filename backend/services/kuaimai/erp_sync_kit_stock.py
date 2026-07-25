"""Sync-role capability for refreshing the kit-stock materialized view."""

from loguru import logger


async def refresh_kit_stock(db) -> bool:
    """Refresh kit stock through the database-owned Sync capability."""
    try:
        result = await db.rpc("sync_refresh_kit_stock").execute()
        refreshed = bool(result.data)
        if refreshed:
            logger.debug("Kit stock materialized view refreshed")
        return refreshed
    except Exception as error:
        logger.warning(f"Kit stock refresh failed | error={error}")
        return False
