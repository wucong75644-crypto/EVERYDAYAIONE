"""Read-only summary discovery. No authority, body or client scope inputs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict

from api.deps import CurrentUserId
from core.config import get_settings
from core.database import get_db
from services.skills.available import available_skills
from services.skills.resolver import SkillSummary


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
