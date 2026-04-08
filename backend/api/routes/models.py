"""
模型列表路由

提供模型基础信息的公开查询接口（无需登录）。
"""

from fastapi import APIRouter, Depends

from api.deps import Database, ScopedDB
from schemas.subscription import ModelListResponse
from services.subscription_service import SubscriptionService

router = APIRouter(prefix="/models", tags=["模型"])


def get_subscription_service(db: ScopedDB) -> SubscriptionService:
    """获取订阅服务实例（租户隔离）"""
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
    获取所有模型的基础信息（id、status）

    公开接口，无需登录。
    """
    models = service.get_all_models()
    return {"models": models}
