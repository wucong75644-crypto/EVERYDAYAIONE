"""
KIE AI 模型适配器包

提供对 KIE AI 平台所有模型的统一访问接口

支持的模型:
- Chat 模型 (OpenAI 兼容):
  - gemini-3-pro: 高级推理模型
  - gemini-3-flash: 快速推理模型

- 图像模型 (异步任务):
  - google/nano-banana: 基础文生图
  - google/nano-banana-edit: 图像编辑
  - nano-banana-pro: 高级文生图

- 视频模型 (异步任务):
  - sora-2-text-to-video: 文生视频
  - sora-2-image-to-video: 图生视频
  - sora-2-pro-storyboard: 故事板视频

使用示例:
    from services.adapters.kie import (
        KieClient,
        KieChatAdapter,
        KieImageAdapter,
        KieVideoAdapter,
    )

    # Chat 模型
    async with KieClient(api_key) as client:
        chat = KieChatAdapter(client, "gemini-3-flash")
        async for chunk in await chat.chat_simple("Hello!"):
            print(chunk.choices[0].delta.content, end="")

    # 图像生成
    async with KieClient(api_key) as client:
        image = KieImageAdapter(client, "google/nano-banana")
        result = await image.generate("A cute cat")
        print(result["image_urls"])

    # 视频生成
    async with KieClient(api_key) as client:
        video = KieVideoAdapter(client, "sora-2-text-to-video")
        result = await video.generate(prompt="A sunset over ocean")
        print(result["video_url"])
"""

from .client import (
    KieClient,
    KieAPIError,
    KieAuthenticationError,
    KieInsufficientBalanceError,
    KieRateLimitError,
    KieTaskFailedError,
    KieTaskTimeoutError,
)

from .chat_adapter import (
    KieChatAdapter,
    create_chat_adapter,
)

from .image_adapter import (
    KieImageAdapter,
    generate_image,
    edit_image,
    generate_image_pro,
)

from .video_adapter import (
    KieVideoAdapter,
    text_to_video,
    image_to_video,
    storyboard_video,
)

from .models import (
    # 枚举
    KieModelType,
    TaskState,
    AspectRatio,
    ImageResolution,
    ImageOutputFormat,
    VideoFrames,
    ReasoningEffort,
    MessageRole,
    # Chat 模型
    ChatMessage,
    ChatContentPart,
    ChatCompletionRequest,
    ChatCompletionChunk,
    TokenUsage,
    # Task 模型
    CreateTaskRequest,
    CreateTaskResponse,
    QueryTaskResponse,
    # 成本
    CostEstimate,
    UsageRecord,
)


__all__ = [
    # Client
    "KieClient",
    "KieAPIError",
    "KieAuthenticationError",
    "KieInsufficientBalanceError",
    "KieRateLimitError",
    "KieTaskFailedError",
    "KieTaskTimeoutError",
    # Adapters
    "KieChatAdapter",
    "KieImageAdapter",
    "KieVideoAdapter",
    # 便捷函数
    "create_chat_adapter",
    "generate_image",
    "edit_image",
    "generate_image_pro",
    "text_to_video",
    "image_to_video",
    "storyboard_video",
    # 枚举
    "KieModelType",
    "TaskState",
    "AspectRatio",
    "ImageResolution",
    "ImageOutputFormat",
    "VideoFrames",
    "ReasoningEffort",
    "MessageRole",
    # 数据模型
    "ChatMessage",
    "ChatContentPart",
    "ChatCompletionRequest",
    "ChatCompletionChunk",
    "TokenUsage",
    "CreateTaskRequest",
    "CreateTaskResponse",
    "QueryTaskResponse",
    "CostEstimate",
    "UsageRecord",
]


# 版本信息
__version__ = "1.0.0"
