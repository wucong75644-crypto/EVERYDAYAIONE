"""Read legacy configuration once and produce values plus matching preflight."""

from __future__ import annotations

import binascii
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from core.crypto import aes_decrypt
from services.configuration.legacy_import import (
    LegacyExternalValue,
    LegacyOrganizationValues,
)
from services.configuration.legacy_migration import (
    ExternalCredentialFact,
    LegacyPreflightReport,
    build_legacy_preflight,
)


class LegacyImportSourceError(RuntimeError):
    """Stable failure for malformed or inaccessible legacy source rows."""


@dataclass(frozen=True)
class LegacyImportSnapshot:
    """One-read snapshot; secret-bearing organizations are hidden from repr."""

    organizations: tuple[LegacyOrganizationValues, ...] = field(repr=False)
    preflight_reports: Mapping[str, LegacyPreflightReport]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "preflight_reports",
            MappingProxyType(dict(self.preflight_reports)),
        )


class LegacyImportSourceReader:
    """Parse fixed legacy rows and keep reports/value snapshots aligned."""

    def __init__(
        self,
        db: Any,
        *,
        global_encrypt_key: str | None,
    ) -> None:
        self._db = db
        self._global_encrypt_key = global_encrypt_key

    def read(self) -> LegacyImportSnapshot:
        """Compatibility path: perform three reads from the legacy DB facade."""
        organization_rows = self._read_rows(
            "organizations",
            "id,wecom_corp_id,encrypt_key",
        )
        config_rows = self._read_rows(
            "org_configs",
            "org_id,config_key,config_value_encrypted",
        )
        external_rows = self._read_rows(
            "kuaimai_external_credentials",
            (
                "org_id,source,status,kuaimai_company_id,"
                "censeid_cookie,cookie_full"
            ),
        )
        return self._build_snapshot(
            organization_rows,
            config_rows,
            external_rows,
        )

    def read_export(
        self,
        exported: Mapping[str, object],
    ) -> LegacyImportSnapshot:
        """Parse the exact three-array payload returned by the export RPC."""
        if set(exported) != {
            "organizations",
            "org_configs",
            "external_credentials",
        }:
            raise LegacyImportSourceError(
                "LEGACY_IMPORT_EXPORT_SHAPE_INVALID"
            )
        organization_rows = self._export_rows(
            exported["organizations"],
            "organizations",
        )
        config_rows = self._export_rows(
            exported["org_configs"],
            "org_configs",
        )
        external_rows = self._export_rows(
            exported["external_credentials"],
            "external_credentials",
        )
        return self._build_snapshot(
            organization_rows,
            config_rows,
            external_rows,
        )

    def _build_snapshot(
        self,
        organization_rows: tuple[dict[str, object], ...],
        config_rows: tuple[dict[str, object], ...],
        external_rows: tuple[dict[str, object], ...],
    ) -> LegacyImportSnapshot:
        organizations = self._organizations_by_id(organization_rows)
        configs = self._group_rows(config_rows, organizations, "org_configs")
        external = self._group_rows(
            external_rows,
            organizations,
            "kuaimai_external_credentials",
        )
        values: list[LegacyOrganizationValues] = []
        reports: dict[str, LegacyPreflightReport] = {}
        for org_id, organization in sorted(organizations.items()):
            source, report = self._read_organization(
                org_id,
                organization,
                configs.get(org_id, ()),
                external.get(org_id, ()),
            )
            values.append(source)
            reports[org_id] = report
        return LegacyImportSnapshot(tuple(values), reports)

    @staticmethod
    def _export_rows(
        value: object,
        name: str,
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(value, list) or not all(
            isinstance(row, dict) for row in value
        ):
            raise LegacyImportSourceError(
                f"LEGACY_IMPORT_EXPORT_ROWS_INVALID:{name}"
            )
        return tuple(value)

    def _read_organization(
        self,
        org_id: str,
        organization: Mapping[str, object],
        config_rows: tuple[dict[str, object], ...],
        external_rows: tuple[dict[str, object], ...],
    ) -> tuple[LegacyOrganizationValues, LegacyPreflightReport]:
        key = self._organization_key(organization)
        config_values: dict[str, str] = {}
        configured_keys: set[str] = set()
        invalid_keys: set[str] = set()
        for row in config_rows:
            config_key = self._required_text(row, "config_key")
            if config_key in configured_keys:
                raise LegacyImportSourceError(
                    "LEGACY_IMPORT_CONFIG_DUPLICATE"
                )
            configured_keys.add(config_key)
            plaintext = self._decrypt(
                row.get("config_value_encrypted"),
                key,
            )
            if plaintext is None:
                invalid_keys.add(config_key)
            else:
                config_values[config_key] = plaintext

        organization_corp_id = str(
            organization.get("wecom_corp_id") or ""
        ).strip()
        legacy_corp_id = config_values.get("wecom_corp_id")
        sources_match = (
            legacy_corp_id.strip() == organization_corp_id
            if legacy_corp_id is not None and organization_corp_id
            else None
        )
        external_values: list[LegacyExternalValue] = []
        external_facts: list[ExternalCredentialFact] = []
        seen_sources: set[str] = set()
        for row in external_rows:
            value, fact = self._external_value(row, key)
            if value.source in seen_sources:
                raise LegacyImportSourceError(
                    "LEGACY_IMPORT_EXTERNAL_DUPLICATE"
                )
            seen_sources.add(value.source)
            external_values.append(value)
            external_facts.append(fact)

        source = LegacyOrganizationValues(
            org_id=org_id,
            organization_corp_id=organization_corp_id or None,
            config_values=config_values,
            external_credentials=tuple(external_values),
        )
        report = build_legacy_preflight(
            configured_keys=configured_keys,
            organization_corp_id_configured=bool(organization_corp_id),
            corp_id_sources_match=sources_match,
            invalid_keys=invalid_keys,
            external_credentials=tuple(external_facts),
        )
        return source, report

    def _external_value(
        self,
        row: Mapping[str, object],
        key: str | None,
    ) -> tuple[LegacyExternalValue, ExternalCredentialFact]:
        source = self._required_text(row, "source")
        status = self._required_text(row, "status")
        if source not in {"thinktank", "viperp"} or status not in {
            "active",
            "expired",
            "invalid",
        }:
            raise LegacyImportSourceError("LEGACY_IMPORT_EXTERNAL_INVALID")
        company_id = row.get("kuaimai_company_id")
        if (
            not isinstance(company_id, int)
            or isinstance(company_id, bool)
            or company_id <= 0
        ):
            raise LegacyImportSourceError(
                "LEGACY_IMPORT_EXTERNAL_COMPANY_INVALID"
            )
        censeid_stored = self._required_text(row, "censeid_cookie")
        full_stored = str(row.get("cookie_full") or "")
        censeid_encrypted = censeid_stored.startswith("enc:")
        full_encrypted = full_stored.startswith("enc:") if full_stored else None
        censeid = self._decrypt_cookie(censeid_stored, key)
        cookie_full = self._decrypt_cookie(full_stored, key)
        decryptable = censeid is not None and (
            not full_stored or cookie_full is not None
        )
        value = LegacyExternalValue(
            source=source,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            company_id=str(company_id),
            censeid_cookie=censeid or "",
            cookie_full=cookie_full or "",
        )
        fact = ExternalCredentialFact(
            source=source,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            censeid_encrypted=censeid_encrypted,
            cookie_full_encrypted=full_encrypted,
            decryptable=decryptable,
        )
        return value, fact

    def _organization_key(
        self,
        organization: Mapping[str, object],
    ) -> str | None:
        key = str(organization.get("encrypt_key") or "").strip()
        return key or self._global_encrypt_key

    @staticmethod
    def _decrypt(value: object, key: str | None) -> str | None:
        if not isinstance(value, str) or not value or not key:
            return None
        try:
            return aes_decrypt(value, key)
        except (binascii.Error, TypeError, ValueError):
            return None

    @classmethod
    def _decrypt_cookie(
        cls,
        value: str,
        key: str | None,
    ) -> str | None:
        if not value.startswith("enc:"):
            return None
        return cls._decrypt(value[4:], key)

    def _read_rows(
        self,
        table: str,
        columns: str,
    ) -> tuple[dict[str, object], ...]:
        try:
            response = self._db.table(table).select(columns).execute()
        except Exception as error:
            raise LegacyImportSourceError(
                f"LEGACY_IMPORT_READ_FAILED:{table}"
            ) from error
        rows = response.data
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise LegacyImportSourceError(
                f"LEGACY_IMPORT_RESPONSE_INVALID:{table}"
            )
        return tuple(rows)

    @classmethod
    def _organizations_by_id(
        cls,
        rows: tuple[dict[str, object], ...],
    ) -> dict[str, dict[str, object]]:
        organizations: dict[str, dict[str, object]] = {}
        for row in rows:
            org_id = cls._required_text(row, "id")
            if org_id in organizations:
                raise LegacyImportSourceError(
                    "LEGACY_IMPORT_ORGANIZATION_DUPLICATE"
                )
            organizations[org_id] = row
        return organizations

    @classmethod
    def _group_rows(
        cls,
        rows: tuple[dict[str, object], ...],
        organizations: Mapping[str, dict[str, object]],
        table: str,
    ) -> dict[str, tuple[dict[str, object], ...]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            org_id = cls._required_text(row, "org_id")
            if org_id not in organizations:
                raise LegacyImportSourceError(
                    f"LEGACY_IMPORT_ORPHAN_ROW:{table}"
                )
            grouped.setdefault(org_id, []).append(row)
        return {
            org_id: tuple(org_rows)
            for org_id, org_rows in grouped.items()
        }

    @staticmethod
    def _required_text(row: Mapping[str, object], field: str) -> str:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise LegacyImportSourceError(
                f"LEGACY_IMPORT_FIELD_INVALID:{field}"
            )
        return value.strip()
