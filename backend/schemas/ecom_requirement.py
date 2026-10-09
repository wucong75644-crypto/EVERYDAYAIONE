"""电商图 AI 帮写：单份可编辑资料及补充更新协议。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUIREMENT_MAX_LENGTH = 10000
ContentType = Literal["default", "main_image", "detail_page"]
Platform = Literal["auto", "taobao", "tmall", "jd", "pdd"]
Language = Literal["zh-CN", "none"]


class RequirementSource(BaseModel):
    type: Literal["detail_project"]
    project_id: str = Field(min_length=1, max_length=100)


class RequirementSettings(BaseModel):
    content_type: ContentType = "main_image"
    platform: Platform = "auto"
    language: Language = "zh-CN"
    aspect_ratio: str = Field(default="1:1", min_length=1, max_length=20)
    quality: Literal["1k", "2k", "4k"] = "1k"
    image_count: int = Field(default=5, ge=1, le=15)
    requirement: str = Field(default="", max_length=REQUIREMENT_MAX_LENGTH)


class DraftField(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequirementSellingPoint(DraftField):
    feature: str = Field(min_length=1, max_length=500)
    benefit: str = Field(min_length=1, max_length=500)
    benefit_basis: Literal["direct", "inferred"]


class RequirementCreativeDirection(DraftField):
    topic: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    basis: Literal["explicit", "suggested"]


class RequirementSupplementQuestion(DraftField):
    question: str = Field(min_length=1, max_length=300)
    why: str = Field(min_length=1, max_length=300)
    can_skip: Literal[True] = True


class RequirementAssistResult(DraftField):
    product_description: str = Field(min_length=1, max_length=3000)
    selling_points: list[RequirementSellingPoint] = Field(default_factory=list, max_length=12)
    creative_requirements: list[RequirementCreativeDirection] = Field(default_factory=list, max_length=20)
    supplement_questions: list[RequirementSupplementQuestion] = Field(default_factory=list, max_length=3)


class RequirementRevision(BaseModel):
    """人工编辑稿是待更新资料，不是已验证事实或系统指令。"""
    draft: RequirementAssistResult
    supplement: str = Field(default="", max_length=4000)
    skipped_questions: list[str] = Field(default_factory=list, max_length=30)


class RequirementSuggestionsRequest(BaseModel):
    source: RequirementSource
    settings: RequirementSettings
    revision: RequirementRevision | None = None


class RequirementImage(BaseModel):
    id: str
    original_url: str
    display_name: str
    position: int = Field(default=0, ge=0)


class RequirementAssistInput(BaseModel):
    user_id: str
    org_id: str | None
    source_type: Literal["detail_project"]
    source_id: str
    product_images: list[RequirementImage] = Field(min_length=1, max_length=9)
    reference_images: list[RequirementImage] = Field(default_factory=list, max_length=8)
    content_type: ContentType
    platform: Platform
    language: Language
    aspect_ratio: str
    quality: Literal["1k", "2k", "4k"]
    image_count: int = Field(ge=1, le=15)
    user_requirement: str = Field(max_length=REQUIREMENT_MAX_LENGTH)
    project_version: int = Field(gt=0)
    revision: RequirementRevision | None = None

    @model_validator(mode="after")
    def validate_total_images(self) -> "RequirementAssistInput":
        if len(self.product_images) + len(self.reference_images) > 9:
            raise ValueError("产品图和参考图合计不能超过9张")
        return self


class RequirementAssistMeta(BaseModel):
    model: str
    fallback_used: bool = False
    latency_ms: int = Field(ge=0)
    project_version: int = Field(gt=0)


class RequirementSuggestionsEnvelope(BaseModel):
    success: bool = True
    data: RequirementAssistResult
    error: None = None
    meta: RequirementAssistMeta
