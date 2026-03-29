"""
智能意图路由器

通过千问 Function Calling 分析用户消息，决定：
1. 生成类型（CHAT / IMAGE / VIDEO）
2. 自动推断人设（system_prompt）
3. 搜索需求（web_search）
4. 动态模型选择 + 失败重试路由

降级链：qwen3.5-plus → qwen3.5-flash → 关键词兜底
重试降级链：千问主模型 → 千问备用 → 确定性兜底 → 放弃
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import settings
from schemas.message import ContentPart, GenerationType, TextPart, ImagePart
from config.smart_model_config import (
    TOOL_TO_TYPE,
    MODEL_TO_GEN_TYPE,
    AUTO_MODEL_DEFAULTS,
    ROUTER_TOOLS,
    SMART_CONFIG,
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    build_retry_tools,
    get_remaining_models,
    get_image_to_video_model,
)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class RoutingDecision:
    """路由决策结果"""

    generation_type: GenerationType
    system_prompt: Optional[str] = None
    tool_params: Dict[str, Any] = field(default_factory=dict)
    search_query: Optional[str] = None
    recommended_model: Optional[str] = None
    raw_tool_name: str = "text_chat"
    routed_by: str = "keyword"


@dataclass
class RetryContext:
    """重试上下文（跨重试传递）"""

    is_smart_mode: bool
    original_content: str
    generation_type: GenerationType
    failed_attempts: List[Dict[str, str]] = field(default_factory=list)
    max_retries: int = 2

    @property
    def can_retry(self) -> bool:
        return self.is_smart_mode and len(self.failed_attempts) < self.max_retries

    @property
    def failed_models(self) -> List[str]:
        return [a["model"] for a in self.failed_attempts]

    def add_failure(self, model: str, error: str) -> None:
        self.failed_attempts.append({"model": model, "error": error})


# ============================================================
# 提示词 & 常量
# ============================================================

def _build_router_prompt() -> str:
    """从 smart_models.json 动态生成路由提示词（消除硬编码模型名）"""
    image_edit_models = [
        m["id"] for m in SMART_CONFIG.get("image", {}).get("models", [])
        if m.get("requires_image")
    ]
    image_pro_models = [
        m["id"] for m in SMART_CONFIG.get("image", {}).get("models", [])
        if not m.get("requires_image") and m["id"] != DEFAULT_IMAGE_MODEL
    ]
    edit_hint = " 或 ".join(image_edit_models + image_pro_models) or "图片编辑模型"
    i2v_model = get_image_to_video_model()

    return (
        "你是智能意图路由器。分析用户消息，必须调用一个工具，"
        "并为该工具选择最合适的 model。\n\n"
        "意图判断：\n"
        "- generate_image: 用户明确要求生成/画/绘制/修改图片\n"
        "- generate_video: 用户明确要求生成/制作视频\n"
        "- web_search: 需要搜索互联网最新信息才能回答\n"
        "- text_chat: 其他所有对话（包括讨论图片风格、分析设计等）\n\n"
        "模型选择要点：\n"
        "- 根据 model 参数的 description 选择最匹配的模型\n"
        f"- 用户有图片且要编辑 → {edit_hint}\n"
        f"- 用户有图片且要做视频 → {i2v_model}\n"
        "- 用户要求高质量/专业级 → 选更高级的模型\n"
        "- 根据各模型的 description 自动匹配最合适的聊天模型\n\n"
        "重要：仅当用户明确要求「生成/画/制作」时才调用生成工具。"
        "讨论、分析、解释等一律用 text_chat。"
    )


ROUTER_SYSTEM_PROMPT = _build_router_prompt()

RETRY_ROUTER_SYSTEM_PROMPT = (
    "你是智能重试路由器。之前选择的模型执行失败了，"
    "你需要从剩余可用模型中选择一个替代模型重试。\n\n"
    "分析失败原因，选择最合适的替代模型。"
    "如果没有合适的替代模型可选，调用 give_up 工具放弃重试。"
)

DASHSCOPE_BASE_URL = settings.dashscope_base_url

# 智能模型 ID（前端 smartModel.ts 对应）
SMART_MODEL_ID = "auto"

# 向后兼容别名（旧代码可能直接引用这些名称）
_MODEL_TO_GEN_TYPE = MODEL_TO_GEN_TYPE
_AUTO_MODEL_DEFAULTS = AUTO_MODEL_DEFAULTS


def resolve_auto_model(
    gen_type: GenerationType,
    content: List[ContentPart],
    recommended_model: Optional[str] = None,
) -> str:
    """根据千问推荐 + 路由意图解析智能模型到实际工作模型"""
    if recommended_model and recommended_model in MODEL_TO_GEN_TYPE:
        if MODEL_TO_GEN_TYPE[recommended_model] == gen_type:
            return recommended_model
        logger.warning(
            f"Model type mismatch | recommended={recommended_model} | "
            f"gen_type={gen_type.value} | falling back to default"
        )

    if gen_type == GenerationType.VIDEO:
        has_images = any(isinstance(p, ImagePart) for p in content)
        if has_images:
            return get_image_to_video_model()
    return AUTO_MODEL_DEFAULTS.get(gen_type, DEFAULT_CHAT_MODEL)


# ============================================================
# 路由器实现
# ============================================================


class IntentRouter:
    """智能意图路由器（DashScope Function Calling）"""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    # ------ 首次路由 ------

    async def route(
        self,
        content: List[ContentPart],
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> RoutingDecision:
        """分析用户消息，返回路由决策"""
        from core.config import get_settings

        settings = get_settings()
        text = self._extract_text(content)
        input_length = len(text) if text else 0
        has_image = any(isinstance(p, ImagePart) for p in content)

        if not settings.intent_router_enabled:
            decision = self._keyword_fallback(content)
            self._record_routing_signal(
                decision, user_id, input_length, has_image, "disabled", org_id=org_id,
            )
            return decision

        if not settings.dashscope_api_key:
            logger.warning("Intent router skipped: DASHSCOPE_API_KEY not configured")
            decision = self._keyword_fallback(content)
            self._record_routing_signal(
                decision, user_id, input_length, has_image, "no_api_key", org_id=org_id,
            )
            return decision

        if not text or len(text.strip()) < 2:
            decision = RoutingDecision(
                generation_type=GenerationType.CHAT,
                routed_by="skip_empty",
            )
            self._record_routing_signal(
                decision, user_id, input_length, has_image, "skip_empty", org_id=org_id,
            )
            return decision

        # 上下文前缀（让千问知道用户是否附带了图片）
        image_count = sum(1 for p in content if isinstance(p, ImagePart))
        context_prefix = ""
        if image_count > 0:
            context_prefix = f"[上下文：用户附带了{image_count}张图片]\n"

        # 查询知识库，增强路由 system prompt
        enhanced_prompt = await self._enhance_with_knowledge(text, org_id=org_id)

        # 过滤熔断 Provider 的模型
        active_tools = self._filter_tools_by_breaker(ROUTER_TOOLS)

        # 降级链：主模型 → 备用模型 → 关键词
        models = [
            settings.intent_router_model,
            settings.intent_router_fallback_model,
        ]

        for model in models:
            try:
                decision = await self._call_model(
                    api_key=settings.dashscope_api_key,
                    model=model,
                    system_prompt=enhanced_prompt,
                    text=context_prefix + text,
                    tools=active_tools,
                    timeout=settings.intent_router_timeout,
                )
                if decision:
                    logger.info(
                        f"Intent routed | tool={decision.raw_tool_name} | "
                        f"type={decision.generation_type.value} | "
                        f"recommended={decision.recommended_model} | "
                        f"router_model={model} | user_id={user_id}"
                    )
                    self._record_routing_signal(
                        decision, user_id, input_length, has_image, model, org_id=org_id,
                    )
                    return decision
            except Exception as e:
                logger.warning(
                    f"Router model failed, trying next | "
                    f"model={model} | error={e}"
                )

        logger.warning("All router models failed, using keyword fallback")
        decision = self._keyword_fallback(content)
        self._record_routing_signal(
            decision, user_id, input_length, has_image, "all_failed", org_id=org_id,
        )
        return decision

    # ------ 重试路由 ------

    async def route_retry(
        self,
        original_content: str,
        generation_type: GenerationType,
        failed_models: List[str],
        error_message: str,
    ) -> Optional[RoutingDecision]:
        """重试路由：将失败信息发给千问，让它选择替代模型

        降级链：千问主模型 → 千问备用 → 确定性兜底 → None
        """
        from core.config import get_settings

        settings = get_settings()
        if not settings.dashscope_api_key:
            return self._deterministic_fallback(generation_type, failed_models)

        retry_message = (
            f"原始请求：{original_content}\n"
            f"已失败的模型：{', '.join(failed_models)}\n"
            f"最近错误：{error_message}\n"
            f"请从剩余可用模型中选择一个替代模型重试。"
        )

        retry_tools = build_retry_tools(generation_type, failed_models)
        real_tools = [t for t in retry_tools if t["function"]["name"] != "give_up"]
        if not real_tools:
            logger.info(f"No remaining models for retry | type={generation_type.value}")
            return None

        router_models = [
            settings.intent_router_model,
            settings.intent_router_fallback_model,
        ]

        for router_model in router_models:
            try:
                decision = await self._call_model(
                    api_key=settings.dashscope_api_key,
                    model=router_model,
                    system_prompt=RETRY_ROUTER_SYSTEM_PROMPT,
                    text=retry_message,
                    tools=retry_tools,
                    timeout=settings.intent_router_timeout,
                )
                if decision and decision.raw_tool_name == "give_up":
                    logger.info(f"Router chose to give_up | model={router_model}")
                    return self._deterministic_fallback(generation_type, failed_models)
                if decision:
                    logger.info(
                        f"Retry routed | recommended={decision.recommended_model} | "
                        f"router_model={router_model} | failed={failed_models}"
                    )
                    return decision
            except Exception as e:
                logger.warning(f"Retry router failed | model={router_model} | error={e}")

        return self._deterministic_fallback(generation_type, failed_models)

    def _deterministic_fallback(
        self,
        generation_type: GenerationType,
        failed_models: List[str],
    ) -> Optional[RoutingDecision]:
        """确定性兜底：按优先级取同类型下一个未试模型"""
        remaining = get_remaining_models(generation_type, failed_models)
        if remaining:
            logger.info(
                f"Deterministic fallback | model={remaining[0]} | "
                f"type={generation_type.value}"
            )
            return RoutingDecision(
                generation_type=generation_type,
                recommended_model=remaining[0],
                routed_by="deterministic_fallback",
            )
        logger.warning(
            f"All models exhausted | type={generation_type.value} | "
            f"failed={failed_models}"
        )
        return None

    # ------ 搜索 ------

    async def execute_search(
        self,
        query: str,
        user_text: str,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """调用千问搜索能力，返回搜索增强的回答摘要"""
        from core.config import get_settings

        settings = get_settings()
        if not settings.dashscope_api_key:
            return None

        try:
            client = await self._get_client(
                settings.dashscope_api_key, timeout=10.0,
            )

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_text})

            response = await client.post("/chat/completions", json={
                "model": settings.intent_router_model,
                "messages": messages,
                "enable_search": True,
                "temperature": 0.3,
                "max_tokens": 2000,
            })
            response.raise_for_status()
            data = response.json()

            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content:
                    logger.info(f"Web search completed | query={query} | len={len(content)}")
                    return content

        except Exception as e:
            logger.warning(f"Web search failed | query={query} | error={e}")

        return None

    # ------ 内部方法 ------

    async def _call_model(
        self,
        api_key: str,
        model: str,
        system_prompt: str,
        text: str,
        tools: List[Dict[str, Any]],
        timeout: float,
    ) -> Optional[RoutingDecision]:
        """统一的千问调用方法（首次路由和重试路由共用）"""
        client = await self._get_client(api_key, timeout)
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 200,
            },
        )
        response.raise_for_status()
        return self._parse_response(response.json())

    def _parse_response(self, data: Dict[str, Any]) -> Optional[RoutingDecision]:
        """解析模型响应中的 tool_calls"""
        choices = data.get("choices", [])
        if not choices:
            return None

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return RoutingDecision(
                generation_type=GenerationType.CHAT,
                raw_tool_name="text_chat",
                routed_by="model_no_tool",
            )

        tool_call = tool_calls[0]
        func = tool_call.get("function", {})
        tool_name = func.get("name", "text_chat")

        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            arguments = {}

        gen_type = TOOL_TO_TYPE.get(tool_name, GenerationType.CHAT)

        return RoutingDecision(
            generation_type=gen_type,
            system_prompt=arguments.get("system_prompt"),
            tool_params=arguments,
            search_query=arguments.get("search_query"),
            recommended_model=arguments.get("model"),
            raw_tool_name=tool_name,
            routed_by="model",
        )

    @staticmethod
    def _filter_tools_by_breaker(
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """移除熔断 Provider 模型的工具 enum 选项"""
        import copy as _copy

        try:
            from services.circuit_breaker import is_provider_available
            from services.adapters.factory import MODEL_REGISTRY
        except Exception:
            return tools

        available_models = {
            mid for mid, cfg in MODEL_REGISTRY.items()
            if is_provider_available(cfg.provider)
        }

        filtered = []
        for tool in tools:
            tool_copy = _copy.deepcopy(tool)
            props = tool_copy["function"]["parameters"]["properties"]
            if "model" in props and "enum" in props["model"]:
                props["model"]["enum"] = [
                    m for m in props["model"]["enum"] if m in available_models
                ]
                if not props["model"]["enum"]:
                    continue
            filtered.append(tool_copy)
        return filtered

    def _keyword_fallback(self, content: List[ContentPart]) -> RoutingDecision:
        """关键词兜底"""
        from schemas.message import infer_generation_type

        gen_type = infer_generation_type(content)
        return RoutingDecision(
            generation_type=gen_type,
            raw_tool_name=f"keyword_{gen_type.value}",
            routed_by="keyword",
        )

    async def _enhance_with_knowledge(self, text: str, org_id: str | None = None) -> str:
        """查询知识库，将相关知识注入路由 system prompt"""
        try:
            from services.knowledge_service import search_relevant

            knowledge_items = await search_relevant(query=text, limit=5, org_id=org_id)
            if knowledge_items:
                knowledge_text = "\n".join(
                    f"- {k['title']}: {k['content']}" for k in knowledge_items
                )
                return (
                    ROUTER_SYSTEM_PROMPT
                    + f"\n\n你已掌握的经验知识：\n{knowledge_text}"
                )
        except Exception as e:
            logger.debug(f"Knowledge injection skipped | error={e}")

        return ROUTER_SYSTEM_PROMPT

    def _extract_text(self, content: List[ContentPart]) -> str:
        """从 ContentPart 列表提取文本"""
        return " ".join(
            part.text for part in content if isinstance(part, TextPart)
        ).strip()

    async def _get_client(
        self, api_key: str, timeout: float
    ) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=DASHSCOPE_BASE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=timeout,
                    write=10.0,
                    pool=5.0,
                ),
            )
        return self._client

    @staticmethod
    def _record_routing_signal(
        decision: RoutingDecision,
        user_id: str,
        input_length: int,
        has_image: bool,
        router_model: str = "keyword",
        org_id: str | None = None,
    ) -> None:
        """记录路由决策信号到 knowledge_metrics（fire-and-forget）"""
        async def _do_record() -> None:
            try:
                from services.knowledge_service import record_metric
                await record_metric(
                    task_type="routing",
                    model_id=router_model,
                    status="success",
                    user_id=user_id,
                    org_id=org_id,
                    params={
                        "routing_tool": decision.raw_tool_name,
                        "routed_by": decision.routed_by,
                        "recommended_model": decision.recommended_model,
                        "input_length": input_length,
                        "has_image": has_image,
                    },
                )
            except Exception as e:
                logger.debug(f"Routing signal record skipped | error={e}")

        asyncio.create_task(_do_record())

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
