"""
记忆功能 Pydantic 模型

定义记忆相关的请求/响应模型。
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ===== 请求模型 =====


class MemoryAddRequest(BaseModel):
    """添加记忆请求"""

    content: str = Field(..., min_length=1, max_length=500, description="记忆内容")


class MemoryUpdateRequest(BaseModel):
    """更新记忆请求"""

    content: str = Field(..., min_length=1, max_length=500, description="记忆内容")


class MemorySettingsUpdateRequest(BaseModel):
    """更新记忆设置请求"""

    memory_enabled: Optional[bool] = None
    retention_days: Optional[int] = Field(None, ge=1, le=90)


# ===== 响应模型 =====


class MemoryMetadata(BaseModel):
    """记忆元数据"""

    source: Literal["auto", "manual"] = "manual"
    conversation_id: Optional[str] = None


class MemoryItem(BaseModel):
    """单条记忆"""

    id: str
    memory: str
    metadata: MemoryMetadata = MemoryMetadata()
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MemoryListResponse(BaseModel):
    """记忆列表响应"""

    memories: List[MemoryItem]
    total: int


class MemoryAddResponse(BaseModel):
    """添加记忆响应"""

    memories: List[MemoryItem] = []
    count: int = 0


class MemoryUpdateResponse(BaseModel):
    """更新记忆响应"""

    id: str
    memory: str
    updated_at: Optional[str] = None


class MemoryDeleteResponse(BaseModel):
    """删除记忆响应"""

    message: str = "记忆已删除"


class MemoryDeleteAllResponse(BaseModel):
    """清空记忆响应"""

    message: str = "所有记忆已清空"


class MemorySettingsResponse(BaseModel):
    """记忆设置响应"""

    memory_enabled: bool = True
    retention_days: int = 7
    updated_at: Optional[str] = None
