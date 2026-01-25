"""
视频生成服务

封装视频生成的业务逻辑，包括积分检查、调用 KIE 适配器、记录消费等。
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
from services.adapters.kie.video_adapter import KieVideoAdapter


class VideoService:
    """视频生成服务"""

    def __init__(self, db: Client):
        """
        初始化服务

        Args:
            db: Supabase 数据库客户端
        """
        self.db = db
        self.settings = get_settings()

    async def generate_text_to_video(
        self,
        user_id: str,
        prompt: str,
        model: str = "sora-2-text-to-video",
        n_frames: str = "10",
        aspect_ratio: str = "landscape",
        remove_watermark: bool = True,
        wait_for_result: bool = False,
    ) -> Dict[str, Any]:
        """
        文本生成视频

        Args:
            user_id: 用户 ID
            prompt: 视频描述
            model: 模型名称
            n_frames: 视频时长 ("10"/"15")
            aspect_ratio: 宽高比 ("portrait"/"landscape")
            remove_watermark: 是否去水印
            wait_for_result: 是否等待结果

        Returns:
            生成结果

        Raises:
            InsufficientCreditsError: 积分不足
            AppException: 生成失败
        """
        # 1. 获取用户并检查积分
        user = await self._get_user(user_id)
        duration = int(n_frames)
        estimated_credits = self._estimate_credits(model, duration)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting text-to-video generation: user_id={user_id}, model={model}, "
            f"duration={duration}s, aspect_ratio={aspect_ratio}"
        )

        # 2. 立即扣除预估积分（异步模式下无法获取实际消耗）
        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"文生视频: {model}",
            metadata={
                "model": model,
                "duration_seconds": duration,
                "aspect_ratio": aspect_ratio,
            },
        )

        try:
            # 3. 调用 KIE 生成视频
            async with KieClient(self.settings.kie_api_key) as client:
                adapter = KieVideoAdapter(client, model)
                result = await adapter.generate(
                    prompt=prompt,
                    n_frames=n_frames,
                    aspect_ratio=aspect_ratio,
                    remove_watermark=remove_watermark,
                    wait_for_result=wait_for_result,
                )

            logger.info(
                f"Video generation started: user_id={user_id}, task_id={result.get('task_id')}, "
                f"credits={estimated_credits}"
            )

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="VIDEO_GENERATION_FAILED",
                message=f"视频生成失败: {str(e)}",
                status_code=500,
            )

    async def generate_image_to_video(
        self,
        user_id: str,
        prompt: str,
        image_url: str,
        model: str = "sora-2-image-to-video",
        n_frames: str = "10",
        aspect_ratio: str = "landscape",
        remove_watermark: bool = True,
        wait_for_result: bool = False,
    ) -> Dict[str, Any]:
        """
        图片生成视频

        Args:
            user_id: 用户 ID
            prompt: 视频描述
            image_url: 首帧图片 URL
            model: 模型名称
            n_frames: 视频时长
            aspect_ratio: 宽高比
            remove_watermark: 是否去水印
            wait_for_result: 是否等待结果

        Returns:
            生成结果
        """
        # 1. 获取用户并检查积分
        user = await self._get_user(user_id)
        duration = int(n_frames)
        estimated_credits = self._estimate_credits(model, duration)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting image-to-video generation: user_id={user_id}, model={model}, "
            f"duration={duration}s"
        )

        # 2. 立即扣除预估积分
        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"图生视频: {model}",
            metadata={
                "model": model,
                "duration_seconds": duration,
                "aspect_ratio": aspect_ratio,
            },
        )

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                adapter = KieVideoAdapter(client, model)
                result = await adapter.generate(
                    prompt=prompt,
                    image_urls=[image_url],
                    n_frames=n_frames,
                    aspect_ratio=aspect_ratio,
                    remove_watermark=remove_watermark,
                    wait_for_result=wait_for_result,
                )

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="VIDEO_GENERATION_FAILED",
                message=f"图生视频失败: {str(e)}",
                status_code=500,
            )

    async def generate_storyboard_video(
        self,
        user_id: str,
        model: str = "sora-2-pro-storyboard",
        n_frames: str = "15",
        storyboard_images: Optional[List[str]] = None,
        aspect_ratio: str = "landscape",
        wait_for_result: bool = False,
    ) -> Dict[str, Any]:
        """
        故事板视频生成

        Args:
            user_id: 用户 ID
            model: 模型名称
            n_frames: 视频时长 ("10"/"15"/"25")
            storyboard_images: 故事板图片列表
            aspect_ratio: 宽高比
            wait_for_result: 是否等待结果

        Returns:
            生成结果
        """
        # 1. 获取用户并检查积分
        user = await self._get_user(user_id)
        duration = int(n_frames)
        estimated_credits = self._estimate_credits(model, duration)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting storyboard video generation: user_id={user_id}, model={model}, "
            f"duration={duration}s"
        )

        # 2. 立即扣除预估积分
        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"故事板视频: {model}",
            metadata={
                "model": model,
                "duration_seconds": duration,
                "aspect_ratio": aspect_ratio,
            },
        )

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                adapter = KieVideoAdapter(client, model)
                result = await adapter.generate(
                    image_urls=storyboard_images,
                    n_frames=n_frames,
                    aspect_ratio=aspect_ratio,
                    wait_for_result=wait_for_result,
                )

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="VIDEO_GENERATION_FAILED",
                message=f"故事板视频生成失败: {str(e)}",
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
                adapter = KieVideoAdapter(client, "sora-2-text-to-video")
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
        获取可用的视频模型列表

        Returns:
            模型信息列表
        """
        models = []
        for model_id, config in KieVideoAdapter.MODEL_CONFIGS.items():
            models.append({
                "model_id": model_id,
                "description": config["description"],
                "requires_image_input": config["requires_image_input"],
                "requires_prompt": config["requires_prompt"],
                "supported_frames": config["supported_frames"],
                "supports_watermark_removal": config.get("supports_watermark_removal", False),
                "credits_per_second": config.get("credits_per_second", 0),
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
        duration_seconds: int,
    ) -> int:
        """估算积分消耗"""
        config = KieVideoAdapter.MODEL_CONFIGS.get(model, {})
        credits_per_second = config.get("credits_per_second", 0)
        return credits_per_second * duration_seconds

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
            "change_type": "video_generation_cost",
            "description": description,
        }).execute()

        logger.info(
            f"Credits deducted: user_id={user_id}, credits={credits}, "
            f"balance_after={new_balance}, description={description}"
        )
