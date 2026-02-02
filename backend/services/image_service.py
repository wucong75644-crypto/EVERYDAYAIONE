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
        conversation_id: Optional[str] = None,
        placeholder_message_id: Optional[str] = None,
        placeholder_created_at: Optional[str] = None,
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
            conversation_id: 对话 ID（用于任务恢复）
            placeholder_message_id: 前端占位符消息 ID
            placeholder_created_at: 占位符创建时间（ISO 8601）

        Returns:
            生成结果

        Raises:
            InsufficientCreditsError: 积分不足
            AppException: 生成失败
        """
        return await self._generate_with_credits(
            user_id=user_id,
            model=model,
            description="图像生成",
            error_code="IMAGE_GENERATION_FAILED",
            error_message="图像生成失败",
            conversation_id=conversation_id,
            resolution=resolution,
            placeholder_message_id=placeholder_message_id,
            placeholder_created_at=placeholder_created_at,
            generate_kwargs={
                "prompt": prompt,
                "size": size,
                "output_format": output_format,
                "resolution": resolution,
                "wait_for_result": wait_for_result,
            },
        )

    async def edit_image(
        self,
        user_id: str,
        prompt: str,
        image_urls: List[str],
        size: str = "1:1",
        output_format: str = "png",
        wait_for_result: bool = True,
        conversation_id: Optional[str] = None,
        placeholder_message_id: Optional[str] = None,
        placeholder_created_at: Optional[str] = None,
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
            conversation_id: 对话 ID（用于任务恢复）
            placeholder_message_id: 前端占位符消息 ID
            placeholder_created_at: 占位符创建时间（ISO 8601）

        Returns:
            编辑结果

        Raises:
            InsufficientCreditsError: 积分不足
            AppException: 生成失败
        """
        return await self._generate_with_credits(
            user_id=user_id,
            model="google/nano-banana-edit",
            description="图像编辑",
            error_code="IMAGE_EDIT_FAILED",
            error_message="图像编辑失败",
            conversation_id=conversation_id,
            resolution=None,
            placeholder_message_id=placeholder_message_id,
            placeholder_created_at=placeholder_created_at,
            generate_kwargs={
                "prompt": prompt,
                "image_urls": image_urls,
                "size": size,
                "output_format": output_format,
                "wait_for_result": wait_for_result,
            },
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
            user_id: 用户 ID（必需，用于权限验证和图片上传到 OSS）

        Returns:
            任务状态

        Raises:
            NotFoundError: 任务不存在
            PermissionError: 用户无权访问该任务
        """
        # 检查 KIE API Key 是否配置
        if not self.settings.kie_api_key:
            raise AppException(
                code="SERVICE_NOT_CONFIGURED",
                message="图像服务未配置，请联系管理员",
                status_code=503,
            )

        # 验证任务所有权（防止未授权访问）
        if not user_id:
            raise AppException(
                code="MISSING_USER_ID",
                message="缺少用户ID",
                status_code=400,
            )

        task_info = await self._verify_task_ownership(
            external_task_id=task_id,
            user_id=user_id,
        )

        # 如果任务已完成，直接返回数据库中的缓存结果（避免调用已过期的 KIE API）
        if task_info.get("status") == "completed" and task_info.get("result"):
            cached_result = task_info["result"]
            logger.debug(
                f"Returning cached result for completed task: task_id={task_id}"
            )
            return {
                "task_id": task_id,
                "status": "success",
                "image_urls": cached_result.get("image_urls", []),
            }

        # 如果任务已失败，直接返回失败信息
        if task_info.get("status") == "failed":
            logger.debug(
                f"Returning cached failure for task: task_id={task_id}"
            )
            return {
                "task_id": task_id,
                "status": "failed",
                "fail_code": task_info.get("fail_code"),
                "fail_msg": task_info.get("error_message"),
            }

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                # 使用基础模型查询（任务查询不需要特定模型）
                adapter = KieImageAdapter(client, "google/nano-banana")
                result = await adapter.query_task(task_id)

            # 如果图片生成完成且提供了 user_id，先上传到 OSS 再更新状态
            # 注意：image adapter 返回的状态是 "success"，不是 "finished"
            if result.get("status") == "success" and user_id:
                try:
                    result = await self._upload_images_to_oss(result, user_id)
                except Exception as e:
                    # OSS 上传失败不影响任务查询结果，只记录日志
                    logger.warning(
                        f"Failed to upload images to OSS during query: "
                        f"task_id={task_id}, error={e}"
                    )

            # 统一更新数据库任务状态（如果成功上传OSS，result已包含OSS URL）
            await self._update_task_status(
                task_id=task_id,
                status=result.get("status"),
                result=result if result.get("status") == "success" else None,
                fail_code=result.get("fail_code"),
                fail_msg=result.get("fail_msg"),
            )

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

    async def _generate_with_credits(
        self,
        user_id: str,
        model: str,
        description: str,
        error_code: str,
        error_message: str,
        conversation_id: Optional[str],
        resolution: Optional[str],
        generate_kwargs: Dict[str, Any],
        placeholder_message_id: Optional[str] = None,
        placeholder_created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        通用图像生成流程（积分检查 -> 扣除 -> 生成）

        Args:
            user_id: 用户 ID
            model: 模型名称
            description: 积分扣除描述
            error_code: 错误代码
            error_message: 错误提示前缀
            conversation_id: 对话 ID
            resolution: 分辨率（用于积分计算）
            generate_kwargs: 传递给 adapter.generate 的参数
            placeholder_message_id: 前端占位符消息 ID
            placeholder_created_at: 占位符创建时间（ISO 8601）

        Returns:
            生成结果

        Raises:
            AppException: 服务未配置或生成失败
            InsufficientCreditsError: 积分不足
        """
        # 1. 验证并扣除积分
        estimated_credits = await self._validate_and_deduct_credits(
            user_id=user_id,
            model=model,
            resolution=resolution,
            description=description,
        )

        try:
            # 2. 调用 KIE API
            result, task_id = await self._call_kie_api(
                model=model,
                generate_kwargs=generate_kwargs,
                user_id=user_id,
                conversation_id=conversation_id,
                estimated_credits=estimated_credits,
                placeholder_message_id=placeholder_message_id,
                placeholder_created_at=placeholder_created_at,
            )

            # 3. 同步模式完成处理
            wait_for_result = generate_kwargs.get("wait_for_result", True)
            if wait_for_result and result.get("status") == "success":
                result = await self._handle_sync_completion(
                    result=result,
                    task_id=task_id,
                    user_id=user_id,
                )

            # 4. 添加 credits_consumed 字段
            result["credits_consumed"] = estimated_credits

            return result

        except KieAPIError as e:
            logger.error(f"KIE API error: user_id={user_id}, error={e}")
            raise AppException(
                code=error_code,
                message=f"{error_message}: {str(e)}",
                status_code=500,
            )

    async def _validate_and_deduct_credits(
        self,
        user_id: str,
        model: str,
        resolution: Optional[str],
        description: str,
    ) -> int:
        """
        验证用户并扣除积分

        Args:
            user_id: 用户 ID
            model: 模型名称
            resolution: 分辨率（用于积分计算）
            description: 积分扣除描述

        Returns:
            预估积分数

        Raises:
            AppException: 服务未配置
            InsufficientCreditsError: 积分不足
        """
        if not self.settings.kie_api_key:
            raise AppException(
                code="SERVICE_NOT_CONFIGURED",
                message="图像服务未配置，请联系管理员",
                status_code=503,
            )

        user = await self._get_user(user_id)
        estimated_credits = self._estimate_credits(model, resolution)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting {description}: user_id={user_id}, model={model}, "
            f"resolution={resolution}"
        )

        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"{description}: {model}",
            change_type="image_generation_cost",
        )

        return estimated_credits

    async def _call_kie_api(
        self,
        model: str,
        generate_kwargs: Dict[str, Any],
        user_id: str,
        conversation_id: Optional[str],
        estimated_credits: int,
        placeholder_message_id: Optional[str] = None,
        placeholder_created_at: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """
        调用 KIE API 生成图像

        Args:
            model: 模型名称
            generate_kwargs: 传递给 adapter.generate 的参数
            user_id: 用户 ID
            conversation_id: 会话 ID
            estimated_credits: 预估积分
            placeholder_message_id: 前端占位符消息 ID
            placeholder_created_at: 占位符创建时间（ISO 8601）

        Returns:
            (result, task_id) 元组
        """
        async with KieClient(self.settings.kie_api_key) as client:
            adapter = KieImageAdapter(client, model)
            result = await adapter.generate(**generate_kwargs)

        task_id = result.get("task_id")

        logger.info(
            f"Image generated: user_id={user_id}, task_id={task_id}, "
            f"credits={estimated_credits}"
        )

        if task_id and conversation_id:
            await self._save_task_to_db(
                user_id=user_id,
                conversation_id=conversation_id,
                task_id=task_id,
                task_type="image",
                request_params={
                    "model": model,
                    **generate_kwargs,
                },
                credits_locked=estimated_credits,
                placeholder_message_id=placeholder_message_id,
                placeholder_created_at=placeholder_created_at,
            )

        return result, task_id

    async def _handle_sync_completion(
        self,
        result: Dict[str, Any],
        task_id: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """
        同步模式完成处理（OSS 上传 + 状态更新）

        Args:
            result: KIE 返回的结果
            task_id: 任务 ID
            user_id: 用户 ID

        Returns:
            更新后的 result
        """
        result = await self._upload_images_to_oss(result, user_id)

        if task_id:
            await self._update_task_status(
                task_id=task_id,
                status="success",
                result=result,
            )

        return result

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

        # 检查是否已经是 OSS URL（避免重复上传）
        cdn_domain = settings.oss_cdn_domain
        oss_domain = f"{settings.oss_bucket_name}.{settings.oss_endpoint}"
        first_url = image_urls[0].lower()
        if cdn_domain and cdn_domain in first_url:
            logger.debug("Images already uploaded to OSS (CDN URL detected)")
            return result
        if oss_domain and oss_domain in first_url:
            logger.debug("Images already uploaded to OSS (OSS URL detected)")
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
