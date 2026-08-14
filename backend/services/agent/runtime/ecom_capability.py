"""Runtime-owned boundary for e-commerce image capabilities."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class RuntimeEcomCapabilityUnavailable(RuntimeError):
    """The e-commerce capability is not enabled in the Runtime composition."""


class RuntimeEcomNonTerminal(RuntimeError):
    """Runtime has not produced a terminal model result yet."""

    def __init__(self, status: str, reason_code: str | None = None) -> None:
        self.status = status
        self.reason_code = reason_code
        super().__init__(f"RUNTIME_ECOM_MODEL_{status.upper()}")


class RuntimeEcomCapabilityPort(Protocol):
    ready: bool

    async def readback(self, **kwargs: Any) -> Any:
        """Read the authoritative Runtime result without dispatching."""

    async def submit_model(self, **kwargs: Any) -> Any:
        """Submit a durable e-commerce ModelLoop request to Runtime."""

    async def invoke_model(
        self, *, messages: list[Mapping[str, Any]], model: str,
        timeout_seconds: float, org_id: str | None,
    ) -> Any:
        """Run an e-commerce planning ModelStep owned by Runtime."""

    async def invoke_image(
        self, *, prompt: str, image_urls: list[str], model: str,
        org_id: str | None, **options: Any,
    ) -> Any:
        """Run an e-commerce image Action/Child Run owned by Runtime."""


class DisabledRuntimeEcomCapability:
    ready = False

    """Explicit fail-closed implementation used until composition is wired."""

    async def readback(self, **_: Any) -> Any:
        raise RuntimeEcomCapabilityUnavailable(
            "RUNTIME_ECOM_MODEL_READBACK_UNAVAILABLE"
        )

    async def submit_model(self, **_: Any) -> Any:
        raise RuntimeEcomCapabilityUnavailable(
            "RUNTIME_ECOM_MODEL_INGRESS_UNAVAILABLE"
        )

    async def invoke_model(self, **_: Any) -> Any:
        raise RuntimeEcomCapabilityUnavailable(
            "RUNTIME_ECOM_MODEL_INGRESS_UNAVAILABLE"
        )

    async def invoke_image(self, **_: Any) -> Any:
        raise RuntimeEcomCapabilityUnavailable(
            "RUNTIME_ECOM_IMAGE_ACTION_UNAVAILABLE"
        )


def get_runtime_ecom_capability() -> RuntimeEcomCapabilityPort:
    """Return the only allowed e-commerce capability owner.

    A production implementation will be injected by Runtime composition;
    silently constructing a Provider adapter here is intentionally forbidden.
    """
    return DisabledRuntimeEcomCapability()


__all__ = [
    "DisabledRuntimeEcomCapability",
    "RuntimeEcomCapabilityPort",
    "RuntimeEcomCapabilityUnavailable",
    "RuntimeEcomNonTerminal",
    "get_runtime_ecom_capability",
]
