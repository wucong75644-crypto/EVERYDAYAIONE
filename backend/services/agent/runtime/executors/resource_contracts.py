"""Isolated Workspace/OSS/Scheduler side-effect contracts."""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Protocol

from services.agent.runtime.executors.materializer import ArtifactMaterializer
from services.agent.runtime.executors.resource_support import (
    checkpoint_fact as _checkpoint_fact,
    ChildRunService,
    child_context as _child_context,
    export_frame as _export_frame,
    phase_object as _phase_object,
    read_frame as _read_frame,
    require_bound as _require_bound,
    resource_params as _resource_params,
    schema as _schema,
    request_source_path as _source_path,
    sync_idempotency_key as _sync_idempotency_key,
    sync_submission_from_facts as _sync_submission_from_facts,
)


class ObjectStore(Protocol):
    async def put_verified(self, key: str, content: bytes, *, content_hash: str) -> Mapping[str, object]: ...
    async def get(self, key: str) -> bytes: ...


@dataclass(frozen=True, kw_only=True)
class ContentAddressedArtifactService:
    root: Path
    staging: Path
    materializer: ArtifactMaterializer
    facts: object | None = None

    async def prepare(self, attempt, request: Mapping[str, object]) -> Mapping[str, object]:
        source = request.get("path") or request.get("source_path")
        if not isinstance(source, str) or not source:
            raise ValueError("ARTIFACT_SOURCE_PATH_REQUIRED")
        raw = Path(source)
        if raw.is_absolute() or ".." in raw.parts:
            raise PermissionError("ARTIFACT_SOURCE_PATH_INVALID")
        path = (self.root / raw).resolve()
        path.relative_to(self.root.resolve())
        content = path.read_bytes()
        checkpoint = self.materializer.checkpoint(content)
        target = self.staging / checkpoint.content_hash
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        result = {"summary": "artifact prepared", "artifact_ref": f"artifact:{checkpoint.content_hash}", "content_hash": checkpoint.content_hash, "byte_size": checkpoint.byte_size, "materialize_status": checkpoint.status}
        await _checkpoint_fact(self.facts, attempt, result, request=request)
        return result


@dataclass(frozen=True, kw_only=True)
class LocalDataService:
    """Read-only local data semantics with content-addressed exports."""

    root: Path
    staging: Path
    materializer: ArtifactMaterializer
    facts: object | None = None

    async def prepare(self, attempt, request: Mapping[str, object]) -> Mapping[str, object]:
        frame = _read_frame(self.root, request)
        mode = str(request.get("mode", "summary"))
        if mode not in {"summary", "detail", "export"}:
            raise ValueError("LOCAL_DATA_MODE_INVALID")
        if mode == "summary":
            return {"mode": mode, "rows": int(len(frame)), "columns": [str(c) for c in frame.columns], "schema": _schema(frame), "sample": frame.head(5).to_dict(orient="records")}
        if mode == "detail":
            limit = min(int(request.get("limit", 100)), 1000)
            return {"mode": mode, "rows": int(len(frame)), "data": frame.head(limit).to_dict(orient="records"), "schema": _schema(frame)}
        return await _export_frame(frame, attempt, request, self.staging, self.materializer, self.facts, "local_data")


@dataclass(frozen=True, kw_only=True)
class FileAnalyzeService:
    """Excel/CSV analysis, Parquet materialization and lineage facts."""

    root: Path
    staging: Path
    materializer: ArtifactMaterializer
    facts: object | None = None

    async def prepare(self, attempt, request: Mapping[str, object]) -> Mapping[str, object]:
        frame = _read_frame(self.root, request)
        return await _export_frame(frame, attempt, request, self.staging, self.materializer, self.facts, "file_analyze", source_path=_source_path(request))


@dataclass(frozen=True, kw_only=True)
class FetchAllPagesService:
    """Registry-bound ERP pagination with bounded concurrency and partial isolation."""

    dispatcher: object
    staging: Path
    materializer: ArtifactMaterializer
    facts: object | None = None
    max_concurrency: int = 4

    async def prepare(self, attempt, request: Mapping[str, object]) -> Mapping[str, object]:
        from services.kuaimai.registry import TOOL_REGISTRIES
        tool = request.get("tool_name")
        action = request.get("action")
        if not isinstance(tool, str) or not isinstance(action, str):
            raise ValueError("ERP_PAGE_ACTION_REQUIRED")
        entry = TOOL_REGISTRIES.get(tool, {}).get(action)
        if entry is None or entry.is_write:
            raise PermissionError("ERP_PAGE_ACTION_NOT_READ_ONLY")
        params = dict(request.get("params", {})) if isinstance(request.get("params"), Mapping) else {}
        total = request.get("total_pages")
        pages = range(max(1, int(total))) if isinstance(total, int) and total > 0 else range(1)
        semaphore = asyncio.Semaphore(max(1, self.max_concurrency))

        async def fetch(page: int) -> tuple[int, object]:
            async with semaphore:
                page_params = {**params, "page": page, "page_size": int(request.get("page_size", 100))}
                result = self.dispatcher.execute(tool, action, page_params)  # type: ignore[attr-defined]
                if hasattr(result, "__await__"):
                    result = await result
                return page, result

        results: list[tuple[int, object]] = []
        errors: list[Mapping[str, object]] = []
        try:
            results = list(await asyncio.gather(*(fetch(page) for page in pages)))
        except Exception as exc:
            errors.append({"type": type(exc).__name__, "message": str(exc)})
        rows = [value for _, value in sorted(results)]
        payload = json.dumps(rows, ensure_ascii=False, default=str).encode()
        checkpoint = self.materializer.checkpoint(payload, partial=bool(errors))
        target = self.staging / checkpoint.content_hash
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        result = {"state": "partial" if errors else "completed", "pages": len(rows), "page_checkpoint": checkpoint.content_hash, "partial_errors": errors, "artifact_ref": f"artifact:{checkpoint.content_hash}", "lineage": {"tool_name": tool, "action": action}}
        await _checkpoint_fact(self.facts, attempt, result, request=request)
        return result


class SchedulerStore(Protocol):
    async def cas(self, task_id: str, expected_version: int, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True, kw_only=True)
class WorkspaceResourceService:
    root: Path
    staging: Path
    objects: ObjectStore
    facts: object | None = None

    async def delete(self, resource_id: str, relative_path: str, oss_key: str, *, attempt: object | None = None) -> Mapping[str, object]:
        await self._bind(resource_id, "file_delete", attempt)
        source = self._safe(relative_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError("WORKSPACE_RESOURCE_NOT_FOUND")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        backup = await self.objects.put_verified(oss_key, content, content_hash=digest)
        if backup.get("content_hash") != digest or backup.get("verified") is not True:
            raise RuntimeError("OSS_RETENTION_NOT_VERIFIED")
        tombstone = self.staging / "deleted" / resource_id
        tombstone.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, tombstone)
        result = {"resource_id": resource_id, "content_hash": digest, "oss_key": oss_key, "state": "completed", "oss_retention_verified": True}
        return result

    async def restore(self, resource_id: str, relative_path: str, oss_key: str, *, attempt: object | None = None) -> Mapping[str, object]:
        await self._bind(resource_id, "restore_file", attempt)
        destination = self._safe(relative_path)
        tombstone = self.staging / "deleted" / resource_id
        content = await self.objects.get(oss_key) if not tombstone.exists() else tombstone.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(f".{destination.name}.{resource_id}.restore")
        temp.write_bytes(content)
        os.replace(temp, destination)
        if tombstone.exists():
            tombstone.unlink()
        result = {"resource_id": resource_id, "content_hash": digest, "state": "completed"}
        return result

    async def _bind(self, resource_id: str, operation: str, attempt: object | None) -> None:
        if self.facts is None or attempt is None:
            return
        params = _resource_params(attempt, operation, resource_id)
        bound = await self.facts.mutate_resource(operation, **params)
        _require_bound(bound)

    def _safe(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or ".." in raw.parts or "\\" in relative_path:
            raise PermissionError("WORKSPACE_RESOURCE_PATH_INVALID")
        target = (self.root / raw).resolve()
        target.relative_to(self.root.resolve())
        if any(part in {".env", ".git", ".ssh", "staging"} for part in raw.parts):
            raise PermissionError("WORKSPACE_RESOURCE_PATH_BLOCKED")
        return target


@dataclass(frozen=True, kw_only=True)
class ScheduledTaskService:
    store: SchedulerStore
    facts: object | None = None

    async def mutate(self, task_id: str, expected_version: int, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        if operation not in {"create", "update", "delete", "pause", "resume", "list"}:
            raise ValueError("SCHEDULED_OPERATION_INVALID")
        attempt = payload.get("_attempt")
        if self.facts is not None and attempt is not None:
            bound = await self.facts.mutate_resource("manage_scheduled_task", **_resource_params(attempt, operation, task_id, payload, expected_version))
            _require_bound(bound)
        result = await self.store.cas(task_id, expected_version, operation, payload)
        return result


@dataclass(frozen=True, kw_only=True)
class ErpSyncService:
    """Explicit sync phases; an incomplete phase never becomes terminal."""

    provider: object
    local_apply: Callable[[Mapping[str, object]], object]
    checkpoint_store: Callable[[Mapping[str, object]], object]
    facts: object | None = None

    async def run(self, request: Mapping[str, object], attempt: object) -> Mapping[str, object]:
        facts = await self._read_facts(attempt)
        checkpointed = facts.get("checkpointed")
        if isinstance(checkpointed, Mapping):
            checkpoint = dict(checkpointed.get("checkpoint", {}))
            return {"state": "completed", "checkpoint": checkpoint}
        submission = await self._read_submission(attempt)
        if submission is None:
            submission = await self._durable_submit_or_recover(request, attempt)
            if submission.get("state") == "unknown":
                if not isinstance(facts.get("submitted"), Mapping):
                    await self._phase(attempt, "submitted", submission)
                await self._phase(attempt, "unknown", submission)
                return submission
            if not isinstance(facts.get("submitted"), Mapping):
                await self._phase(attempt, "submitted", submission)
        progress = await self.progress(submission)
        await self._phase(attempt, "progressing", progress)
        if progress.get("state") not in {"completed", "ready"}:
            unknown = {"state": "unknown", "submission": dict(submission), "progress": dict(progress), "checkpoint": {}}
            await self._phase(attempt, "unknown", unknown)
            return unknown
        try:
            applying = facts.get("applying")
            applied = (
                dict(applying.get("checkpoint", {}))
                if isinstance(applying, Mapping) and isinstance(applying.get("checkpoint"), Mapping)
                else await self.apply(progress)
            )
            await self._phase(attempt, "applying", {"checkpoint": applied, "progress": dict(progress)})
            checkpoint = await self.checkpoint(applied)
        except Exception as exc:
            unknown = {"state": "unknown", "submission": dict(submission), "progress": dict(progress), "checkpoint": {"error": type(exc).__name__}}
            return unknown
        await self._phase(attempt, "checkpointed", checkpoint)
        completed = {"state": "completed", "submission": dict(submission), "progress": dict(progress), "apply": dict(applied), "checkpoint": dict(checkpoint)}
        await self._phase(attempt, "completed", completed)
        return completed

    async def _phase(self, attempt: object, phase: str, value: Mapping[str, object], *, ownership_token: str | None = None) -> None:
        if self.facts is not None and hasattr(self.facts, "sync_phase"):
            ownership_token = ownership_token or str(attempt.lease.fencing_token)
            await self.facts.sync_phase(
                p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id),
                p_ownership_token=ownership_token,
                p_expected_state_version=int(getattr(attempt, "state_version", 1)),
                p_lease_expires_at=attempt.lease.expires_at,
                p_request_hash=str(attempt.request_hash), p_phase=phase,
                p_checkpoint=dict(value.get("checkpoint", value)),
                p_provider_receipt={**dict(value.get("provider_receipt", value)), **({"reconciliation_token": ownership_token} if ownership_token else {})},
            )

    async def _read_submission(self, attempt: object) -> Mapping[str, object] | None:
        if self.facts is None or not hasattr(self.facts, "read_sync_facts"):
            return None
        facts = await self.facts.read_sync_facts(
            p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id),
            p_request_hash=str(attempt.request_hash),
        )
        if not isinstance(facts, Mapping):
            raise RuntimeError("ERP_SYNC_FACT_READBACK_INVALID")
        for phase in ("unknown", "submitted", "progressing", "applying", "checkpointed"):
            value = facts.get(phase)
            if isinstance(value, Mapping):
                candidate = value.get("submission") or value.get("checkpoint")
                if isinstance(candidate, Mapping):
                    if candidate.get("provider_task_ref") or candidate.get("submission"):
                        return candidate.get("submission", candidate)
        return None

    async def _durable_submit_or_recover(self, request: Mapping[str, object], attempt: object) -> Mapping[str, object]:
        if self.facts is None or not hasattr(self.facts, "create_or_get_sync_submission"):
            return await self.submit(request)
        key = _sync_idempotency_key(attempt, request)
        domain = str(request.get("domain", "default"))
        scope_id = str(request.get("scope_id", getattr(getattr(attempt, "scope", None), "scope_id", "")))
        identity = await self.facts.create_or_get_sync_submission(
            p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id),
            p_request_hash=str(attempt.request_hash), p_scope_id=scope_id,
            p_sync_domain=domain, p_external_idempotency_key=key, p_provider="erp_sync",
        )
        task_ref = identity.get("provider_task_ref")
        if isinstance(task_ref, str) and task_ref:
            return {"state": "accepted", "provider_task_ref": task_ref, "external_idempotency_key": key}
        recovered = await self._recover_submission(key, attempt, identity)
        if recovered.get("state") == "accepted":
            return recovered
        if recovered.get("state") == "unknown":
            return recovered
        await self.facts.record_sync_submission_result(
            p_submission_id=str(identity["submission_id"]), p_external_idempotency_key=key,
            p_request_hash=str(attempt.request_hash), p_provider_task_ref="",
            p_submission_state="unknown", p_enqueue_checkpoint={"state": "dispatching"},
        )
        try:
            result = await self.submit_or_get(request, key)
        except Exception as exc:
            recovered = await self._recover_submission(key, attempt, identity)
            if recovered.get("state") != "accepted":
                return {"state": "unknown", "external_idempotency_key": key, "evidence": {"error_code": "ERP_SYNC_SUBMIT_RESPONSE_UNKNOWN", "type": type(exc).__name__}}
            return recovered
        result["external_idempotency_key"] = key
        await self.facts.record_sync_submission_result(
            p_submission_id=str(identity["submission_id"]), p_external_idempotency_key=key,
            p_request_hash=str(attempt.request_hash), p_provider_task_ref=str(result.get("provider_task_ref", "")),
            p_submission_state="found", p_enqueue_checkpoint={"state": "submission_recorded"},
        )
        return result

    async def _recover_submission(self, key: str, attempt: object, identity: Mapping[str, object]) -> Mapping[str, object]:
        recovered = await self.facts.recover_sync_submission(
            p_external_idempotency_key=key, p_request_hash=str(attempt.request_hash),
        )
        if recovered.get("outcome") == "found" and recovered.get("provider_task_ref"):
            return {"state": "accepted", "provider_task_ref": recovered["provider_task_ref"], "external_idempotency_key": key}
        if recovered.get("outcome") == "unknown" and hasattr(self.provider, "recover_submission"):
            result = self.provider.recover_submission(idempotency_key=key)  # type: ignore[attr-defined]
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, Mapping) and result.get("outcome") == "FOUND" and result.get("provider_task_ref"):
                await self.facts.record_sync_submission_result(
                    p_submission_id=str(identity["submission_id"]), p_external_idempotency_key=key,
                    p_request_hash=str(attempt.request_hash), p_provider_task_ref=str(result["provider_task_ref"]),
                    p_submission_state="found", p_enqueue_checkpoint={"state": "recovered"},
                )
                return {"state": "accepted", "provider_task_ref": result["provider_task_ref"], "external_idempotency_key": key}
            if isinstance(result, Mapping) and result.get("outcome") == "PROVEN_NOT_SUBMITTED":
                await self.facts.record_sync_submission_result(
                    p_submission_id=str(identity["submission_id"]), p_external_idempotency_key=key,
                    p_request_hash=str(attempt.request_hash), p_provider_task_ref="",
                    p_submission_state="proven_not_submitted", p_enqueue_checkpoint={"state": "proven_not_submitted"},
                )
                return {"state": "proven_not_submitted", "external_idempotency_key": key}
        if recovered.get("outcome") == "proven_not_submitted":
            return {"state": "proven_not_submitted", "external_idempotency_key": key}
        return {"state": "unknown", "external_idempotency_key": key, "evidence": {"error_code": "ERP_SYNC_SUBMISSION_RECOVERY_UNKNOWN"}}

    async def submit(self, request: Mapping[str, object]) -> Mapping[str, object]:
        result = self.provider.submit(request)  # type: ignore[attr-defined]
        if hasattr(result, "__await__"):
            result = await result
        return _phase_object(result, "ERP_SYNC_SUBMISSION_INVALID")

    async def submit_or_get(self, request: Mapping[str, object], idempotency_key: str) -> Mapping[str, object]:
        if not hasattr(self.provider, "submit_or_get"):
            raise RuntimeError("ERP_SYNC_SUBMIT_OR_GET_CONTRACT_REQUIRED")
        result = self.provider.submit_or_get(request, idempotency_key=idempotency_key)  # type: ignore[attr-defined]
        if hasattr(result, "__await__"):
            result = await result
        return _phase_object(result, "ERP_SYNC_SUBMIT_OR_GET_INVALID")

    async def progress(self, submission: Mapping[str, object]) -> Mapping[str, object]:
        if not hasattr(self.provider, "progress"):
            raise RuntimeError("ERP_SYNC_PROGRESS_UNAVAILABLE")
        result = self.provider.progress(submission)  # type: ignore[attr-defined]
        if hasattr(result, "__await__"):
            result = await result
        return _phase_object(result, "ERP_SYNC_PROGRESS_INVALID")

    async def apply(self, progress: Mapping[str, object]) -> Mapping[str, object]:
        result = self.local_apply(progress)
        if hasattr(result, "__await__"):
            result = await result
        return _phase_object(result, "ERP_SYNC_APPLY_INVALID")

    async def checkpoint(self, applied: Mapping[str, object]) -> Mapping[str, object]:
        result = self.checkpoint_store(applied)
        if hasattr(result, "__await__"):
            result = await result
        return _phase_object(result, "ERP_SYNC_CHECKPOINT_INVALID")

    async def reconcile(self, attempt: object, receipt: Mapping[str, object], *, operation: str) -> Mapping[str, object]:
        """Read provider status from durable submission identity; never resubmit."""
        facts = await self._read_facts(attempt)
        submission = receipt.get("submission") or receipt.get("provider_task_ref")
        if not isinstance(submission, (Mapping, str)):
            submission = _sync_submission_from_facts(facts)
        if isinstance(submission, str):
            submission = {"provider_task_ref": submission}
        if not isinstance(submission, Mapping):
            return {"state": "unknown", "evidence": {"error_code": "ERP_SYNC_SUBMISSION_IDENTITY_MISSING"}}
        progress = await self.progress(submission)
        if progress.get("state") not in {"completed", "ready"}:
            return {"state": "accepted", "submission": dict(submission), "progress": dict(progress)}
        ownership_token = receipt.get("reconciliation_token")
        if not isinstance(ownership_token, str):
            ownership_token = None
        if "checkpointed" not in facts:
            await self._phase(attempt, "applying", progress, ownership_token=ownership_token)
            applied = await self.apply(progress)
            checkpoint = await self.checkpoint(applied)
            await self._phase(attempt, "checkpointed", checkpoint, ownership_token=ownership_token)
        completed = {"state": "completed", "submission": dict(submission), "progress": dict(progress)}
        await self._phase(attempt, "completed", completed, ownership_token=ownership_token)
        return completed

    async def _read_facts(self, attempt: object) -> Mapping[str, object]:
        if self.facts is None or not hasattr(self.facts, "read_sync_facts"):
            return {}
        result = await self.facts.read_sync_facts(
            p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id),
            p_request_hash=str(attempt.request_hash),
        )
        return result if isinstance(result, Mapping) else {}


@dataclass(frozen=True, kw_only=True)
class RuntimeResourceMutationService:
    """Concrete adapter joining Workspace/OSS and Scheduler services."""

    workspace: WorkspaceResourceService
    scheduler: ScheduledTaskService
    sync: object | None = None
    facts: object | None = None

    async def mutate(self, attempt, request: Mapping[str, object], *, operation: str) -> Mapping[str, object]:
        if operation == "file_delete":
            return await self.workspace.delete(str(request["resource_id"]), str(request["relative_path"]), str(request["oss_key"]), attempt=attempt)
        if operation == "restore_file":
            return await self.workspace.restore(str(request["resource_id"]), str(request["relative_path"]), str(request["oss_key"]), attempt=attempt)
        if operation == "manage_scheduled_task":
            return await self.scheduler.mutate(
                str(request["task_id"]), int(request["state_version"]),
                str(request["operation"]), {**request, "_attempt": attempt},
            )
        if operation == "trigger_erp_sync" and self.sync is not None:
            if hasattr(self.sync, "run"):
                return await self.sync.run(request, attempt)  # type: ignore[attr-defined]
            if not all(hasattr(self.sync, name) for name in ("submit", "progress", "apply", "checkpoint")):
                raise RuntimeError("ERP_SYNC_PHASE_CONTRACT_REQUIRED")
            submitted = await self.sync.submit(request)  # type: ignore[attr-defined]
            progress = await self.sync.progress(submitted)  # type: ignore[attr-defined]
            applied = await self.sync.apply(progress)  # type: ignore[attr-defined]
            checkpoint = await self.sync.checkpoint(applied)  # type: ignore[attr-defined]
            result = {"state": "completed", "submission": submitted, "progress": progress, "apply": applied, "checkpoint": checkpoint}
            return result
        raise ValueError("RUNTIME_RESOURCE_OPERATION_INVALID")


__all__ = ["ChildRunService", "ContentAddressedArtifactService", "ErpSyncService", "FetchAllPagesService", "FileAnalyzeService", "LocalDataService", "ObjectStore", "RuntimeResourceMutationService", "ScheduledTaskService", "SchedulerStore", "WorkspaceResourceService"]
