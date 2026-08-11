"""Composition boundary for Runtime-owned Scheduled WeCom delivery."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from loguru import logger

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
    database_scope_from_client,
)
from services.agent.runtime.application.scheduled_wecom_app_dispatch import (
    ScheduledWecomAppDispatchService,
)
from services.agent.runtime.application.scheduled_wecom_router import (
    ScheduledWecomRouter,
)
from services.agent.runtime.application.scheduled_wecom_smart_dispatch import (
    ScheduledWecomSmartDispatchService,
)
from services.agent.runtime.credential_broker import (
    CredentialAuditEvent,
    CredentialAuditSink,
)
from services.agent.runtime.infrastructure.postgres.scheduled_wecom_delivery import (
    PostgresScheduledWecomDeliveryRepository,
)
from services.agent.runtime.wecom_app_credentials import (
    WECOM_APP_PROVIDER,
    WECOM_APP_SEND_PURPOSE,
)
from services.configuration.material_service import SecretMaterialService
from services.wecom.app_outbound import AppHttpClient
from services.wecom.scheduled_app_binding import (
    AccessTokenGetter,
    ScheduledWecomAppBindingResolver,
)
from services.wecom.scheduled_runtime_worker import ScheduledRuntimeWecomWorker
from services.wecom.scheduled_smart_transport import (
    ScheduledSmartTransportResolver,
)


_OPAQUE_WECOM_APP_IDENTITY = re.compile(r"^wecom-app:[0-9a-f]{64}$")
_AUDIT_OUTCOMES = frozenset({
    "backend_not_ready",
    "unavailable",
    "backend_error",
    "binding_rejected",
    "issued",
})


@dataclass(frozen=True)
class ScheduledWecomRuntimeComponents:
    """Non-owning object graph for one WeCom process."""

    worker: ScheduledRuntimeWecomWorker
    repository: PostgresScheduledWecomDeliveryRepository
    router: ScheduledWecomRouter
    smart_transport_resolver: ScheduledSmartTransportResolver
    app_binding_resolver: ScheduledWecomAppBindingResolver


class ScheduledWecomCredentialJournalAuditSink:
    """Write only validated, Secret-free credential lifecycle facts."""

    def __repr__(self) -> str:
        return "ScheduledWecomCredentialJournalAuditSink(<secret-free>)"

    async def record(self, event: CredentialAuditEvent) -> None:
        fields = _validated_audit_fields(event)
        logger.info(
            "scheduled_wecom_credential_audit | {}",
            json.dumps(fields, ensure_ascii=True, separators=(",", ":")),
        )


def build_scheduled_wecom_runtime_components(
    *,
    database: Any,
    get_ws_client: Callable[[str], object | None],
    material_service: SecretMaterialService,
    get_access_token: AccessTokenGetter,
    outbound_http_client: AppHttpClient,
    worker_id: str,
    audit_sink: CredentialAuditSink | None = None,
) -> ScheduledWecomRuntimeComponents:
    """Compose existing Scheduled WeCom ports without starting or owning them."""
    _validate_dependencies(
        database=database,
        get_ws_client=get_ws_client,
        material_service=material_service,
        get_access_token=get_access_token,
        outbound_http_client=outbound_http_client,
        audit_sink=audit_sink,
    )
    scoped_database = AsyncScopedDatabaseClient(
        database,
        DatabaseScope(
            actor_user_id=None,
            org_id=None,
            access_kind=DatabaseAccessKind.WORKER,
            request_id=_scope_request_id(worker_id),
        ),
    )
    repository = PostgresScheduledWecomDeliveryRepository(scoped_database)
    smart_resolver = ScheduledSmartTransportResolver(get_ws_client)
    app_resolver = ScheduledWecomAppBindingResolver(
        database=database,
        material_service=material_service,
        get_access_token=get_access_token,
        outbound_http_client=outbound_http_client,
        audit_sink=(
            audit_sink
            if audit_sink is not None
            else ScheduledWecomCredentialJournalAuditSink()
        ),
    )
    smart_dispatch = ScheduledWecomSmartDispatchService(
        repository,
        smart_resolver,
    )
    app_dispatch = ScheduledWecomAppDispatchService(repository)
    router = ScheduledWecomRouter(
        repository,
        smart_dispatch,
        app_dispatch,
        app_resolver,
    )
    worker = ScheduledRuntimeWecomWorker(
        repository,
        router,
        worker_id=worker_id,
    )
    return ScheduledWecomRuntimeComponents(
        worker=worker,
        repository=repository,
        router=router,
        smart_transport_resolver=smart_resolver,
        app_binding_resolver=app_resolver,
    )


def _validate_dependencies(
    *,
    database: Any,
    get_ws_client: object,
    material_service: object,
    get_access_token: object,
    outbound_http_client: object,
    audit_sink: object | None,
) -> None:
    if (
        database is None
        or not callable(getattr(database, "rpc", None))
        or database_scope_from_client(database) is not None
    ):
        raise ValueError("SCHEDULED_WECOM_RAW_DATABASE_REQUIRED")
    if not callable(get_ws_client):
        raise ValueError("SCHEDULED_WECOM_WS_CLIENT_RESOLVER_REQUIRED")
    if not isinstance(material_service, SecretMaterialService):
        raise ValueError("SCHEDULED_WECOM_MATERIAL_SERVICE_REQUIRED")
    if not callable(get_access_token):
        raise ValueError("SCHEDULED_WECOM_TOKEN_MANAGER_REQUIRED")
    if outbound_http_client is None or not callable(
        getattr(outbound_http_client, "post", None),
    ):
        raise ValueError("SCHEDULED_WECOM_HTTP_CLIENT_REQUIRED")
    if audit_sink is not None and not callable(getattr(audit_sink, "record", None)):
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_REQUIRED")


def _scope_request_id(worker_id: object) -> str:
    if (
        not isinstance(worker_id, str)
        or not worker_id
        or worker_id != worker_id.strip()
        or len(worker_id) > 128
    ):
        raise ValueError("invalid scheduled runtime worker_id")
    digest = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()
    return f"scheduled-wecom-runtime:{digest}"


def _validated_audit_fields(event: CredentialAuditEvent) -> dict[str, str]:
    if type(event) is not CredentialAuditEvent:
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if not _canonical_uuid(event.tenant_id):
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if (
        not isinstance(event.handle, str)
        or _OPAQUE_WECOM_APP_IDENTITY.fullmatch(event.handle) is None
    ):
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if (
        not isinstance(event.revision, str)
        or _OPAQUE_WECOM_APP_IDENTITY.fullmatch(event.revision) is None
    ):
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if not isinstance(event.provider, str) or event.provider != WECOM_APP_PROVIDER:
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if not isinstance(event.purpose, str) or event.purpose != WECOM_APP_SEND_PURPOSE:
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if not isinstance(event.outcome, str) or event.outcome not in _AUDIT_OUTCOMES:
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    if (
        not isinstance(event.occurred_at, datetime)
        or event.occurred_at.tzinfo is None
        or event.occurred_at.utcoffset() is None
    ):
        raise ValueError("SCHEDULED_WECOM_CREDENTIAL_AUDIT_INVALID")
    return {
        "tenant_id": event.tenant_id,
        "handle": event.handle,
        "provider": event.provider,
        "revision": event.revision,
        "purpose": event.purpose,
        "outcome": event.outcome,
        "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(),
    }


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


__all__ = [
    "ScheduledWecomCredentialJournalAuditSink",
    "ScheduledWecomRuntimeComponents",
    "build_scheduled_wecom_runtime_components",
]
