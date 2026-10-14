"""Bounded user configuration; authority always comes from OrgCtx."""

from typing import Literal, Annotated
from datetime import date, datetime, timezone
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
    client_request_id: UUID | None = None
    visual: dict[str, str] = Field(default_factory=dict)
    kind: Literal["plan", "draft", "polish", "check", "cards"]
    job_id: UUID
    content_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    topic: str = Field(default="", max_length=4000)
    topic_id: UUID | None = None
    expected_topic_revision: int | None = Field(default=None, ge=1)
    stages: list[StageSelection] = Field(min_length=1, max_length=4)
    max_credits: int = Field(default=20, ge=1, le=1000)

    @field_validator("visual")
    @classmethod
    def visual_valid(cls, value):
        return VisualTemplate.model_validate(value).model_dump()


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


class VisualTemplate(Input):
    background: str = Field(default="#FFF8F0", pattern=r"^#[0-9a-fA-F]{6}$")
    foreground: str = Field(default="#30343B", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#2563EB", pattern=r"^#[0-9a-fA-F]{6}$")
    watermark: str = Field(default="", max_length=40)


class Card(Input):
    role: Literal["cover", "body", "closing"]
    headline: str = Field(min_length=1, max_length=100)
    body: str = Field(default="", max_length=1500)


class CardEdit(Input):
    client_request_id: UUID
    expected_revision: int = Field(ge=1)
    cards: list[Card] = Field(min_length=2, max_length=9)
    visual: VisualTemplate = Field(default_factory=VisualTemplate)


class MediaEdit(Input):
    expected_revision: int = Field(ge=1)
    asset_ids: list[UUID] = Field(min_length=1, max_length=9)


class MediaImport(Input):
    expected_revision: int = Field(ge=1)
    resource_ref: str = Field(min_length=8, max_length=16384, pattern=r"^fref1_")
    source: str = Field(min_length=1, max_length=1000)
    rights: str = Field(min_length=1, max_length=1000)
    position: int = Field(ge=0, le=8)


class PackageCheck(Input):
    expected_revision: int = Field(ge=1)
    semantic_run_id: UUID


class PublishCreate(Input):
    content_id: UUID
    expected_revision: int = Field(ge=1)
    connection_id: UUID
    check_id: UUID
    visibility: Literal["public", "private"]


class PublishConfirm(Input):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_risks: Literal[True]


class PublishVerify(Input):
    remote_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    url: str = Field(max_length=300)
    note: str = Field(min_length=10, max_length=2000)


class MediaLocate(Input):
    path: str = Field(min_length=1, max_length=1000)


class PublishNotPublished(Input):
    note: str = Field(min_length=30, max_length=2000)
    account_checked: Literal[True]
    all_statuses_checked: Literal[True]
    acknowledge_new_confirmation: Literal[True]


class PublishSchedule(PublishCreate):
    scheduled_at: datetime
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    grace_seconds: int = Field(default=60, ge=60, le=3600)


class MetricCollect(Input):
    client_request_id: UUID
    url: str = Field(min_length=1, max_length=2048)
    comment_pages: int = Field(default=0, ge=0, le=3)
    published_at: datetime | None = None
    content_type: Literal["image", "video", "text"] = "image"

    @field_validator("published_at")
    @classmethod
    def aware_publication(cls, value):
        if value and (value.tzinfo is None or value > datetime.now(timezone.utc)):
            raise ValueError("发布时间必须包含时区且不能在未来")
        return value


class ReviewCreate(Input):
    client_request_id: UUID
    intent_ids: list[UUID] = Field(min_length=1, max_length=30)
    start: datetime
    end: datetime
    kind: Literal["postmortem", "comment_insights", "strategy"] = "postmortem"
    stages: list[StageSelection] = Field(min_length=1, max_length=1)
    max_credits: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def range_valid(self):
        if not self.start.tzinfo or not self.end.tzinfo or self.start >= self.end:
            raise ValueError("需要带时区且递增的时间范围")
        if len(set(self.intent_ids)) != len(self.intent_ids):
            raise ValueError("内容不能重复")
        return self


class SuggestionDecision(Input):
    decision: Literal["accept", "reject"]
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    expected_profile_revision: int | None = Field(default=None, ge=1)


class AnalyticsSchedule(Input):
    intent_id: UUID
    collection: MetricCollect
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=720)
    review_frequency: Literal["none", "weekly", "monthly"] = "none"
    review_stage: StageSelection | None = None
    max_credits: int = Field(default=10, ge=1, le=100)
