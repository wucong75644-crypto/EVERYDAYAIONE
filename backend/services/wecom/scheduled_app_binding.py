"""Trusted existing-tenant configuration adapter for Scheduled WeCom App."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
)
from services.agent.runtime.credential_broker import (
    BackendCredential,
    CredentialAuditSink,
    CredentialBroker,
    CredentialBrokerError,
)
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.ports.scheduled_wecom_app_dispatch import (
    ScheduledWecomAppBinding,
)
from services.agent.runtime.wecom_app_credentials import (
    WECOM_APP_PROVIDER,
    WECOM_APP_SEND_PURPOSE,
    build_runtime_wecom_app_outbound,
)
from services.configuration.bundles import (
    AsyncSecretBundleResolver,
    ResolvedConfigurationBundle,
)
from services.configuration.material_service import SecretMaterialService
from services.wecom.app_outbound import AppHttpClient


_AGENT_ID = re.compile(r"^[1-9][0-9]*$")
_CONFIG_KEYS = (
    "wecom.corp_id",
    "wecom.oauth_agent_id",
    "wecom.oauth_agent_secret",
)
_SECRET_KEY = "wecom.oauth_agent_secret"
_MAX_EXPIRY = datetime.max.replace(tzinfo=timezone.utc)
AccessTokenGetter = Callable[[str, str, str], Awaitable[str | None]]


@dataclass(frozen=True, repr=False)
class _WecomAppMaterial:
    org_id: str
    corp_id: str
    agent_secret: str = field(repr=False)

    def __repr__(self) -> str:
        return "_WecomAppMaterial(<redacted>)"

    def __getstate__(self) -> Mapping[str, object]:
        raise TypeError("WECOM_APP_MATERIAL_NOT_SERIALIZABLE")


class _ExactCredentialBackend:
    """One-result backend whose lookup tuple must match in full."""

    operational = True
    production_ready = True

    def __init__(self, record: BackendCredential) -> None:
        self._record = record

    def __repr__(self) -> str:
        return "_ExactCredentialBackend(<redacted>)"

    async def resolve(
        self,
        *,
        tenant_id: str,
        handle: str,
        provider: str,
        revision: str,
        purpose: str,
    ) -> BackendCredential:
        record = self._record
        if (
            tenant_id,
            handle,
            provider,
            revision,
            purpose,
        ) != (
            record.tenant_id,
            record.handle,
            record.provider,
            record.revision,
            record.purpose,
        ):
            raise CredentialBrokerError("CREDENTIAL_UNAVAILABLE")
        return record


class _ExistingAccessTokenExchange:
    operational = True
    production_ready = True

    def __init__(self, get_access_token: AccessTokenGetter) -> None:
        if not callable(get_access_token):
            raise ValueError("WECOM_APP_TOKEN_MANAGER_REQUIRED")
        self._get_access_token = get_access_token

    def __repr__(self) -> str:
        return "_ExistingAccessTokenExchange(<redacted>)"

    async def exchange(self, material: object) -> str | None:
        if not isinstance(material, _WecomAppMaterial):
            return None
        return await self._get_access_token(
            material.org_id,
            material.corp_id,
            material.agent_secret,
        )


class ScheduledWecomAppBindingResolver:
    """Resolve an exact tenant Bundle and export only a non-secret binding."""

    def __init__(
        self,
        *,
        database: Any,
        material_service: SecretMaterialService,
        get_access_token: AccessTokenGetter,
        outbound_http_client: AppHttpClient,
        audit_sink: CredentialAuditSink,
    ) -> None:
        if database is None or not callable(getattr(database, "rpc", None)):
            raise ValueError("WECOM_APP_DATABASE_REQUIRED")
        if not isinstance(material_service, SecretMaterialService):
            raise ValueError("WECOM_APP_MATERIAL_SERVICE_REQUIRED")
        if not callable(get_access_token):
            raise ValueError("WECOM_APP_TOKEN_MANAGER_REQUIRED")
        if outbound_http_client is None or not callable(
            getattr(outbound_http_client, "post", None),
        ):
            raise ValueError("WECOM_APP_HTTP_CLIENT_REQUIRED")
        if audit_sink is None or not callable(getattr(audit_sink, "record", None)):
            raise ValueError("WECOM_APP_CREDENTIAL_AUDIT_REQUIRED")
        self._database = database
        self._material_service = material_service
        self._get_access_token = get_access_token
        self._outbound_http_client = outbound_http_client
        self._audit_sink = audit_sink

    def __repr__(self) -> str:
        return "ScheduledWecomAppBindingResolver(<trusted-adapter>)"

    async def resolve_app_binding(
        self,
        *,
        org_id: str,
        corp_id: str,
    ) -> ScheduledWecomAppBinding | None:
        normalized_org = _canonical_uuid(org_id)
        if normalized_org is None or not _exact_nonempty(corp_id):
            return None
        try:
            bundle = await AsyncSecretBundleResolver(
                self._scoped_database(normalized_org),
                self._material_service,
            ).wecom_app()
            facts = _validated_facts(bundle, corp_id)
            if facts is None:
                return None
            agent_id, agent_secret, versions = facts
            return self._binding(
                org_id=normalized_org,
                corp_id=corp_id,
                agent_id=agent_id,
                agent_secret=agent_secret,
                versions=versions,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    def _scoped_database(self, org_id: str) -> AsyncScopedDatabaseClient:
        return AsyncScopedDatabaseClient(
            self._database,
            DatabaseScope(
                actor_user_id=None,
                org_id=org_id,
                access_kind=DatabaseAccessKind.WORKER,
                request_id=f"scheduled-wecom-app-binding:{org_id}",
            ),
        )

    def _binding(
        self,
        *,
        org_id: str,
        corp_id: str,
        agent_id: int,
        agent_secret: str,
        versions: Mapping[str, int],
    ) -> ScheduledWecomAppBinding:
        handle, revision = _binding_identity(org_id, corp_id, agent_id, versions)
        scope = RuntimeScope(
            ScopeKind.SYSTEM,
            f"scheduled-wecom-app:{org_id}",
            None,
            org_id,
        )
        material = _WecomAppMaterial(org_id, corp_id, agent_secret)
        backend = _ExactCredentialBackend(BackendCredential(
            tenant_id=org_id,
            handle=handle,
            provider=WECOM_APP_PROVIDER,
            revision=revision,
            purpose=WECOM_APP_SEND_PURPOSE,
            expires_at=_MAX_EXPIRY,
            _material=material,
        ))
        transport = build_runtime_wecom_app_outbound(
            broker=CredentialBroker(backend, self._audit_sink),
            scope=scope,
            credential_handle=handle,
            provider_revision=revision,
            token_exchange=_ExistingAccessTokenExchange(self._get_access_token),
            outbound_http_client=self._outbound_http_client,
        )
        return ScheduledWecomAppBinding(
            org_id=org_id,
            corp_id=corp_id,
            agent_id=agent_id,
            transport=transport,
        )


def _validated_facts(
    bundle: ResolvedConfigurationBundle,
    expected_corp_id: str,
) -> tuple[int, str, Mapping[str, int]] | None:
    if bundle.name != "wecom.app":
        return None
    if set(bundle.values) != set(_CONFIG_KEYS):
        return None
    if set(bundle.sources) != set(_CONFIG_KEYS):
        return None
    if set(bundle.versions) != set(_CONFIG_KEYS):
        return None
    if any(bundle.sources.get(key) != "organization" for key in _CONFIG_KEYS):
        return None
    if any(
        not isinstance(bundle.versions.get(key), int)
        or isinstance(bundle.versions.get(key), bool)
        or bundle.versions[key] < 1
        for key in _CONFIG_KEYS
    ):
        return None
    if bundle.values.get("wecom.corp_id") != expected_corp_id:
        return None
    raw_agent_id = bundle.values.get("wecom.oauth_agent_id")
    if not isinstance(raw_agent_id, str) or _AGENT_ID.fullmatch(raw_agent_id) is None:
        return None
    secret_payload = bundle.values.get(_SECRET_KEY)
    if (
        not isinstance(secret_payload, Mapping)
        or set(secret_payload) != {"agent_secret"}
        or not _exact_nonempty(secret_payload.get("agent_secret"))
    ):
        return None
    return int(raw_agent_id), secret_payload["agent_secret"], bundle.versions


def _binding_identity(
    org_id: str,
    corp_id: str,
    agent_id: int,
    versions: Mapping[str, int],
) -> tuple[str, str]:
    facts = json.dumps(
        {
            "org_id": org_id,
            "corp_id": corp_id,
            "agent_id": agent_id,
            "versions": {key: versions[key] for key in _CONFIG_KEYS},
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    handle_digest = hashlib.sha256(b"wecom-app-handle-v1\0" + facts).hexdigest()
    revision_digest = hashlib.sha256(b"wecom-app-revision-v1\0" + facts).hexdigest()
    return f"wecom-app:{handle_digest}", f"wecom-app:{revision_digest}"


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = str(UUID(value))
    except ValueError:
        return None
    return normalized if value == normalized else None


def _exact_nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


__all__ = ["AccessTokenGetter", "ScheduledWecomAppBindingResolver"]
