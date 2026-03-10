"""
订阅相关的请求/响应模型

定义模型订阅管理接口的数据结构。
"""

from typing import Optional

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    """模型基础信息"""

    id: str = Field(..., description="模型ID")
    status: str = Field(default="active", description="模型状态")


class ModelListResponse(BaseModel):
    """模型列表响应"""

    models: list[ModelInfo] = Field(..., description="模型列表")


class SubscriptionItem(BaseModel):
    """单个订阅记录"""

    model_id: str = Field(..., description="模型ID")
    subscribed_at: str = Field(..., description="订阅时间")


class SubscriptionListResponse(BaseModel):
    """订阅列表响应"""

    subscriptions: list[SubscriptionItem] = Field(..., description="订阅列表")


class SubscriptionActionResponse(BaseModel):
    """订阅操作响应"""

    message: str = Field(..., description="操作结果消息")
    model_id: str = Field(..., description="模型ID")
