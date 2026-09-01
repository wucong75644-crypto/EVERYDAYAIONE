"""ChangeSet 通用表的持久化边界。

该仓储只访问 change_sets/change_checks/change_events 三张通用表及其 RPC，
绝不接收或执行业务表名、业务 JSON 更新语句。
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4


class ChangeSetRepositoryError(RuntimeError):
    """ChangeSet 持久化结果不符合契约。"""


class ChangeSetNotFound(ChangeSetRepositoryError):
    pass


class ChangeSetIdempotencyConflict(ChangeSetRepositoryError):
    def __init__(self, message: str, *, existing: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.existing = dict(existing or {})


class ChangeSetConcurrencyError(ChangeSetRepositoryError):
    def __init__(self, message: str, *, current: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.current = dict(current or {})


def _response_dict(response: Any, error_code: str) -> dict[str, Any]:
    data = response.data if response else None
    if not isinstance(data, dict):
        raise ChangeSetRepositoryError(error_code)
    return data


class ChangeSetRepository:
    """通过现有 LocalDB/Supabase 兼容客户端持久化 ChangeSet。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        change_set_id = str(payload.get("id") or uuid4())
        result = self._db.rpc("create_change_set", {
            "p_id": change_set_id,
            "p_org_id": str(payload["org_id"]),
            "p_resource_type": str(payload["resource_type"]),
            "p_resource_id": str(payload["resource_id"]),
            "p_operation": str(payload["operation"]),
            "p_base_revision": str(payload["base_revision"]),
            "p_base_snapshot": dict(payload.get("base_snapshot") or {}),
            "p_proposed_snapshot": dict(payload.get("proposed_snapshot") or {}),
            "p_patch": list(payload.get("patch") or []),
            "p_diff": dict(payload.get("diff") or {}),
            "p_risk_level": str(payload.get("risk_level") or "medium"),
            "p_policy_snapshot": dict(payload.get("policy_snapshot") or {}),
            "p_plan_snapshot": payload.get("plan_snapshot"),
            "p_tool_policy_snapshot": payload.get("tool_policy_snapshot"),
            "p_check_summary": payload.get("check_summary"),
            "p_idempotency_key": str(payload["idempotency_key"]),
            "p_expires_at": payload.get("expires_at"),
            "p_actor_id": str(payload.get("actor_id") or ""),
            "p_actor_type": str(payload.get("actor_type") or "user"),
            "p_audit_subject": dict(payload.get("audit_subject") or {}),
            "p_recovery_of_id": payload.get("recovery_of_id"),
        }).execute()
        data = _response_dict(result, "CHANGESET_CREATE_RESULT_INVALID")
        outcome = data.get("outcome")
        if outcome == "idempotency_conflict":
            raise ChangeSetIdempotencyConflict(
                "idempotency key is already bound to another ChangeSet",
                existing=data.get("change_set"),
            )
        change_set = data.get("change_set")
        if not isinstance(change_set, dict):
            raise ChangeSetRepositoryError("CHANGESET_CREATE_ROW_INVALID")
        return change_set

    def get_by_idempotency_key(
        self, *, org_id: str, idempotency_key: str,
    ) -> dict[str, Any] | None:
        """读取同一组织下幂等键已绑定的 ChangeSet。

        业务服务在执行昂贵且可能不确定的规划前调用它，确保重复请求
        直接回放原候选，而不是重新生成另一个 plan_snapshot 后再被数据库
        判定为幂等冲突。
        """
        result = self._db.table("change_sets").select("*").eq(
            "org_id", str(org_id),
        ).eq("idempotency_key", str(idempotency_key)).limit(1).execute()
        rows = result.data or []
        return rows[0] if rows else None

    def get(self, change_set_id: str, org_id: str) -> dict[str, Any]:
        result = self._db.table("change_sets").select("*").eq(
            "id", change_set_id,
        ).eq("org_id", org_id).limit(1).execute()
        rows = result.data or []
        if not rows:
            raise ChangeSetNotFound("ChangeSet 不存在")
        return rows[0]

    def list_active_for_actor(
        self, *, org_id: str, actor_id: str, resource_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """返回发起人仍可继续处理的 ChangeSet，用于刷新后恢复界面。

        终态不由聊天消息或前端缓存推导；这里按数据库真实状态过滤。
        """
        active = {"draft", "resolving", "proposed", "validating", "preflighting", "awaiting_approval", "committing"}
        query = self._db.table("change_sets").select("*").eq("org_id", str(org_id)).eq(
            "created_by", str(actor_id),
        ).in_("status", sorted(active))
        if resource_type:
            query = query.eq("resource_type", str(resource_type))
        result = query.order("updated_at", desc=True).limit(limit).execute()
        # 保留本地兼容数据库的二次过滤，避免旧适配器忽略 in_ 时返回终态。
        return [dict(row) for row in (result.data or []) if str(row.get("status")) in active]

    def enrich_proposal(
        self, *, change_set_id: str, org_id: str, expected_status: str,
        proposed_snapshot: Mapping[str, Any], patch: list[dict[str, Any]],
        diff: Mapping[str, Any], risk_level: str, policy_snapshot: Mapping[str, Any],
        plan_snapshot: Mapping[str, Any] | None, tool_policy_snapshot: Mapping[str, Any] | None,
        check_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        response = self._db.rpc("enrich_change_set_proposal", {
            "p_change_set_id": change_set_id, "p_org_id": org_id,
            "p_expected_status": expected_status,
            "p_proposed_snapshot": dict(proposed_snapshot), "p_patch": list(patch),
            "p_diff": dict(diff), "p_risk_level": risk_level,
            "p_policy_snapshot": dict(policy_snapshot), "p_plan_snapshot": plan_snapshot,
            "p_tool_policy_snapshot": tool_policy_snapshot,
            "p_check_summary": dict(check_summary),
        }).execute()
        data = _response_dict(response, "CHANGESET_ENRICH_RESULT_INVALID")
        row = data.get("change_set")
        if data.get("outcome") != "enriched" or not isinstance(row, dict):
            raise ChangeSetConcurrencyError(
                "ChangeSet 状态已被其他请求改变", current=row if isinstance(row, dict) else None,
            )
        return row

    def list_checks(self, change_set_id: str, org_id: str) -> list[dict[str, Any]]:
        result = self._db.table("change_checks").select("*").eq(
            "change_set_id", change_set_id,
        ).eq("org_id", org_id).order("created_at").execute()
        return list(result.data or [])

    def list_events(self, change_set_id: str, org_id: str) -> list[dict[str, Any]]:
        result = self._db.table("change_events").select("*").eq(
            "change_set_id", change_set_id,
        ).eq("org_id", org_id).order("sequence").execute()
        return list(result.data or [])

    def transition(
        self,
        *,
        change_set_id: str,
        org_id: str,
        expected_status: str,
        next_status: str,
        actor_id: str,
        actor_type: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._db.rpc("transition_change_set", {
            "p_change_set_id": change_set_id,
            "p_org_id": org_id,
            "p_expected_status": expected_status,
            "p_next_status": next_status,
            "p_actor_id": actor_id,
            "p_actor_type": actor_type,
            "p_event_type": event_type,
            "p_payload": dict(payload or {}),
        }).execute()
        data = _response_dict(response, "CHANGESET_TRANSITION_RESULT_INVALID")
        if data.get("outcome") == "missing":
            raise ChangeSetNotFound("ChangeSet 不存在")
        if data.get("outcome") == "state_conflict":
            raise ChangeSetConcurrencyError(
                "ChangeSet 状态已被其他请求改变", current=data.get("change_set"),
            )
        if data.get("outcome") == "invalid_transition":
            raise ChangeSetRepositoryError("CHANGESET_INVALID_TRANSITION")
        if data.get("outcome") == "expired":
            change_set = data.get("change_set")
            if isinstance(change_set, dict):
                return change_set
            raise ChangeSetRepositoryError("CHANGESET_EXPIRED_ROW_INVALID")
        change_set = data.get("change_set")
        if not isinstance(change_set, dict):
            raise ChangeSetRepositoryError("CHANGESET_TRANSITION_ROW_INVALID")
        return change_set

    def record_check(
        self,
        *,
        change_set_id: str,
        org_id: str,
        check_type: str,
        check_key: str,
        status: str,
        input_data: Mapping[str, Any] | None,
        result: Mapping[str, Any] | None,
        actor_id: str,
        actor_type: str,
    ) -> dict[str, Any]:
        response = self._db.rpc("record_change_check", {
            "p_check_id": str(uuid4()),
            "p_change_set_id": change_set_id,
            "p_org_id": org_id,
            "p_check_type": check_type,
            "p_check_key": check_key,
            "p_status": status,
            "p_input": dict(input_data or {}),
            "p_result": dict(result or {}),
            "p_actor_id": actor_id,
            "p_actor_type": actor_type,
        }).execute()
        data = _response_dict(response, "CHANGESET_CHECK_RESULT_INVALID")
        check = data.get("check")
        if not isinstance(check, dict):
            raise ChangeSetRepositoryError("CHANGESET_CHECK_ROW_INVALID")
        return check
