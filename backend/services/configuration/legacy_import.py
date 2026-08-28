"""Pure transformation of decrypted legacy values into import RPC items."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping
from uuid import UUID

from services.configuration.definitions import (
    CONFIG_REGISTRY,
)
from services.configuration.legacy_migration import LegacyPreflightReport
from services.configuration.material_service import SecretMaterialService


ExternalSource = Literal["thinktank", "viperp"]


class LegacyImportPlanError(RuntimeError):
    """Stable failure raised before any database import is attempted."""


@dataclass(frozen=True)
class LegacyExternalValue:
    """One decrypted external credential held only during planning."""

    source: ExternalSource
    status: Literal["active", "expired", "invalid"]
    company_id: str
    censeid_cookie: str = field(repr=False)
    cookie_full: str = field(repr=False)


@dataclass(frozen=True)
class LegacyOrganizationValues:
    """Decrypted legacy values for one organization."""

    org_id: str
    organization_corp_id: str | None
    config_values: Mapping[str, str] = field(repr=False)
    external_credentials: tuple[LegacyExternalValue, ...] = field(
        repr=False,
        default=(),
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "config_values",
            MappingProxyType(dict(self.config_values)),
        )


@dataclass(frozen=True)
class LegacyImportItem:
    """One encrypted or non-secret item accepted by migration 161."""

    org_id: str
    definition_version: str
    config_key: str
    value_json: object | None = field(repr=False)
    secret_envelope: Mapping[str, object] | None = field(repr=False)

    def database_value(self) -> dict[str, object]:
        """Return the exact five-field JSON object required by the RPC."""
        return {
            "org_id": self.org_id,
            "definition_version": self.definition_version,
            "config_key": self.config_key,
            "value_json": self.value_json,
            "secret_envelope": (
                dict(self.secret_envelope)
                if self.secret_envelope is not None
                else None
            ),
        }


@dataclass(frozen=True)
class LegacyImportPlan:
    """Secret-free plan metadata plus encrypted RPC items."""

    import_id: str
    items: tuple[LegacyImportItem, ...] = field(repr=False)

    @property
    def org_count(self) -> int:
        return len({item.org_id for item in self.items})

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def config_keys(self) -> tuple[str, ...]:
        return tuple(sorted({item.config_key for item in self.items}))


_SIMPLE_SECRET_TARGETS = MappingProxyType({
    "ai.google.api_key": ("ai_google_api_key", "api_key"),
    "ai.kie.api_key": ("ai_kie_api_key", "api_key"),
    "ai.openrouter.api_key": ("ai_openrouter_api_key", "api_key"),
    "wecom.oauth_agent_secret": (
        "wecom_agent_secret",
        "agent_secret",
    ),
})
_ATOMIC_SECRET_TARGETS = MappingProxyType({
    "erp.app_credentials": (
        ("kuaimai_app_key", "app_key"),
        ("kuaimai_app_secret", "app_secret"),
    ),
    "erp.token_pair": (
        ("kuaimai_access_token", "access_token"),
        ("kuaimai_refresh_token", "refresh_token"),
    ),
    "wecom.bot_credentials": (
        ("wecom_bot_id", "bot_id"),
        ("wecom_bot_secret", "bot_secret"),
    ),
})


class LegacyImportPlanner:
    """Validate preflight parity and build version-one import items."""

    def __init__(self, material_service: SecretMaterialService) -> None:
        self._material_service = material_service

    def build(
        self,
        *,
        import_id: str,
        organizations: tuple[LegacyOrganizationValues, ...],
        preflight_reports: Mapping[str, LegacyPreflightReport],
    ) -> LegacyImportPlan:
        """Build an all-organizations plan without performing database writes."""
        self._validate_import_id(import_id)
        source_ids = [source.org_id for source in organizations]
        if (
            len(source_ids) != len(set(source_ids))
            or set(source_ids) != set(preflight_reports)
        ):
            raise LegacyImportPlanError("LEGACY_IMPORT_ORGANIZATION_MISMATCH")
        blocked = sorted(
            org_id
            for org_id, report in preflight_reports.items()
            if not report.can_migrate
        )
        if blocked:
            raise LegacyImportPlanError("LEGACY_IMPORT_PREFLIGHT_BLOCKED")

        items = tuple(
            item
            for source in sorted(organizations, key=lambda item: item.org_id)
            for item in self._organization_items(
                source,
                preflight_reports[source.org_id],
            )
        )
        if not items:
            raise LegacyImportPlanError("LEGACY_IMPORT_EMPTY")
        return LegacyImportPlan(import_id=import_id, items=items)

    def _organization_items(
        self,
        source: LegacyOrganizationValues,
        report: LegacyPreflightReport,
    ) -> tuple[LegacyImportItem, ...]:
        ready = {
            item.target_key
            for item in report.items
            if item.status == "ready" and item.target_enabled is not False
        }
        values = source.config_values
        items: list[LegacyImportItem] = []
        for target, (legacy_key, payload_key) in _SIMPLE_SECRET_TARGETS.items():
            if target in ready:
                items.append(self._secret_item(
                    source.org_id,
                    target,
                    {payload_key: self._required(values, legacy_key)},
                ))
        for target, fields in _ATOMIC_SECRET_TARGETS.items():
            if target in ready:
                items.append(self._secret_item(
                    source.org_id,
                    target,
                    {
                        payload_key: self._required(values, legacy_key)
                        for legacy_key, payload_key in fields
                    },
                ))
        if "erp.warehouse_ids" in ready:
            items.append(self._plain_item(
                source.org_id,
                "erp.warehouse_ids",
                self._warehouse_ids(self._required(
                    values,
                    "erp_warehouse_ids",
                )),
            ))
        if "wecom.corp_id" in ready:
            corp_id = (
                source.organization_corp_id
                or self._required(values, "wecom_corp_id")
            )
            items.append(self._plain_item(
                source.org_id,
                "wecom.corp_id",
                self._nonempty(corp_id),
            ))
        if "wecom.oauth_agent_id" in ready:
            items.append(self._plain_item(
                source.org_id,
                "wecom.oauth_agent_id",
                self._required(values, "wecom_agent_id"),
            ))
        items.extend(self._external_items(source, ready))
        return tuple(sorted(items, key=lambda item: item.config_key))

    def _external_items(
        self,
        source: LegacyOrganizationValues,
        ready: set[str],
    ) -> tuple[LegacyImportItem, ...]:
        items: list[LegacyImportItem] = []
        seen: set[str] = set()
        for credential in source.external_credentials:
            if credential.source in seen:
                raise LegacyImportPlanError(
                    "LEGACY_IMPORT_EXTERNAL_DUPLICATE"
                )
            seen.add(credential.source)
            prefix = f"kuaimai_external.{credential.source}"
            if credential.status != "active":
                continue
            if {f"{prefix}.cookie", f"{prefix}.company_id"} - ready:
                raise LegacyImportPlanError(
                    "LEGACY_IMPORT_PREFLIGHT_MISMATCH"
                )
            items.extend((
                self._secret_item(
                    source.org_id,
                    f"{prefix}.cookie",
                    {
                        "censeid_cookie": self._nonempty(
                            credential.censeid_cookie
                        ),
                        "cookie_full": self._nonempty(
                            credential.cookie_full
                        ),
                    },
                ),
                self._plain_item(
                    source.org_id,
                    f"{prefix}.company_id",
                    self._nonempty(credential.company_id),
                ),
            ))
        return tuple(items)

    def _secret_item(
        self,
        org_id: str,
        key: str,
        payload: Mapping[str, object],
    ) -> LegacyImportItem:
        definition = CONFIG_REGISTRY.get(key)
        envelope = self._material_service.encrypt_payload(
            scope_kind="organization",
            scope_id=org_id,
            secret_name=definition.secret_name or "",
            payload_version=1,
            payload=payload,
        )
        return LegacyImportItem(
            org_id=org_id,
            definition_version=CONFIG_REGISTRY.version,
            config_key=key,
            value_json=None,
            secret_envelope={
                "payload_ciphertext": envelope.payload_ciphertext,
                "wrapped_dek": envelope.wrapped_dek,
                "kek_version": envelope.kek_version,
            },
        )

    @staticmethod
    def _plain_item(
        org_id: str,
        key: str,
        value: object,
    ) -> LegacyImportItem:
        return LegacyImportItem(
            org_id=org_id,
            definition_version=CONFIG_REGISTRY.version,
            config_key=key,
            value_json=value,
            secret_envelope=None,
        )

    @classmethod
    def _required(cls, values: Mapping[str, str], key: str) -> str:
        try:
            return cls._nonempty(values[key])
        except KeyError as error:
            raise LegacyImportPlanError(
                "LEGACY_IMPORT_SOURCE_MISSING"
            ) from error

    @staticmethod
    def _nonempty(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise LegacyImportPlanError("LEGACY_IMPORT_VALUE_INVALID")
        return value

    @classmethod
    def _warehouse_ids(cls, value: str) -> list[str]:
        items = [item.strip() for item in value.split(",") if item.strip()]
        unique = list(dict.fromkeys(items))
        if not unique:
            raise LegacyImportPlanError("LEGACY_IMPORT_VALUE_INVALID")
        return unique

    @staticmethod
    def _validate_import_id(import_id: str) -> None:
        try:
            normalized = str(UUID(import_id))
        except (TypeError, ValueError) as error:
            raise LegacyImportPlanError(
                "LEGACY_IMPORT_ID_INVALID"
            ) from error
        if normalized != import_id:
            raise LegacyImportPlanError("LEGACY_IMPORT_ID_INVALID")
