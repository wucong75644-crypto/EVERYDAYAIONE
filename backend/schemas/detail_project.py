"""主图详情页草稿 API 数据结构。"""

from typing import Literal

from pydantic import BaseModel, Field


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
    content_type: Literal["main_image", "detail_page"] | None = None
    platform: Literal["auto", "taobao", "tmall", "jd", "pdd"] | None = None
    requirement: str | None = Field(default=None, max_length=2000)
    language: Literal["zh-CN", "none"] | None = None
    aspect_ratio: str | None = Field(default=None, min_length=1, max_length=20)
    quality: Literal["1k", "2k", "4k"] | None = None
    image_count: int | None = Field(default=None, ge=1, le=9)


class DetailProjectVersionRequest(BaseModel):
    version: int = Field(gt=0)


class DetailImageCategoryPatch(DetailProjectVersionRequest):
    category: Literal["product", "reference"]


class DetailImageOrderRequest(DetailProjectVersionRequest):
    image_ids: list[str] = Field(min_length=1, max_length=9)
