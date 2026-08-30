"""ChangeSet 生命周期服务。

该服务协调通用状态、检查和适配器回调；业务对象的真实提交始终由 adapter.commit
完成，内核不会根据 proposed_snapshot 生成 SQL 或更新业务表。
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from services.changeset.contracts import (
    ChangeSetAdapter,
    ChangeSetContext,
    CommitRequest,
    ChangeSetStatus,
)
from services.changeset.repository import (
    ChangeSetConcurrencyError,
    ChangeSetRepository,
)
from services.changeset.state_machine import is_terminal, require_transition


class ChangeSetService:
    def __init__(self, repository: ChangeSetRepository) -> None:
        self.repository = repository

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """按幂等键创建一次变更交易；重复请求返回同一行。"""
        return self.repository.create(payload)

    def get(self, change_set_id: str, org_id: str) -> dict[str, Any]:
        return self.repository.get(change_set_id, org_id)

    def cancel(self, *, change_set_id: str, org_id: str, actor_id: str,
               actor_type: str = "user", reason: str = "") -> dict[str, Any]:
        current = self.repository.get(change_set_id, org_id)
        status = ChangeSetStatus(current["status"])
        if status is ChangeSetStatus.CANCELLED:
            return current  # 幂等取消
        if is_terminal(status):
            raise ChangeSetConcurrencyError(
                f"ChangeSet 已处于终态 {status.value}，不能取消", current=current,
            )
        require_transition(status, ChangeSetStatus.CANCELLED)
        return self.repository.transition(
            change_set_id=change_set_id, org_id=org_id,
            expected_status=status.value, next_status=ChangeSetStatus.CANCELLED.value,
            actor_id=actor_id, actor_type=actor_type, event_type="cancelled",
            payload={"reason": reason[:500]} if reason else {},
        )

    def expire(self, *, change_set_id: str, org_id: str, actor_id: str = "system",
               actor_type: str = "system") -> dict[str, Any]:
        current = self.repository.get(change_set_id, org_id)
        status = ChangeSetStatus(current["status"])
        if status is ChangeSetStatus.EXPIRED:
            return current
        if is_terminal(status) or status is ChangeSetStatus.COMMITTING:
            return current
        require_transition(status, ChangeSetStatus.EXPIRED)
        return self.repository.transition(
            change_set_id=change_set_id, org_id=org_id,
            expected_status=status.value, next_status=ChangeSetStatus.EXPIRED.value,
            actor_id=actor_id, actor_type=actor_type, event_type="expired",
        )

    async def confirm(
        self,
        *,
        change_set_id: str,
        org_id: str,
        actor_id: str,
        adapter: ChangeSetAdapter | None = None,
        actor_type: str = "user",
    ) -> dict[str, Any]:
        """确认一次 ChangeSet。

        没有适配器时只推进到 committing，供执行器接管；有适配器时执行一次业务提交。
        applied 的重复确认直接回放已存在结果，不再次调用 adapter。
        """
        current = self.repository.get(change_set_id, org_id)
        status = ChangeSetStatus(current["status"])
        if status is ChangeSetStatus.APPLIED:
            return current
        if status is ChangeSetStatus.COMMITTING:
            return current
        if status is not ChangeSetStatus.AWAITING_APPROVAL:
            raise ChangeSetConcurrencyError(
                f"ChangeSet 当前不可确认：{status.value}", current=current,
            )
        try:
            committing = self.repository.transition(
                change_set_id=change_set_id, org_id=org_id,
                expected_status=ChangeSetStatus.AWAITING_APPROVAL.value,
                next_status=ChangeSetStatus.COMMITTING.value,
                actor_id=actor_id, actor_type=actor_type, event_type="confirmed",
            )
        except ChangeSetConcurrencyError:
            # 两个确认请求同时到达时，后到者回放已抢到 committing/applied 的结果。
            winner = self.repository.get(change_set_id, org_id)
            if ChangeSetStatus(winner["status"]) in {
                ChangeSetStatus.COMMITTING, ChangeSetStatus.APPLIED,
            }:
                return winner
            raise
        self.repository.record_check(
            change_set_id=change_set_id, org_id=org_id,
            check_type="approval", check_key="user_confirmation",
            status="passed", input_data={"actor_id": actor_id},
            result={"confirmed": True}, actor_id=actor_id, actor_type=actor_type,
        )
        if adapter is None:
            return committing
        return await self._commit(
            committing, org_id=org_id, actor_id=actor_id,
            actor_type=actor_type, adapter=adapter,
        )

    async def resume_committing(
        self, *, change_set_id: str, org_id: str, actor_id: str,
        adapter: ChangeSetAdapter, actor_type: str = "system",
    ) -> dict[str, Any]:
        """恢复进程崩溃后停在 committing 的 ChangeSet；适配器必须幂等。"""
        current = self.repository.get(change_set_id, org_id)
        if ChangeSetStatus(current["status"]) is not ChangeSetStatus.COMMITTING:
            return current
        return await self._commit(
            current, org_id=org_id, actor_id=actor_id,
            actor_type=actor_type, adapter=adapter,
        )

    def recover_failed(
        self, *, change_set_id: str, org_id: str, actor_id: str,
        actor_type: str = "user", idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """失败是终态；恢复创建新的 draft，保留原失败时间线和基线。"""
        current = self.repository.get(change_set_id, org_id)
        if ChangeSetStatus(current["status"]) is not ChangeSetStatus.FAILED:
            raise ChangeSetConcurrencyError("只有 failed ChangeSet 可以恢复", current=current)
        key = idempotency_key or f"{current['id']}:recovery:{uuid4()}"
        return self.repository.create({
            "org_id": org_id, "resource_type": current["resource_type"],
            "resource_id": current["resource_id"], "operation": current["operation"],
            "base_revision": current["base_revision"],
            "base_snapshot": current.get("base_snapshot") or {},
            "proposed_snapshot": current.get("proposed_snapshot") or {},
            "patch": current.get("patch") or [], "diff": current.get("diff") or {},
            "risk_level": current.get("risk_level") or "medium",
            "policy_snapshot": current.get("policy_snapshot") or {},
            "plan_snapshot": current.get("plan_snapshot"),
            "tool_policy_snapshot": current.get("tool_policy_snapshot"),
            "check_summary": current.get("check_summary"),
            "idempotency_key": key, "actor_id": actor_id, "actor_type": actor_type,
            "audit_subject": {"recovery_of_id": current["id"]},
            "recovery_of_id": current["id"],
        })

    async def _commit(
        self, current: Mapping[str, Any], *, org_id: str, actor_id: str,
        actor_type: str, adapter: ChangeSetAdapter,
    ) -> dict[str, Any]:
        context = _context_from_row(current)
        try:
            result = await adapter.commit(CommitRequest(
                context=context, idempotency_key=str(current["idempotency_key"]),
            ))
            if result.conflict or not result.applied:
                conflict = dict(result.conflict or {"reason": "adapter_commit_not_applied"})
                self.repository.record_check(
                    change_set_id=str(current["id"]), org_id=org_id,
                    check_type="conflict", check_key="base_revision",
                    status="failed", input_data={"base_revision": context.base_revision},
                    result=conflict, actor_id=actor_id, actor_type=actor_type,
                )
                return self.repository.transition(
                    change_set_id=str(current["id"]), org_id=org_id,
                    expected_status=ChangeSetStatus.COMMITTING.value,
                    next_status=ChangeSetStatus.CONFLICTED.value,
                    actor_id=actor_id, actor_type=actor_type, event_type="conflicted",
                    payload=conflict,
                )
            self.repository.record_check(
                change_set_id=str(current["id"]), org_id=org_id,
                check_type="commit", check_key="business_commit",
                status="passed", input_data={"base_revision": context.base_revision},
                result={"new_revision": result.new_revision, **dict(result.receipt)},
                actor_id=actor_id, actor_type=actor_type,
            )
            return self.repository.transition(
                change_set_id=str(current["id"]), org_id=org_id,
                expected_status=ChangeSetStatus.COMMITTING.value,
                next_status=ChangeSetStatus.APPLIED.value,
                actor_id=actor_id, actor_type=actor_type, event_type="applied",
                payload={"new_revision": result.new_revision, **dict(result.receipt)},
            )
        except Exception as exc:
            try:
                return self.repository.transition(
                    change_set_id=str(current["id"]), org_id=org_id,
                    expected_status=ChangeSetStatus.COMMITTING.value,
                    next_status=ChangeSetStatus.FAILED.value,
                    actor_id=actor_id, actor_type=actor_type, event_type="failed",
                    payload={"error_type": type(exc).__name__, "error_message": str(exc)[:500]},
                )
            except Exception:
                raise


def _context_from_row(row: Mapping[str, Any]) -> ChangeSetContext:
    return ChangeSetContext(
        id=str(row["id"]), org_id=str(row["org_id"]),
        resource_type=str(row["resource_type"]), resource_id=str(row["resource_id"]),
        operation=str(row["operation"]), base_revision=str(row["base_revision"]),
        base_snapshot=row.get("base_snapshot") or {},
        proposed_snapshot=row.get("proposed_snapshot") or {},
        patch=row.get("patch") or [], diff=row.get("diff") or {},
        policy_snapshot=row.get("policy_snapshot") or {},
        plan_snapshot=row.get("plan_snapshot"),
        tool_policy_snapshot=row.get("tool_policy_snapshot"),
        check_summary=row.get("check_summary"),
    )
