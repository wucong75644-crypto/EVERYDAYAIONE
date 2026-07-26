"""Resolve one organization's encrypted WeCom callback configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from services.configuration.bundles import SecretBundleResolver
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService


@dataclass(frozen=True)
class WecomCallbackConfig:
    org_id: str
    corp_id: str
    token: str
    encoding_aes_key: str
    agent_id: str
    agent_secret: str


def resolve_wecom_callback_config(
    worker_db: Any,
    org_id: str,
) -> WecomCallbackConfig:
    """Resolve and decrypt the exact organization's callback Bundle."""
    scoped = ScopedDatabaseClient(
        worker_db,
        DatabaseScope(
            actor_user_id=None,
            org_id=org_id,
            access_kind=DatabaseAccessKind.WORKER,
            request_id=f"wecom-callback:{org_id}",
        ),
    )
    bundle = SecretBundleResolver(
        scoped,
        SecretMaterialService(LocalKEKProvider.from_environment()),
    ).wecom_callback()
    corp_id = bundle.values.get("wecom.corp_id")
    credentials = bundle.values.get("wecom.callback_credentials")
    agent_id = bundle.values.get("wecom.oauth_agent_id")
    agent_secret_payload = bundle.values.get("wecom.oauth_agent_secret")
    if (
        not isinstance(corp_id, str)
        or not corp_id
        or not isinstance(credentials, Mapping)
        or not isinstance(credentials.get("token"), str)
        or not credentials["token"]
        or not isinstance(credentials.get("encoding_aes_key"), str)
        or len(credentials["encoding_aes_key"]) != 43
        or not isinstance(agent_id, str)
        or not agent_id
        or not isinstance(agent_secret_payload, Mapping)
        or not isinstance(agent_secret_payload.get("agent_secret"), str)
        or not agent_secret_payload["agent_secret"]
    ):
        raise ValueError("WECOM_CALLBACK_CONFIG_INVALID")
    return WecomCallbackConfig(
        org_id=org_id,
        corp_id=corp_id,
        token=credentials["token"],
        encoding_aes_key=credentials["encoding_aes_key"],
        agent_id=agent_id,
        agent_secret=agent_secret_payload["agent_secret"],
    )
