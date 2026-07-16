"""电商图 AI 帮写的请求、标准输入与响应结构。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


ContentType = Literal["main_image", "detail_page"]
Platform = Literal["auto", "taobao", "tmall", "jd", "pdd"]
Language = Literal["zh-CN", "none"]
SourceType = Literal["detail_project"]
SuggestionId = Literal["selling_point", "scene", "creative"]


class RequirementSource(BaseModel):
    type: SourceType
    project_id: str = Field(min_length=1, max_length=100)


class RequirementSettings(BaseModel):
    content_type: ContentType = "main_image"
    platform: Platform = "auto"
    language: Language = "zh-CN"
    aspect_ratio: str = Field(default="1:1", min_length=1, max_length=20)
    quality: Literal["1k", "2k", "4k"] = "1k"
    image_count: int = Field(default=5, ge=1, le=9)
    requirement: str = Field(default="", max_length=2000)


class RequirementSuggestionsRequest(BaseModel):
    source: RequirementSource
    settings: RequirementSettings


class RequirementImage(BaseModel):
    id: str
    original_url: str
    display_name: str


class RequirementAssistInput(BaseModel):
    user_id: str
    org_id: str | None
    source_type: SourceType
    source_id: str
    product_images: list[RequirementImage] = Field(min_length=1, max_length=9)
    reference_images: list[RequirementImage] = Field(default_factory=list, max_length=8)
    content_type: ContentType
    platform: Platform
    language: Language
    aspect_ratio: str
    quality: Literal["1k", "2k", "4k"]
    image_count: int = Field(ge=1, le=9)
    user_requirement: str = Field(max_length=2000)
    project_version: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_total_images(self) -> "RequirementAssistInput":
        if len(self.product_images) + len(self.reference_images) > 9:
            raise ValueError("产品图和参考图合计不能超过9张")
        return self


class ProductFacts(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)
    confirmed_attributes: list[str] = Field(default_factory=list, max_length=30)
    unclear_items: list[str] = Field(default_factory=list, max_length=30)


class ReferenceAnalysis(BaseModel):
    image_id: str
    primary_uses: list[
        Literal["background", "composition", "color", "lighting", "texture", "typography", "rhythm"]
    ] = Field(min_length=1, max_length=7)
    summary: str = Field(min_length=1, max_length=500)
    excluded_elements: list[str] = Field(default_factory=list, max_length=20)


class RequirementConflict(BaseModel):
    field: str = Field(min_length=1, max_length=100)
    user_value: str = Field(min_length=1, max_length=200)
    confirmed_value: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=500)
    blocked_claims: list[str] = Field(min_length=1, max_length=10)


class RequirementSuggestion(BaseModel):
    id: SuggestionId
    name: str = Field(min_length=1, max_length=50)
    style_name: str = Field(min_length=1, max_length=100)
    brief_markdown: str = Field(min_length=1, max_length=4000)


class RequirementAssistResult(BaseModel):
    product_facts: ProductFacts
    reference_analyses: list[ReferenceAnalysis] = Field(default_factory=list, max_length=8)
    conflicts: list[RequirementConflict] = Field(default_factory=list, max_length=20)
    suggestions: list[RequirementSuggestion] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_suggestion_ids(self) -> "RequirementAssistResult":
        expected = {"selling_point", "scene", "creative"}
        if {item.id for item in self.suggestions} != expected:
            raise ValueError("必须返回 selling_point、scene、creative 三套方案")
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
