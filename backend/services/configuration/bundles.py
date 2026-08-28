"""Fixed Bundle RPC orchestration and request-local Secret decryption."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from loguru import logger

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from services.configuration.definitions import CONFIG_REGISTRY, ConfigDefinition
from services.configuration.envelope import (
    KekVersionMissingError,
    LocalKEKProvider,
    SecretMaterialError,
)
from services.configuration.material_service import SecretMaterialService
from services.configuration.resolver import (
    ConfigurationResolutionError,
    EffectiveConfigResolver,
    SecretReference,
)


@dataclass(frozen=True)
class ResolvedConfigurationBundle:
    """Request-local values plus non-secret source/version diagnostics."""

    name: str
    values: Mapping[str, object | None]
    sources: Mapping[str, str | None]
    versions: Mapping[str, int]


@dataclass(frozen=True)
class WecomBotTarget:
    """Decrypted credentials for one exact organization workload."""

    org_id: str
    corp_id: str
    bot_id: str
    bot_secret: str


class SecretBundleResolver:
    """Expose only fixed named Bundle methods to application consumers."""

    def __init__(
        self,
        db: Any,
        material_service: SecretMaterialService,
        effective_resolver: EffectiveConfigResolver | None = None,
    ) -> None:
        self._db = db
        self._material_service = material_service
        self._effective_resolver = effective_resolver or EffectiveConfigResolver()

    def ai_dashscope(self) -> ResolvedConfigurationBundle:
        return self._resolve("ai.provider.dashscope", "get_ai_dashscope_bundle")

    def ai_openrouter(self) -> ResolvedConfigurationBundle:
        return self._resolve("ai.provider.openrouter", "get_ai_openrouter_bundle")

    def ai_kie(self) -> ResolvedConfigurationBundle:
        return self._resolve("ai.provider.kie", "get_ai_kie_bundle")

    def ai_google(self) -> ResolvedConfigurationBundle:
        return self._resolve("ai.provider.google", "get_ai_google_bundle")

    def erp_runtime(self) -> ResolvedConfigurationBundle:
        return self._resolve("erp.runtime", "get_erp_runtime_bundle")

    def wecom_bot(self) -> ResolvedConfigurationBundle:
        return self._resolve("wecom.bot", "get_wecom_bot_bundle")

    def wecom_bot_admin_test(self) -> ResolvedConfigurationBundle:
        return self._resolve(
            "wecom.bot",
            "get_wecom_bot_admin_test_bundle",
        )

    def wecom_oauth_public(self) -> ResolvedConfigurationBundle:
        return self._resolve(
            "wecom.oauth.public",
            "get_wecom_oauth_public_bundle",
        )

    def wecom_oauth_exchange(self) -> ResolvedConfigurationBundle:
        return self._resolve(
            "wecom.oauth.exchange",
            "get_wecom_oauth_exchange_bundle",
        )

    def wecom_contact(self) -> ResolvedConfigurationBundle:
        return self._resolve("wecom.contact", "get_wecom_contact_bundle")

    def wecom_callback(self) -> ResolvedConfigurationBundle:
        return self._resolve("wecom.callback", "get_wecom_callback_bundle")

    def kuaimai_thinktank(self) -> ResolvedConfigurationBundle:
        return self._resolve(
            "kuaimai_external.thinktank",
            "get_kuaimai_thinktank_bundle",
        )

    def kuaimai_viperp(self) -> ResolvedConfigurationBundle:
        return self._resolve(
            "kuaimai_external.viperp",
            "get_kuaimai_viperp_bundle",
        )

    def _resolve(
        self,
        bundle_name: str,
        rpc_name: str,
    ) -> ResolvedConfigurationBundle:
        try:
            response = self._db.rpc(rpc_name).execute()
        except Exception as error:
            raise ConfigurationResolutionError(
                self._database_error_code(error)
            ) from error
        effective = self._effective_resolver.parse(bundle_name, response.data)
        values: dict[str, object | None] = {}
        sources: dict[str, str | None] = {}
        versions: dict[str, int] = {}
        for key, item in effective.items.items():
            sources[key] = item.source
            versions[key] = item.version
            if not item.configured:
                values[key] = None
            elif item.secret_ref is None:
                values[key] = item.value
            else:
                values[key] = self._decrypt_secret(
                    CONFIG_REGISTRY.get(key),
                    item.secret_ref,
                )
        return ResolvedConfigurationBundle(
            name=bundle_name,
            values=MappingProxyType(values),
            sources=MappingProxyType(sources),
            versions=MappingProxyType(versions),
        )

    def _decrypt_secret(
        self,
        definition: ConfigDefinition,
        reference: SecretReference,
    ) -> object:
        try:
            payload = self._material_service.decrypt_payload(
                reference.envelope,
                scope_kind=reference.source,
                scope_id=reference.scope_id,
                secret_name=reference.secret_name,
            )
        except KekVersionMissingError as error:
            raise ConfigurationResolutionError(
                "KEK_VERSION_MISSING"
            ) from error
        except (SecretMaterialError, ValueError) as error:
            raise ConfigurationResolutionError(
                "SECRET_MATERIAL_UNAVAILABLE"
            ) from error
        required = set(definition.validation.get("required", ()))
        if set(payload) != required or any(
            not isinstance(value, str) or not value
            for value in payload.values()
        ):
            raise ConfigurationResolutionError(
                "SECRET_MATERIAL_UNAVAILABLE"
            )
        return payload

    @staticmethod
    def _database_error_code(error: Exception) -> str:
        message = str(error)
        for code in (
            "CONFIG_BUNDLE_AUTHORITY_DENIED",
            "CONFIG_BUNDLE_INCOMPLETE",
            "CONFIG_BUNDLE_UNKNOWN",
            "CONFIG_SECRET_UNAVAILABLE",
            "CONFIG_REGISTRY_DRIFT",
        ):
            if code in message:
                return code
        return "CONFIG_BUNDLE_UNAVAILABLE"


class AsyncSecretBundleResolver(SecretBundleResolver):
    """Async database variant with the same fixed Bundle contract."""

    async def erp_runtime(self) -> ResolvedConfigurationBundle:
        return await self._resolve_async(
            "erp.runtime",
            "get_erp_runtime_bundle",
        )

    async def wecom_app(self) -> ResolvedConfigurationBundle:
        return await self._resolve_async(
            "wecom.app",
            "get_wecom_app_bundle",
        )

    async def kuaimai_thinktank(self) -> ResolvedConfigurationBundle:
        return await self._resolve_async(
            "kuaimai_external.thinktank",
            "get_kuaimai_thinktank_bundle",
        )

    async def kuaimai_viperp(self) -> ResolvedConfigurationBundle:
        return await self._resolve_async(
            "kuaimai_external.viperp",
            "get_kuaimai_viperp_bundle",
        )

    async def _resolve_async(
        self,
        bundle_name: str,
        rpc_name: str,
        params: Mapping[str, object] | None = None,
    ) -> ResolvedConfigurationBundle:
        try:
            response = await self._db.rpc(
                rpc_name, dict(params) if params is not None else None,
            ).execute()
        except Exception as error:
            raise ConfigurationResolutionError(
                self._database_error_code(error)
            ) from error
        effective = self._effective_resolver.parse(bundle_name, response.data)
        values: dict[str, object | None] = {}
        sources: dict[str, str | None] = {}
        versions: dict[str, int] = {}
        for key, item in effective.items.items():
            sources[key] = item.source
            versions[key] = item.version
            if not item.configured:
                values[key] = None
            elif item.secret_ref is None:
                values[key] = item.value
            else:
                values[key] = self._decrypt_secret(
                    CONFIG_REGISTRY.get(key),
                    item.secret_ref,
                )
        return ResolvedConfigurationBundle(
            name=bundle_name,
            values=MappingProxyType(values),
            sources=MappingProxyType(sources),
            versions=MappingProxyType(versions),
        )


class WecomBotTargetResolver:
    """Discover organizations without secrets, then resolve each exact Bundle."""

    def __init__(
        self,
        worker_db: Any,
        material_service: SecretMaterialService | None = None,
    ) -> None:
        self._worker_db = worker_db
        self._material_service = material_service or SecretMaterialService(
            LocalKEKProvider.from_environment()
        )

    def list_targets(self) -> list[WecomBotTarget]:
        discovery_db = self._scoped_worker_db(
            org_id=None,
            request_id="wecom-bot-discovery",
        )
        response = discovery_db.rpc("discover_wecom_bot_targets").execute()
        targets: list[WecomBotTarget] = []
        for candidate in response.data or []:
            org_id = str(candidate.get("org_id", ""))
            try:
                target = self._resolve_target(org_id)
            except (ConfigurationResolutionError, ValueError) as error:
                logger.warning(
                    "wecom_bot_bundle_unavailable | "
                    f"org_id={org_id or 'invalid'} | "
                    f"error={type(error).__name__}"
                )
                continue
            targets.append(target)
        return targets

    def _resolve_target(self, org_id: str) -> WecomBotTarget:
        execution_db = self._scoped_worker_db(
            org_id=org_id,
            request_id=f"wecom-bot:{org_id}",
        )
        bundle = SecretBundleResolver(
            execution_db,
            self._material_service,
        ).wecom_bot()
        credentials = bundle.values["wecom.bot_credentials"]
        corp_id = bundle.values["wecom.corp_id"]
        if (
            not isinstance(corp_id, str)
            or not corp_id
            or not isinstance(credentials, Mapping)
            or not isinstance(credentials.get("bot_id"), str)
            or not credentials["bot_id"]
            or not isinstance(credentials.get("bot_secret"), str)
            or not credentials["bot_secret"]
        ):
            raise ConfigurationResolutionError(
                "SECRET_MATERIAL_UNAVAILABLE"
            )
        return WecomBotTarget(
            org_id=org_id,
            corp_id=corp_id,
            bot_id=credentials["bot_id"],
            bot_secret=credentials["bot_secret"],
        )

    def _scoped_worker_db(
        self,
        *,
        org_id: str | None,
        request_id: str,
    ) -> ScopedDatabaseClient:
        return ScopedDatabaseClient(
            self._worker_db,
            DatabaseScope(
                actor_user_id=None,
                org_id=org_id,
                access_kind=DatabaseAccessKind.WORKER,
                request_id=request_id,
            ),
        )
