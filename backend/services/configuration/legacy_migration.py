"""Secret-free migration contract for the legacy configuration stores."""

from __future__ import annotations

import binascii
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from core.crypto import aes_decrypt


PreflightStatus = Literal[
    "ready",
    "skipped",
    "incomplete",
    "conflict",
    "invalid",
]


@dataclass(frozen=True)
class LegacyTargetContract:
    """One target configuration and the legacy keys needed to build it."""

    target_key: str
    source_keys: tuple[str, ...]


@dataclass(frozen=True)
class ExternalCredentialFact:
    """Non-secret facts about one legacy Kuaimai Web credential."""

    source: Literal["thinktank", "viperp"]
    status: Literal["active", "expired", "invalid"]
    censeid_encrypted: bool
    cookie_full_encrypted: bool | None
    decryptable: bool = True


@dataclass(frozen=True)
class LegacyPreflightItem:
    """Secret-free readiness result for one target configuration."""

    target_key: str
    status: PreflightStatus
    source_keys: tuple[str, ...]
    reason: str
    target_enabled: bool | None = None


@dataclass(frozen=True)
class LegacyPreflightReport:
    """Immutable preflight report that never contains configuration values."""

    can_migrate: bool
    items: tuple[LegacyPreflightItem, ...]
    unknown_keys: tuple[str, ...]


@dataclass(frozen=True)
class LegacyOrganizationPreflight:
    """One organization's identity and its value-free preflight report."""

    org_id: str
    report: LegacyPreflightReport


class LegacyPreflightCollectionError(RuntimeError):
    """Stable failure for malformed legacy rows or missing organizations."""


_TARGET_CONTRACTS = (
    LegacyTargetContract(
        "ai.google.api_key",
        ("ai_google_api_key",),
    ),
    LegacyTargetContract(
        "ai.kie.api_key",
        ("ai_kie_api_key",),
    ),
    LegacyTargetContract(
        "ai.openrouter.api_key",
        ("ai_openrouter_api_key",),
    ),
    LegacyTargetContract(
        "erp.app_credentials",
        ("kuaimai_app_key", "kuaimai_app_secret"),
    ),
    LegacyTargetContract(
        "erp.token_pair",
        ("kuaimai_access_token", "kuaimai_refresh_token"),
    ),
    LegacyTargetContract(
        "erp.warehouse_ids",
        ("erp_warehouse_ids",),
    ),
    LegacyTargetContract(
        "wecom.bot_credentials",
        ("wecom_bot_id", "wecom_bot_secret"),
    ),
    LegacyTargetContract(
        "wecom.oauth_agent_id",
        ("wecom_agent_id",),
    ),
    LegacyTargetContract(
        "wecom.oauth_agent_secret",
        ("wecom_agent_secret",),
    ),
)

LEGACY_TARGET_CONTRACTS: Mapping[str, LegacyTargetContract] = MappingProxyType({
    contract.target_key: contract for contract in _TARGET_CONTRACTS
})

_EXTERNAL_TARGETS = MappingProxyType({
    "thinktank": (
        "kuaimai_external.thinktank.cookie",
        "kuaimai_external.thinktank.company_id",
    ),
    "viperp": (
        "kuaimai_external.viperp.cookie",
        "kuaimai_external.viperp.company_id",
    ),
})

_KNOWN_LEGACY_KEYS = frozenset(
    key
    for contract in _TARGET_CONTRACTS
    for key in contract.source_keys
) | {"wecom_corp_id"}


def build_legacy_preflight(
    *,
    configured_keys: set[str] | frozenset[str],
    organization_corp_id_configured: bool,
    corp_id_sources_match: bool | None,
    invalid_keys: set[str] | frozenset[str] = frozenset(),
    external_credentials: tuple[ExternalCredentialFact, ...] = (),
) -> LegacyPreflightReport:
    """Build a value-free readiness report from already inspected facts."""
    normalized_keys = frozenset(configured_keys)
    items = [
        _group_item(contract, normalized_keys, frozenset(invalid_keys))
        for contract in _TARGET_CONTRACTS
    ]
    items.append(_corp_id_item(
        "wecom_corp_id" in normalized_keys,
        organization_corp_id_configured,
        corp_id_sources_match,
        "wecom_corp_id" in invalid_keys,
    ))
    items.extend(
        _external_items(credential)
        for credential in external_credentials
    )
    flattened = tuple(
        item
        for result in items
        for item in (result if isinstance(result, tuple) else (result,))
    )
    unknown_keys = tuple(sorted(normalized_keys - _KNOWN_LEGACY_KEYS))
    blocked = {"incomplete", "conflict", "invalid"}
    return LegacyPreflightReport(
        can_migrate=not unknown_keys and all(
            item.status not in blocked for item in flattened
        ),
        items=flattened,
        unknown_keys=unknown_keys,
    )


def _group_item(
    contract: LegacyTargetContract,
    configured_keys: frozenset[str],
    invalid_keys: frozenset[str],
) -> LegacyPreflightItem:
    present = tuple(
        key for key in contract.source_keys if key in configured_keys
    )
    if not present:
        return LegacyPreflightItem(
            contract.target_key,
            "skipped",
            (),
            "LEGACY_SOURCE_ABSENT",
        )
    if any(key in invalid_keys for key in present):
        return LegacyPreflightItem(
            contract.target_key,
            "invalid",
            present,
            "LEGACY_SOURCE_UNREADABLE",
        )
    if len(present) != len(contract.source_keys):
        return LegacyPreflightItem(
            contract.target_key,
            "incomplete",
            present,
            "LEGACY_SOURCE_INCOMPLETE",
        )
    return LegacyPreflightItem(
        contract.target_key,
        "ready",
        present,
        "LEGACY_SOURCE_COMPLETE",
    )


def _corp_id_item(
    legacy_configured: bool,
    organization_configured: bool,
    sources_match: bool | None,
    legacy_invalid: bool,
) -> LegacyPreflightItem:
    if legacy_invalid:
        return LegacyPreflightItem(
            "wecom.corp_id",
            "invalid",
            ("wecom_corp_id",),
            "LEGACY_SOURCE_UNREADABLE",
        )
    if legacy_configured and organization_configured:
        if sources_match is True:
            return LegacyPreflightItem(
                "wecom.corp_id",
                "ready",
                ("organizations.wecom_corp_id", "wecom_corp_id"),
                "LEGACY_CORP_ID_SOURCES_MATCH",
            )
        return LegacyPreflightItem(
            "wecom.corp_id",
            "conflict",
            ("organizations.wecom_corp_id", "wecom_corp_id"),
            "LEGACY_CORP_ID_COMPARISON_REQUIRED",
        )
    if organization_configured:
        return LegacyPreflightItem(
            "wecom.corp_id",
            "ready",
            ("organizations.wecom_corp_id",),
            "LEGACY_ORGANIZATION_CORP_ID_SELECTED",
        )
    if legacy_configured:
        return LegacyPreflightItem(
            "wecom.corp_id",
            "ready",
            ("wecom_corp_id",),
            "LEGACY_ENCRYPTED_CORP_ID_SELECTED",
        )
    return LegacyPreflightItem(
        "wecom.corp_id",
        "skipped",
        (),
        "LEGACY_SOURCE_ABSENT",
    )


def _external_items(
    credential: ExternalCredentialFact,
) -> tuple[LegacyPreflightItem, LegacyPreflightItem]:
    targets = _EXTERNAL_TARGETS[credential.source]
    if (
        not credential.censeid_encrypted
        or credential.cookie_full_encrypted is False
    ):
        status: PreflightStatus = "conflict"
        reason = "LEGACY_EXTERNAL_PLAINTEXT_REJECTED"
    elif credential.cookie_full_encrypted is None:
        status = "incomplete"
        reason = "LEGACY_EXTERNAL_SOURCE_INCOMPLETE"
    elif not credential.decryptable:
        status = "invalid"
        reason = "LEGACY_EXTERNAL_UNREADABLE"
    else:
        status = "ready"
        reason = "LEGACY_EXTERNAL_ENCRYPTED"
    enabled = credential.status == "active"
    return tuple(
        LegacyPreflightItem(
            target,
            status,
            (f"kuaimai_external_credentials.{credential.source}",),
            reason,
            target_enabled=enabled,
        )
        for target in targets
    )


class LegacyConfigurationFactCollector:
    """Batch-read and validate old stores without returning secret values."""

    def __init__(
        self,
        db: Any,
        *,
        global_encrypt_key: str | None,
    ) -> None:
        self._db = db
        self._global_encrypt_key = global_encrypt_key

    def collect(self) -> tuple[LegacyOrganizationPreflight, ...]:
        """Read three legacy tables and return one report per organization."""
        organizations = self._read_rows(
            "organizations",
            "id,wecom_corp_id,encrypt_key",
        )
        config_rows = self._read_rows(
            "org_configs",
            "org_id,config_key,config_value_encrypted",
        )
        external_rows = self._read_rows(
            "kuaimai_external_credentials",
            "org_id,source,status,censeid_cookie,cookie_full",
        )
        orgs = self._organizations_by_id(organizations)
        configs = self._group_rows(config_rows, orgs, "org_configs")
        external = self._group_rows(
            external_rows,
            orgs,
            "kuaimai_external_credentials",
        )
        return tuple(
            self._collect_org(
                org_id,
                org,
                configs.get(org_id, ()),
                external.get(org_id, ()),
            )
            for org_id, org in sorted(orgs.items())
        )

    def _collect_org(
        self,
        org_id: str,
        organization: dict[str, object],
        config_rows: tuple[dict[str, object], ...],
        external_rows: tuple[dict[str, object], ...],
    ) -> LegacyOrganizationPreflight:
        key = self._organization_key(organization)
        configured_keys: set[str] = set()
        invalid_keys: set[str] = set()
        legacy_corp_id: str | None = None
        for row in config_rows:
            config_key = self._required_text(row, "config_key")
            configured_keys.add(config_key)
            plaintext = self._decrypt(
                row.get("config_value_encrypted"),
                key,
            )
            if plaintext is None:
                invalid_keys.add(config_key)
            elif config_key == "wecom_corp_id":
                legacy_corp_id = plaintext.strip()

        organization_corp_id = str(
            organization.get("wecom_corp_id") or ""
        ).strip()
        sources_match = (
            legacy_corp_id == organization_corp_id
            if legacy_corp_id is not None and organization_corp_id
            else None
        )
        external_facts = tuple(
            self._external_fact(row, key) for row in external_rows
        )
        return LegacyOrganizationPreflight(
            org_id=org_id,
            report=build_legacy_preflight(
                configured_keys=configured_keys,
                organization_corp_id_configured=bool(organization_corp_id),
                corp_id_sources_match=sources_match,
                invalid_keys=invalid_keys,
                external_credentials=external_facts,
            ),
        )

    def _external_fact(
        self,
        row: dict[str, object],
        key: str | None,
    ) -> ExternalCredentialFact:
        source = self._required_text(row, "source")
        status = self._required_text(row, "status")
        if source not in _EXTERNAL_TARGETS or status not in {
            "active",
            "expired",
            "invalid",
        }:
            raise LegacyPreflightCollectionError(
                "LEGACY_EXTERNAL_ROW_INVALID"
            )
        censeid = self._required_text(row, "censeid_cookie")
        cookie_full = str(row.get("cookie_full") or "")
        censeid_encrypted = censeid.startswith("enc:")
        full_encrypted = (
            cookie_full.startswith("enc:") if cookie_full else None
        )
        decryptable = (
            self._decrypt_cookie(censeid, key)
            and (not cookie_full or self._decrypt_cookie(cookie_full, key))
        )
        return ExternalCredentialFact(
            source=source,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            censeid_encrypted=censeid_encrypted,
            cookie_full_encrypted=full_encrypted,
            decryptable=bool(decryptable),
        )

    def _organization_key(
        self,
        organization: dict[str, object],
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
    def _decrypt_cookie(cls, value: str, key: str | None) -> bool:
        if not value.startswith("enc:"):
            return False
        return cls._decrypt(value[4:], key) is not None

    def _read_rows(
        self,
        table: str,
        columns: str,
    ) -> tuple[dict[str, object], ...]:
        try:
            response = self._db.table(table).select(columns).execute()
        except Exception as error:
            raise LegacyPreflightCollectionError(
                f"LEGACY_READ_FAILED:{table}"
            ) from error
        rows = response.data
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise LegacyPreflightCollectionError(
                f"LEGACY_RESPONSE_INVALID:{table}"
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
                raise LegacyPreflightCollectionError(
                    "LEGACY_ORGANIZATION_DUPLICATE"
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
                raise LegacyPreflightCollectionError(
                    f"LEGACY_ORPHAN_ROW:{table}"
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
            raise LegacyPreflightCollectionError(
                f"LEGACY_FIELD_INVALID:{field}"
            )
        return value.strip()
