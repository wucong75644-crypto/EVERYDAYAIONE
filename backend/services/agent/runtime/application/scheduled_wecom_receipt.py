"""Canonical identities and receipt hashes for Scheduled Runtime WeCom."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from uuid import UUID, uuid5

from services.agent.runtime.ports.scheduled_wecom_delivery import (
    DispatchOutcome,
    DispatchPayload,
    ProviderDispatchIdentity,
    ReceiptMetadata,
    ReceiptType,
    ReconcileResult,
)


_REQUEST_NAMESPACE = UUID("4990db79-f27b-4d4a-8b90-af6ab9e88f48")
_SMART_IDENTITY_DOMAIN = "everydayai.scheduled_wecom.smart_dispatch_identity.v1"
_APP_IDENTITY_DOMAIN = "everydayai.scheduled_wecom.app_dispatch_identity.v1"
_RECEIPT_DOMAIN = "everydayai.scheduled_wecom.dispatch_receipt.v1"
_RECONCILE_READBACK_DOMAIN = "everydayai.scheduled_wecom.reconcile_readback.v1"


def scheduled_wecom_smart_identity(payload: DispatchPayload) -> ProviderDispatchIdentity:
    """Derive provider identities from the frozen payload and provider revision."""
    facts = {
        "domain": _SMART_IDENTITY_DOMAIN,
        "item_id": payload.item_id,
        "payload_hash": payload.payload_hash,
        "provider_revision": payload.provider_revision,
    }
    digest = hashlib.sha256(_canonical_json(facts).encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{_SMART_IDENTITY_DOMAIN}:idempotency:{digest}".encode("ascii"),
    ).hexdigest()
    return ProviderDispatchIdentity(
        provider_request_id=f"scheduled-wecom-smart:{digest}",
        idempotency_key=idempotency_key,
        provider_revision=payload.provider_revision,
    )


def scheduled_wecom_app_identity(
    payload: DispatchPayload,
    *,
    org_id: str,
    corp_id: str,
    agent_id: int,
) -> ProviderDispatchIdentity:
    """Derive App identities from frozen payload and non-secret tenant binding."""
    binding_json = json.dumps(
        {"agent_id": agent_id, "corp_id": corp_id, "org_id": org_id},
        ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
    )
    binding_hash = hashlib.sha256(binding_json.encode("utf-8")).hexdigest()
    facts = {
        "binding_hash": binding_hash,
        "domain": _APP_IDENTITY_DOMAIN,
        "item_id": payload.item_id,
        "payload_hash": payload.payload_hash,
        "provider_revision": payload.provider_revision,
    }
    digest = hashlib.sha256(_canonical_json(facts).encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{_APP_IDENTITY_DOMAIN}:idempotency:{digest}".encode("ascii"),
    ).hexdigest()
    return ProviderDispatchIdentity(
        provider_request_id=f"scheduled-wecom-app:{digest}",
        idempotency_key=idempotency_key,
        provider_revision=payload.provider_revision,
    )


def scheduled_wecom_request_id(kind: str, identity: str) -> str:
    """Derive a replay-stable UUID for one durable repository mutation."""
    return str(uuid5(_REQUEST_NAMESPACE, f"{kind}:{identity}"))


def scheduled_wecom_receipt_hash(
    *,
    dispatch_outcome: DispatchOutcome,
    receipt_type: ReceiptType,
    receipt_code: str | None,
    metadata: ReceiptMetadata,
    identity: ProviderDispatchIdentity,
) -> str:
    """Match SQL `_agent_runtime_scheduled_wecom_receipt_hash` exactly."""
    facts = {
        "domain": _RECEIPT_DOMAIN,
        "dispatch_outcome": dispatch_outcome.value,
        "receipt_type": receipt_type.value,
        "receipt_code": receipt_code,
        "receipt_metadata": _metadata_json(metadata),
        "provider_request_id": identity.provider_request_id.strip(),
        "idempotency_key": identity.idempotency_key,
        "provider_revision": identity.provider_revision,
    }
    return hashlib.sha256(_canonical_json(facts).encode("utf-8")).hexdigest()


def scheduled_wecom_reconcile_readback_hash(
    *,
    reconcile_result: ReconcileResult,
    receipt_type: ReceiptType,
    receipt_code: str | None,
    metadata: ReceiptMetadata,
    identity: ProviderDispatchIdentity,
) -> str:
    """Match SQL `_agent_runtime_scheduled_wecom_reconcile_readback_hash`."""
    facts = {
        "domain": _RECONCILE_READBACK_DOMAIN,
        "reconcile_result": reconcile_result.value,
        "readback_type": receipt_type.value,
        "readback_code": receipt_code,
        "readback_metadata": _metadata_json(metadata),
        "provider_request_id": identity.provider_request_id.strip(),
        "idempotency_key": identity.idempotency_key,
        "provider_revision": identity.provider_revision,
    }
    return hashlib.sha256(_canonical_json(facts).encode("utf-8")).hexdigest()


def _metadata_json(metadata: ReceiptMetadata) -> dict[str, str | int]:
    values = {
        "provider_message_id": metadata.provider_message_id,
        "trace_id": metadata.trace_id,
        "provider_code": metadata.provider_code,
        "http_status": metadata.http_status,
        "wecom_errcode": metadata.wecom_errcode,
    }
    return {key: value for key, value in values.items() if value is not None}


def _canonical_json(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    if not encoded.isascii():
        raise ValueError("SCHEDULED_WECOM_CANONICAL_NON_ASCII")
    return encoded
