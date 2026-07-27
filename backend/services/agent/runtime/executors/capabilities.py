"""Action-bound capability objects exposed to professional Executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping

from services.agent.runtime.domain.identity import require_stable_value


ReadOperation = Callable[[str, Mapping[str, object]], Awaitable[Mapping[str, object]]]
WorkspaceOperation = Callable[[str], Awaitable[bytes]]
SecretOperation = Callable[[str], Awaitable[str]]
NetworkOperation = Callable[
    [str, str, bytes | None], Awaitable[tuple[int, bytes]]
]


@dataclass(frozen=True, kw_only=True)
class CapabilityBinding:
    action_id: str
    attempt_id: str
    expires_at: datetime
    obligations: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        require_stable_value(self.action_id, "action_id")
        require_stable_value(self.attempt_id, "attempt_id")
        if self.expires_at.tzinfo is None:
            raise ValueError("capability expiry must be timezone-aware")

    def assert_live(self, action_id: str, attempt_id: str) -> None:
        if self.action_id != action_id or self.attempt_id != attempt_id:
            raise PermissionError("CAPABILITY_BINDING_MISMATCH")
        if self.expires_at <= datetime.now(timezone.utc):
            raise PermissionError("CAPABILITY_EXPIRED")


@dataclass(frozen=True, kw_only=True)
class RestrictedDatabaseCapability:
    binding: CapabilityBinding
    allowed_operations: frozenset[str]
    _execute: ReadOperation = field(repr=False)

    async def read(
        self, action_id: str, attempt_id: str,
        operation: str, parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        self.binding.assert_live(action_id, attempt_id)
        if operation not in self.allowed_operations:
            raise PermissionError("DATABASE_OPERATION_NOT_ALLOWED")
        return await self._execute(operation, parameters)


@dataclass(frozen=True, kw_only=True)
class RestrictedWorkspaceCapability:
    binding: CapabilityBinding
    allowed_refs: frozenset[str]
    _read: WorkspaceOperation = field(repr=False)

    async def read_ref(
        self, action_id: str, attempt_id: str, resource_ref: str,
    ) -> bytes:
        self.binding.assert_live(action_id, attempt_id)
        if resource_ref not in self.allowed_refs:
            raise PermissionError("WORKSPACE_REF_NOT_ALLOWED")
        return await self._read(resource_ref)


@dataclass(frozen=True, kw_only=True)
class RestrictedSecretCapability:
    binding: CapabilityBinding
    allowed_handles: frozenset[str]
    _resolve: SecretOperation = field(repr=False)

    async def resolve_handle(
        self, action_id: str, attempt_id: str, handle: str,
    ) -> str:
        self.binding.assert_live(action_id, attempt_id)
        if handle not in self.allowed_handles:
            raise PermissionError("SECRET_HANDLE_NOT_ALLOWED")
        return await self._resolve(handle)


@dataclass(frozen=True, kw_only=True)
class RestrictedNetworkCapability:
    binding: CapabilityBinding
    allowed_origins: frozenset[str]
    allowed_methods: frozenset[str]
    _request: NetworkOperation = field(repr=False)

    async def request(
        self, action_id: str, attempt_id: str, method: str,
        origin: str, body: bytes | None = None,
    ) -> tuple[int, bytes]:
        self.binding.assert_live(action_id, attempt_id)
        normalized_method = method.upper()
        if normalized_method not in self.allowed_methods:
            raise PermissionError("NETWORK_METHOD_NOT_ALLOWED")
        if origin not in self.allowed_origins:
            raise PermissionError("NETWORK_ORIGIN_NOT_ALLOWED")
        return await self._request(normalized_method, origin, body)
