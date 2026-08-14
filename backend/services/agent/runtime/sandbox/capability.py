"""Attempt-scoped capability exposed to the Sandbox professional Executor."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from services.agent.runtime.executors.capabilities import CapabilityBinding
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobReceipt,
    SandboxJobRepositoryPort,
)

from .contracts import SandboxResourceLimits
from .workspace import SandboxWorkspaceStore


@dataclass(frozen=True, kw_only=True)
class SandboxJobCapability:
    binding: CapabilityBinding
    _jobs: SandboxJobRepositoryPort = field(repr=False)
    _workspace: SandboxWorkspaceStore = field(repr=False)
    runtime_revision: str
    allowed_operations: frozenset[str]
    allowed_artifact_refs: frozenset[str] = field(default_factory=frozenset)
    _read_artifact: Callable[[str], Awaitable[bytes]] | None = field(
        default=None, repr=False,
    )

    async def submit(
        self, *, action_id: str, attempt_id: str,
        dispatch_intent_id: str, expected_action_version: int,
        expected_attempt_version: int, external_idempotency_key: str,
        request_hash: str, executor_type: str, executor_revision: int,
        workspace_scope_ref: str, code: str,
        input_manifest: Mapping[str, object],
        resource_limits: SandboxResourceLimits,
    ) -> SandboxJobReceipt:
        self._assert_operation("submit")
        self.binding.assert_live(action_id, attempt_id)
        encoded = code.encode("utf-8")
        if not encoded:
            raise ValueError("SANDBOX_CODE_REQUIRED")
        digest = hashlib.sha256(encoded).hexdigest()
        await self._workspace.stage_code(
            action_id=action_id, attempt_id=attempt_id,
            content=encoded, expected_sha256=digest,
        )
        for item in _manifest_items(input_manifest):
            reference = str(item["artifact_ref"])
            if (
                reference not in self.allowed_artifact_refs
                or self._read_artifact is None
            ):
                raise PermissionError("SANDBOX_INPUT_REF_NOT_ALLOWED")
            content = await self._read_artifact(reference)
            expected = str(item["content_sha256"])
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("SANDBOX_INPUT_HASH_CONFLICT")
            if len(content) != int(item["size_bytes"]):
                raise ValueError("SANDBOX_INPUT_SIZE_CONFLICT")
            await self._workspace.stage_artifact(
                action_id=action_id, attempt_id=attempt_id,
                reference=reference,
                content=content, expected_sha256=expected,
            )
        return await self._jobs.create_or_get(
            action_id=action_id,
            attempt_id=attempt_id,
            dispatch_intent_id=dispatch_intent_id,
            expected_action_version=expected_action_version,
            expected_attempt_version=expected_attempt_version,
            external_idempotency_key=external_idempotency_key,
            request_hash=request_hash,
            executor_type=executor_type,
            executor_revision=executor_revision,
            runtime_revision=self.runtime_revision,
            workspace_scope_ref=workspace_scope_ref,
            code_sha256=digest,
            input_manifest=input_manifest,
            resource_limits=resource_limits.as_dict(),
        )

    async def get(
        self, *, action_id: str, attempt_id: str, job_id: str,
    ) -> SandboxJobReceipt:
        self._assert_operation("get")
        self.binding.assert_live(action_id, attempt_id)
        return await self._jobs.get(job_id=job_id)

    async def readback_after_submit_loss(
        self, *, action_id: str, attempt_id: str,
        request_hash: str, binding: Mapping[str, object],
    ) -> SandboxJobReceipt:
        self._assert_operation("readback")
        self.binding.assert_live(action_id, attempt_id)
        if (
            binding.get("action_id") != action_id
            or binding.get("attempt_id") != attempt_id
            or binding.get("request_hash") != request_hash
        ):
            raise ValueError("SANDBOX_DISPATCH_BINDING_CONFLICT")
        return await self._jobs.readback_by_binding(
            external_idempotency_key=_fact(
                binding, "external_idempotency_key",
            ),
            action_id=action_id, attempt_id=attempt_id,
            dispatch_intent_id=_fact(binding, "dispatch_intent_id"),
            request_hash=request_hash,
            org_id=_optional_fact(binding, "org_id"),
            user_id=_optional_fact(binding, "user_id"),
            session_id=_fact(binding, "session_id"),
            run_id=_fact(binding, "run_id"),
            executor_type=_fact(binding, "executor_type"),
            executor_revision=_positive(binding, "executor_revision"),
            runtime_revision=_fact(binding, "runtime_revision"),
        )

    async def request_cancel(
        self, *, action_id: str, attempt_id: str,
        job_id: str, expected_version: int,
    ) -> SandboxJobReceipt:
        self._assert_operation("cancel")
        self.binding.assert_live(action_id, attempt_id)
        return await self._jobs.request_cancel(
            job_id=job_id, expected_version=expected_version,
        )

    async def request_runtime_cancel(
        self, *, action_id: str, attempt_id: str, job_id: str,
        reconciliation_token: str, expected_action_state_version: int,
        request_hash: str,
    ) -> SandboxJobReceipt:
        self._assert_operation("cancel")
        self.binding.assert_live(action_id, attempt_id)
        return await self._jobs.request_runtime_cancel(
            job_id=job_id, attempt_id=attempt_id,
            reconciliation_token=reconciliation_token,
            expected_action_state_version=expected_action_state_version,
            request_hash=request_hash,
        )

    def cleanup_staged_attempt(
        self, *, action_id: str, attempt_id: str,
    ) -> bool:
        self._assert_operation("cleanup")
        self.binding.assert_live(action_id, attempt_id)
        return self._workspace.cleanup_staged_attempt(
            action_id=action_id, attempt_id=attempt_id,
        )

    def _assert_operation(self, operation: str) -> None:
        if operation not in self.allowed_operations:
            raise PermissionError("SANDBOX_CAPABILITY_OPERATION_NOT_ALLOWED")


def _manifest_items(manifest: Mapping[str, object]) -> tuple[Mapping, ...]:
    items = manifest.get("items")
    if manifest.get("schema_revision") != 1 or not isinstance(items, list):
        raise ValueError("SANDBOX_INPUT_MANIFEST_INVALID")
    if any(not isinstance(item, Mapping) for item in items):
        raise ValueError("SANDBOX_INPUT_MANIFEST_INVALID")
    return tuple(items)


def _fact(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"SANDBOX_{field.upper()}_REQUIRED")
    return item


def _optional_fact(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    return None if item is None else _fact(value, field)


def _positive(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"SANDBOX_{field.upper()}_REQUIRED")
    return item
