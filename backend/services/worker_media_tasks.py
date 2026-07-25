"""跨租户媒体任务的 Worker RPC 应用层边界。"""

from typing import Any


class WorkerMediaTasks:
    """隐藏媒体 Worker 的 SECURITY DEFINER RPC 协议细节。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    def discover(self, limit: int = 100) -> list[dict[str, Any]]:
        result = self._db.rpc(
            "worker_discover_media_tasks",
            {"p_limit": limit},
        ).execute()
        return list(result.data or [])

    def discover_legacy_active(self) -> list[dict[str, Any]]:
        result = self._db.rpc(
            "worker_discover_legacy_active_tasks",
        ).execute()
        return list(result.data or [])

    def get(self, external_task_id: str) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_get_media_task",
            {"p_external_task_id": external_task_id},
        ).execute()
        return result.data if isinstance(result.data, dict) else None

    def touch(self, external_task_id: str) -> bool:
        result = self._db.rpc(
            "worker_touch_media_task",
            {"p_external_task_id": external_task_id},
        ).execute()
        return result.data is True

    def claim_completion(
        self,
        external_task_id: str,
        expected_version: int,
    ) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_claim_media_task_completion",
            {
                "p_external_task_id": external_task_id,
                "p_expected_version": expected_version,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        task = data.get("task")
        return task if data.get("outcome") == "claimed" else None

    def settle_batch_item(
        self,
        external_task_id: str,
        expected_version: int,
        status: str,
        result_data: dict[str, Any],
        error_message: str | None = None,
    ) -> list[dict[str, Any]] | None:
        result = self._db.rpc(
            "worker_settle_media_batch_item",
            {
                "p_external_task_id": external_task_id,
                "p_expected_version": expected_version,
                "p_status": status,
                "p_result_data": result_data,
                "p_error_message": error_message,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        tasks = data.get("batch_tasks")
        return list(tasks) if data.get("outcome") == "settled" else None

    def fail_legacy_stale(
        self,
        task_id: str,
        error_message: str,
        message_content: list[dict[str, Any]] | None,
    ) -> bool:
        result = self._db.rpc(
            "worker_fail_legacy_stale_task",
            {
                "p_task_id": task_id,
                "p_error_message": error_message,
                "p_message_content": message_content,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data.get("outcome") in {"failed", "already_terminal"}

    def get_batch_message(self, batch_id: str) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_get_media_batch_message",
            {"p_batch_id": batch_id},
        ).execute()
        return result.data if isinstance(result.data, dict) else None

    def commit_batch_message(
        self,
        batch_id: str,
        message: dict[str, Any],
        preview: str | None = None,
    ) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_commit_media_batch_message",
            {
                "p_batch_id": batch_id,
                "p_message": message,
                "p_preview": preview,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        committed = data.get("message")
        return committed if data.get("outcome") == "committed" else None

    def commit_video_terminal(
        self,
        external_task_id: str,
        expected_version: int,
        status: str,
        content: list[dict[str, Any]],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_commit_video_terminal",
            {
                "p_external_task_id": external_task_id,
                "p_expected_version": expected_version,
                "p_status": status,
                "p_content": content,
                "p_error_code": error_code,
                "p_error_message": error_message,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data if data.get("outcome") in {
            "committed", "failed", "already_terminal"
        } else None

    def prepare_retry(
        self,
        external_task_id: str,
        expected_version: int,
        new_model: str,
    ) -> str | None:
        result = self._db.rpc(
            "worker_prepare_media_retry",
            {
                "p_external_task_id": external_task_id,
                "p_expected_version": expected_version,
                "p_new_model": new_model,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        transaction_id = data.get("transaction_id")
        return str(transaction_id) if data.get("outcome") == "prepared" else None

    def abort_retry(
        self,
        external_task_id: str,
        expected_version: int,
        transaction_id: str,
    ) -> bool:
        result = self._db.rpc(
            "worker_abort_media_retry",
            {
                "p_external_task_id": external_task_id,
                "p_expected_version": expected_version,
                "p_transaction_id": transaction_id,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        return data.get("outcome") == "aborted"

    def commit_retry(
        self,
        external_task_id: str,
        expected_version: int,
        new_external_task_id: str,
        new_model: str,
        request_params: dict[str, Any],
        transaction_id: str,
    ) -> dict[str, Any] | None:
        result = self._db.rpc(
            "worker_commit_media_retry",
            {
                "p_external_task_id": external_task_id,
                "p_expected_version": expected_version,
                "p_new_external_task_id": new_external_task_id,
                "p_new_model": new_model,
                "p_request_params": request_params,
                "p_transaction_id": transaction_id,
            },
        ).execute()
        data = result.data if isinstance(result.data, dict) else {}
        task = data.get("task")
        return task if data.get("outcome") == "committed" else None
