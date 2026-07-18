"""逻辑关系图消息协议。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DiagramPart(BaseModel):
    """结构化 Mermaid 内容块，原始源码是唯一可信数据。"""

    type: Literal["diagram"] = "diagram"
    format: Literal["mermaid"] = "mermaid"
    source: str = Field(..., min_length=1, max_length=100_000)
    title: str = ""

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("diagram source 不能为空")
        return value
