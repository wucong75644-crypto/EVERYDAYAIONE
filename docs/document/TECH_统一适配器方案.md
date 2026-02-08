# 统一适配器方案

> **版本**：v1.1 | **日期**：2026-02-06 | **状态**：待确认
>
> **v1.1 更新**：补充连接池管理、消息格式转换、多模态统一结构

## 一、需求背景

### 1.1 现状分析

当前项目仅支持 KIE 平台的 Gemini 模型，调用方直接依赖 `KieChatAdapter`：

```python
# message_ai_helpers.py - 现有代码
from services.adapters.kie.client import KieClient
from services.adapters.kie.chat_adapter import KieChatAdapter

def prepare_ai_stream_client(model_id: Optional[str]):
    # 硬编码模型判断
    model = model_id if model_id in ("gemini-3-pro", "gemini-3-flash") else "gemini-3-flash"
    client = KieClient(settings.kie_api_key)
    adapter = KieChatAdapter(client, model)
    return model, client, adapter
```

### 1.2 问题

| 问题 | 影响 |
|------|------|
| **强耦合** | 调用方直接依赖 `KieClient`、`KieChatAdapter` |
| **扩展困难** | 新增 Provider 需修改所有调用方 |
| **模型映射分散** | 模型判断逻辑散落在各处 |
| **配置不统一** | 各处取 `settings.kie_api_key` |

### 1.3 目标

- **统一入口**：调用方只需 `创建聊天适配器(model_id)` 即可
- **易于扩展**：新增 Provider 只需实现接口 + 注册模型
- **渐进式改造**：现有代码无需大改，逐步迁移

---

## 二、业界方案参考

### 2.1 LiteLLM（GitHub 24K+ Stars）

**核心设计**：
- 统一 100+ LLM API 为 OpenAI 格式
- `completion()` 函数自动路由到对应 Provider
- 标准输入 → 适配转换 → 标准输出

```python
# LiteLLM 用法
from litellm import completion

response = completion(
    model="gpt-4",           # 或 "gemini/gemini-pro"
    messages=[{"role": "user", "content": "Hello"}]
)
```

### 2.2 One API（GitHub 24.2K Stars）

**核心设计**：
- 国内主流多模型网关
- 集中管理模型映射（渠道配置）
- 标准化 20+ 主流大模型 API 为 OpenAI 格式
- 支持负载均衡、多 Key 轮询、Token 计费

### 2.3 LangChain

**核心设计**：
- `BaseChatModel` 抽象基类
- 各 Provider 继承实现
- 统一的 `invoke()` / `stream()` 接口

### 2.4 设计启示

| 方案 | 核心思想 | 借鉴点 |
|------|---------|--------|
| LiteLLM | 单一函数入口 | `completion(model=...)` 自动路由 |
| One API | 集中管理 | 模型注册表、渠道配置 |
| LangChain | 抽象基类 | `BaseChatModel` + 继承实现 |

---

## 三、架构设计

### 3.1 目录结构

```
backend/services/adapters/
├── __init__.py              # 统一导出入口
├── 基类.py                   # 抽象基类定义
├── 工厂.py                   # 工厂函数 + 模型注册表
├── kie/                     # KIE 适配器（现有，最小改动）
│   ├── __init__.py
│   ├── client.py            # HTTP 客户端（不变）
│   ├── chat_adapter.py      # 聊天适配器（继承基类）
│   ├── image_adapter.py     # 图像适配器（不变）
│   ├── video_adapter.py     # 视频适配器（不变）
│   └── models.py            # 数据模型（不变）
└── google/                  # Google 官方适配器（新增，Phase 2）
    ├── __init__.py
    ├── client.py            # Google SDK 客户端
    └── chat_adapter.py      # 聊天适配器
```

### 3.2 架构图

```
【现有方案】直接耦合
┌─────────────────────┐     ┌─────────────────────┐
│ message_ai_helpers  │────▶│ KieChatAdapter      │
│ message_stream_svc  │     │ (硬编码依赖)         │
└─────────────────────┘     └─────────────────────┘

【统一入口方案】工厂模式
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ message_ai_helpers  │────▶│ 创建聊天适配器()     │────▶│ 模型注册表           │
│ message_stream_svc  │     └──────────┬──────────┘     └─────────────────────┘
└─────────────────────┘                │
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                  ┌───────────┐ ┌───────────┐ ┌───────────┐
                  │ KIE适配器  │ │Google适配器│ │OpenAI适配器│
                  │ (现有)     │ │ (Phase 2) │ │ (预留)     │
                  └───────────┘ └───────────┘ └───────────┘
```

### 3.3 核心组件

| 组件 | 职责 | 文件 |
|------|------|------|
| **抽象基类** | 定义统一接口（流式聊天、成本估算） | `基类.py` |
| **模型注册表** | 集中管理模型 ID → Provider 映射 | `工厂.py` |
| **工厂函数** | 根据模型 ID 创建对应适配器 | `工厂.py` |
| **Provider 适配器** | 实现具体的 API 调用逻辑 | `kie/`、`google/` |

---

## 四、详细设计

### 4.1 抽象基类（`基类.py`）

```python
"""
AI 模型适配器抽象基类

参考：
- LiteLLM: 统一输入输出格式
- LangChain: BaseChatModel 抽象
- One API: 标准化 OpenAI 格式
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, AsyncIterator


# ============================================================
# 枚举定义
# ============================================================


class 模型提供商(str, Enum):
    """支持的模型提供商"""
    KIE = "kie"              # KIE AI 平台
    GOOGLE = "google"        # Google 官方 Gemini
    OPENAI = "openai"        # OpenAI（预留）
    ANTHROPIC = "anthropic"  # Claude（预留）


# ============================================================
# 数据模型
# ============================================================


@dataclass
class 流式块:
    """
    统一流式响应块（OpenAI 兼容格式）

    与现有 ChatCompletionChunk 结构对齐，确保 chat_stream_manager.py 兼容
    """
    content: Optional[str] = None           # 增量文本内容
    finish_reason: Optional[str] = None     # 结束原因
    # Token 使用量（通常在最后一帧返回）
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def 有内容(self) -> bool:
        return bool(self.content)

    @property
    def 有用量(self) -> bool:
        return self.prompt_tokens > 0 or self.completion_tokens > 0


@dataclass
class 聊天响应:
    """统一非流式聊天响应"""
    content: str                            # 完整回复内容
    finish_reason: Optional[str] = None     # 结束原因
    prompt_tokens: int = 0                  # 输入 token 数
    completion_tokens: int = 0              # 输出 token 数


@dataclass
class 成本估算:
    """
    成本估算结果

    与现有 CostEstimate 结构对齐
    """
    model: str                              # 模型名称
    estimated_cost_usd: Decimal             # 预估美元成本
    estimated_credits: int                  # 预估积分消耗
    breakdown: Dict[str, Any] = field(default_factory=dict)


@dataclass
class 模型配置:
    """单个模型的配置信息"""
    model_id: str                           # 模型 ID（用户传入）
    provider: 模型提供商                     # 提供商
    provider_model: str                     # 提供商侧的模型名
    display_name: str                       # 显示名称
    # 价格（$/1M tokens）
    input_price: float
    output_price: float
    # 积分价格（积分/1K tokens）
    credits_per_1k_input: float = 1.0
    credits_per_1k_output: float = 1.0
    # 能力标记
    supports_vision: bool = False
    supports_video: bool = False
    supports_tools: bool = False
    max_tokens: int = 8192
    context_window: int = 128000


# ============================================================
# 多模态统一结构（v1.1 新增）
# ============================================================


class 媒体类型(str, Enum):
    """媒体类型枚举"""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"


@dataclass
class 多模态部件:
    """
    统一多模态内容部件

    解决不同 Provider 格式差异：
    - KIE (OpenAI): {"type": "image_url", "image_url": {"url": "..."}}
    - Google: {"inline_data": {"mime_type": "...", "data": "..."}}

    适配器内部负责转换为对应格式
    """
    type: 媒体类型                          # 媒体类型
    url: Optional[str] = None               # URL（http/https/data:base64）
    mime_type: Optional[str] = None         # MIME 类型（image/png, video/mp4）
    data: Optional[str] = None              # Base64 数据（可选，用于直接传数据）

    def to_kie_format(self) -> Dict[str, Any]:
        """转换为 KIE (OpenAI) 格式"""
        return {
            "type": "image_url",
            "image_url": {"url": self.url or f"data:{self.mime_type};base64,{self.data}"}
        }

    def to_google_format(self) -> Dict[str, Any]:
        """转换为 Google 官方格式"""
        if self.data:
            return {
                "inline_data": {
                    "mime_type": self.mime_type or "image/png",
                    "data": self.data
                }
            }
        # URL 需要先下载转 base64，或使用 File API
        return {
            "file_data": {
                "file_uri": self.url,
                "mime_type": self.mime_type
            }
        }


@dataclass
class 统一消息:
    """
    统一消息格式

    调用方传入此格式，适配器内部转换为 Provider 格式
    """
    role: str                               # user / assistant / system
    content: str                            # 文本内容
    parts: List[多模态部件] = field(default_factory=list)  # 多模态部件列表


# ============================================================
# 抽象基类
# ============================================================


class 聊天适配器基类(ABC):
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
    def 提供商(self) -> 模型提供商:
        """返回提供商标识"""
        pass

    @property
    @abstractmethod
    def 支持流式(self) -> bool:
        """是否支持流式输出"""
        pass

    @abstractmethod
    async def 流式聊天(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[流式块]:
        """
        流式聊天

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            reasoning_effort: 推理强度 (minimal/low/medium/high)
            thinking_mode: 思考模式 (default/deep_think)

        Yields:
            流式块: 包含增量内容和 token 使用量
        """
        pass

    @abstractmethod
    async def 聊天(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> 聊天响应:
        """非流式聊天"""
        pass

    @abstractmethod
    def 估算成本(self, input_tokens: int, output_tokens: int) -> 成本估算:
        """估算成本"""
        pass

    @abstractmethod
    async def 关闭(self) -> None:
        """关闭连接，释放资源"""
        pass

    # ==================== 消息格式转换（v1.1 新增）====================

    @abstractmethod
    def _转换消息格式(self, messages: List[统一消息]) -> List[Any]:
        """
        将统一消息格式转换为 Provider 特定格式

        各 Provider 必须实现此方法，处理格式差异：
        - KIE: OpenAI 格式 {"role": "user", "content": [...]}
        - Google: {"role": "user", "parts": [{"text": "..."}]}

        Args:
            messages: 统一格式的消息列表

        Returns:
            Provider 特定格式的消息列表
        """
        pass

    def _解析多模态URL(self, url: str) -> 多模态部件:
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
            return 多模态部件(
                type=媒体类型.IMAGE if "image" in mime_type else 媒体类型.VIDEO,
                mime_type=mime_type,
                data=data,
            )
        else:
            # 普通 URL
            return 多模态部件(
                type=媒体类型.IMAGE,  # 默认图片，可通过后缀判断
                url=url,
            )

    async def __aenter__(self) -> "聊天适配器基类":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.关闭()


# 英文别名（兼容现有代码风格）
ModelProvider = 模型提供商
StreamChunk = 流式块
ChatResponse = 聊天响应
CostEstimate = 成本估算
ModelConfig = 模型配置
BaseChatAdapter = 聊天适配器基类
# v1.1 新增
MediaType = 媒体类型
MultimodalPart = 多模态部件
UnifiedMessage = 统一消息
```

### 4.2 模型注册表 + 工厂（`工厂.py`）

```python
"""
模型工厂 + 注册表

参考：
- One API: 集中管理模型映射（渠道配置）
- LiteLLM: model_list 配置驱动
"""

import asyncio
from typing import Dict, Optional, Any
from loguru import logger

from core.config import get_settings
from .基类 import 聊天适配器基类, 模型提供商, 模型配置


# ============================================================
# 模型注册表（集中管理，易于扩展）
# ============================================================

模型注册表: Dict[str, 模型配置] = {
    # ==================== KIE 平台模型 ====================
    "gemini-3-pro": 模型配置(
        model_id="gemini-3-pro",
        provider=模型提供商.KIE,
        provider_model="gemini-3-pro",
        display_name="Gemini 3 Pro",
        input_price=0.50,       # $0.50 / 1M
        output_price=3.50,      # $3.50 / 1M
        credits_per_1k_input=1,
        credits_per_1k_output=7,
        supports_vision=True,
        supports_video=True,
        supports_tools=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),
    "gemini-3-flash": 模型配置(
        model_id="gemini-3-flash",
        provider=模型提供商.KIE,
        provider_model="gemini-3-flash",
        display_name="Gemini 3 Flash",
        input_price=0.15,       # $0.15 / 1M
        output_price=0.90,      # $0.90 / 1M
        credits_per_1k_input=0.3,
        credits_per_1k_output=1.8,
        supports_vision=True,
        supports_video=True,
        supports_tools=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),

    # ==================== Google 官方模型（Phase 2）====================
    "gemini-2.5-flash": 模型配置(
        model_id="gemini-2.5-flash",
        provider=模型提供商.GOOGLE,
        provider_model="gemini-2.5-flash-preview-05-20",
        display_name="Gemini 2.5 Flash",
        input_price=0.15,
        output_price=0.60,
        credits_per_1k_input=0.3,
        credits_per_1k_output=1.2,
        supports_vision=True,
        supports_video=True,
        supports_tools=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),
    "gemini-2.5-pro": 模型配置(
        model_id="gemini-2.5-pro",
        provider=模型提供商.GOOGLE,
        provider_model="gemini-2.5-pro-preview-05-06",
        display_name="Gemini 2.5 Pro",
        input_price=1.25,
        output_price=10.0,
        credits_per_1k_input=2.5,
        credits_per_1k_output=20,
        supports_vision=True,
        supports_video=True,
        supports_tools=True,
        max_tokens=65536,
        context_window=1_000_000,
    ),
}

# 默认模型
默认模型ID = "gemini-3-flash"


# ============================================================
# 客户端池管理（v1.1 新增，可选优化）
# ============================================================

class 客户端池:
    """
    HTTP 客户端连接池

    解决问题：
    - 每次请求创建新 KieClient 会频繁创建/销毁 HTTP 连接
    - 高并发场景下影响性能

    设计：
    - 按 Provider 维护单例客户端
    - 复用 httpx.AsyncClient 内部连接池
    - 支持优雅关闭

    使用场景：
    - 当前用户量不大时可不启用（直接每次 new）
    - 用户量增长后启用此优化
    """
    _instances: Dict[模型提供商, Any] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def 获取客户端(cls, provider: 模型提供商, api_key: str) -> Any:
        """获取或创建客户端（单例）"""
        async with cls._lock:
            if provider not in cls._instances:
                if provider == 模型提供商.KIE:
                    from .kie import KieClient
                    cls._instances[provider] = KieClient(api_key)
                elif provider == 模型提供商.GOOGLE:
                    from .google import GoogleClient
                    cls._instances[provider] = GoogleClient(api_key)
            return cls._instances[provider]

    @classmethod
    async def 关闭所有(cls):
        """关闭所有客户端（应用退出时调用）"""
        async with cls._lock:
            for client in cls._instances.values():
                if hasattr(client, 'close'):
                    await client.close()
            cls._instances.clear()


# 是否启用客户端池（可通过环境变量控制）
启用客户端池 = False  # 默认关闭，用户量增长后开启


# ============================================================
# 工厂函数
# ============================================================


def 创建聊天适配器(model_id: Optional[str] = None) -> 聊天适配器基类:
    """
    根据模型 ID 创建对应的聊天适配器

    Args:
        model_id: 模型 ID，为空则使用默认模型

    Returns:
        对应 Provider 的聊天适配器实例

    Raises:
        ValueError: 模型不存在或 Provider 未实现

    示例:
        # 使用 KIE 平台
        adapter = 创建聊天适配器("gemini-3-flash")

        # 使用 Google 官方（Phase 2）
        adapter = 创建聊天适配器("gemini-2.5-flash")

        # 使用默认模型
        adapter = 创建聊天适配器()
    """
    settings = get_settings()

    # 获取模型配置
    实际模型ID = model_id if model_id in 模型注册表 else 默认模型ID
    config = 模型注册表[实际模型ID]

    logger.debug(f"创建适配器: model_id={实际模型ID}, provider={config.provider}")

    # 根据 Provider 创建适配器
    if config.provider == 模型提供商.KIE:
        from .kie import KieClient, KieChatAdapter

        if not settings.kie_api_key:
            raise ValueError("KIE API Key 未配置")

        # v1.1: 可选使用客户端池
        if 启用客户端池:
            client = await 客户端池.获取客户端(模型提供商.KIE, settings.kie_api_key)
        else:
            client = KieClient(settings.kie_api_key)

        return KieChatAdapter(client, config.provider_model)

    elif config.provider == 模型提供商.GOOGLE:
        # Phase 2 实现
        from .google import GoogleChatAdapter

        if not settings.google_api_key:
            raise ValueError("Google API Key 未配置")

        return GoogleChatAdapter(
            model_id=config.provider_model,
            api_key=settings.google_api_key,
        )

    else:
        raise ValueError(f"Provider {config.provider} 暂未实现")


def 获取模型配置(model_id: str) -> Optional[模型配置]:
    """获取模型配置信息"""
    return 模型注册表.get(model_id)


def 获取所有模型() -> Dict[str, 模型配置]:
    """获取所有可用模型"""
    return 模型注册表.copy()


def 按提供商获取模型(provider: 模型提供商) -> Dict[str, 模型配置]:
    """按 Provider 筛选模型"""
    return {
        k: v for k, v in 模型注册表.items()
        if v.provider == provider
    }


# 英文别名
create_chat_adapter = 创建聊天适配器
get_model_config = 获取模型配置
get_all_models = 获取所有模型
DEFAULT_MODEL_ID = 默认模型ID
```

### 4.3 KIE 适配器改造（最小改动）

**改动说明**：KIE 适配器只需继承基类，现有方法保持不变。

```python
# kie/chat_adapter.py - 仅添加继承和适配方法

from services.adapters.基类 import (
    聊天适配器基类,
    模型提供商,
    流式块,
    聊天响应,
    成本估算,
)

class KieChatAdapter(聊天适配器基类):
    """
    KIE Chat 模型适配器

    继承统一基类，保持现有接口不变。
    """

    # ==================== 现有代码保持不变 ====================

    MODEL_CONFIGS = { ... }  # 不变

    def __init__(self, client: KieClient, model: str):
        super().__init__(model)  # 调用基类初始化
        self.client = client
        self.model = model
        self.config = self.MODEL_CONFIGS[model]

    # 所有现有方法保持不变:
    # - format_text_message()
    # - format_multimodal_message()
    # - format_messages_from_history()
    # - create_google_search_tool()
    # - create_function_tool()
    # - create_response_format()
    # - chat()
    # - chat_simple()
    # - estimate_cost()
    # - calculate_usage()

    # ==================== 新增：实现基类抽象方法 ====================

    @property
    def 提供商(self) -> 模型提供商:
        return 模型提供商.KIE

    @property
    def 支持流式(self) -> bool:
        return True

    async def 流式聊天(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[流式块]:
        """
        统一格式的流式聊天

        将现有 chat() 方法的输出转换为统一的 流式块 格式
        """
        from services.message_ai_helpers import parse_thinking_effort, parse_thinking_mode

        # 转换消息格式
        formatted_messages = self.format_messages_from_history(messages)

        # 解析参数
        effort = parse_thinking_effort(reasoning_effort)
        mode = parse_thinking_mode(thinking_mode)

        # 调用现有方法
        stream = await self.chat(
            messages=formatted_messages,
            stream=True,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
            **kwargs,
        )

        # 转换输出格式
        async for chunk in stream:
            yield 流式块(
                content=chunk.choices[0].delta.content if chunk.choices else None,
                finish_reason=chunk.choices[0].finish_reason if chunk.choices else None,
                prompt_tokens=chunk.usage.prompt_tokens if chunk.usage else 0,
                completion_tokens=chunk.usage.completion_tokens if chunk.usage else 0,
            )

    async def 聊天(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs,
    ) -> 聊天响应:
        """统一格式的非流式聊天"""
        from services.message_ai_helpers import parse_thinking_effort, parse_thinking_mode

        formatted_messages = self.format_messages_from_history(messages)
        effort = parse_thinking_effort(reasoning_effort)
        mode = parse_thinking_mode(thinking_mode)

        response = await self.chat(
            messages=formatted_messages,
            stream=False,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
            **kwargs,
        )

        content = ""
        if response.choices:
            content = response.choices[0].delta.content or ""

        return 聊天响应(
            content=content,
            finish_reason=response.choices[0].finish_reason if response.choices else None,
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    def 估算成本(self, input_tokens: int, output_tokens: int) -> 成本估算:
        """转换现有 estimate_cost 的输出"""
        result = self.estimate_cost(input_tokens, output_tokens)
        return 成本估算(
            model=result.model,
            estimated_cost_usd=result.estimated_cost_usd,
            estimated_credits=result.estimated_credits,
            breakdown=result.breakdown,
        )

    async def 关闭(self) -> None:
        """关闭客户端连接"""
        await self.client.close()
```

### 4.4 调用方改造（渐进式）

**Phase 1**：保持现有代码不变，工厂函数可选使用

```python
# message_ai_helpers.py - 渐进式改造

# 方式1：现有代码（保持不变）
from services.adapters.kie import KieClient, KieChatAdapter

def prepare_ai_stream_client(model_id: Optional[str]):
    """现有实现，保持不变"""
    settings = get_settings()
    model = model_id if model_id in ("gemini-3-pro", "gemini-3-flash") else "gemini-3-flash"
    client = KieClient(settings.kie_api_key)
    adapter = KieChatAdapter(client, model)
    return model, client, adapter


# 方式2：新代码推荐使用（Phase 2 开始）
from services.adapters.工厂 import 创建聊天适配器

def prepare_ai_stream_client_v2(model_id: Optional[str]):
    """
    使用工厂模式创建适配器

    支持多 Provider，自动选择
    """
    adapter = 创建聊天适配器(model_id)
    return adapter.model_id, adapter.client, adapter
```

**Phase 2**：完成 Google 适配器后，切换到工厂模式

```python
# message_ai_helpers.py - 完成改造

from services.adapters.工厂 import 创建聊天适配器

def prepare_ai_stream_client(model_id: Optional[str]):
    """使用工厂模式（推荐）"""
    adapter = 创建聊天适配器(model_id)
    return adapter.model_id, adapter.client, adapter
```

---

## 五、兼容性分析

### 5.1 与现有代码的兼容性

| 组件 | 兼容性 | 说明 |
|------|--------|------|
| `chat_stream_manager.py` | ✅ 完全兼容 | `流式块` 结构与 `ChatCompletionChunk` 对齐 |
| `message_stream_service.py` | ✅ 完全兼容 | 依赖的 `chunk.choices`、`chunk.usage` 保留 |
| `message_ai_helpers.py` | ✅ 渐进式 | 可选使用工厂，现有代码不变 |
| `KieChatAdapter` | ✅ 最小改动 | 只需继承基类，现有方法保持 |

### 5.2 关键对齐点

```python
# chat_stream_manager.py 依赖的结构
async for chunk in stream:
    if chunk.choices:                           # ← 流式块.content 对应
        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
    if chunk.usage:                             # ← 流式块.prompt_tokens 对应
        cost = adapter.estimate_cost(
            chunk.usage.prompt_tokens,
            chunk.usage.completion_tokens,
        )

# 流式块 结构设计
@dataclass
class 流式块:
    content: Optional[str] = None           # → chunk.choices[0].delta.content
    prompt_tokens: int = 0                  # → chunk.usage.prompt_tokens
    completion_tokens: int = 0              # → chunk.usage.completion_tokens
```

### 5.3 向后兼容策略

| 策略 | 实现方式 |
|------|---------|
| **双轨运行** | 现有代码和新工厂并存，逐步迁移 |
| **接口不变** | KIE 适配器现有方法全部保留 |
| **类型别名** | 提供英文别名（`StreamChunk`、`CostEstimate`）兼容现有风格 |

---

## 六、实施计划

### 6.1 分阶段实施

| 阶段 | 内容 | 风险 | 工作量 |
|------|------|------|--------|
| **Phase 1** | 创建 `基类.py`、`工厂.py`，KIE 继承基类 | 零风险 | 2h |
| **Phase 2** | 实现 Google 官方适配器 | 低风险 | 3h |
| **Phase 3** | 改造 `message_ai_helpers.py` 使用工厂 | 中风险 | 1.5h |
| **Phase 4** | 清理旧代码、更新文档 | 低风险 | 0.5h |
| **总计** | | | **7h** |

### 6.2 Phase 1 详细步骤

1. 创建 `backend/services/adapters/基类.py`
2. 创建 `backend/services/adapters/工厂.py`
3. 修改 `backend/services/adapters/kie/chat_adapter.py`（添加继承）
4. 更新 `backend/services/adapters/__init__.py`（导出新模块）
5. 测试验证现有功能不受影响

### 6.3 Phase 2 详细步骤

1. 创建 `backend/services/adapters/google/` 目录
2. 实现 `GoogleChatAdapter`
3. 添加 `google_api_key` 到 `core/config.py`
4. 在工厂中启用 Google Provider
5. 测试 Google 模型调用

### 6.4 Phase 3 详细步骤

1. 修改 `prepare_ai_stream_client()` 使用工厂
2. 修改 `stream_ai_response()` 使用统一格式
3. 测试所有聊天场景

---

## 七、新增 Provider 流程

完成统一适配器后，新增 Provider 只需 3 步：

### 步骤 1：在模型注册表添加配置

```python
# 工厂.py
模型注册表["gpt-4o"] = 模型配置(
    model_id="gpt-4o",
    provider=模型提供商.OPENAI,
    provider_model="gpt-4o",
    display_name="GPT-4o",
    input_price=2.50,
    output_price=10.0,
    credits_per_1k_input=5,
    credits_per_1k_output=20,
    supports_vision=True,
    supports_tools=True,
)
```

### 步骤 2：实现适配器类

```python
# openai/chat_adapter.py
class OpenAIChatAdapter(聊天适配器基类):

    @property
    def 提供商(self) -> 模型提供商:
        return 模型提供商.OPENAI

    async def 流式聊天(self, messages, **kwargs) -> AsyncIterator[流式块]:
        # 实现 OpenAI API 调用
        ...

    def 估算成本(self, input_tokens, output_tokens) -> 成本估算:
        # 实现成本计算
        ...
```

### 步骤 3：在工厂添加分支

```python
# 工厂.py
elif config.provider == 模型提供商.OPENAI:
    from .openai import OpenAIChatAdapter
    return OpenAIChatAdapter(config.provider_model, settings.openai_api_key)
```

---

## 八、工程细节补遗（v1.1 新增）

### 8.1 生命周期管理

#### 8.1.1 问题分析

当前代码每次请求都创建新的 `KieClient`：

```python
# message_ai_helpers.py - 现有代码（每次都 new）
client = KieClient(settings.kie_api_key)
adapter = KieChatAdapter(client, model)

# image_service.py - 用完即销毁
async with KieClient(self.settings.kie_api_key) as client:
    ...
```

**影响**：
- 低并发（当前）：影响可忽略，`httpx.AsyncClient` 内部有连接池
- 高并发（未来）：频繁创建/销毁会增加 TCP 握手开销

#### 8.1.2 解决方案

**方案 A：客户端池（推荐，已加入工厂设计）**

```python
# 工厂.py
启用客户端池 = True  # 开启后使用单例

client = await 客户端池.获取客户端(模型提供商.KIE, api_key)
```

**方案 B：应用级单例**

```python
# main.py - FastAPI 生命周期
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    yield
    # 关闭时清理
    await 客户端池.关闭所有()

app = FastAPI(lifespan=lifespan)
```

#### 8.1.3 实施建议

| 用户量 | 建议 |
|--------|------|
| < 100 并发 | 保持现有（每次 new），无需优化 |
| 100-1000 并发 | 启用客户端池 |
| > 1000 并发 | 客户端池 + 负载均衡 |

---

### 8.2 消息格式转换

#### 8.2.1 格式差异对比

| Provider | 消息格式 | 多模态格式 |
|----------|---------|-----------|
| **KIE (OpenAI)** | `{"role": "user", "content": "..."}` | `{"type": "image_url", "image_url": {"url": "..."}}` |
| **Google 官方** | `{"role": "user", "parts": [{"text": "..."}]}` | `{"inline_data": {"mime_type": "...", "data": "..."}}` |
| **OpenAI** | 同 KIE | 同 KIE |
| **Anthropic** | `{"role": "user", "content": [...]}` | `{"type": "image", "source": {...}}` |

#### 8.2.2 转换策略

**基类定义统一输入格式**：

```python
# 调用方传入
messages = [
    统一消息(
        role="user",
        content="描述这张图片",
        parts=[
            多模态部件(type=媒体类型.IMAGE, url="https://...")
        ]
    )
]
```

**各适配器实现 `_转换消息格式()`**：

```python
# KIE 适配器
def _转换消息格式(self, messages: List[统一消息]) -> List[Dict]:
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

# Google 适配器
def _转换消息格式(self, messages: List[统一消息]) -> List[Dict]:
    result = []
    for msg in messages:
        parts = [{"text": msg.content}]
        for part in msg.parts:
            parts.append(part.to_google_format())
        result.append({"role": msg.role, "parts": parts})
    return result
```

---

### 8.3 多模态处理

#### 8.3.1 图片处理差异

| Provider | 支持格式 | 最佳实践 |
|----------|---------|---------|
| **KIE** | URL / Base64 data URL | 直接传 URL |
| **Google 官方** | Base64 / File API | 小图用 Base64，大图用 File API |

#### 8.3.2 视频处理差异

| Provider | 支持格式 | 限制 |
|----------|---------|------|
| **KIE** | URL | 通过 URL 访问 |
| **Google 官方** | File API | 必须先上传到 Google Cloud |

#### 8.3.3 统一处理流程

```python
async def 处理多模态输入(self, url: str) -> 多模态部件:
    """
    统一处理多模态输入

    1. 判断是 URL 还是 Base64
    2. 根据 Provider 决定是否需要转换
    3. 大文件考虑使用 File API
    """
    部件 = self._解析多模态URL(url)

    # Google 官方需要特殊处理
    if self.提供商 == 模型提供商.GOOGLE:
        if 部件.type == 媒体类型.VIDEO:
            # 视频必须使用 File API
            部件 = await self._上传到Google文件API(部件)
        elif len(部件.data or "") > 10 * 1024 * 1024:  # > 10MB
            # 大图片也用 File API
            部件 = await self._上传到Google文件API(部件)

    return 部件
```

---

### 8.4 积分计算职责界定

**原则**：适配器只负责**估算**，不负责**执行**

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   适配器        │────▶│  chat_stream_   │────▶│  扣款服务        │
│ estimate_cost() │     │  manager.py     │     │ deduct_credits() │
│ (估算)          │     │ (调用估算)       │     │ (执行扣款)       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**已符合**：当前设计中 `估算成本()` 只返回估算值，扣款在 `chat_stream_manager.py` 中调用 `deduct_user_credits()`。

---

## 九、风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Phase 1 影响现有功能 | 极低 | KIE 适配器只添加继承，不改现有方法 |
| Phase 3 改造出错 | 中 | 充分测试所有聊天场景 |
| 中文命名不规范 | 低 | 提供英文别名兼容现有代码风格 |
| Google API 不稳定 | 中 | 错误重试 + 降级到 KIE |
| **高并发连接开销（v1.1）** | 低 | 启用客户端池优化 |
| **消息格式转换出错（v1.1）** | 中 | 各适配器充分测试多模态场景 |

---

## 十、检查清单

### 10.1 Phase 1 检查清单

- [ ] 创建 `backend/services/adapters/基类.py`
- [ ] 创建 `backend/services/adapters/工厂.py`
- [ ] 修改 `backend/services/adapters/kie/chat_adapter.py`（继承基类）
- [ ] 更新 `backend/services/adapters/__init__.py`
- [ ] 测试：现有聊天功能正常
- [ ] 测试：流式响应正常
- [ ] 测试：积分计费正常

### 10.2 Phase 2 检查清单

- [ ] 创建 `backend/services/adapters/google/` 目录
- [ ] 实现 `GoogleChatAdapter`
- [ ] 实现 `_转换消息格式()` 方法（v1.1）
- [ ] 实现多模态处理（Base64 / File API）（v1.1）
- [ ] 添加 `google_api_key` 配置
- [ ] 测试：Google 模型调用
- [ ] 测试：流式响应
- [ ] 测试：成本估算
- [ ] 测试：多模态输入（图片、视频）（v1.1）

### 10.3 Phase 3 检查清单

- [ ] 改造 `message_ai_helpers.py`
- [ ] 测试：所有模型切换正常
- [ ] 测试：多模态输入（图片、视频）
- [ ] 测试：推理强度参数传递
- [ ] 更新 PROJECT_OVERVIEW.md
- [ ] 更新 FUNCTION_INDEX.md

### 10.4 性能优化检查清单（可选，v1.1）

- [ ] 评估当前并发量是否需要客户端池
- [ ] 启用 `启用客户端池 = True`
- [ ] 配置 FastAPI lifespan 清理
- [ ] 压测验证连接复用效果

### 10.5 回归测试检查清单（Phase 3 完成后）

**目标**：确保改造未引入新 Bug

#### 10.5.1 功能回归测试

| 场景 | 测试内容 | 预期结果 |
|------|---------|---------|
| **基础聊天** | 发送纯文本消息 | 正常返回 AI 回复 |
| **流式响应** | 观察逐字输出 | 内容连续、无乱码 |
| **多模态-图片** | 发送图片 + 提问 | AI 能描述图片内容 |
| **多模态-视频** | 发送视频 + 提问 | AI 能理解视频内容 |
| **推理强度** | 切换 minimal/low/medium/high | 响应速度和质量有差异 |
| **思考模式** | 使用 deep_think（Pro） | 返回更详细的推理 |
| **积分扣除** | 完成对话后检查余额 | 积分正确扣除 |
| **错误处理** | 模拟 API 超时/失败 | 显示友好错误消息 |
| **任务恢复** | 刷新页面恢复进行中任务 | 能继续接收流式内容 |
| **模型切换** | KIE 模型间切换 | 各模型正常工作 |
| **Google 模型** | 使用 gemini-2.5-flash | 正常返回（Phase 2） |

#### 10.5.2 边界场景测试

| 场景 | 测试方法 | 预期 |
|------|---------|------|
| **空消息** | 发送空字符串 | 前端拦截或后端返回错误 |
| **超长消息** | 发送 10000 字符 | 正常处理或截断提示 |
| **并发请求** | 同时发多条消息 | 不丢失、顺序正确 |
| **网络中断** | 断网后重连 | 任务恢复正常 |
| **无效模型ID** | 传入不存在的模型 | 降级到默认模型 |

#### 10.5.3 性能基线对比

```bash
# 改造前记录基线
# 1. 单次请求响应时间（首 token）
# 2. 完整响应时间
# 3. 内存占用

# 改造后对比
# 确保无明显性能退化（允许 ±10%）
```

### 10.6 代码清理检查清单（Phase 4）

**目标**：删除冗余代码，保持代码库整洁

#### 10.6.1 待删除/重构的代码

| 文件 | 内容 | 处理方式 |
|------|------|---------|
| `message_ai_helpers.py` | 旧的 `prepare_ai_stream_client()` | 改用工厂后删除旧实现 |
| `message_ai_helpers.py` | 硬编码的模型判断逻辑 | 迁移到模型注册表后删除 |
| `message_stream_service.py` | 直接 import KieClient | 改为通过工厂获取 |
| `chat_stream_manager.py` | 直接 import KieChatAdapter | 改为通过工厂获取 |

#### 10.6.2 清理步骤

```bash
# 1. 搜索直接导入 KIE 的代码
grep -r "from services.adapters.kie import" backend/services/
grep -r "from services.adapters.kie.client import" backend/

# 2. 搜索硬编码的模型判断
grep -r "gemini-3-pro\|gemini-3-flash" backend/ --include="*.py"

# 3. 搜索未使用的导入
# 使用 IDE 或 autoflake
autoflake --remove-all-unused-imports --in-place --recursive backend/

# 4. 检查未使用的函数/类
# 使用 vulture 或 IDE 检查
vulture backend/services/adapters/
```

#### 10.6.3 清理检查项

- [ ] 删除 `prepare_ai_stream_client()` 旧实现（保留新的工厂版本）
- [ ] 删除 `message_ai_helpers.py` 中硬编码的模型列表
- [ ] 移除未使用的 import 语句
- [ ] 移除被注释掉的旧代码
- [ ] 检查是否有重复的工具函数可合并
- [ ] 确认没有遗留的 `# TODO: 迁移到工厂` 注释
- [ ] 运行 `ruff check` 或 `flake8` 确认无 lint 错误

#### 10.6.4 向后兼容过渡期

如果需要保留旧接口一段时间：

```python
# message_ai_helpers.py - 过渡期兼容

# 旧接口（标记废弃，保留 2 个版本后删除）
import warnings

def prepare_ai_stream_client(model_id: Optional[str]):
    """
    @deprecated 请使用 创建聊天适配器()
    将在 v2.0 删除
    """
    warnings.warn(
        "prepare_ai_stream_client 已废弃，请使用 创建聊天适配器()",
        DeprecationWarning,
        stacklevel=2,
    )
    # 内部转发到新实现
    adapter = 创建聊天适配器(model_id)
    return adapter.model_id, adapter.client, adapter
```

#### 10.6.5 清理后验证

- [ ] 所有测试通过
- [ ] 无 import 错误
- [ ] 无运行时警告
- [ ] 代码覆盖率无下降
- [ ] PR Review 确认清理完整

---

## 十一、文档更新检查清单

完成实施后需更新的文档：

| 文档 | 更新内容 |
|------|---------|
| `PROJECT_OVERVIEW.md` | 新增 `基类.py`、`工厂.py` 文件说明 |
| `FUNCTION_INDEX.md` | 新增 `创建聊天适配器()`、`获取模型配置()` 等函数 |
| `.env.example` | 新增 `GOOGLE_API_KEY` 配置项 |
| `README.md` | 更新支持的模型列表（如需） |

---

## 十二、深度检查清单（Phase 4 完成后）

> **目标**：确保改造彻底，无残留旧代码、重复逻辑、冗余调用

### 12.1 重复逻辑检查

#### 12.1.1 模型判断逻辑

**问题**：改造前，模型判断逻辑可能散落在多处

```bash
# 搜索硬编码的模型判断
grep -rn "gemini-3-pro\|gemini-3-flash" backend/ --include="*.py"
grep -rn "in (\"gemini\|in \[\"gemini" backend/ --include="*.py"
```

**期望结果**：
- 模型判断 **只存在于** `factory.py` 的 `MODEL_REGISTRY`
- 其他文件不应有模型名硬编码

**清理动作**：
- [ ] `message_ai_helpers.py` 中的 `if model_id in ("gemini-3-pro", "gemini-3-flash")` → 删除
- [ ] 其他发现的硬编码 → 改用 `MODEL_REGISTRY.keys()` 或工厂函数

#### 12.1.2 API Key 获取逻辑

**问题**：API Key 获取可能在多处重复

```bash
# 搜索 API Key 获取
grep -rn "settings.kie_api_key\|settings.google_api_key" backend/ --include="*.py"
```

**期望结果**：
- API Key 获取 **只存在于** `factory.py` 的 `create_chat_adapter()`
- 调用方不应直接取 API Key

**清理动作**：
- [ ] 直接取 `settings.kie_api_key` 的代码 → 改用工厂函数
- [ ] 统一由工厂处理 API Key 校验

#### 12.1.3 成本估算逻辑

**问题**：成本计算逻辑可能存在重复

```bash
# 搜索成本计算相关代码
grep -rn "estimate_cost\|estimated_credits\|credits_per_1k" backend/ --include="*.py"
```

**期望结果**：
- 成本计算 **只存在于** 各适配器的 `estimate_cost()` / `estimate_cost_unified()` 方法
- 调用方只调用适配器方法，不自行计算

**清理动作**：
- [ ] 检查是否有手动计算积分的代码
- [ ] 统一使用 `adapter.estimate_cost()` 或 `adapter.estimate_cost_unified()`

---

### 12.2 旧代码清理

#### 12.2.1 待删除的函数/类

| 文件 | 函数/类 | 原因 | 替代方案 |
|------|---------|------|---------|
| `message_ai_helpers.py` | `prepare_ai_stream_client()` 旧实现 | 被工厂取代 | `create_chat_adapter()` |
| `kie/chat_adapter.py` | `create_chat_adapter()` 旧函数 | 移至工厂 | `adapters.create_chat_adapter()` |

#### 12.2.2 待删除的导入

```bash
# 搜索直接导入 KIE 的代码（应改用工厂）
grep -rn "from services.adapters.kie import KieClient" backend/services/ --include="*.py"
grep -rn "from services.adapters.kie.client import KieClient" backend/services/ --include="*.py"
grep -rn "from services.adapters.kie import KieChatAdapter" backend/services/ --include="*.py"
```

**期望结果**：
- 业务代码不应直接导入 `KieClient`、`KieChatAdapter`
- 应通过 `from services.adapters import create_chat_adapter` 使用

**例外**：
- `adapters/__init__.py` 为了向后兼容可保留导出
- `factory.py` 内部需要导入各适配器

#### 12.2.3 清理命令

```bash
# 使用 autoflake 清理未使用的导入
pip install autoflake
autoflake --remove-all-unused-imports --in-place --recursive backend/services/

# 使用 vulture 检测死代码
pip install vulture
vulture backend/services/adapters/ --min-confidence 80
```

---

### 12.3 重复调用检查

#### 12.3.1 客户端创建

**问题**：同一请求中可能多次创建客户端

```python
# 错误示例：多次创建
client1 = KieClient(api_key)
adapter1 = KieChatAdapter(client1, model)
# ... 后续又创建 ...
client2 = KieClient(api_key)  # 重复创建！
```

**检查点**：
- [ ] 单次请求只创建一个适配器实例
- [ ] 适配器复用同一个客户端

#### 12.3.2 配置读取

**问题**：`get_settings()` 可能被重复调用

```bash
# 搜索配置读取
grep -rn "get_settings()" backend/services/ --include="*.py" | wc -l
```

**期望**：
- 每个函数内最多调用一次 `get_settings()`
- 不在循环内重复调用

#### 12.3.3 数据库查询

检查是否有可合并的查询：

```bash
# 搜索连续的数据库查询
grep -rn "self.db.table" backend/services/message_stream_service.py
```

---

### 12.4 冗余代码清理

#### 12.4.1 冗余的类型别名

检查 `base.py` 中的类型别名是否都被使用：

```bash
# 检查别名使用情况
grep -rn "ModelProvider\|StreamChunk\|ChatResponse\|CostEstimate\|BaseChatAdapter" backend/ --include="*.py"
```

**清理规则**：
- 如果别名未被使用 → 删除
- 如果只有一处使用 → 考虑直接使用原名

#### 12.4.2 冗余的 try-except

检查是否有不必要的异常捕获：

```python
# 冗余示例
try:
    result = some_operation()
except Exception as e:
    raise e  # 无意义的重新抛出
```

#### 12.4.3 冗余的日志

```bash
# 检查重复的日志模式
grep -rn "logger.debug\|logger.info\|logger.error" backend/services/adapters/ --include="*.py"
```

**清理规则**：
- 入口和出口各一条日志即可
- 删除过于冗余的调试日志

---

### 12.5 代码质量审查

#### 12.5.1 质量指标检查

| 指标 | 阈值 | 检查命令 |
|------|------|---------|
| 文件行数 | ≤ 500 行 | `wc -l backend/services/adapters/*.py` |
| 函数行数 | ≤ 120 行 | 人工审查长函数 |
| 圈复杂度 | ≤ 15 | `radon cc backend/services/adapters/ -s` |
| 嵌套层级 | ≤ 4 层 | 人工审查 |

#### 12.5.2 Lint 检查

```bash
# Ruff 检查
pip install ruff
ruff check backend/services/adapters/

# 自动修复
ruff check backend/services/adapters/ --fix
```

#### 12.5.3 类型检查

```bash
# Mypy 检查（可选）
pip install mypy
mypy backend/services/adapters/ --ignore-missing-imports
```

---

### 12.6 深度检查清单汇总

#### Phase 4 完成后必须完成：

**重复逻辑**
- [ ] 模型判断逻辑只在 `MODEL_REGISTRY`
- [ ] API Key 获取只在工厂函数
- [ ] 成本估算只在适配器方法

**旧代码**
- [ ] 删除 `message_ai_helpers.py` 中旧的 `prepare_ai_stream_client()`
- [ ] 清理直接导入 `KieClient`/`KieChatAdapter` 的代码
- [ ] 运行 `autoflake` 清理未使用导入

**重复调用**
- [ ] 单次请求只创建一个适配器
- [ ] `get_settings()` 不在循环中调用

**冗余代码**
- [ ] 删除未使用的类型别名
- [ ] 删除无意义的 try-except
- [ ] 精简过多的日志

**质量指标**
- [ ] 所有文件 ≤ 500 行
- [ ] 所有函数 ≤ 120 行
- [ ] `ruff check` 无错误
- [ ] 所有测试通过

---

### 12.7 自动化检查脚本

创建一个检查脚本供执行：

```bash
#!/bin/bash
# scripts/check_adapter_cleanup.sh

echo "=== 统一适配器深度检查 ==="

echo ""
echo "1. 检查硬编码模型名..."
grep -rn "gemini-3-pro\|gemini-3-flash" backend/services/ --include="*.py" | grep -v "factory.py\|MODEL_REGISTRY" || echo "✓ 无硬编码"

echo ""
echo "2. 检查直接导入 KIE..."
grep -rn "from services.adapters.kie import KieClient\|from services.adapters.kie.client import" backend/services/ --include="*.py" | grep -v "__init__.py\|factory.py" || echo "✓ 无直接导入"

echo ""
echo "3. 检查文件行数..."
wc -l backend/services/adapters/*.py backend/services/adapters/kie/*.py

echo ""
echo "4. 运行 Ruff..."
ruff check backend/services/adapters/ || echo "⚠ 有 lint 问题"

echo ""
echo "5. 检查死代码..."
vulture backend/services/adapters/ --min-confidence 80 || echo "⚠ 可能有死代码"

echo ""
echo "=== 检查完成 ==="
```

---

**确认后开始实施。**
