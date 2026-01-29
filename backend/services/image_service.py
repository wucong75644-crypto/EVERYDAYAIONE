"""
图像生成服务

封装图像生成的业务逻辑，包括积分检查、调用 KIE 适配器、记录消费等。
"""

from typing import Optional, List, Dict, Any

from loguru import logger
from supabase import Client

from core.config import settings
from core.exceptions import AppException, InsufficientCreditsError
from services.adapters.kie.client import KieClient, KieAPIError
from services.adapters.kie.image_adapter import KieImageAdapter
from services.base_generation_service import BaseGenerationService
from services.oss_service import get_oss_service


class ImageService(BaseGenerationService):
    """图像生成服务"""

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
        # 0. 检查 KIE API Key 是否配置
        if not self.settings.kie_api_key:
            raise AppException(
                code="SERVICE_NOT_CONFIGURED",
                message="图像生成服务未配置，请联系管理员",
                status_code=503,
            )

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
            change_type="image_generation_cost",
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

            # 4. 如果生成完成，将图片上传到 OSS
            # 注意：adapter 返回的状态是 "success"，不是 "finished"
            if wait_for_result and result.get("status") == "success":
                result = await self._upload_images_to_oss(result, user_id)

            # 5. 确保返回实际扣除的积分数（异步模式下 adapter 返回 0）
            result["credits_consumed"] = estimated_credits

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
        # 0. 检查 KIE API Key 是否配置
        if not self.settings.kie_api_key:
            raise AppException(
                code="SERVICE_NOT_CONFIGURED",
                message="图像编辑服务未配置，请联系管理员",
                status_code=503,
            )

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
            change_type="image_generation_cost",
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

            # 如果生成完成，将图片上传到 OSS
            if wait_for_result and result.get("status") == "success":
                result = await self._upload_images_to_oss(result, user_id)

            # 确保返回实际扣除的积分数
            result["credits_consumed"] = estimated_credits

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code="IMAGE_EDIT_FAILED",
                message=f"图像编辑失败: {str(e)}",
                status_code=500,
            )

    async def query_task(
        self,
        task_id: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        查询任务状态

        Args:
            task_id: 任务 ID
            user_id: 用户 ID（可选，用于图片完成时上传到 OSS）

        Returns:
            任务状态
        """
        # 检查 KIE API Key 是否配置
        if not self.settings.kie_api_key:
            raise AppException(
                code="SERVICE_NOT_CONFIGURED",
                message="图像服务未配置，请联系管理员",
                status_code=503,
            )

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                # 使用基础模型查询（任务查询不需要特定模型）
                adapter = KieImageAdapter(client, "google/nano-banana")
                result = await adapter.query_task(task_id)

            # 如果图片生成完成且提供了 user_id，上传到 OSS
            # 注意：image adapter 返回的状态是 "success"，不是 "finished"
            if result.get("status") == "success" and user_id:
                result = await self._upload_images_to_oss(result, user_id)

            return result

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

    async def _upload_images_to_oss(
        self,
        result: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """
        将生成的图片上传到 OSS

        Args:
            result: KIE 返回的结果（包含 image_urls 字段）
            user_id: 用户 ID

        Returns:
            替换 URL 后的结果
        """
        image_urls = result.get("image_urls", [])
        if not image_urls:
            return result

        # 检查 OSS 是否配置
        if not settings.oss_access_key_id:
            logger.warning("OSS not configured, skipping upload")
            return result

        try:
            oss_service = get_oss_service()
        except ValueError as e:
            logger.warning(f"OSS service init failed: {e}, skipping upload")
            return result

        oss_urls = []
        for i, image_url in enumerate(image_urls):
            try:
                upload_result = await oss_service.upload_from_url(
                    url=image_url,
                    user_id=user_id,
                    category="generated",
                )
                oss_urls.append(upload_result["url"])
                logger.info(
                    f"Image {i+1}/{len(image_urls)} uploaded to OSS: "
                    f"object_key={upload_result['object_key']}"
                )
            except Exception as e:
                logger.error(f"Failed to upload image to OSS: {e}")
                # 上传失败时保留原 URL
                oss_urls.append(image_url)

        # 替换 URL
        result["image_urls"] = oss_urls
        result["oss_uploaded"] = True

        return result
