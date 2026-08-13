"""Attempt-fenced KIE credential materialization for Runtime media."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.domain import ActionAttempt
from services.configuration.bundles import AsyncSecretBundleResolver


class PostgresRuntimeKieCredentialSource:
    def __init__(self, resolver: AsyncSecretBundleResolver) -> None:
        self._resolver = resolver

    async def api_key(
        self, attempt: ActionAttempt, *, provider_request_hash: str,
    ) -> str:
        bundle = await self._resolver.runtime_media({
            "p_action_id": str(attempt.action_id),
            "p_attempt_id": str(attempt.attempt_id),
            "p_worker_id": attempt.worker_id,
            "p_owner_token": str(attempt.lease.fencing_token),
            "p_expected_attempt_version": attempt.state_version,
            "p_request_hash": attempt.request_hash,
            "p_provider_request_hash": provider_request_hash,
        })
        secret = bundle.values.get("ai.kie.api_key")
        if not isinstance(secret, Mapping) or set(secret) != {"api_key"}:
            raise RuntimeError("KIE_CREDENTIAL_UNAVAILABLE")
        api_key = secret.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError("KIE_CREDENTIAL_UNAVAILABLE")
        return api_key


__all__ = ["PostgresRuntimeKieCredentialSource"]
