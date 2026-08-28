"""Actorless, exact-organization configuration access for the Sync process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
)
from services.configuration.bundles import (
    AsyncSecretBundleResolver,
    ResolvedConfigurationBundle,
)
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from services.configuration.resolver import ConfigurationResolutionError


ExternalSource = Literal["thinktank", "viperp"]


@dataclass(frozen=True)
class SyncErpCredentials:
    org_id: str
    app_key: str
    app_secret: str
    access_token: str
    refresh_token: str
    warehouse_ids: tuple[str, ...]
    token_version: int


@dataclass(frozen=True)
class SyncExternalCredentials:
    org_id: str
    source: ExternalSource
    kuaimai_company_id: int
    censeid_cookie: str
    cookie_full: str


class SyncConfigurationResolver:
    """Resolve only fixed Sync bundles and rotate one exact ERP token pair."""

    def __init__(
        self,
        db: Any,
        material_service: SecretMaterialService | None = None,
    ) -> None:
        self._db = db
        self._material_service = material_service

    async def discover_erp_org_ids(self) -> list[str]:
        response = await self._db.rpc("sync_discover_erp_targets").execute()
        return [
            str(row["org_id"])
            for row in (response.data or [])
            if isinstance(row, dict) and row.get("org_id")
        ]

    async def discover_external_targets(
        self,
    ) -> list[tuple[str, ExternalSource]]:
        response = await self._db.rpc(
            "sync_discover_external_targets"
        ).execute()
        targets: list[tuple[str, ExternalSource]] = []
        for row in response.data or []:
            if not isinstance(row, dict):
                continue
            org_id = row.get("org_id")
            source = row.get("source")
            if org_id and source in ("thinktank", "viperp"):
                targets.append((str(org_id), source))
        return targets

    async def erp_credentials(self, org_id: str) -> SyncErpCredentials:
        bundle = await self._bundle(org_id).erp_runtime()
        app = self._mapping(bundle, "erp.app_credentials")
        token = self._mapping(bundle, "erp.token_pair")
        warehouses = bundle.values.get("erp.warehouse_ids")
        if warehouses is None:
            warehouse_ids: tuple[str, ...] = ()
        elif isinstance(warehouses, list) and all(
            isinstance(item, str) for item in warehouses
        ):
            warehouse_ids = tuple(warehouses)
        else:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            )
        return SyncErpCredentials(
            org_id=org_id,
            app_key=self._text(app, "app_key"),
            app_secret=self._text(app, "app_secret"),
            access_token=self._text(token, "access_token"),
            refresh_token=self._text(token, "refresh_token"),
            warehouse_ids=warehouse_ids,
            token_version=bundle.versions["erp.token_pair"],
        )

    async def external_credentials(
        self,
        org_id: str,
        source: ExternalSource,
    ) -> SyncExternalCredentials:
        resolver = self._bundle(org_id)
        bundle = (
            await resolver.kuaimai_thinktank()
            if source == "thinktank"
            else await resolver.kuaimai_viperp()
        )
        prefix = f"kuaimai_external.{source}"
        cookie = self._mapping(bundle, f"{prefix}.cookie")
        company_id = bundle.values.get(f"{prefix}.company_id")
        try:
            normalized_company_id = int(company_id)
        except (TypeError, ValueError) as error:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            ) from error
        return SyncExternalCredentials(
            org_id=org_id,
            source=source,
            kuaimai_company_id=normalized_company_id,
            censeid_cookie=self._text(cookie, "censeid_cookie"),
            cookie_full=self._text(cookie, "cookie_full"),
        )

    async def commit_erp_token_pair(
        self,
        credentials: SyncErpCredentials,
        access_token: str,
        refresh_token: str,
    ) -> int:
        if not access_token or not refresh_token:
            raise ValueError("ERP Token 不能为空")
        next_version = credentials.token_version + 1
        envelope = self._secrets.encrypt_payload(
            scope_kind="organization",
            scope_id=credentials.org_id,
            secret_name="erp.token_pair",
            payload_version=next_version,
            payload={
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )
        response = await self._scoped(credentials.org_id).rpc(
            "sync_commit_erp_token_pair",
            {
                "p_org_id": credentials.org_id,
                "p_secret_envelope": {
                    "payload_ciphertext": envelope.payload_ciphertext,
                    "wrapped_dek": envelope.wrapped_dek,
                    "kek_version": envelope.kek_version,
                },
                "p_expected_version": credentials.token_version,
            },
        ).execute()
        payload = response.data
        if not isinstance(payload, dict) or payload.get("version") != next_version:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            )
        return next_version

    def _bundle(self, org_id: str) -> AsyncSecretBundleResolver:
        return AsyncSecretBundleResolver(
            self._scoped(org_id),
            self._secrets,
        )

    @property
    def _secrets(self) -> SecretMaterialService:
        if self._material_service is None:
            self._material_service = SecretMaterialService(
                LocalKEKProvider.from_environment()
            )
        return self._material_service

    def _scoped(self, org_id: str) -> AsyncScopedDatabaseClient:
        return AsyncScopedDatabaseClient(
            self._db,
            DatabaseScope(
                actor_user_id=None,
                org_id=org_id,
                access_kind=DatabaseAccessKind.SYNC,
                request_id=f"sync-config:{org_id}",
            ),
        )

    @staticmethod
    def _mapping(
        bundle: ResolvedConfigurationBundle,
        key: str,
    ) -> Mapping[str, object]:
        value = bundle.values.get(key)
        if not isinstance(value, Mapping):
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            )
        return value

    @staticmethod
    def _text(value: Mapping[str, object], key: str) -> str:
        item = value.get(key)
        if not isinstance(item, str) or not item:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            )
        return item
