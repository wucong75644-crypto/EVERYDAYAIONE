"""Authenticated Skill discovery, explicit bindings and optional recommendation feedback."""

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from psycopg import Error as DatabaseError

from api.deps import CurrentUserId
from core.config import get_settings
from core.database import get_db
from services.skills.available import available_skills
from services.skills.resolver import SkillSummary
from services.skills.bindings import ConversationSkillBindings, SkillBinding, binding_authority
from services.skills.contracts import SkillError, SkillFileType
from services.skills.selection import SkillSelection
from services.skills.recommendations import RecommendationBatch
from services.skills.recommendation_service import recommendations_enabled, web_recommendations, web_feedback


router = APIRouter(prefix="/skills", tags=["skills"])


class AvailableSkillsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID


@router.get("/available", response_model=list[SkillSummary])
async def get_available_skills(
    query: Annotated[AvailableSkillsQuery, Query()], user_id: CurrentUserId,
    response: Response, settings=Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    # Avoid even creating a DB pool for this optional feature while disabled.
    if settings.skill_catalog_enabled is not True:
        return []
    return await available_skills(
        get_db(), settings, actor_user_id=user_id, conversation_id=query.conversation_id,
    )


async def get_bindings(conversation_id: UUID, user_id: CurrentUserId,
                       response: Response, settings=Depends(get_settings)):
    response.headers["Cache-Control"] = "no-store"
    if settings.skill_catalog_enabled is not True:
        raise HTTPException(503, "Skill 功能暂未开放")
    db = get_db()
    org, owner = await asyncio.to_thread(binding_authority, db, str(UUID(user_id)), conversation_id)
    return ConversationSkillBindings(db, settings, user_id, conversation_id, org, owner)


Bindings = Annotated[ConversationSkillBindings, Depends(get_bindings)]


async def binding_operation(operation, *args):
    try:
        return await operation(*args)
    except SkillError as error:
        code = str(error)
        message = {
            "SKILL_NOT_AVAILABLE": "该 Skill 当前不可用或你暂无使用权限",
            "SKILL_SELECTION_CHANGED": "Skill 版本已更新，请刷新后重新添加",
            "SKILL_BINDING_REVISION_UNAVAILABLE": "Skill 版本不可用，请刷新后重新添加",
            "SKILL_BINDING_CONFLICT": "会话已固定该 Skill 的其他版本，请先移除",
        }.get(code, "无法修改会话 Skill，请刷新后重试")
        raise HTTPException(409, message) from None
    except DatabaseError as error:
        if error.sqlstate == "23514" and "SKILL_BINDING_LIMIT" in str(error):
            raise HTTPException(409, "每个会话最多固定 4 个 Skill") from None
        raise HTTPException(503, "暂时无法访问会话 Skill，请重试") from None


@router.get("/conversations/{conversation_id}/bindings", response_model=list[SkillBinding])
async def list_bindings(bindings: Bindings):
    return await binding_operation(bindings.list)


@router.post("/conversations/{conversation_id}/bindings", status_code=201)
async def add_binding(selection: SkillSelection, bindings: Bindings):
    binding_id = await binding_operation(bindings.add, selection)
    return {"binding_id": str(binding_id)}


@router.delete("/conversations/{conversation_id}/bindings/{binding_id}", status_code=204)
async def remove_binding(binding_id: UUID, bindings: Bindings):
    await binding_operation(bindings.remove, binding_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    selected_file_types: tuple[SkillFileType, ...] = Field(default=(), max_length=7)
    permission_mode: Literal["auto", "ask", "plan"] = "auto"


class RecommendationFeedback(SkillSelection):
    conversation_id: UUID
    feedback: Literal["selected", "dismissed", "not_relevant"]


@router.post("/recommendations", response_model=RecommendationBatch)
async def get_recommendations(request: RecommendationRequest, user_id: CurrentUserId,
                              response: Response, settings=Depends(get_settings)):
    response.headers["Cache-Control"] = "no-store"
    if not recommendations_enabled(settings):
        return RecommendationBatch(status="disabled")
    return await web_recommendations(get_db(), settings, actor_user_id=user_id, **request.model_dump())


@router.post("/recommendations/{recommendation_id}/feedback", status_code=204)
async def record_recommendation_feedback(recommendation_id: UUID, request: RecommendationFeedback,
                                         user_id: CurrentUserId, settings=Depends(get_settings)):
    if not recommendations_enabled(settings):
        raise HTTPException(503, "Skill 推荐暂未开放")
    try:
        recorded = await web_feedback(get_db(), actor_user_id=user_id, recommendation_id=recommendation_id,
                                      **request.model_dump())
    except DatabaseError:
        raise HTTPException(503, "暂时无法保存推荐反馈") from None
    if not recorded:
        raise HTTPException(404, "推荐不存在或无权反馈")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
