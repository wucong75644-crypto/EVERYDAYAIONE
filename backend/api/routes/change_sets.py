"""ChangeSet 状态与时间线 API。

该路由只读通用变更交易，或取消/恢复 ChangeSet；真实业务提交由业务适配器执行。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from api.deps import CurrentUserId, Database, OrgCtx, ScopedDB
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


router = APIRouter(prefix="/change-sets", tags=["ChangeSet"])


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
    return ChangeSetDTO.model_validate({**row, "checks": checks}).model_dump(mode="json")


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
