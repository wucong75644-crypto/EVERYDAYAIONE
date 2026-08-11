"""Thin Runtime adapters over the existing data and file capabilities."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from services.agent.agent_result import AgentResult
from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.provider_adapters import (
    ErpDispatcherFactoryPort,
)
from services.agent.runtime.executors.resource_manifest import (
    PostgresRuntimeResourceManifestResolver,
)


def _tool_executor(**kwargs: object):
    from services.agent.tool_executor import ToolExecutor

    return ToolExecutor(**kwargs)


@dataclass(frozen=True, kw_only=True)
class RuntimeFileAnalyzeAdapter:
    database: object
    manifest_resolver: PostgresRuntimeResourceManifestResolver
    executor_factory: Callable[..., object] = _tool_executor

    async def prepare(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> Mapping[str, object]:
        manifest = await self.manifest_resolver.resolve(attempt, request)
        asset = manifest.resolve_file(request)
        if request.get("sheet") is not None:
            raise ValueError("FILE_ANALYZE_SHEET_UNSUPPORTED")
        executor = self.executor_factory(
            db=self.database, user_id=manifest.user_id,
            conversation_id=manifest.conversation_id,
            org_id=manifest.org_id,
            workspace_user_id=manifest.workspace_owner_id,
            resource_manifest=manifest,
            allowed_tools=frozenset({"file_analyze"}),
        )
        arguments: dict[str, object] = {"path": asset.workspace_path}
        result = await executor.execute("file_analyze", arguments)  # type: ignore[attr-defined]
        payload = dict(_public_result(result, operation="file_analyze"))
        payload["source_asset_id"] = asset.asset_id
        payload["source_workspace_path"] = asset.workspace_path
        return payload


@dataclass(frozen=True, kw_only=True)
class RuntimeFetchAllPagesAdapter:
    dispatcher_factory: ErpDispatcherFactoryPort
    max_concurrency: int = 4

    async def prepare(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not attempt.scope.org_id:
            raise ValueError("ERP_ORG_SCOPE_REQUIRED")
        tool = _optional_text(request, "tool", 80) or _text(
            request, "tool_name", 80,
        )
        action = _text(request, "action", 120)
        _assert_read_operation(tool, action)
        params = request.get("params", {})
        if not isinstance(params, Mapping):
            raise ValueError("ERP_PAGE_PARAMS_INVALID")
        page_size = _bounded_int(
            request.get("page_size"), default=100, maximum=200, minimum=20,
        )
        max_pages = _bounded_int(
            request.get("max_pages"), default=200, maximum=500,
        )
        dispatcher = await self.dispatcher_factory.create(attempt, request)
        try:
            from services.agent.erp_pagination import paginate_erp

            result = await paginate_erp(
                tool, action, {**dict(params), "page_size": page_size},
                max_pages=max_pages, _dispatcher=dispatcher,
                _semaphore=asyncio.Semaphore(max(1, self.max_concurrency)),
            )
        finally:
            await dispatcher.close()
        rows = result.get("list", [])
        if not isinstance(rows, list):
            raise RuntimeError("ERP_PAGE_RESPONSE_INVALID")
        if result.get("error") and not rows:
            return {
                "operation": "fetch_all_pages", "status": "error",
                "summary": "ERP pagination failed",
                "error_code": "ERP_PAGE_PROVIDER_FAILED",
            }
        payload = {
            "operation": "fetch_all_pages",
            "status": "partial" if result.get("warning") else "success",
            "summary": f"Fetched {len(rows)} ERP records",
            "count": len(rows), "data": rows,
            "warning": str(result.get("warning") or ""),
            "lineage": {"tool_name": tool, "action": action},
        }
        partial_error = result.get("partial_error")
        if isinstance(partial_error, Mapping):
            payload["partial_error"] = {
                "error_code": str(partial_error.get("error_code") or ""),
                "failed_page": partial_error.get("failed_page"),
            }
        return payload


def _assert_read_operation(tool: str, action: str) -> None:
    from services.kuaimai.registry import TOOL_REGISTRIES

    entry = TOOL_REGISTRIES.get(tool, {}).get(action)
    if entry is None or entry.is_write:
        raise PermissionError("ERP_PAGE_ACTION_NOT_READ_ONLY")


def _public_result(value: object, *, operation: str) -> Mapping[str, object]:
    if not isinstance(value, AgentResult):
        raise RuntimeError("RUNTIME_DATA_RESULT_INVALID")
    status = str(value.status)
    failed = value.is_failure or status == "rejected"
    payload: dict[str, object] = {
        "operation": operation, "status": status,
        "summary": f"{operation} failed" if failed else value.summary,
    }
    if failed:
        payload["error_code"] = "RUNTIME_DATA_OPERATION_FAILED"
        return payload
    if value.data is not None:
        payload["data"] = value.data
        payload["count"] = len(value.data)
    if value.file_ref is not None:
        payload["file"] = {
            "id": value.file_ref.id, "filename": value.file_ref.filename,
            "sandbox_ref": value.file_ref.sandbox_ref,
            "format": value.file_ref.format,
            "row_count": value.file_ref.row_count,
            "size_bytes": value.file_ref.size_bytes,
        }
    return payload


def _text(request: Mapping[str, object], key: str, maximum: int) -> str:
    value = _optional_text(request, key, maximum)
    if value is None:
        raise ValueError(f"RUNTIME_DATA_{key.upper()}_REQUIRED")
    return value


def _optional_text(
    request: Mapping[str, object], key: str, maximum: int,
) -> str | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"RUNTIME_DATA_{key.upper()}_INVALID")
    return value.strip()


def _bounded_int(
    value: object, *, default: int, maximum: int, minimum: int = 1,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("RUNTIME_DATA_LIMIT_INVALID")
    if value < minimum or value > maximum:
        raise ValueError("RUNTIME_DATA_LIMIT_OUT_OF_RANGE")
    return value


__all__ = [
    "RuntimeFetchAllPagesAdapter", "RuntimeFileAnalyzeAdapter",
]
