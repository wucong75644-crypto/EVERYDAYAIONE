"""ChangeSet 状态与时间线 API。

该路由只读通用变更交易，或取消/恢复 ChangeSet；真实业务提交由业务适配器执行。
"""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUserId, Database, OrgCtx, ScopedDB
from core.config import get_settings
from core.db_scope import DatabaseAccessKind, DatabaseScope
from schemas.changeset import (
    CancelChangeSetRequest,
    ChangeSetDTO,
    ChangeSetTimelineDTO,
    RecoverChangeSetRequest,
)
from services.changeset.repository import (
    ChangeSetConcurrencyError,
    ChangeSetNotFound,
    ChangeSetRepository,
)
from services.changeset.service import ChangeSetService
from services.scheduler.scheduled_task_change_adapter import (
    ScheduledTaskChangeAdapter,
    build_change_set_approval_actions,
)
from services.skills.chat_creation import commit_draft, content_digest, _org_admin
from services.skills.authoring_contracts import DraftContent
from services.skills.contracts import SkillError
from services.skills.repository import SkillRepository


router = APIRouter(prefix="/change-sets", tags=["ChangeSet"])


class SkillDraftConfirmation(BaseModel):
    expected_change_set_revision: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _org_id(org_ctx: Any) -> str:
    if not org_ctx.org_id:
        raise HTTPException(400, "ChangeSet 需要企业上下文")
    return str(org_ctx.org_id)


def _can_access(row: Dict[str, Any], user_id: str, org_ctx: Any) -> bool:
    return str(row.get("created_by")) == str(user_id) or org_ctx.org_role in {"owner", "admin"}


def _get_owned(
    repo: ChangeSetRepository, change_set_id: str, org_id: str,
    user_id: str, org_ctx: Any,
) -> Dict[str, Any]:
    try:
        row = repo.get(change_set_id, org_id)
    except ChangeSetNotFound:
        raise HTTPException(404, "ChangeSet 不存在")
    if not _can_access(row, user_id, org_ctx):
        raise HTTPException(403, "无权查看此 ChangeSet")
    return row


def _to_dto(row: Dict[str, Any], checks: list[Dict[str, Any]]) -> Dict[str, Any]:
    # Pydantic 只负责稳定字段校验；数据库的时间字符串由 FastAPI 原样输出。
    policy = row.get("policy_snapshot") or {}
    value = {
        **row,
        "checks": checks,
        "risk": policy,
        "plan": row.get("plan_snapshot"),
        "approval_actions": ([
            {"action": "confirm", "enabled": True, "method": "POST", "path": f"/api/change-sets/{row.get('id')}/confirm"},
            {"action": "cancel", "enabled": True, "method": "POST", "path": f"/api/change-sets/{row.get('id')}/cancel"},
        ] if row.get("resource_type") == "skill_draft" and row.get("status") == "awaiting_approval"
            else build_change_set_approval_actions(row)),
        "result": {
            "status": row.get("status"),
            "committed_revision": row.get("committed_revision"),
            "conflict": row.get("conflict"),
            "error_code": row.get("error_code"),
            "error_message": row.get("error_message"),
        },
    }
    return ChangeSetDTO.model_validate(value).model_dump(mode="json")


@router.get("/active", summary="读取当前用户尚未结束的 ChangeSet")
async def list_active_change_sets(
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    resource_type: str | None = None,
) -> Dict[str, Any]:
    org_id = _org_id(org_ctx)
    repo = ChangeSetRepository(scoped_db)
    rows = repo.list_active_for_actor(
        org_id=org_id, actor_id=user_id, resource_type=resource_type,
    )
    return {
        "success": True,
        "data": [_to_dto(row, repo.list_checks(str(row["id"]), org_id)) for row in rows],
    }


@router.get("/{change_set_id}", summary="读取 ChangeSet 状态")
async def get_change_set(
    change_set_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
) -> Dict[str, Any]:
    org_id = _org_id(org_ctx)
    repo = ChangeSetRepository(scoped_db)
    row = _get_owned(repo, change_set_id, org_id, user_id, org_ctx)
    return {"success": True, "data": _to_dto(row, repo.list_checks(change_set_id, org_id))}


@router.get("/{change_set_id}/timeline", summary="读取 ChangeSet 完整时间线")
async def get_change_set_timeline(
    change_set_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
) -> Dict[str, Any]:
    org_id = _org_id(org_ctx)
    repo = ChangeSetRepository(scoped_db)
    _get_owned(repo, change_set_id, org_id, user_id, org_ctx)
    dto = ChangeSetTimelineDTO(
        change_set_id=change_set_id,
        events=repo.list_events(change_set_id, org_id),
    )
    return {"success": True, "data": dto.model_dump(mode="json")}


@router.post("/{change_set_id}/cancel", summary="取消 ChangeSet")
async def cancel_change_set(
    change_set_id: str,
    payload: CancelChangeSetRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
) -> Dict[str, Any]:
    org_id = _org_id(org_ctx)
    repo = ChangeSetRepository(scoped_db)
    row = _get_owned(repo, change_set_id, org_id, user_id, org_ctx)
    if str(row.get("created_by")) != str(user_id):
        raise HTTPException(403, "只有变更发起人可以取消 ChangeSet")
    try:
        updated = ChangeSetService(repo).cancel(
            change_set_id=change_set_id, org_id=org_id,
            actor_id=user_id, reason=payload.reason,
        )
    except ChangeSetConcurrencyError as exc:
        raise HTTPException(409, str(exc))
    return {"success": True, "data": _to_dto(updated, repo.list_checks(change_set_id, org_id))}


@router.post("/{change_set_id}/confirm", summary="确认并提交 ChangeSet")
async def confirm_change_set(
    change_set_id: str,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    db: Database,
    confirmation: SkillDraftConfirmation | None = Body(default=None),
) -> Dict[str, Any]:
    org_id = _org_id(org_ctx)
    repo = ChangeSetRepository(scoped_db)
    row = _get_owned(repo, change_set_id, org_id, user_id, org_ctx)
    if row.get("resource_type") == "skill_draft":
        if confirmation is None:
            raise HTTPException(422, "请确认卡片中的 Skill 版本")
        if row.get("status") == "applied":
            return {"success": True, "data": _to_dto(row, repo.list_checks(change_set_id, org_id))}
        settings = get_settings()
        if (settings.skill_catalog_enabled is not True
                or settings.skill_chat_creation_enabled is not True):
            raise HTTPException(404, "Skill 对话创建尚未启用")
        revision = int(row.get("revision") or 0)
        if row.get("status") == "awaiting_approval" and revision != confirmation.expected_change_set_revision:
            raise HTTPException(409, "Skill 候选已更新，请刷新卡片后重新核对")
        if row.get("status") == "committing" and revision - 1 != confirmation.expected_change_set_revision:
            raise HTTPException(409, "Skill 候选版本与本次提交不一致")
        proposal = row.get("proposed_snapshot") or {}
        content = DraftContent.model_validate(proposal.get("content") or {})
        digest = content_digest(content)
        if (digest != confirmation.content_sha256 or digest != proposal.get("content_sha256")
                or digest != (row.get("audit_subject") or {}).get("candidate_sha256")):
            raise HTTPException(409, "Skill 候选内容校验失败，请重新打开卡片")
        try:
            _org_admin(db, str(user_id), org_id)
        except PermissionError:
            raise HTTPException(403, "仅组织管理员可创建 Skill 草稿") from None
        if row.get("status") == "awaiting_approval":
            try:
                row = repo.transition(
                    change_set_id=change_set_id, org_id=org_id,
                    expected_status="awaiting_approval", next_status="committing",
                    actor_id=user_id, actor_type="user", event_type="skill_draft_confirmed",
                    payload={"candidate_sha256": digest},
                )
            except ChangeSetConcurrencyError:
                row = repo.get(change_set_id, org_id)
                if row.get("status") == "applied":
                    return {"success": True, "data": _to_dto(row, repo.list_checks(change_set_id, org_id))}
                if row.get("status") != "committing":
                    raise HTTPException(409, "Skill 候选状态已变化") from None
        elif row.get("status") != "committing":
            raise HTTPException(409, "Skill 候选当前不能创建草稿")
        repository = SkillRepository(db.pool, DatabaseScope(
            actor_user_id=str(user_id), org_id=org_id, access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
            request_id=str(uuid4()),
        ))
        try:
            receipt = commit_draft(repository, get_settings(), change_set=row,
                                   actor_id=str(user_id), org_id=org_id)
        except SkillError as exc:
            code = str(exc)
            if code in {
                "SKILL_VERSION_CONFLICT", "SKILL_TRANSITION_INVALID",
                "SKILL_PACKAGE_UNAVAILABLE", "SKILL_OWNER_SCOPE_MISMATCH", "SKILL_KEY_EXISTS",
            }:
                # The business transaction made no write, so this candidate can
                # no longer be retried successfully in its current form. Keep
                # it out of the perpetual `committing` state and preserve the
                # precise stale-candidate reason in the ChangeSet timeline.
                try:
                    repo.record_check(
                        change_set_id=change_set_id, org_id=org_id, check_type="conflict",
                        check_key="skill_draft_base", status="failed",
                        input_data={"content_sha256": digest},
                        result={"error_code": code}, actor_id=user_id, actor_type="user",
                    )
                    row = repo.transition(
                        change_set_id=change_set_id, org_id=org_id,
                        expected_status="committing", next_status="conflicted",
                        actor_id=user_id, actor_type="user", event_type="skill_draft_conflicted",
                        payload={"error_code": code},
                    )
                except Exception:
                    # A receipt may have committed before a transient response
                    # failure. Leave `committing` so the idempotent retry can
                    # recover it from that receipt.
                    pass
            status = 409 if code in {
                "SKILL_VERSION_CONFLICT", "SKILL_TRANSITION_INVALID", "SKILL_KEY_EXISTS",
            } else 404 if code == "SKILL_PACKAGE_UNAVAILABLE" else 403
            message = "目标草稿已变化，请重新生成候选" if status == 409 else (
                "目标 Skill 已不可访问" if status == 404 else "当前账号已无权创建此 Skill 草稿"
            )
            raise HTTPException(status, message) from None
        except PermissionError as exc:
            raise HTTPException(403, "仅活跃组织管理员可创建 Skill 草稿") from exc
        except ValueError as exc:
            code = str(exc)
            status = 409 if any(token in code for token in ("CONFLICT", "VERSION", "UNAVAILABLE")) else 422
            raise HTTPException(status, code) from exc
        try:
            repo.record_check(
                change_set_id=change_set_id, org_id=org_id, check_type="commit",
                check_key="skill_draft_receipt", status="passed",
                input_data={"content_sha256": digest}, result=receipt,
                actor_id=user_id, actor_type="user",
            )
            row = repo.transition(
                change_set_id=change_set_id, org_id=org_id,
                expected_status="committing", next_status="applied",
                actor_id=user_id, actor_type="user", event_type="skill_draft_created",
                payload={"package_id": receipt["package_id"],
                         "draft_revision": receipt["draft_revision"],
                         "draft_version": receipt["draft_version"],
                         "content_sha256": digest},
            )
        except ChangeSetConcurrencyError:
            row = repo.get(change_set_id, org_id)
            if row.get("status") != "applied":
                raise HTTPException(409, "Skill 草稿已保存，状态正在恢复，请刷新卡片") from None
        return {"success": True, "data": _to_dto(row, repo.list_checks(change_set_id, org_id))}
    if row.get("resource_type") != "scheduled_task":
        raise HTTPException(422, "当前 ChangeSet 没有可用的业务适配器")
    adapter = ScheduledTaskChangeAdapter(db, user_id=user_id, org_id=org_id)
    try:
        updated = await ChangeSetService(repo).confirm(
            change_set_id=change_set_id, org_id=org_id, actor_id=user_id, adapter=adapter,
        )
    except Exception as exc:
        if hasattr(exc, "current"):
            raise HTTPException(409, str(exc)) from exc
        raise
    return {"success": True, "data": _to_dto(updated, repo.list_checks(change_set_id, org_id))}


@router.post("/{change_set_id}/recover", summary="从失败 ChangeSet 创建恢复草稿")
async def recover_change_set(
    change_set_id: str,
    payload: RecoverChangeSetRequest,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
) -> Dict[str, Any]:
    org_id = _org_id(org_ctx)
    repo = ChangeSetRepository(scoped_db)
    row = _get_owned(repo, change_set_id, org_id, user_id, org_ctx)
    if str(row.get("created_by")) != str(user_id):
        raise HTTPException(403, "只有变更发起人可以恢复 ChangeSet")
    try:
        recovered = ChangeSetService(repo).recover_failed(
            change_set_id=change_set_id, org_id=org_id,
            actor_id=user_id, idempotency_key=payload.idempotency_key,
        )
    except ChangeSetConcurrencyError as exc:
        raise HTTPException(409, str(exc))
    return {"success": True, "data": _to_dto(recovered, repo.list_checks(recovered["id"], org_id))}
