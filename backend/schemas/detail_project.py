"""主图详情页草稿 API 数据结构。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from schemas.ecom_requirement import REQUIREMENT_MAX_LENGTH


class DetailImageAttachRequest(BaseModel):
    workspace_path: str = Field(min_length=1, max_length=500)
    category: Literal["product", "reference"]


class DetailProjectEnvelope(BaseModel):
    success: bool = True
    data: dict
    error: None = None
    meta: dict = Field(default_factory=dict)


class DetailProjectSettingsPatch(BaseModel):
    version: int = Field(gt=0)
    content_type: Literal["default", "main_image", "detail_page"] | None = None
    prompt_model: Literal["kimi-k3", "gemini-3.8-flash"] | None = None
    platform: Literal["auto", "taobao", "tmall", "jd", "pdd"] | None = None
    requirement: str | None = Field(default=None, max_length=REQUIREMENT_MAX_LENGTH)
    language: Literal["zh-CN", "none"] | None = None
    aspect_ratio: str | None = Field(default=None, min_length=1, max_length=20)
    quality: Literal["1k", "2k", "4k"] | None = None
    image_count: int | None = Field(default=None, ge=1, le=15)


class DetailProjectCreateRequest(BaseModel):
    request_id: UUID


class DetailRunRequest(BaseModel):
    version: int = Field(gt=0)
    request_id: UUID


class DetailResumeRequest(BaseModel):
    plan_id: UUID
    request_id: UUID


class DetailProjectVersionRequest(BaseModel):
    version: int = Field(gt=0)


class DetailImageCategoryPatch(DetailProjectVersionRequest):
    category: Literal["product", "reference"]


class DetailImageOrderRequest(DetailProjectVersionRequest):
    image_ids: list[str] = Field(min_length=1, max_length=9)
