"""
模型列表路由

提供模型基础信息的公开查询接口（无需登录）。
"""

from fastapi import APIRouter

from schemas.subscription import ModelListResponse
from services.subscription_service import SubscriptionService

router = APIRouter(prefix="/models", tags=["模型"])


@router.get(
    "",
    response_model=ModelListResponse,
    summary="获取所有模型列表",
)
async def get_models() -> dict:
    """
    获取所有模型的基础信息（id、status）

    公开接口，无需登录。
    """
    models = SubscriptionService.get_all_models()
    return {"models": models}
