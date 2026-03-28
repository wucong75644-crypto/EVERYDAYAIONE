"""
Agent Loop — 多步工具编排引擎 (ReAct 模式)

对齐行业标准：OpenAI Agents SDK / Claude Agent SDK / LangGraph
核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复

工具分类（2 类）：
- INFO_TOOLS: 信息采集工具，结果回传大脑
- ROUTING_TOOLS: 路由决策工具，记录决策 + 确认回传

安全护栏（代码级，不靠提示词）：
- 最大轮次限制（max_turns）
- Token 预算追踪（max_total_tokens）
- 循环检测（连续 3 次相同调用 → 中止）
- Schema 验证（拒绝幻觉工具调用）
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

import httpx
from schemas.message import ContentPart, FilePart, ImagePart
from services.agent_context import AgentContextMixin
from services.agent_loop_infra import AgentInfraMixin
from services.agent_loop_tools import (
    AgentToolsMixin, ToolExpansionNeeded, _SLOW_TOOL_TIMEOUT,
)
from services.agent_loop_v2 import AgentLoopV2Mixin
from services.agent_types import AgentResult, PendingAsyncTool, AgentGuardrails
from services.tool_executor import ToolExecutor

# 向后兼容：外部通过 from services.agent_loop import ... 导入
__all__ = [
    "AgentLoop", "AgentResult", "PendingAsyncTool",
    "AgentGuardrails", "ToolExpansionNeeded", "_SLOW_TOOL_TIMEOUT",
]


class AgentLoop(
    AgentContextMixin, AgentToolsMixin, AgentInfraMixin, AgentLoopV2Mixin,
):
    """
    多步工具编排引擎 (ReAct 模式)

    核心循环：调 LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复
    上下文构建方法继承自 AgentContextMixin。
    工具处理方法继承自 AgentToolsMixin。
    基础设施方法继承自 AgentInfraMixin。
    """

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.executor = ToolExecutor(db, user_id, conversation_id, org_id=org_id)
        self._client: Optional[httpx.AsyncClient] = None
        self._settings: Optional[Any] = None

    async def run(
        self,
        content: List[ContentPart],
        thinking_mode: Optional[str] = None,
        task_id: Optional[str] = None,
        user_location: Optional[str] = None,
    ) -> AgentResult:
        """执行 Agent Loop，返回路由结果"""
        text = self._extract_text(content)
        has_image = any(isinstance(p, ImagePart) for p in content)
        has_file = any(isinstance(p, FilePart) for p in content)
        self._user_text = text  # 意图学习需要原始文本
        self._has_image = has_image  # 模型校验需要
        self._thinking_mode = thinking_mode  # 深度思考开关状态
        self._task_id = task_id  # WS 进度通知用
        self._user_location = user_location  # IP 定位城市

        result = await self._execute_loop(content)

        # 意图学习：路由成功（非 ask_user）时检查是否有 pending 的意图确认
        self._check_intent_learning(result, text)

        # 记录 Agent Loop 路由信号
        self._record_loop_signal(result, len(text), has_image, has_file)

        return result

    def _parse_phase1_response(
        self, response: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """解析 Phase 1 响应 → (domain, signals)

        Phase 1 工具名与 v1 ROUTING_TOOLS 不同（route_chat vs route_to_chat），
        不走 _process_tool_call()。独立解析，只提取 domain 和信号。
        """
        from config.phase_tools import PHASE1_TOOL_TO_DOMAIN

        choices = response.get("choices", [])
        if not choices:
            return "chat", {}

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls", [])
        if not tool_calls:
            return "chat", {}

        tc = tool_calls[0]
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Phase1 JSON parse error | raw={func.get('arguments')}",
            )
            arguments = {}

        domain = PHASE1_TOOL_TO_DOMAIN.get(tool_name, "chat")
        return domain, arguments

    async def _execute_loop(self, content: List[ContentPart]) -> AgentResult:
        """Agent Loop 入口 — 全量走 v2（意图优先 + 动态工具加载）"""
        from core.config import get_settings

        self._settings = get_settings()
        return await self._execute_loop_v2(content)
