"""Private helpers and child-run adapter for resource executors."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from dataclasses import dataclass
from typing import Mapping


def request_source_path(request: Mapping[str, object]) -> str:
    value = request.get("path") or request.get("source_path")
    if not isinstance(value, str) or not value:
        raise ValueError("ARTIFACT_SOURCE_PATH_REQUIRED")
    return value


def phase_object(value: object, error: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(error)
    if value.get("state") in {"unknown", "failed"}:
        raise RuntimeError("ERP_SYNC_PHASE_NOT_TERMINAL")
    return dict(value)


def sync_submission_from_facts(facts: Mapping[str, object]) -> Mapping[str, object] | None:
    for phase in ("unknown", "submitted", "progressing", "applying", "checkpointed"):
        value = facts.get(phase)
        if isinstance(value, Mapping):
            checkpoint = value.get("checkpoint")
            if isinstance(checkpoint, Mapping):
                candidate = checkpoint.get("submission", checkpoint)
                if isinstance(candidate, Mapping) and candidate.get("provider_task_ref"):
                    return candidate
    return None


def child_context(attempt: object, request: Mapping[str, object]) -> dict[str, object]:
    supplied = request.get("context")
    context = dict(supplied) if isinstance(supplied, Mapping) else {}
    context.setdefault("policy_receipt_id", request.get("policy_receipt_id"))
    context.setdefault("capability", request.get("capability"))
    context.setdefault("budget_remaining", request.get("budget_remaining", 0))
    context.setdefault("scope", {"org_id": attempt.scope.org_id, "user_id": attempt.scope.user_id})
    if not context.get("policy_receipt_id") or not context.get("capability"):
        raise ValueError("CHILD_VERIFIED_CONTEXT_REQUIRED")
    return context


def read_frame(root: Path, request: Mapping[str, object]):
    import pandas as pd
    source = Path(request_source_path(request))
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


def schema(frame) -> list[Mapping[str, str]]:
    return [{"name": str(name), "dtype": str(dtype)} for name, dtype in frame.dtypes.items()]


async def export_frame(frame, attempt, request, staging: Path, materializer: object, facts: object | None, role: str, *, source_path: str | None = None) -> Mapping[str, object]:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    content = buffer.getvalue()
    checkpoint = materializer.checkpoint(content)
    target = staging / checkpoint.content_hash
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    result = {"state": "completed", "artifact_ref": f"artifact:{checkpoint.content_hash}", "content_hash": checkpoint.content_hash, "byte_size": checkpoint.byte_size, "schema": schema(frame), "rows": int(len(frame)), "lineage": {"role": role, "source_path": source_path or request_source_path(request), "output_format": "parquet"}}
    await checkpoint_fact(facts, attempt, result, request=request)
    return result


async def checkpoint_fact(facts: object | None, attempt: object, result: Mapping[str, object], *, request: Mapping[str, object] | None = None) -> None:
    if facts is None or not hasattr(facts, "checkpoint_materialization"):
        return
    artifact_id = (request or {}).get("artifact_id") or result.get("artifact_id")
    if artifact_id is not None and hasattr(facts, "link_artifact"):
        await facts.link_artifact(p_action_id=str(attempt.action_id), p_attempt_id=str(attempt.attempt_id), p_artifact_id=str(artifact_id), p_role=str(result.get("lineage", {}).get("role", "materialized")), p_parent_artifact_id=None, p_content_hash=str(result["content_hash"]), p_materialize_revision=1, p_materialize_status=str(result.get("state", "materialized")), p_sensitivity="normal")
    if artifact_id is not None:
        await facts.checkpoint_materialization(action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id), artifact_id=str(artifact_id), materialize_revision=1, materialize_status=str(result.get("state", result.get("materialize_status", "materialized"))))


def resource_params(attempt: object, operation: str, resource_id: str, payload: Mapping[str, object] | None = None, expected_version: int = 0) -> dict[str, object]:
    params = {"p_action_id": str(attempt.action_id), "p_attempt_id": str(attempt.attempt_id), "p_request_hash": str(attempt.request_hash), "p_idempotency_key": str(attempt.idempotency_key), "p_execution_token": str(attempt.lease.fencing_token)}
    if operation == "manage_scheduled_task":
        params.update({"p_task_id": resource_id, "p_expected_state_version": expected_version, "p_payload": dict(payload or {})})
    else:
        params["p_deleted_file_id"] = int(resource_id)
    return params


def sync_idempotency_key(attempt: object, request: Mapping[str, object]) -> str:
    raw = f"sync:{attempt.action_id}:{attempt.attempt_id}:{request.get('domain', 'default')}:{attempt.request_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_bound(value: object) -> None:
    if not isinstance(value, Mapping) or value.get("outcome") not in {"bound", "updated", "already_exists"}:
        raise RuntimeError("RUNTIME_RESOURCE_INTENT_UNPROVEN")


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
            p_child_ordinal=int(request["child_ordinal"]), p_capability=str(request["capability"]),
            p_context=child_context(attempt, request),
        )
        created = phase_object(result, "CHILD_RUN_CREATE_INVALID")
        if created.get("outcome") in {"created", "already_exists"}:
            return {**created, "state": "accepted", "provider_task_ref": str(created.get("child_run_id"))}
        return created

    async def readback(self, attempt, receipt: Mapping[str, object]) -> Mapping[str, object]:
        child_id = receipt.get("child_run_id")
        if not child_id or not attempt.run_id:
            return {"state": "unknown", "evidence": {"error_code": "CHILD_RUN_READBACK_BINDING_MISSING"}}
        token = str(receipt.get("reconciliation_token") or attempt.lease.fencing_token)
        version = receipt.get("reconciliation_state_version", receipt.get("state_version", 1))
        result = await self.repository.read_child_run(
            child_run_id=str(child_id), parent_run_id=attempt.run_id,
            parent_action_id=str(attempt.action_id), parent_attempt_id=str(attempt.attempt_id),
            parent_request_hash=attempt.request_hash, ownership_token=token,
            expected_state_version=int(version), child_ordinal=int(receipt.get("child_ordinal", 0)),
        )
        readback = phase_object(result, "CHILD_RUN_READBACK_INVALID")
        return {**readback, "state": str(readback.get("status", "unknown"))}

    async def complete(self, attempt, receipt: Mapping[str, object], result: Mapping[str, object]) -> Mapping[str, object]:
        child_id = receipt.get("child_run_id")
        if not child_id or not attempt.run_id:
            raise ValueError("CHILD_RUN_COMPLETE_BINDING_MISSING")
        return await self.repository.complete_child_run(
            p_child_run_id=str(child_id), p_parent_run_id=attempt.run_id,
            p_parent_action_id=str(attempt.action_id), p_parent_request_hash=attempt.request_hash,
            p_parent_attempt_id=str(attempt.attempt_id),
            p_reconciliation_token=str(receipt.get("reconciliation_token", attempt.lease.fencing_token)),
            p_expected_state_version=int(receipt.get("reconciliation_state_version", receipt.get("state_version", 1))),
            p_aggregation_revision=int(result.get("aggregation_revision", 1)), p_result=dict(result),
        )

    async def cancel(self, attempt, receipt: Mapping[str, object]) -> Mapping[str, object]:
        child_id = receipt.get("child_run_id")
        if not child_id or not attempt.run_id:
            return {"state": "unknown", "evidence": {"error_code": "CHILD_RUN_CANCEL_BINDING_MISSING"}}
        result = await self.repository.cancel_child_run(
            p_child_run_id=str(child_id), p_parent_run_id=attempt.run_id,
            p_parent_action_id=str(attempt.action_id), p_parent_request_hash=attempt.request_hash,
            p_parent_attempt_id=str(attempt.attempt_id),
            p_reconciliation_token=str(receipt.get("reconciliation_token", attempt.lease.fencing_token)),
            p_expected_state_version=int(receipt.get("reconciliation_state_version", receipt.get("state_version", 1))),
            p_reason=str(receipt.get("cancel_reason", "parent_cancel")),
        )
        if isinstance(result, Mapping):
            return {**result, "fencing_confirmed": result.get("outcome") == "cancelled"}
        return {"state": "unknown", "evidence": {"error_code": "CHILD_RUN_CANCEL_INVALID"}}
