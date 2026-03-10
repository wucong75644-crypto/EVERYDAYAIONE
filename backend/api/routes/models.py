"""
模型列表路由

提供模型基础信息的公开查询接口（无需登录）。
"""

from fastapi import APIRouter, Depends

from api.deps import Database
from schemas.subscription import ModelListResponse
from services.subscription_service import SubscriptionService

router = APIRouter(prefix="/models", tags=["模型"])


def get_subscription_service(db: Database) -> SubscriptionService:
    """获取订阅服务实例"""
    return SubscriptionService(db)


@router.get(
    "",
    response_model=ModelListResponse,
    summary="获取所有模型列表",
)
async def get_models(
    service: SubscriptionService = Depends(get_subscription_service),
) -> dict:
    """
    获取所有模型的基础信息（is_default、status）

    公开接口，无需登录。
    """
    models = service.get_all_models()
    return {"models": models}
