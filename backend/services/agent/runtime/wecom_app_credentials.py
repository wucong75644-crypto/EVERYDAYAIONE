"""CredentialBroker adapter for the WeCom App token provider port."""

from __future__ import annotations

import asyncio
from typing import Optional, Protocol

from services.agent.runtime.credential_broker import CredentialBroker
from services.agent.runtime.domain import RuntimeScope
from services.wecom.app_outbound import (
    APP_CREDENTIAL_TIMEOUT,
    APP_HTTP_TIMEOUT,
    APP_OUTBOUND_CAPACITY,
    AppAccessTokenProvider,
    AppHttpClient,
    WecomAppOutbound,
)


WECOM_APP_PROVIDER = "wecom_app"
WECOM_APP_SEND_PURPOSE = "wecom.app.send"


class TokenExchange(Protocol):
    """Injected secret-material consumer; the implementation owns its schema."""

    operational: bool
    production_ready: bool

    async def exchange(self, material: object) -> Optional[str]: ...


def build_wecom_app_token_provider(
    *,
    broker: CredentialBroker,
    scope: RuntimeScope,
    credential_handle: str,
    provider_revision: str,
    token_exchange: TokenExchange,
) -> AppAccessTokenProvider:
    """Adapt one immutable credential binding to the D2-B2a token provider."""

    async def provide() -> Optional[str]:
        try:
            if not (
                getattr(token_exchange, "operational", False) is True
                and getattr(token_exchange, "production_ready", False) is True
            ):
                return None
            lease = await broker.resolve(
                scope=scope,
                credential_handle=credential_handle,
                provider=WECOM_APP_PROVIDER,
                revision=provider_revision,
                purpose=WECOM_APP_SEND_PURPOSE,
            )

            async def exchange(material: object) -> Optional[str]:
                return await token_exchange.exchange(material)

            token = await lease.use(
                scope=scope,
                provider=WECOM_APP_PROVIDER,
                revision=provider_revision,
                purpose=WECOM_APP_SEND_PURPOSE,
                consumer=exchange,
            )
            return token if _is_token(token) else None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    return provide


def build_runtime_wecom_app_outbound(
    *,
    broker: CredentialBroker,
    scope: RuntimeScope,
    credential_handle: str,
    provider_revision: str,
    token_exchange: TokenExchange,
    outbound_http_client: AppHttpClient,
    capacity: int = APP_OUTBOUND_CAPACITY,
    credential_timeout: float = APP_CREDENTIAL_TIMEOUT,
    post_timeout: float = APP_HTTP_TIMEOUT,
) -> WecomAppOutbound:
    """Compose the adapter with an explicitly supplied App send HTTP client."""
    if outbound_http_client is None or not callable(
        getattr(outbound_http_client, "post", None),
    ):
        raise ValueError("WECOM_APP_HTTP_CLIENT_REQUIRED")
    return WecomAppOutbound(
        token_provider=build_wecom_app_token_provider(
            broker=broker,
            scope=scope,
            credential_handle=credential_handle,
            provider_revision=provider_revision,
            token_exchange=token_exchange,
        ),
        http_client=outbound_http_client,
        capacity=capacity,
        credential_timeout=credential_timeout,
        post_timeout=post_timeout,
    )


def _is_token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


__all__ = [
    "TokenExchange",
    "WECOM_APP_PROVIDER",
    "WECOM_APP_SEND_PURPOSE",
    "build_runtime_wecom_app_outbound",
    "build_wecom_app_token_provider",
]
