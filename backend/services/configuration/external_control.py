"""Runtime-admin control plane for Kuaimai external credentials."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from services.configuration.bundles import AsyncSecretBundleResolver
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService


ExternalSource = Literal["thinktank", "viperp"]


@dataclass(frozen=True)
class ExternalCredential:
    id: str
    org_id: str
    source: ExternalSource
    kuaimai_company_id: int
    censeid_cookie: str
    cookie_full: str
    status: str
    last_health_check_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    created_at: datetime
    updated_at: datetime


class ExternalConfigurationControl:
    """Manage one two-entry credential bundle through atomic RPCs."""

    def __init__(
        self,
        db: Any,
        material_service: SecretMaterialService | None = None,
    ) -> None:
        self._db = db
        self._secrets = material_service or SecretMaterialService(
            LocalKEKProvider.from_environment()
        )

    async def list(self, org_id: str) -> list[ExternalCredential]:
        statuses = await self._statuses(org_id)
        credentials = []
        for source in ("thinktank", "viperp"):
            if self._configured(statuses, source):
                credentials.append(
                    await self._resolve(org_id, source, statuses)
                )
        return credentials

    async def get(
        self,
        org_id: str,
        source: ExternalSource,
    ) -> ExternalCredential | None:
        statuses = await self._statuses(org_id)
        if not self._configured(statuses, source):
            return None
        return await self._resolve(org_id, source, statuses)

    async def set(
        self,
        *,
        org_id: str,
        source: ExternalSource,
        company_id: int,
        censeid_cookie: str,
        cookie_full: str,
    ) -> ExternalCredential:
        statuses = await self._statuses(org_id)
        cookie_key, company_key = self._keys(source)
        cookie_version = self._version(statuses, cookie_key)
        company_version = self._version(statuses, company_key)
        envelope = self._secrets.encrypt_payload(
            scope_kind="organization",
            scope_id=org_id,
            secret_name=f"kuaimai_external.{source}_cookie",
            payload_version=cookie_version + 1,
            payload={
                "censeid_cookie": censeid_cookie,
                "cookie_full": cookie_full,
            },
        )
        response = await self._db.rpc(
            "runtime_set_external_configuration",
            {
                "p_org_id": org_id,
                "p_source": source,
                "p_cookie_envelope": {
                    "payload_ciphertext": envelope.payload_ciphertext,
                    "wrapped_dek": envelope.wrapped_dek,
                    "kek_version": envelope.kek_version,
                },
                "p_company_id": str(company_id),
                "p_expected_cookie_version": cookie_version,
                "p_expected_company_version": company_version,
            },
        ).execute()
        if not isinstance(response.data, dict):
            raise RuntimeError("CONFIG_RESPONSE_INVALID")
        refreshed = await self.get(org_id, source)
        if refreshed is None:
            raise RuntimeError("CONFIG_RESPONSE_INVALID")
        return refreshed

    async def delete(
        self,
        *,
        org_id: str,
        source: ExternalSource,
    ) -> bool:
        statuses = await self._statuses(org_id)
        if not self._configured(statuses, source):
            return False
        cookie_key, company_key = self._keys(source)
        response = await self._db.rpc(
            "runtime_delete_external_configuration",
            {
                "p_org_id": org_id,
                "p_source": source,
                "p_expected_cookie_version": self._version(
                    statuses, cookie_key
                ),
                "p_expected_company_version": self._version(
                    statuses, company_key
                ),
            },
        ).execute()
        return isinstance(response.data, dict)

    async def _resolve(
        self,
        org_id: str,
        source: ExternalSource,
        statuses: dict[str, dict[str, object]],
    ) -> ExternalCredential:
        resolver = AsyncSecretBundleResolver(self._db, self._secrets)
        bundle = (
            await resolver.kuaimai_thinktank()
            if source == "thinktank"
            else await resolver.kuaimai_viperp()
        )
        prefix = f"kuaimai_external.{source}"
        cookie = bundle.values[f"{prefix}.cookie"]
        company_id = bundle.values[f"{prefix}.company_id"]
        if not isinstance(cookie, dict):
            raise RuntimeError("CONFIG_BUNDLE_RESPONSE_INVALID")
        updated = self._updated_at(statuses, source)
        return ExternalCredential(
            id=source,
            org_id=org_id,
            source=source,
            kuaimai_company_id=int(company_id),
            censeid_cookie=str(cookie["censeid_cookie"]),
            cookie_full=str(cookie["cookie_full"]),
            status="active",
            last_health_check_at=None,
            last_sync_at=None,
            last_sync_status=None,
            last_sync_error=None,
            created_at=updated,
            updated_at=updated,
        )

    async def _statuses(
        self,
        org_id: str,
    ) -> dict[str, dict[str, object]]:
        response = await self._db.rpc(
            "list_org_configuration_status",
            {"p_org_id": org_id},
        ).execute()
        if not isinstance(response.data, list):
            raise RuntimeError("CONFIG_RESPONSE_INVALID")
        return {
            str(row["key"]): row
            for row in response.data
            if isinstance(row, dict) and row.get("key")
        }

    @staticmethod
    def _keys(source: ExternalSource) -> tuple[str, str]:
        prefix = f"kuaimai_external.{source}"
        return f"{prefix}.cookie", f"{prefix}.company_id"

    def _configured(
        self,
        statuses: dict[str, dict[str, object]],
        source: ExternalSource,
    ) -> bool:
        return all(
            bool(statuses.get(key, {}).get("configured"))
            for key in self._keys(source)
        )

    @staticmethod
    def _version(
        statuses: dict[str, dict[str, object]],
        key: str,
    ) -> int:
        return int(statuses.get(key, {}).get("version") or 0)

    def _updated_at(
        self,
        statuses: dict[str, dict[str, object]],
        source: ExternalSource,
    ) -> datetime:
        values = [
            statuses.get(key, {}).get("updated_at")
            for key in self._keys(source)
        ]
        parsed = [
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            for value in values
            if value
        ]
        return max(parsed) if parsed else datetime.now(timezone.utc)
