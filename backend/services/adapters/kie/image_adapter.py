"""
KIE 图像模型适配器

适配 Nano Banana 系列图像生成模型
"""

from typing import List, Optional, Dict, Any
from decimal import Decimal

from loguru import logger

from .client import KieClient, KieAPIError, KieTaskFailedError, KieTaskTimeoutError
from .models import (
    CreateTaskRequest,
    QueryTaskResponse,
    NanoBananaInput,
    NanoBananaEditInput,
    NanoBananaProInput,
    AspectRatio,
    ImageResolution,
    ImageOutputFormat,
    CostEstimate,
    UsageRecord,
    KieModelType,
    TaskState,
)

class KieImageAdapter:
    """
    KIE 图像生成适配器

    支持模型:
    - google/nano-banana: 基础文生图
    - google/nano-banana-edit: 图像编辑 (需要输入图片)
    - nano-banana-pro: 高级文生图 (支持图片参考、高分辨率)

    特性:
    - 异步任务模式 (创建任务 → 轮询状态 → 获取结果)
    - 支持多种宽高比
    - 支持多种输出格式 (PNG/JPEG)
    - nano-banana-pro 支持 1K/2K/4K 分辨率
    """

    # 模型配置
    MODEL_CONFIGS = {
        "google/nano-banana": {
            "model_id": "google/nano-banana",
            "description": "基础文生图",
            "requires_image_input": False,
            "max_prompt_length": 20000,
            "supported_sizes": [
                "1:1", "9:16", "16:9", "3:4", "4:3",
                "3:2", "2:3", "5:4", "4:5", "21:9", "auto"
            ],
            "supported_formats": ["png", "jpeg"],
            "supports_resolution": False,
            "cost_per_image": Decimal("0.02"),
            "credits_per_image": 5,
        },
        "google/nano-banana-edit": {
            "model_id": "google/nano-banana-edit",
            "description": "图像编辑",
            "requires_image_input": True,
            "max_images": 10,
            "max_image_size_mb": 10,
            "max_prompt_length": 20000,
            "supported_sizes": [
                "1:1", "9:16", "16:9", "3:4", "4:3",
                "3:2", "2:3", "5:4", "4:5", "21:9", "auto"
            ],
            "supported_formats": ["png", "jpeg"],
            "supports_resolution": False,
            "cost_per_image": Decimal("0.02"),
            "credits_per_image": 6,
        },
        "nano-banana-pro": {
            "model_id": "nano-banana-pro",
            "description": "高级文生图 (支持4K)",
            "requires_image_input": False,
            "max_images": 8,  # 参考图片
            "max_image_size_mb": 30,
            "max_prompt_length": 20000,
            "supported_sizes": [
                "1:1", "2:3", "3:2", "3:4", "4:3",
                "4:5", "5:4", "9:16", "16:9", "21:9", "auto"
            ],
            "supported_formats": ["png", "jpg"],
            "supports_resolution": True,
            "supported_resolutions": ["1K", "2K", "4K"],
            "cost_per_image": {
                "1K": Decimal("0.12"),
                "2K": Decimal("0.18"),
                "4K": Decimal("0.24"),
            },
            "credits_per_image": {
                "1K": 25,
                "2K": 36,
                "4K": 48,
            },
        },
    }

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

        self.client = client
        self.model = model
        self.config = self.MODEL_CONFIGS[model]

    @property
    def model_type(self) -> KieModelType:
        return KieModelType.IMAGE

    @property
    def model_id(self) -> str:
        return self.config["model_id"]

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
        """验证输出格式"""
        supported = self.config["supported_formats"]
        if fmt not in supported:
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
    ) -> Dict[str, Any]:
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
                return {
                    "task_id": create_response.task_id,
                    "status": "pending",
                    "image_urls": [],
                    "cost_usd": 0,
                    "credits_consumed": 0,
                }
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

        else:
            raise ValueError(f"Unknown model: {self.model}")

    def _format_result(
        self,
        result: QueryTaskResponse,
        resolution: Optional[str] = None,
    ) -> Dict[str, Any]:
        """格式化结果"""
        cost_estimate = self.estimate_cost(resolution=resolution)

        return {
            "task_id": result.task_id,
            "status": result.state.value if result.state else "unknown",
            "image_urls": result.result_urls,
            "cost_usd": float(cost_estimate.estimated_cost_usd),
            "credits_consumed": cost_estimate.estimated_credits,
            "cost_time_ms": result.cost_time,
        }

    async def query_task(self, task_id: str) -> Dict[str, Any]:
        """
        查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态
        """
        try:
            result = await self.client.query_task(task_id)

            # 映射 KIE 状态到 API 状态
            status_map = {
                "waiting": "pending",
                "success": "success",
                "fail": "failed",
            }
            status = status_map.get(
                result.state.value if result.state else "unknown",
                "processing"
            )

            return {
                "task_id": result.task_id,
                "status": status,
                "image_urls": result.result_urls if result.state == TaskState.SUCCESS else [],
                "fail_code": result.fail_code,
                "fail_msg": result.fail_msg,
            }
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

async def generate_image(
    api_key: str,
    prompt: str,
    model: str = "google/nano-banana",
    **kwargs,
) -> Dict[str, Any]:
    """快速生成图像 (默认使用 google/nano-banana)"""
    try:
        async with KieClient(api_key) as client:
            adapter = KieImageAdapter(client, model)
            return await adapter.generate(prompt, **kwargs)
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"generate_image failed: model={model}, error={e}")
        raise KieAPIError(f"generate_image failed: {e}") from e

async def edit_image(
    api_key: str,
    prompt: str,
    image_urls: List[str],
    **kwargs,
) -> Dict[str, Any]:
    """快速编辑图像 (使用 google/nano-banana-edit)"""
    try:
        async with KieClient(api_key) as client:
            adapter = KieImageAdapter(client, "google/nano-banana-edit")
            return await adapter.generate(prompt, image_urls=image_urls, **kwargs)
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"edit_image failed: image_count={len(image_urls)}, error={e}")
        raise KieAPIError(f"edit_image failed: {e}") from e

async def generate_image_pro(
    api_key: str,
    prompt: str,
    resolution: str = "2K",
    reference_images: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """高级图像生成 (使用 nano-banana-pro, 支持 1K/2K/4K 分辨率)"""
    try:
        async with KieClient(api_key) as client:
            adapter = KieImageAdapter(client, "nano-banana-pro")
            return await adapter.generate(
                prompt,
                image_urls=reference_images,
                resolution=resolution,
                **kwargs,
            )
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"generate_image_pro failed: resolution={resolution}, error={e}")
        raise KieAPIError(f"generate_image_pro failed: {e}") from e
