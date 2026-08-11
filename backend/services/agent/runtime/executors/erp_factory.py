"""Fenced construction of the existing tenant ERP read dispatcher."""

from __future__ import annotations

from collections.abc import Mapping

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.provider_adapters import (
    ErpDispatcherFactoryPort, ErpDispatcherPort,
)
from services.configuration.bundles import AsyncSecretBundleResolver
from services.configuration.material_service import SecretMaterialService
from services.configuration.resolver import ConfigurationResolutionError


class OrgScopedErpDispatcherFactory:
    """Resolve one enterprise ERP credential bundle for each Runtime scope."""

    def __init__(
        self, database, *, worker_id: str,
        material_service: SecretMaterialService,
    ) -> None:
        self._database = database
        self._worker_id = worker_id
        self._material_service = material_service

    async def create(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> ErpDispatcherPort:
        scope = attempt.scope
        org_id = scope.org_id
        if not org_id:
            raise ValueError("ERP_ORG_SCOPE_REQUIRED")

        expected_version = _expected_attempt_version(request)
        params = {
            "p_attempt_id": attempt.attempt_id,
            "p_worker_id": self._worker_id,
            "p_execution_token": attempt.lease.fencing_token,
            "p_expected_attempt_version": expected_version,
            "p_request_hash": attempt.request_hash,
        }
        resolver = AsyncSecretBundleResolver(
            self._database, self._material_service,
        )
        bundle = await resolver.runtime_erp(params)
        app = _mapping(bundle.values.get("erp.app_credentials"))
        token = _mapping(bundle.values.get("erp.token_pair"))
        token_version = bundle.versions.get("erp.token_pair")
        if not isinstance(token_version, int) or token_version < 1:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            )

        from services.kuaimai.client import KuaiMaiClient
        from services.kuaimai.dispatcher import ErpDispatcher

        async def persist_token(
            persisted_org_id: str, access_token: str, refresh_token: str,
        ) -> None:
            nonlocal token_version
            if persisted_org_id != org_id:
                raise ValueError("ERP_ORG_SCOPE_MISMATCH")
            envelope = self._material_service.encrypt_payload(
                scope_kind="organization", scope_id=org_id,
                secret_name="erp.token_pair",
                payload_version=token_version + 1,
                payload={
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                },
            )
            response = await self._database.rpc(
                "rotate_agent_runtime_erp_token_pair_v1", {
                    **params,
                    "p_secret_envelope": {
                        "payload_ciphertext": envelope.payload_ciphertext,
                        "wrapped_dek": envelope.wrapped_dek,
                        "kek_version": envelope.kek_version,
                    },
                    "p_expected_config_version": token_version,
                },
            ).execute()
            payload = response.data
            next_version = (
                payload.get("version") if isinstance(payload, Mapping) else None
            )
            if next_version != token_version + 1:
                raise ConfigurationResolutionError(
                    "CONFIG_BUNDLE_RESPONSE_INVALID"
                )
            token_version = next_version

        client = KuaiMaiClient(
            app_key=_text(app, "app_key"),
            app_secret=_text(app, "app_secret"),
            access_token=_text(token, "access_token"),
            refresh_token=_text(token, "refresh_token"),
            org_id=org_id,
            token_persister=persist_token,
        )
        try:
            return ErpDispatcher(
                client, db_source=None, record_param_knowledge=False,
                log_request_params=False,
            )
        except Exception:
            await client.close()
            raise


def _expected_attempt_version(request: Mapping[str, object]) -> int:
    context = request.get("_dispatch_context")
    if not isinstance(context, Mapping):
        raise ValueError("ERP_DISPATCH_CONTEXT_REQUIRED")
    value = context.get("expected_attempt_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("ERP_ATTEMPT_VERSION_REQUIRED")
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
    return value


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
    return item


__all__ = ["ErpDispatcherFactoryPort", "OrgScopedErpDispatcherFactory"]
