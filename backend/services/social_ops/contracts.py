"""Bounded user configuration; authority always comes from OrgCtx."""

from typing import Literal, Annotated
from datetime import date
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from services.agent.agent_reach.contracts import platform_for_url


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileData(Input):
    name: str = Field(min_length=1, max_length=120)
    positioning: str = Field(min_length=1, max_length=4000)
    audience: str = Field(min_length=1, max_length=4000)
    facts: str = Field(default="", max_length=12000)
    pillars: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=12
    )
    tone: str = Field(default="", max_length=2000)
    prohibited: str = Field(default="", max_length=4000)
    experience: str = Field(default="", max_length=4000)
    visual_template: str = Field(default="", max_length=1000)


class ProfileUpdate(ProfileData):
    expected_revision: int = Field(ge=1)


class ResearchCreate(Input):
    client_request_id: UUID
    connection_id: UUID
    profile_id: UUID
    keywords: list[str] = Field(default_factory=list, max_length=10)
    sample_urls: list[str] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=5, ge=1, le=30)
    comment_pages: int = Field(default=0, ge=0, le=3)
    interval_hours: int | None = Field(default=None, ge=1, le=720)

    @field_validator("keywords")
    @classmethod
    def keywords_valid(cls, values):
        if any(not s.strip() or len(s) > 100 for s in values):
            raise ValueError("关键词必须非空且不超过100字")
        return list(dict.fromkeys(s.strip() for s in values))

    @field_validator("sample_urls")
    @classmethod
    def urls_valid(cls, values):
        from services.agent.agent_reach.xhs_adapter import XHSAdapter

        for url in values:
            if len(url) > 2048 or platform_for_url(url) != "xiaohongshu":
                raise ValueError("只支持小红书样本链接")
            XHSAdapter.target(url)
        return values


class BudgetConfig(Input):
    daily_limit: int = Field(ge=2, le=10000)
    min_interval_seconds: int = Field(ge=1, le=86400)
    allowed_start_hour: int = Field(default=0, ge=0, le=23)
    allowed_end_hour: int = Field(default=24, ge=1, le=24)
    max_comment_pages: int = Field(default=3, ge=0, le=100)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("无效时区") from None
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if self.allowed_start_hour == self.allowed_end_hour:
            raise ValueError("开始与结束小时不能相同；全天请设置0到24")
        return self


class StageSelection(Input):
    skill_id: str = Field(min_length=1, max_length=64)
    revision: str = Field(min_length=1, max_length=128)


class Produce(Input):
    kind: Literal["plan", "draft", "polish", "check"]
    job_id: UUID
    content_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    topic: str = Field(default="", max_length=4000)
    topic_id: UUID | None = None
    expected_topic_revision: int | None = Field(default=None, ge=1)
    stages: list[StageSelection] = Field(min_length=1, max_length=4)
    max_credits: int = Field(default=20, ge=1, le=1000)


class DraftData(Input):
    expected_revision: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=20)


class TopicUpdate(Input):
    expected_revision: int = Field(ge=1)
    decision: Literal["adopt", "adjust", "defer"]
    planned_date: date | None = None
