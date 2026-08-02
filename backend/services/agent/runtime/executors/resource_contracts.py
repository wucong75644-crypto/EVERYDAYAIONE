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
        submission = await self._read_submission(attempt)
        if submission is None:
            submission = await self.submit(request)
            await self._phase(attempt, "submitted", submission)
        progress = await self.progress(submission)
        await self._phase(attempt, "progressing", progress)
        if progress.get("state") not in {"completed", "ready"}:
            unknown = {"state": "unknown", "submission": dict(submission), "progress": dict(progress), "checkpoint": {}}
            await self._phase(attempt, "unknown", unknown)
            return unknown
        await self._phase(attempt, "applying", progress)
        try:
            applied = await self.apply(progress)
            checkpoint = await self.checkpoint(applied)
        except Exception as exc:
            unknown = {"state": "unknown", "submission": dict(submission), "progress": dict(progress), "checkpoint": {"error": type(exc).__name__}}
            await self._phase(attempt, "unknown", unknown)
            return unknown
        await self._phase(attempt, "checkpointed", checkpoint)
        completed = {"state": "completed", "submission": dict(submission), "progress": dict(progress), "apply": dict(applied), "checkpoint": dict(checkpoint)}
        await self._phase(attempt, "completed", completed)
        return completed

    async def _phase(self, attempt: object, phase: str, value: Mapping[str, object]) -> None:
        if self.facts is not None and hasattr(self.facts, "sync_phase"):
            await self.facts.sync_phase(
                p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id),
                p_execution_token=str(attempt.lease.fencing_token),
                p_request_hash=str(attempt.request_hash), p_phase=phase,
                p_checkpoint=dict(value.get("checkpoint", value)),
                p_provider_receipt=dict(value.get("provider_receipt", value)),
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

    async def submit(self, request: Mapping[str, object]) -> Mapping[str, object]:
        result = self.provider.submit(request)  # type: ignore[attr-defined]
        if hasattr(result, "__await__"):
            result = await result
        return _phase_object(result, "ERP_SYNC_SUBMISSION_INVALID")

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
        submission = receipt.get("submission") or receipt.get("provider_task_ref")
        if isinstance(submission, str):
            submission = {"provider_task_ref": submission}
        if not isinstance(submission, Mapping):
            return {"state": "unknown", "evidence": {"error_code": "ERP_SYNC_SUBMISSION_IDENTITY_MISSING"}}
        progress = await self.progress(submission)
        return {"state": "completed" if progress.get("state") in {"completed", "ready"} else "accepted", "submission": dict(submission), "progress": dict(progress)}


@dataclass(frozen=True, kw_only=True)
class ChildRunService:
    """Repository-backed child-run lifecycle with readback and fencing."""

    repository: object

    async def create(self, attempt, request: Mapping[str, object]) -> Mapping[str, object]:
        if not attempt.run_id:
            raise ValueError("CHILD_PARENT_RUN_REQUIRED")
        result = await self.repository.create_child_run(
            p_parent_run_id=attempt.run_id, p_parent_action_id=str(attempt.action_id),
            p_parent_request_hash=attempt.request_hash,
            p_parent_execution_token=str(attempt.lease.fencing_token),
            p_child_ordinal=int(request["child_ordinal"]),
            p_capability=str(request["capability"]),
            p_context=_child_context(attempt, request),
        )
        created = _phase_object(result, "CHILD_RUN_CREATE_INVALID")
        if created.get("outcome") in {"created", "already_exists"}:
            return {**created, "state": "accepted", "provider_task_ref": str(created.get("child_run_id"))}
        return created

    async def readback(self, attempt, receipt: Mapping[str, object]) -> Mapping[str, object]:
        child_id = receipt.get("child_run_id")
        if not child_id or not attempt.run_id:
            return {"state": "unknown", "evidence": {"error_code": "CHILD_RUN_READBACK_BINDING_MISSING"}}
        result = await self.repository.read_child_run(child_run_id=str(child_id), parent_run_id=attempt.run_id, parent_action_id=str(attempt.action_id), parent_request_hash=attempt.request_hash)
        readback = _phase_object(result, "CHILD_RUN_READBACK_INVALID")
        return {**readback, "state": str(readback.get("status", "unknown"))}

    async def complete(self, attempt, receipt: Mapping[str, object], result: Mapping[str, object]) -> Mapping[str, object]:
        child_id = receipt.get("child_run_id")
        if not child_id or not attempt.run_id:
            raise ValueError("CHILD_RUN_COMPLETE_BINDING_MISSING")
        return await self.repository.complete_child_run(
            p_child_run_id=str(child_id), p_parent_run_id=attempt.run_id,
            p_parent_action_id=str(attempt.action_id), p_parent_request_hash=attempt.request_hash,
            p_aggregation_revision=int(result.get("aggregation_revision", 1)),
            p_result=dict(result),
        )

    async def cancel(self, attempt, receipt: Mapping[str, object]) -> Mapping[str, object]:
        child_id = receipt.get("child_run_id")
        if not child_id or not attempt.run_id:
            return {"state": "unknown", "evidence": {"error_code": "CHILD_RUN_CANCEL_BINDING_MISSING"}}
        result = await self.repository.cancel_child_run(
            p_child_run_id=str(child_id), p_parent_run_id=attempt.run_id,
            p_parent_action_id=str(attempt.action_id), p_parent_request_hash=attempt.request_hash,
            p_reason=str(receipt.get("cancel_reason", "parent_cancel")),
        )
        if isinstance(result, Mapping):
            return {**result, "fencing_confirmed": result.get("outcome") == "cancelled"}
        return {"state": "unknown", "evidence": {"error_code": "CHILD_RUN_CANCEL_INVALID"}}


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


def _source_path(request: Mapping[str, object]) -> str:
    value = request.get("path") or request.get("source_path")
    if not isinstance(value, str) or not value:
        raise ValueError("ARTIFACT_SOURCE_PATH_REQUIRED")
    return value


def _phase_object(value: object, error: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(error)
    if value.get("state") in {"unknown", "failed"}:
        raise RuntimeError("ERP_SYNC_PHASE_NOT_TERMINAL")
    return dict(value)


def _child_context(attempt: object, request: Mapping[str, object]) -> dict[str, object]:
    supplied = request.get("context")
    if not isinstance(supplied, Mapping):
        supplied = {}
    context = dict(supplied)
    context.setdefault("policy_receipt_id", request.get("policy_receipt_id"))
    context.setdefault("capability", request.get("capability"))
    context.setdefault("budget_remaining", request.get("budget_remaining", 0))
    context.setdefault("scope", {"org_id": attempt.scope.org_id, "user_id": attempt.scope.user_id})
    if not context.get("policy_receipt_id") or not context.get("capability"):
        raise ValueError("CHILD_VERIFIED_CONTEXT_REQUIRED")
    return context


def _read_frame(root: Path, request: Mapping[str, object]):
    import pandas as pd
    source = Path(_source_path(request))
    if source.is_absolute() or ".." in source.parts:
        raise PermissionError("ARTIFACT_SOURCE_PATH_INVALID")
    path = (root / source).resolve()
    path.relative_to(root.resolve())
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",")
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=request.get("sheet_name", 0))
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".json":
        return pd.read_json(path)
    raise ValueError("ARTIFACT_FORMAT_UNSUPPORTED")


def _schema(frame) -> list[Mapping[str, str]]:
    return [{"name": str(name), "dtype": str(dtype)} for name, dtype in frame.dtypes.items()]


async def _export_frame(frame, attempt, request, staging: Path, materializer: ArtifactMaterializer, facts: object | None, role: str, *, source_path: str | None = None) -> Mapping[str, object]:
    import io
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    content = buffer.getvalue()
    checkpoint = materializer.checkpoint(content)
    target = staging / checkpoint.content_hash
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    result = {"state": "completed", "artifact_ref": f"artifact:{checkpoint.content_hash}", "content_hash": checkpoint.content_hash, "byte_size": checkpoint.byte_size, "schema": _schema(frame), "rows": int(len(frame)), "lineage": {"role": role, "source_path": source_path or _source_path(request), "output_format": "parquet"}}
    await _checkpoint_fact(facts, attempt, result, request=request)
    return result


async def _checkpoint_fact(facts: object | None, attempt: object, result: Mapping[str, object], *, request: Mapping[str, object] | None = None) -> None:
    if facts is None or not hasattr(facts, "checkpoint_materialization"):
        return
    artifact_id = (request or {}).get("artifact_id") or result.get("artifact_id")
    if artifact_id is not None and hasattr(facts, "link_artifact"):
        await facts.link_artifact(
            p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id),
            p_artifact_id=str(artifact_id), p_role=str(result.get("lineage", {}).get("role", "materialized")),
            p_parent_artifact_id=None, p_content_hash=str(result["content_hash"]),
            p_materialize_revision=1, p_materialize_status=str(result.get("state", "materialized")),
            p_sensitivity="normal",
        )
    if artifact_id is None:
        return
    await facts.checkpoint_materialization(action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id), artifact_id=str(artifact_id), materialize_revision=1, materialize_status=str(result.get("state", result.get("materialize_status", "materialized"))))


def _resource_params(attempt: object, operation: str, resource_id: str, payload: Mapping[str, object] | None = None, expected_version: int = 0) -> dict[str, object]:
    params = {"p_action_id": str(attempt.action_id), "p_attempt_id": str(attempt.attempt_id), "p_request_hash": str(attempt.request_hash), "p_idempotency_key": str(attempt.idempotency_key), "p_execution_token": str(attempt.lease.fencing_token)}
    if operation == "manage_scheduled_task":
        params.update({"p_task_id": resource_id, "p_expected_state_version": expected_version, "p_payload": dict(payload or {})})
    else:
        params["p_deleted_file_id"] = int(resource_id)
    return params


def _require_bound(value: object) -> None:
    if not isinstance(value, Mapping) or value.get("outcome") not in {"bound", "updated", "already_exists"}:
        raise RuntimeError("RUNTIME_RESOURCE_INTENT_UNPROVEN")




__all__ = ["ChildRunService", "ContentAddressedArtifactService", "ErpSyncService", "FetchAllPagesService", "FileAnalyzeService", "LocalDataService", "ObjectStore", "RuntimeResourceMutationService", "ScheduledTaskService", "SchedulerStore", "WorkspaceResourceService"]
