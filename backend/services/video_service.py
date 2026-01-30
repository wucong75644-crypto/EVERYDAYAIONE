"""
视频生成服务

封装视频生成的业务逻辑，包括积分检查、调用 KIE 适配器、记录消费等。
"""

from typing import Optional, List, Dict, Any

from loguru import logger
from supabase import Client

from core.config import settings
from core.exceptions import AppException, InsufficientCreditsError
from services.adapters.kie.client import KieClient, KieAPIError
from services.adapters.kie.video_adapter import KieVideoAdapter
from services.base_generation_service import BaseGenerationService
from services.oss_service import get_oss_service


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
        conversation_id: Optional[str] = None,
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
            conversation_id=conversation_id,
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
        conversation_id: Optional[str] = None,
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
            conversation_id=conversation_id,
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
        conversation_id: Optional[str] = None,
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
            conversation_id=conversation_id,
            generate_kwargs={
                "image_urls": storyboard_images,
                "n_frames": n_frames,
                "aspect_ratio": aspect_ratio,
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
            user_id: 用户 ID（必需，用于权限验证和视频上传到 OSS）

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
                message="视频服务未配置，请联系管理员",
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
                "video_url": cached_result.get("video_url"),
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
                adapter = KieVideoAdapter(client, "sora-2-text-to-video")
                result = await adapter.query_task(task_id)

            # 如果视频生成完成且提供了 user_id，先上传到 OSS 再更新状态
            if result.get("status") == "success" and user_id:
                try:
                    result = await self._upload_videos_to_oss(result, user_id)
                except Exception as e:
                    # OSS 上传失败不影响任务查询结果，只记录日志
                    logger.warning(
                        f"Failed to upload video to OSS during query: "
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

    async def _validate_and_deduct_credits(
        self,
        user_id: str,
        model: str,
        n_frames: str,
        description: str,
    ) -> int:
        """
        验证用户并扣除积分

        Args:
            user_id: 用户 ID
            model: 模型名称
            n_frames: 视频时长
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
                message="视频生成服务未配置，请联系管理员",
                status_code=503,
            )

        user = await self._get_user(user_id)
        duration = int(n_frames)
        estimated_credits = self._estimate_credits(model, duration)

        if user["credits"] < estimated_credits:
            raise InsufficientCreditsError(
                required=estimated_credits,
                current=user["credits"],
            )

        logger.info(
            f"Starting {description}: user_id={user_id}, model={model}, "
            f"duration={duration}s"
        )

        await self._deduct_credits(
            user_id=user_id,
            credits=estimated_credits,
            description=f"{description}: {model}",
            change_type="video_generation_cost",
        )

        return estimated_credits

    async def _call_kie_api(
        self,
        model: str,
        generate_kwargs: Dict[str, Any],
        user_id: str,
        conversation_id: Optional[str],
        n_frames: str,
        estimated_credits: int,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """
        调用 KIE API 生成视频

        Args:
            model: 模型名称
            generate_kwargs: 传递给 adapter.generate 的参数
            user_id: 用户 ID
            conversation_id: 会话 ID
            n_frames: 视频时长
            estimated_credits: 预估积分

        Returns:
            (result, task_id) 元组
        """
        async with KieClient(self.settings.kie_api_key) as client:
            adapter = KieVideoAdapter(client, model)
            result = await adapter.generate(**generate_kwargs)

        task_id = result.get("task_id")

        logger.info(
            f"Video generation started: user_id={user_id}, task_id={task_id}, "
            f"credits={estimated_credits}"
        )

        if task_id and conversation_id:
            await self._save_task_to_db(
                user_id=user_id,
                conversation_id=conversation_id,
                task_id=task_id,
                task_type="video",
                request_params={
                    "model": model,
                    "n_frames": n_frames,
                    **generate_kwargs,
                },
                credits_locked=estimated_credits,
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
        result = await self._upload_videos_to_oss(result, user_id)

        if task_id:
            await self._update_task_status(
                task_id=task_id,
                status="success",
                result=result,
            )

        return result

    async def _generate_with_credits(
        self,
        user_id: str,
        model: str,
        n_frames: str,
        description: str,
        error_message: str,
        conversation_id: Optional[str],
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
            conversation_id: 会话 ID
            generate_kwargs: 传递给 adapter.generate 的参数

        Returns:
            生成结果
        """
        # 1. 验证并扣除积分
        estimated_credits = await self._validate_and_deduct_credits(
            user_id=user_id,
            model=model,
            n_frames=n_frames,
            description=description,
        )

        try:
            # 2. 调用 KIE API
            result, task_id = await self._call_kie_api(
                model=model,
                generate_kwargs=generate_kwargs,
                user_id=user_id,
                conversation_id=conversation_id,
                n_frames=n_frames,
                estimated_credits=estimated_credits,
            )

            # 3. 同步模式完成处理
            wait_for_result = generate_kwargs.get("wait_for_result", False)
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
                code="VIDEO_GENERATION_FAILED",
                message=f"{error_message}: {str(e)}",
                status_code=500,
            )

    def _estimate_credits(
        self,
        model: str,
        duration_seconds: int,
    ) -> int:
        """估算积分消耗（支持阶梯定价）"""
        config = KieVideoAdapter.MODEL_CONFIGS.get(model, {})

        # 优先使用阶梯定价（如 sora-2-pro-storyboard）
        credits_by_duration = config.get("credits_by_duration")
        if credits_by_duration:
            return credits_by_duration.get(str(duration_seconds), 0)

        # 否则使用按秒计费
        credits_per_second = config.get("credits_per_second", 0)
        return credits_per_second * duration_seconds

    async def _upload_videos_to_oss(
        self,
        result: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """
        将生成的视频上传到 OSS

        Args:
            result: KIE 返回的结果（包含 video_url 字段）
            user_id: 用户 ID

        Returns:
            替换 URL 后的结果
        """
        video_url = result.get("video_url")
        if not video_url:
            return result

        # 检查是否已经是 OSS URL（避免重复上传）
        cdn_domain = settings.oss_cdn_domain
        oss_domain = f"{settings.oss_bucket_name}.{settings.oss_endpoint}"
        video_url_lower = video_url.lower()
        if cdn_domain and cdn_domain in video_url_lower:
            logger.debug("Video already uploaded to OSS (CDN URL detected)")
            return result
        if oss_domain and oss_domain in video_url_lower:
            logger.debug("Video already uploaded to OSS (OSS URL detected)")
            return result

        # 检查 OSS 是否配置
        if not settings.oss_access_key_id:
            logger.warning("OSS not configured, skipping video upload")
            return result

        try:
            oss_service = get_oss_service()
        except ValueError as e:
            logger.warning(f"OSS service init failed: {e}, skipping video upload")
            return result

        try:
            upload_result = await oss_service.upload_from_url(
                url=video_url,
                user_id=user_id,
                category="generated",
                media_type="video",
            )
            result["video_url"] = upload_result["url"]
            result["oss_uploaded"] = True
            logger.info(
                f"Video uploaded to OSS: object_key={upload_result['object_key']}, "
                f"size={upload_result['size']} bytes"
            )
        except Exception as e:
            logger.error(f"Failed to upload video to OSS: {e}")
            # 上传失败时保留原 URL

        return result
