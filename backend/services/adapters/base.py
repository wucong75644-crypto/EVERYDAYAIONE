"""
AI 模型适配器抽象基类

参考：
- LiteLLM: 统一输入输出格式
- LangChain: BaseChatModel 抽象
- One API: 标准化 OpenAI 格式
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, AsyncIterator

# 从 types.py 导入所有类型定义
from .types import (
    # 枚举
    ModelProvider,
    MediaType,
    TaskStatus,
    # 数据模型
    StreamChunk,
    ChatResponse,
    CostEstimate,
    ModelConfig,
    # 生成结果
    ImageGenerateResult,
    VideoGenerateResult,
    # 多模态
    MultimodalPart,
    UnifiedMessage,
)

# 向后兼容：重新导出所有类型
__all__ = [
    # 枚举
    "ModelProvider",
    "MediaType",
    "TaskStatus",
    # 数据模型
    "StreamChunk",
    "ChatResponse",
    "CostEstimate",
    "ModelConfig",
    # 生成结果
    "ImageGenerateResult",
    "VideoGenerateResult",
    # 多模态
    "MultimodalPart",
    "UnifiedMessage",
    # 抽象基类
    "BaseChatAdapter",
    "BaseImageAdapter",
    "BaseVideoAdapter",
]


# ============================================================
# 抽象基类
# ============================================================


class BaseChatAdapter(ABC):
    """
    聊天模型适配器抽象基类

    所有 Provider 的聊天适配器必须继承此类。

    设计思路（参考 LiteLLM）：
    1. 统一输入：调用方传入标准格式的消息和配置
    2. 适配转换：各 Provider 实现自己的转换逻辑
    3. 统一输出：返回标准格式的响应
    """

    def __init__(self, model_id: str):
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    @abstractmethod
    def provider(self) -> ModelProvider:
        """返回提供商标识"""
        pass

    @property
    @abstractmethod
    def supports_streaming(self) -> bool:
        """是否支持流式输出"""
        pass

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """
        流式聊天

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            reasoning_effort: 推理强度 (minimal/low/medium/high)
            thinking_mode: 思考模式 (default/deep_think)

        Yields:
            StreamChunk: 包含增量内容和 token 使用量
        """
        pass

    @abstractmethod
    async def chat_sync(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        """非流式聊天（统一接口，避免与现有 chat 方法冲突）"""
        pass

    @abstractmethod
    def estimate_cost_unified(self, input_tokens: int, output_tokens: int) -> CostEstimate:
        """估算成本（统一接口，返回基类 CostEstimate）"""
        pass

    @abstractmethod
    async def close(self) -> None:
        """关闭连接，释放资源"""
        pass

    # ==================== 消息格式转换 ====================

    def _convert_message_format(self, messages: List[UnifiedMessage]) -> List[Any]:
        """
        将统一消息格式转换为 Provider 特定格式

        各 Provider 可重写此方法，处理格式差异：
        - KIE: OpenAI 格式 {"role": "user", "content": [...]}
        - Google: {"role": "user", "parts": [{"text": "..."}]}

        Args:
            messages: 统一格式的消息列表

        Returns:
            Provider 特定格式的消息列表
        """
        # 默认实现：KIE/OpenAI 格式
        result = []
        for msg in messages:
            if msg.parts:
                content = [{"type": "text", "text": msg.content}]
                for part in msg.parts:
                    content.append(part.to_kie_format())
                result.append({"role": msg.role, "content": content})
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

    def _parse_multimodal_url(self, url: str) -> MultimodalPart:
        """
        解析多模态 URL 为统一部件

        支持：
        - http/https URL
        - data:image/png;base64,... 格式
        """
        if url.startswith("data:"):
            # 解析 data URL
            # data:image/png;base64,xxxxx
            header, data = url.split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            return MultimodalPart(
                type=MediaType.IMAGE if "image" in mime_type else MediaType.VIDEO,
                mime_type=mime_type,
                data=data,
            )
        else:
            # 普通 URL
            return MultimodalPart(
                type=MediaType.IMAGE,  # 默认图片，可通过后缀判断
                url=url,
            )

    async def __aenter__(self) -> "BaseChatAdapter":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class BaseImageAdapter(ABC):
    """
    图片生成模型适配器抽象基类

    所有 Provider 的图片适配器必须继承此类。

    设计思路：
    1. 统一输入：prompt、size、format 等
    2. 统一输出：ImageGenerateResult
    3. 异步任务模式：create → poll → result
    """

    def __init__(self, model_id: str):
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    @abstractmethod
    def provider(self) -> ModelProvider:
        """返回提供商标识"""
        pass

    @property
    @abstractmethod
    def requires_image_input(self) -> bool:
        """是否需要输入图片（编辑模式）"""
        pass

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        image_urls: Optional[List[str]] = None,
        size: str = "1:1",
        output_format: str = "png",
        resolution: Optional[str] = None,
        wait_for_result: bool = True,
        **kwargs,
    ) -> ImageGenerateResult:
        """
        生成图片

        Args:
            prompt: 图片描述
            image_urls: 输入图片 URL（编辑/参考模式）
            size: 宽高比 (1:1, 16:9, 等)
            output_format: 输出格式 (png/jpeg)
            resolution: 分辨率 (1K/2K/4K，部分模型支持)
            wait_for_result: 是否等待结果

        Returns:
            ImageGenerateResult: 统一结果格式
        """
        pass

    @abstractmethod
    async def query_task(self, task_id: str) -> ImageGenerateResult:
        """
        查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            ImageGenerateResult: 当前状态
        """
        pass

    @abstractmethod
    def estimate_cost(
        self,
        image_count: int = 1,
        resolution: Optional[str] = None,
    ) -> CostEstimate:
        """
        估算成本

        Args:
            image_count: 生成图片数量
            resolution: 分辨率

        Returns:
            CostEstimate: 成本估算
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """关闭连接，释放资源"""
        pass

    # ==================== 回调解析（Webhook） ====================

    @classmethod
    @abstractmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        """
        从回调 payload 中提取任务 ID

        用于在解析前快速定位任务记录。
        每个 Provider 的 payload 格式不同，由各自实现。

        Args:
            payload: Provider 发送的原始回调数据

        Returns:
            external_task_id

        Raises:
            ValueError: payload 中缺少任务 ID
        """
        pass

    @classmethod
    @abstractmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> ImageGenerateResult:
        """
        解析 Provider 回调 payload 为统一结果格式

        每个 Provider 实现自己的解析逻辑：
        - KIE: taskId + state + resultJson
        - Google: operationId + done + response（预留）

        Args:
            payload: Provider 发送的原始回调数据

        Returns:
            ImageGenerateResult: 统一结果格式

        Raises:
            ValueError: payload 格式无效
        """
        pass

    async def __aenter__(self) -> "BaseImageAdapter":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class BaseVideoAdapter(ABC):
    """
    视频生成模型适配器抽象基类

    所有 Provider 的视频适配器必须继承此类。

    设计思路：
    1. 统一输入：prompt、duration、aspect_ratio 等
    2. 统一输出：VideoGenerateResult
    3. 异步任务模式：create → poll → result
    """

    def __init__(self, model_id: str):
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    @abstractmethod
    def provider(self) -> ModelProvider:
        """返回提供商标识"""
        pass

    @property
    @abstractmethod
    def requires_image_input(self) -> bool:
        """是否需要输入图片（图生视频模式）"""
        pass

    @property
    @abstractmethod
    def requires_prompt(self) -> bool:
        """是否需要 prompt"""
        pass

    @abstractmethod
    async def generate(
        self,
        prompt: Optional[str] = None,
        image_urls: Optional[List[str]] = None,
        duration_seconds: int = 10,
        aspect_ratio: str = "landscape",
        remove_watermark: bool = True,
        wait_for_result: bool = True,
        **kwargs,
    ) -> VideoGenerateResult:
        """
        生成视频

        Args:
            prompt: 视频描述
            image_urls: 输入图片 URL（图生视频模式）
            duration_seconds: 视频时长（秒）
            aspect_ratio: 宽高比 (portrait/landscape)
            remove_watermark: 是否去水印
            wait_for_result: 是否等待结果

        Returns:
            VideoGenerateResult: 统一结果格式
        """
        pass

    @abstractmethod
    async def query_task(self, task_id: str) -> VideoGenerateResult:
        """
        查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            VideoGenerateResult: 当前状态
        """
        pass

    @abstractmethod
    def estimate_cost(self, duration_seconds: int) -> CostEstimate:
        """
        估算成本

        Args:
            duration_seconds: 视频时长（秒）

        Returns:
            CostEstimate: 成本估算
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """关闭连接，释放资源"""
        pass

    # ==================== 回调解析（Webhook） ====================

    @classmethod
    @abstractmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        """
        从回调 payload 中提取任务 ID

        Args:
            payload: Provider 发送的原始回调数据

        Returns:
            external_task_id

        Raises:
            ValueError: payload 中缺少任务 ID
        """
        pass

    @classmethod
    @abstractmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> VideoGenerateResult:
        """
        解析 Provider 回调 payload 为统一结果格式

        Args:
            payload: Provider 发送的原始回调数据

        Returns:
            VideoGenerateResult: 统一结果格式

        Raises:
            ValueError: payload 格式无效
        """
        pass

    async def __aenter__(self) -> "BaseVideoAdapter":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
