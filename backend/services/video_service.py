"""
视频生成服务

封装视频生成的业务逻辑，包括积分检查、调用 KIE 适配器、记录消费等。
"""

from typing import Optional, List, Dict, Any

from loguru import logger
from supabase import Client

from core.exceptions import AppException, InsufficientCreditsError
from services.adapters.kie.client import KieClient, KieAPIError
from services.adapters.kie.video_adapter import KieVideoAdapter
from services.base_generation_service import BaseGenerationService


class VideoService(BaseGenerationService):
    """视频生成服务"""

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
        return await self._generate_with_credits(
            user_id=user_id,
            model=model,
            n_frames=n_frames,
            description="文生视频",
            error_message="视频生成失败",
            generate_kwargs={
                "prompt": prompt,
                "n_frames": n_frames,
                "aspect_ratio": aspect_ratio,
                "remove_watermark": remove_watermark,
                "wait_for_result": wait_for_result,
            },
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
        return await self._generate_with_credits(
            user_id=user_id,
            model=model,
            n_frames=n_frames,
            description="图生视频",
            error_message="图生视频失败",
            generate_kwargs={
                "prompt": prompt,
                "image_urls": [image_url],
                "n_frames": n_frames,
                "aspect_ratio": aspect_ratio,
                "remove_watermark": remove_watermark,
                "wait_for_result": wait_for_result,
            },
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
        return await self._generate_with_credits(
            user_id=user_id,
            model=model,
            n_frames=n_frames,
            description="故事板视频",
            error_message="故事板视频生成失败",
            generate_kwargs={
                "image_urls": storyboard_images,
                "n_frames": n_frames,
                "aspect_ratio": aspect_ratio,
                "wait_for_result": wait_for_result,
            },
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

    async def _generate_with_credits(
        self,
        user_id: str,
        model: str,
        n_frames: str,
        description: str,
        error_message: str,
        generate_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        通用视频生成流程（积分检查→扣除→生成）

        Args:
            user_id: 用户 ID
            model: 模型名称
            n_frames: 视频时长
            description: 积分扣除描述
            error_message: 错误提示前缀
            generate_kwargs: 传递给 adapter.generate 的参数

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
            f"Starting {description}: user_id={user_id}, model={model}, duration={duration}s"
        )

        # 2. 立即扣除预估积分
        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"{description}: {model}",
            change_type="video_generation_cost",
        )

        try:
            # 3. 调用 KIE 生成视频
            async with KieClient(self.settings.kie_api_key) as client:
                adapter = KieVideoAdapter(client, model)
                result = await adapter.generate(**generate_kwargs)

            logger.info(
                f"Video generation started: user_id={user_id}, task_id={result.get('task_id')}, "
                f"credits={estimated_credits}"
            )

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="VIDEO_GENERATION_FAILED",
                message=f"{error_message}: {str(e)}",
                status_code=500,
            )

    def _estimate_credits(
        self,
        model: str,
        duration_seconds: int,
    ) -> int:
        """估算积分消耗"""
        config = KieVideoAdapter.MODEL_CONFIGS.get(model, {})
        credits_per_second = config.get("credits_per_second", 0)
        return credits_per_second * duration_seconds
