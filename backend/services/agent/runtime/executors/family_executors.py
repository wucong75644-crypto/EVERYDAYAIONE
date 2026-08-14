"""Business-semantic Executor classes for each AR-17.3 family."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor


class FamilyExecutor(SpecialistExecutor):
    family = "specialist"

    def __init__(self, *, action_kind: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.action_kind = action_kind

    async def dispatch(self, attempt: ActionAttempt, request: Mapping[str, object]):
        self.validate_request(request)
        return await super().dispatch(attempt, request)

    def validate_request(self, request: Mapping[str, object]) -> None:
        if not isinstance(request, Mapping):
            raise ValueError("SPECIALIST_REQUEST_OBJECT_REQUIRED")


class RemoteReadExecutor(FamilyExecutor):
    family = "remote_read"

    def validate_request(self, request):
        super().validate_request(request)
        if "query" not in request and self.action_kind in {"web_search", "social_crawler", "erp_api_search"}:
            raise ValueError("REMOTE_READ_QUERY_REQUIRED")


class ArtifactJobExecutor(FamilyExecutor):
    family = "artifact_job"

    def validate_request(self, request):
        super().validate_request(request)
        if self.action_kind == "local_data" and not isinstance(
            request.get("doc_type"), str,
        ):
            raise ValueError("LOCAL_DATA_DOC_TYPE_REQUIRED")
        if self.action_kind == "file_analyze" and not any(
            key in request for key in ("file_id", "path")
        ):
            raise ValueError("FILE_ANALYZE_RESOURCE_REQUIRED")
        if self.action_kind == "fetch_all_pages" and not (
            isinstance(request.get("tool") or request.get("tool_name"), str)
            and isinstance(request.get("action"), str)
        ):
            raise ValueError("ERP_PAGE_ACTION_REQUIRED")


class MediaGenerationExecutor(FamilyExecutor):
    family = "media_generation"

    def validate_request(self, request):
        super().validate_request(request)
        if not isinstance(request.get("prompt"), str) or not request["prompt"].strip():
            raise ValueError("MEDIA_PROMPT_REQUIRED")


class ErpMutationExecutor(FamilyExecutor):
    family = "erp_mutation"

    def validate_request(self, request):
        super().validate_request(request)
        if not isinstance(request.get("operation"), str) or not request["operation"].strip():
            raise ValueError("ERP_OPERATION_REQUIRED")


class SyncExecutor(FamilyExecutor):
    family = "erp_sync"

    def validate_request(self, request):
        super().validate_request(request)
        if request.get("scope") is not None and not isinstance(request.get("scope"), str):
            raise ValueError("SYNC_SCOPE_INVALID")


class WorkspaceMutationExecutor(FamilyExecutor):
    family = "workspace_mutation"

    def validate_request(self, request):
        super().validate_request(request)
        if not isinstance(request.get("resource_id") or request.get("deleted_file_id"), (str, int)):
            raise ValueError("WORKSPACE_RESOURCE_REQUIRED")


class ScheduledTaskExecutor(FamilyExecutor):
    family = "scheduled_task"

    def validate_request(self, request):
        super().validate_request(request)
        operation = request.get("operation")
        if operation not in {"create", "update", "delete", "pause", "resume"}:
            raise ValueError("SCHEDULED_OPERATION_INVALID")
        if operation != "create" and not isinstance(request.get("task_id"), str):
            raise ValueError("SCHEDULED_TASK_ID_REQUIRED")
        if operation in {"update", "delete", "pause", "resume"} and "state_version" not in request:
            raise ValueError("SCHEDULED_STATE_VERSION_REQUIRED")


class ChildRunExecutor(FamilyExecutor):
    family = "child_run"

    def validate_request(self, request):
        super().validate_request(request)
        if not isinstance(request.get("child_ordinal"), int) or request["child_ordinal"] < 0:
            raise ValueError("CHILD_ORDINAL_REQUIRED")
        if not isinstance(request.get("capability"), str) or not request["capability"]:
            raise ValueError("CHILD_CAPABILITY_REQUIRED")


EXECUTOR_BY_FAMILY = {
    "remote_read": RemoteReadExecutor,
    "erp_catalog": RemoteReadExecutor,
    "artifact_job": ArtifactJobExecutor,
    "media_generation": MediaGenerationExecutor,
    "erp_mutation": ErpMutationExecutor,
    "erp_sync": SyncExecutor,
    "workspace_mutation": WorkspaceMutationExecutor,
    "scheduled_task": ScheduledTaskExecutor,
    "child_run": ChildRunExecutor,
}


__all__ = ["EXECUTOR_BY_FAMILY", "FamilyExecutor"]
