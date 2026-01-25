"""
图像生成服务

封装图像生成的业务逻辑，包括积分检查、调用 KIE 适配器、记录消费等。
"""

from typing import Optional, List, Dict, Any

from loguru import logger
from supabase import Client

from core.config import get_settings
from core.exceptions import (
    AppException,
    InsufficientCreditsError,
    NotFoundError,
)
from services.adapters.kie.client import KieClient, KieAPIError
from services.adapters.kie.image_adapter import KieImageAdapter


class ImageService:
    """图像生成服务"""

    def __init__(self, db: Client):
        """
        初始化服务

        Args:
            db: Supabase 数据库客户端
        """
        self.db = db
        self.settings = get_settings()

    async def generate_image(
        self,
        user_id: str,
        prompt: str,
        model: str = "google/nano-banana",
        size: str = "1:1",
        output_format: str = "png",
        resolution: Optional[str] = None,
        wait_for_result: bool = True,
    ) -> Dict[str, Any]:
        """
        生成图像

        Args:
            user_id: 用户 ID
            prompt: 图像描述
            model: 模型名称
            size: 宽高比
            output_format: 输出格式
            resolution: 分辨率（仅 nano-banana-pro）
            wait_for_result: 是否等待结果

        Returns:
            生成结果

        Raises:
            InsufficientCreditsError: 积分不足
            AppException: 生成失败
        """
        # 1. 获取用户并检查积分
        user = await self._get_user(user_id)
        estimated_credits = self._estimate_credits(model, resolution)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting image generation: user_id={user_id}, model={model}, "
            f"size={size}, resolution={resolution}"
        )

        # 2. 立即扣除预估积分（异步模式下无法获取实际消耗）
        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"图像生成: {model}",
            metadata={
                "model": model,
                "size": size,
                "resolution": resolution,
            },
        )

        try:
            # 3. 调用 KIE 生成图像
            async with KieClient(self.settings.kie_api_key) as client:
                adapter = KieImageAdapter(client, model)
                result = await adapter.generate(
                    prompt=prompt,
                    size=size,
                    output_format=output_format,
                    resolution=resolution,
                    wait_for_result=wait_for_result,
                )

            logger.info(
                f"Image generated: user_id={user_id}, task_id={result.get('task_id')}, "
                f"credits={estimated_credits}"
            )

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="IMAGE_GENERATION_FAILED",
                message=f"图像生成失败: {str(e)}",
                status_code=500,
            )

    async def edit_image(
        self,
        user_id: str,
        prompt: str,
        image_urls: List[str],
        size: str = "1:1",
        output_format: str = "png",
        wait_for_result: bool = True,
    ) -> Dict[str, Any]:
        """
        编辑图像

        Args:
            user_id: 用户 ID
            prompt: 编辑指令
            image_urls: 输入图片 URL
            size: 输出宽高比
            output_format: 输出格式
            wait_for_result: 是否等待结果

        Returns:
            编辑结果
        """
        model = "google/nano-banana-edit"

        # 1. 获取用户并检查积分
        user = await self._get_user(user_id)
        estimated_credits = self._estimate_credits(model)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting image edit: user_id={user_id}, image_count={len(image_urls)}"
        )

        # 立即扣除预估积分
        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description="图像编辑",
            metadata={
                "model": model,
            },
        )

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                adapter = KieImageAdapter(client, model)
                result = await adapter.generate(
                    prompt=prompt,
                    image_urls=image_urls,
                    size=size,
                    output_format=output_format,
                    wait_for_result=wait_for_result,
                )

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="IMAGE_EDIT_FAILED",
                message=f"图像编辑失败: {str(e)}",
                status_code=500,
            )

    async def query_task(self, task_id: str) -> Dict[str, Any]:
        """
        查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态
        """
        try:
            async with KieClient(self.settings.kie_api_key) as client:
                # 使用基础模型查询（任务查询不需要特定模型）
                adapter = KieImageAdapter(client, "google/nano-banana")
                return await adapter.query_task(task_id)

        except KieAPIError as e:
            logger.error(f"Query task failed: task_id={task_id}, error={e}")
            raise AppException(
                code="TASK_QUERY_FAILED",
                message=f"任务查询失败: {str(e)}",
                status_code=500,
            )

    def get_available_models(self) -> List[Dict[str, Any]]:
        """
        获取可用的图像模型列表

        Returns:
            模型信息列表
        """
        models = []
        for model_id, config in KieImageAdapter.MODEL_CONFIGS.items():
            models.append({
                "model_id": model_id,
                "description": config["description"],
                "requires_image_input": config["requires_image_input"],
                "supported_sizes": config["supported_sizes"],
                "supported_formats": config["supported_formats"],
                "supports_resolution": config["supports_resolution"],
                "credits_per_image": config.get("credits_per_image", 0),
            })
        return models

    # ============================================================
    # 私有方法
    # ============================================================

    async def _get_user(self, user_id: str) -> Dict[str, Any]:
        """获取用户信息"""
        response = (
            self.db.table("users")
            .select("id, credits")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not response.data:
            raise NotFoundError("用户")

        return response.data

    def _estimate_credits(
        self,
        model: str,
        resolution: Optional[str] = None,
    ) -> int:
        """估算积分消耗"""
        config = KieImageAdapter.MODEL_CONFIGS.get(model, {})
        credits_per_image = config.get("credits_per_image", 0)

        if isinstance(credits_per_image, dict):
            return credits_per_image.get(resolution or "1K", 25)

        return credits_per_image

    async def _deduct_credits(
        self,
        user_id: str,
        credits: int,
        description: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        扣除用户积分

        Args:
            user_id: 用户 ID
            credits: 扣除的积分数
            description: 描述
            metadata: 额外元数据
        """
        # 1. 获取当前积分
        current_credits = (
            self.db.table("users")
            .select("credits")
            .eq("id", user_id)
            .single()
            .execute()
            .data["credits"]
        )

        new_balance = current_credits - credits

        # 2. 更新用户积分
        self.db.table("users").update({
            "credits": new_balance
        }).eq("id", user_id).execute()

        # 3. 记录积分历史
        self.db.table("credits_history").insert({
            "user_id": user_id,
            "change_amount": -credits,
            "balance_after": new_balance,
            "change_type": "image_generation_cost",
            "description": description,
        }).execute()

        logger.info(
            f"Credits deducted: user_id={user_id}, credits={credits}, "
            f"balance_after={new_balance}, description={description}"
        )
