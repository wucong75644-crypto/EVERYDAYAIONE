"""
订阅管理路由

提供模型订阅的查询、订阅和取消订阅接口。
"""

from fastapi import APIRouter, Depends

from api.deps import CurrentUser, Database
from schemas.subscription import (
    SubscriptionActionResponse,
    SubscriptionListResponse,
)
from services.subscription_service import SubscriptionService

router = APIRouter(prefix="/subscriptions", tags=["订阅管理"])


def get_subscription_service(db: Database) -> SubscriptionService:
    """获取订阅服务实例"""
    return SubscriptionService(db)


@router.get(
    "",
    response_model=SubscriptionListResponse,
    summary="获取用户订阅列表",
)
async def get_subscriptions(
    current_user: CurrentUser,
    service: SubscriptionService = Depends(get_subscription_service),
) -> dict:
    """
    获取当前用户已订阅的模型列表

    需要登录（Authorization: Bearer <token>）
    """
    subs = service.get_user_subscriptions(current_user["id"])
    return {
        "subscriptions": [
            {
                "model_id": s["model_id"],
                "subscribed_at": s["subscribed_at"],
            }
            for s in subs
        ],
    }


@router.post(
    "/{model_id}",
    response_model=SubscriptionActionResponse,
    summary="订阅模型",
)
async def subscribe_model(
    model_id: str,
    current_user: CurrentUser,
    service: SubscriptionService = Depends(get_subscription_service),
) -> dict:
    """
    订阅指定模型（幂等：已订阅则直接返回成功）

    - **model_id**: 模型 ID（路径参数）
    """
    return service.subscribe(current_user["id"], model_id)


@router.delete(
    "/{model_id}",
    response_model=SubscriptionActionResponse,
    summary="取消订阅",
)
async def unsubscribe_model(
    model_id: str,
    current_user: CurrentUser,
    service: SubscriptionService = Depends(get_subscription_service),
) -> dict:
    """
    取消订阅指定模型（默认模型不可取消）

    - **model_id**: 模型 ID（路径参数）
    """
    return service.unsubscribe(current_user["id"], model_id)
