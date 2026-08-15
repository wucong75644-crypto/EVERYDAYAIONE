"""Runtime-scoped construction of the existing ERP read dispatcher."""

from __future__ import annotations

from services.agent.runtime.domain import RuntimeScope
from services.agent.runtime.executors.provider_adapters import (
    ErpDispatcherFactoryPort, ErpDispatcherPort,
)


class OrgScopedErpDispatcherFactory:
    """Resolve one enterprise ERP credential bundle for each Runtime scope."""

    def __init__(self, database) -> None:
        self._database = database

    async def create(self, scope: RuntimeScope) -> ErpDispatcherPort:
        org_id = scope.org_id
        if not org_id:
            raise ValueError("ERP_ORG_SCOPE_REQUIRED")

        from services.kuaimai.client import KuaiMaiClient
        from services.kuaimai.dispatcher import ErpDispatcher
        from services.org.config_resolver import AsyncOrgConfigResolver

        resolver = AsyncOrgConfigResolver(self._database)
        credentials = await resolver.get_erp_credentials(org_id)

        async def persist_token(
            persisted_org_id: str, access_token: str, refresh_token: str,
        ) -> None:
            if persisted_org_id != org_id:
                raise ValueError("ERP_ORG_SCOPE_MISMATCH")
            await resolver.update_erp_token(
                org_id, access_token, refresh_token,
            )

        client = KuaiMaiClient(
            app_key=credentials["kuaimai_app_key"],
            app_secret=credentials["kuaimai_app_secret"],
            access_token=credentials["kuaimai_access_token"],
            refresh_token=credentials["kuaimai_refresh_token"],
            org_id=org_id,
            token_persister=persist_token,
        )
        try:
            await client.load_cached_token()
            return ErpDispatcher(client, db_source=self._database)
        except Exception:
            await client.close()
            raise


__all__ = ["ErpDispatcherFactoryPort", "OrgScopedErpDispatcherFactory"]
