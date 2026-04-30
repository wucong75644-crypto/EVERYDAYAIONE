"""
KIE 图像模型适配器

适配 Nano Banana 系列图像生成模型
"""

import json
from typing import List, Optional, Dict, Any

from loguru import logger

from ..base import (
    BaseImageAdapter,
    ModelProvider,
    TaskStatus,
    ImageGenerateResult,
    CostEstimate,
)
from .client import KieClient, KieAPIError, KieTaskFailedError, KieTaskTimeoutError
from .models import (
    CreateTaskRequest,
    QueryTaskResponse,
    NanoBananaInput,
    NanoBananaEditInput,
    NanoBananaProInput,
    GptImage2Input,
    AspectRatio,
    ImageResolution,
    ImageOutputFormat,
    UsageRecord,
    KieModelType,
    TaskState,
)
from .configs import IMAGE_MODEL_CONFIGS


class KieImageAdapter(BaseImageAdapter):
    """
    KIE 图像生成适配器

    支持模型:
    - google/nano-banana: 基础文生图
    - google/nano-banana-edit: 图像编辑 (需要输入图片)
    - nano-banana-pro: 高级文生图 (支持图片参考、高分辨率)
    - gpt-image-2-text-to-image: GPT Image 2 文生图 (OpenAI 最强)

    特性:
    - 异步任务模式 (创建任务 → 轮询状态 → 获取结果)
    - 支持多种宽高比
    - 支持多种输出格式 (PNG/JPEG)
    - nano-banana-pro 支持 1K/2K/4K 分辨率
    """

    # 模型配置（从 configs.py 导入）
    MODEL_CONFIGS = IMAGE_MODEL_CONFIGS

    def __init__(self, client: KieClient, model: str):
        """
        初始化适配器

        Args:
            client: KIE HTTP 客户端
            model: 模型名称
        """
        if model not in self.MODEL_CONFIGS:
            raise ValueError(
                f"Unsupported model: {model}. "
                f"Supported: {list(self.MODEL_CONFIGS.keys())}"
            )

        super().__init__(model)
        self.client = client
        self.model = model
        self.config = self.MODEL_CONFIGS[model]

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.KIE

    @property
    def model_type(self) -> KieModelType:
        return KieModelType.IMAGE

    @property
    def requires_image_input(self) -> bool:
        return self.config["requires_image_input"]

    @property
    def supports_resolution(self) -> bool:
        return self.config["supports_resolution"]

    def validate_prompt(self, prompt: str) -> None:
        """验证 prompt"""
        max_length = self.config["max_prompt_length"]
        if len(prompt) > max_length:
            raise ValueError(
                f"Prompt too long: {len(prompt)} > {max_length}"
            )

    def validate_image_urls(self, image_urls: List[str]) -> None:
        """验证图片 URL 列表"""
        if not self.requires_image_input and self.model != "nano-banana-pro":
            return

        max_images = self.config.get("max_images", 0)
        if len(image_urls) > max_images:
            raise ValueError(
                f"Too many images: {len(image_urls)} > {max_images}"
            )

    def validate_size(self, size: str) -> None:
        """验证尺寸"""
        supported = self.config["supported_sizes"]
        if size not in supported:
            raise ValueError(
                f"Unsupported size: {size}. Supported: {supported}"
            )

    def validate_format(self, fmt: str) -> None:
        """验证输出格式（jpeg/jpg 视为等价）"""
        supported = self.config["supported_formats"]
        # jpeg 与 jpg 是同一格式的两种写法，归一化后再校验
        aliases = {"jpeg": "jpg", "jpg": "jpeg"}
        if fmt not in supported and aliases.get(fmt) not in supported:
            raise ValueError(
                f"Unsupported format: {fmt}. Supported: {supported}"
            )

    def validate_resolution(self, resolution: str) -> None:
        """验证分辨率 (仅 nano-banana-pro)"""
        if not self.supports_resolution:
            raise ValueError(f"Model {self.model} does not support resolution setting")

        supported = self.config["supported_resolutions"]
        if resolution not in supported:
            raise ValueError(
                f"Unsupported resolution: {resolution}. Supported: {supported}"
            )

    async def generate(
        self,
        prompt: str,
        image_urls: Optional[List[str]] = None,
        size: str = "1:1",
        output_format: str = "png",
        resolution: Optional[str] = None,
        callback_url: Optional[str] = None,
        wait_for_result: bool = True,
        poll_interval: float = 2.0,
        max_wait_time: float = 300.0,
        **kwargs,
    ) -> ImageGenerateResult:
        """
        生成图像

        Args:
            prompt: 图像描述
            image_urls: 输入图片 URL (编辑模式必填，Pro 模式可选作为参考)
            size: 宽高比
            output_format: 输出格式
            resolution: 分辨率 (仅 nano-banana-pro)
            callback_url: 回调 URL
            wait_for_result: 是否等待结果
            poll_interval: 轮询间隔
            max_wait_time: 最大等待时间

        Returns:
            Dict with task_id, status, image_urls, cost_usd, credits_consumed
        """
        # 参数验证
        self.validate_prompt(prompt)
        self.validate_size(size)
        self.validate_format(output_format)

        if image_urls:
            self.validate_image_urls(image_urls)

        if resolution:
            self.validate_resolution(resolution)

        try:
            # 构建输入参数
            input_params = self._build_input_params(
                prompt=prompt,
                image_urls=image_urls,
                size=size,
                output_format=output_format,
                resolution=resolution,
            )

            # 创建任务请求
            request = CreateTaskRequest(
                model=self.model_id,
                input=input_params,
                callBackUrl=callback_url,
            )

            logger.info(
                f"Creating image task: model={self.model}, size={size}, "
                f"resolution={resolution}, prompt_len={len(prompt)}"
            )

            if wait_for_result:
                # 创建并等待
                result = await self.client.create_and_wait(
                    request,
                    poll_interval=poll_interval,
                    max_wait_time=max_wait_time,
                )
                return self._format_result(result, resolution)
            else:
                # 仅创建任务
                create_response = await self.client.create_task(request)
                return ImageGenerateResult(
                    task_id=create_response.task_id,
                    status=TaskStatus.PENDING,
                    image_urls=[],
                    cost_usd=0,
                    credits_consumed=0,
                )
        except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
            raise
        except Exception as e:
            logger.error(
                f"Image generate failed: model={self.model}, "
                f"prompt_preview={prompt[:50]}..., error={e}"
            )
            raise KieAPIError(f"Image generate failed: {e}") from e

    def _build_input_params(
        self,
        prompt: str,
        image_urls: Optional[List[str]],
        size: str,
        output_format: str,
        resolution: Optional[str],
    ) -> Dict[str, Any]:
        """构建输入参数"""

        if self.model == "google/nano-banana":
            return NanoBananaInput(
                prompt=prompt,
                image_size=AspectRatio(size),
                output_format=ImageOutputFormat(output_format),
            ).model_dump()

        elif self.model == "google/nano-banana-edit":
            if not image_urls:
                raise ValueError("nano-banana-edit requires image_urls")
            return NanoBananaEditInput(
                prompt=prompt,
                image_urls=image_urls,
                image_size=AspectRatio(size),
                output_format=ImageOutputFormat(output_format),
            ).model_dump()

        elif self.model == "nano-banana-pro":
            # nano-banana-pro 使用不同的参数名
            fmt = "jpg" if output_format == "jpeg" else output_format
            return NanoBananaProInput(
                prompt=prompt,
                image_input=image_urls or [],
                aspect_ratio=AspectRatio(size),
                resolution=ImageResolution(resolution or "1K"),
                output_format=ImageOutputFormat(fmt),
            ).model_dump()

        elif self.model == "gpt-image-2-text-to-image":
            return GptImage2Input(
                prompt=prompt,
                aspect_ratio=AspectRatio(size),
                resolution=ImageResolution(resolution or "1K"),
            ).model_dump()

        else:
            raise ValueError(f"Unknown model: {self.model}")

    def _format_result(
        self,
        result: QueryTaskResponse,
        resolution: Optional[str] = None,
    ) -> ImageGenerateResult:
        """格式化结果（状态值已映射为前端格式）"""
        cost_estimate = self.estimate_cost(resolution=resolution)

        # 状态映射：KIE → TaskStatus
        status_map = {
            "success": TaskStatus.SUCCESS,
            "fail": TaskStatus.FAILED,
            "waiting": TaskStatus.PENDING,
        }
        raw_status = result.state.value if result.state else "unknown"
        status = status_map.get(raw_status, TaskStatus.PROCESSING)

        return ImageGenerateResult(
            task_id=result.task_id,
            status=status,
            image_urls=result.result_urls,
            cost_usd=float(cost_estimate.estimated_cost_usd),
            credits_consumed=cost_estimate.estimated_credits,
            cost_time_ms=result.cost_time,
        )

    async def query_task(self, task_id: str) -> ImageGenerateResult:
        """
        查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            ImageGenerateResult: 当前状态
        """
        try:
            result = await self.client.query_task(task_id)

            # 映射 KIE 状态到 TaskStatus
            status_map = {
                "waiting": TaskStatus.PENDING,
                "success": TaskStatus.SUCCESS,
                "fail": TaskStatus.FAILED,
            }
            status = status_map.get(
                result.state.value if result.state else "unknown",
                TaskStatus.PROCESSING
            )

            return ImageGenerateResult(
                task_id=result.task_id,
                status=status,
                image_urls=result.result_urls if result.state == TaskState.SUCCESS else [],
                fail_code=result.fail_code,
                fail_msg=result.fail_msg,
            )
        except KieAPIError:
            raise
        except Exception as e:
            logger.error(f"Query image task failed: task_id={task_id}, error={e}")
            raise KieAPIError(f"Query image task failed: {e}") from e

    def estimate_cost(
        self,
        image_count: int = 1,
        resolution: Optional[str] = None,
    ) -> CostEstimate:
        """
        估算成本

        Args:
            image_count: 生成图片数量
            resolution: 分辨率 (仅 nano-banana-pro)

        Returns:
            成本估算
        """
        if self.supports_resolution:
            res = resolution or "1K"
            cost_per_image = self.config["cost_per_image"][res]
            credits_per_image = self.config["credits_per_image"][res]
        else:
            cost_per_image = self.config["cost_per_image"]
            credits_per_image = self.config["credits_per_image"]

        total_cost = cost_per_image * image_count
        total_credits = credits_per_image * image_count

        return CostEstimate(
            model=self.model,
            estimated_cost_usd=total_cost,
            estimated_credits=total_credits,
            breakdown={
                "image_count": image_count,
                "resolution": resolution,
                "cost_per_image": float(cost_per_image),
                "credits_per_image": credits_per_image,
            },
        )

    def calculate_usage(
        self,
        image_count: int = 1,
        resolution: Optional[str] = None,
    ) -> UsageRecord:
        """计算实际使用量"""
        estimate = self.estimate_cost(image_count, resolution)

        return UsageRecord(
            model=self.model,
            model_type=KieModelType.IMAGE,
            image_count=image_count,
            cost_usd=estimate.estimated_cost_usd,
            credits_consumed=estimate.estimated_credits,
        )

    async def close(self) -> None:
        """关闭连接（KieClient 由调用方管理）"""
        pass

    # ==================== 回调解析 ====================

    @classmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        """从 KIE 回调 payload 提取任务 ID"""
        task_id = payload.get("taskId")
        if not task_id:
            raise ValueError("Missing taskId in KIE callback payload")
        return task_id

    @classmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> ImageGenerateResult:
        """
        解析 KIE 图片回调 payload

        KIE 回调格式：
        {
            "taskId": "xxx",
            "state": "success" | "fail",
            "resultJson": "{\"resultUrls\": [\"https://...\"]}",
            "failCode": "...",
            "failMsg": "...",
            "costTime": 12345
        }
        """
        task_id = cls.extract_task_id(payload)
        state = payload.get("state")
        cost_time = payload.get("costTime")

        if state == "success":
            # 解析 resultJson
            result_json_raw = payload.get("resultJson", "{}")
            if isinstance(result_json_raw, str):
                try:
                    result_data = json.loads(result_json_raw)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid resultJson | task_id={task_id} | error={e}")
                    return ImageGenerateResult(
                        task_id=task_id,
                        status=TaskStatus.FAILED,
                        fail_code="INVALID_RESULT_JSON",
                        fail_msg="回调数据解析失败",
                        cost_time_ms=cost_time,
                    )
            else:
                result_data = result_json_raw or {}

            image_urls = result_data.get("resultUrls", [])

            # 空结果视为失败
            if not image_urls:
                logger.warning(f"Empty resultUrls in success callback | task_id={task_id}")
                return ImageGenerateResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    fail_code="EMPTY_RESULT",
                    fail_msg="生成结果为空",
                    cost_time_ms=cost_time,
                )

            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                image_urls=image_urls,
                cost_time_ms=cost_time,
            )
        else:
            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                fail_code=payload.get("failCode", "UNKNOWN"),
                fail_msg=payload.get("failMsg", "任务失败"),
                cost_time_ms=cost_time,
            )