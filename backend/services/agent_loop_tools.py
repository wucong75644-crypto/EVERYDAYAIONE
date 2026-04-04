"""
Agent Loop 工具处理 Mixin

负责 tool_call 的分发与执行：
- INFO 工具：执行 → 结果回传大脑
- ROUTING 工具：模型校验 → 记录决策 → 返回确认
- Schema 验证 + 循环检测 + 知识记录

与 AgentLoop 通过 Mixin 继承组合，共享 self.executor / self._settings 等属性。
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from config.agent_tools import INFO_TOOLS, ROUTING_TOOLS, validate_tool_call


# 慢速工具超时配置（秒），未列出的工具默认 30s
_SLOW_TOOL_TIMEOUT = {
    "generate_video": 360.0,
    "social_crawler": 180.0,
    "generate_image": 120.0,
    "code_execute": 120.0,
    "file_search": 60.0,
}


class ToolExpansionNeeded(Exception):
    """AI 调了不在筛选列表但系统支持的工具/action → 需要扩充后重跑"""

    def __init__(self, tool_name: str, action: str = ""):
        self.tool_name = tool_name
        self.action = action
        detail = f"{tool_name}.{action}" if action else tool_name
        super().__init__(f"扩充: {detail}")


class AgentToolsMixin:
    """Agent 工具处理方法集（Mixin，由 AgentLoop 继承）"""

    async def _process_tool_call(
        self,
        tc: Dict[str, Any],
        turn: int,
        guardrails: "AgentGuardrails",
        tool_results: List[Dict[str, Any]],
        accumulated_context: List[str],
        routing_holder: Dict[str, Any],
    ) -> None:
        """处理单个 tool_call（INFO → 执行回传，ROUTING → 记录决策）"""
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        tc_id = tc.get("id", "")

        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            arguments = {}

        # Schema 验证
        if not validate_tool_call(tool_name, arguments):
            logger.warning(
                f"Invalid tool call | tool={tool_name} | args={arguments}"
            )
            self._fire_and_forget_knowledge(
                task_type="tool_validation", model_id=tool_name,
                status="failed",
                error_message=f"幻觉工具调用: {tool_name}, 参数: {arguments}",
            )
            tool_results.append({
                "tool_call_id": tc_id,
                "content": f"无效的工具调用：{tool_name}",
                "is_error": True,
            })
            return

        # 循环检测（仅 INFO 工具）
        if tool_name in INFO_TOOLS and guardrails.detect_loop(
            tool_name, arguments,
        ):
            logger.warning(f"Loop detected | tool={tool_name}")
            self._fire_and_forget_knowledge(
                task_type="loop_detection", model_id=tool_name,
                status="failed",
                error_message=f"连续3次相同调用被中止: {tool_name}({arguments})",
            )
            routing_holder["_loop_abort"] = True
            return

        logger.info(
            f"agent_step | turn={turn} | tool={tool_name} | "
            f"args={arguments} | "
            f"conv={self.conversation_id} | "
            f"tokens_cum={guardrails.tokens_used}"
        )

        # 信息工具：执行 → 结果回传大脑
        if tool_name in INFO_TOOLS:
            await self._execute_info_tool(
                tool_name, arguments, tc_id, turn,
                tool_results, accumulated_context,
            )
            return

        # 路由工具：模型校验 + 记录决策 + 返回确认文本
        if tool_name in ROUTING_TOOLS:
            self._handle_routing_tool(
                tool_name, arguments, tc_id,
                tool_results, routing_holder,
            )
            return

    async def _execute_info_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tc_id: str,
        turn: int,
        tool_results: List[Dict[str, Any]],
        accumulated_context: List[str],
    ) -> None:
        """执行 INFO 工具并收集结果"""
        await self._notify_progress(turn, tool_name, "executing")
        try:
            timeout = _SLOW_TOOL_TIMEOUT.get(tool_name, 30.0)
            result = await asyncio.wait_for(
                self.executor.execute(tool_name, arguments),
                timeout=timeout,
            )
            tool_results.append({
                "tool_call_id": tc_id, "content": result,
            })
            accumulated_context.append(result)
        except asyncio.TimeoutError:
            logger.warning(
                f"Slow tool timeout | tool={tool_name} | "
                f"timeout={timeout}s"
            )
            tool_results.append({
                "tool_call_id": tc_id,
                "content": f"工具执行超时（{int(timeout)}秒），请缩小范围后重试",
                "is_error": True,
            })
        except Exception as e:
            logger.warning(
                f"Sync tool error | tool={tool_name} | error={e}"
            )
            self._fire_and_forget_knowledge(
                task_type="tool_execution", model_id=tool_name,
                status="failed",
                error_message=f"工具 {tool_name} 执行异常: {e}",
            )
            tool_results.append({
                "tool_call_id": tc_id,
                "content": f"工具执行失败: {str(e)}",
                "is_error": True,
            })

    def _handle_routing_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tc_id: str,
        tool_results: List[Dict[str, Any]],
        routing_holder: Dict[str, Any],
    ) -> None:
        """处理路由工具：模型校验 + 记录决策"""
        # route_to_chat 模型校验（图片/搜索能力匹配）
        model_warning = self._validate_routing_model(
            tool_name, arguments,
        )
        if model_warning:
            tool_results.append({
                "tool_call_id": tc_id, "content": model_warning,
            })
            return

        routing_holder["decision"] = {
            "tool_name": tool_name,
            "arguments": arguments,
        }
        # 意图学习：ask_user 时记录上下文（fire-and-forget）
        if tool_name == "ask_user":
            self._record_ask_user_context(arguments.get("message", ""))

        confirmation = self._build_routing_confirmation(
            tool_name, arguments,
        )
        tool_results.append({
            "tool_call_id": tc_id, "content": confirmation,
        })

    def _build_routing_confirmation(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> str:
        """构建路由确认文本（回传给大脑）"""
        if tool_name == "route_to_chat":
            return (
                f"已选择 {arguments.get('model', '')} 进行对话回复"
            )
        if tool_name == "route_to_image":
            count = len(arguments.get("prompts", []))
            return f"已安排生成 {count} 张图片"
        if tool_name == "route_to_video":
            return "已安排生成视频"
        if tool_name == "ask_user":
            return "将向用户发送询问"
        return "已确认"

    def _validate_routing_model(
        self, tool_name: str, arguments: Dict[str, Any],
    ) -> Optional[str]:
        """校验路由决策的模型选择，不匹配返回警告文本（大脑可重选）"""
        if tool_name != "route_to_chat":
            return None

        model_id = arguments.get("model", "")
        has_image = getattr(self, "_has_image", False)
        needs_search = arguments.get("needs_google_search", False)

        try:
            from config.smart_model_config import validate_model_choice
            warning = validate_model_choice(
                model_id, has_image=has_image, needs_search=needs_search,
            )
            if warning:
                logger.warning(
                    f"AgentLoop model mismatch | model={model_id} "
                    f"has_image={has_image} needs_search={needs_search}"
                )
                self._fire_and_forget_knowledge(
                    task_type="model_selection", model_id=model_id,
                    status="failed", error_message=warning,
                )
            return warning
        except Exception as e:
            logger.error(f"AgentLoop model validation error: {e}")
            return None

    def _fire_and_forget_knowledge(
        self, *, task_type: str, model_id: str,
        status: str, error_message: Optional[str] = None,
    ) -> None:
        """Fire-and-forget 知识记录（不阻塞主循环）"""
        try:
            from services.knowledge_extractor import extract_and_save
            asyncio.create_task(
                extract_and_save(
                    task_type=task_type, model_id=model_id,
                    status=status, error_message=error_message,
                )
            )
        except Exception as e:
            logger.debug(f"Knowledge recording skipped | error={e}")


# ============================================================
# 工具扩充工具函数（从 agent_loop_v2 迁移，Phase 2 循环使用）
# ============================================================

def get_action_enum(tool_schema: Dict[str, Any]) -> List[str]:
    """从工具 schema 中��取 action 的 enum 列表"""
    return (
        tool_schema.get("function", {})
        .get("parameters", {})
        .get("properties", {})
        .get("action", {})
        .get("enum", [])
    )


def try_expand_tools(
    tool_calls: List[Dict[str, Any]],
    current_tools: List[Dict[str, Any]],
    all_tools: List[Dict[str, Any]],
    expand_state: Dict[str, bool],
) -> Optional[List[Dict[str, Any]]]:
    """检测 AI 调了不在筛选列表的工具/action，自动补充

    Returns:
        None — 无需扩充
        List — 扩充后的工具列表（替换 current_tools）
    """
    import json as _json

    current_names = {t["function"]["name"] for t in current_tools}
    current_map = {t["function"]["name"]: t for t in current_tools}
    all_map = {t["function"]["name"]: t for t in all_tools}

    for tc in tool_calls:
        func = tc.get("function", {})
        tool_name = func.get("name", "")

        # 工具不在筛选列表 → 尝试从全量列表补充
        if tool_name not in current_names:
            if expand_state["tool_expanded"]:
                continue
            full_schema = all_map.get(tool_name)
            if full_schema:
                expand_state["tool_expanded"] = True
                logger.info(f"Tool expansion: adding {tool_name}")
                return current_tools + [full_schema]
            continue

        # action 不在筛选列表 → 尝试从全量 schema 补充
        try:
            args = _json.loads(func.get("arguments", "{}"))
        except (ValueError, TypeError):
            continue
        action = args.get("action")
        if not action:
            continue

        # 检查 action 是否在当前 enum 中
        cur_schema = current_map.get(tool_name)
        if not cur_schema:
            continue
        if action in get_action_enum(cur_schema):
            continue  # action 已在列表中
        if expand_state["action_expanded"]:
            continue

        # 从全量 schema 获取完��� enum
        full_schema = all_map.get(tool_name)
        if not full_schema:
            continue
        if action not in get_action_enum(full_schema):
            continue

        expand_state["action_expanded"] = True
        logger.info(f"Action expansion: {tool_name}.{action}")
        return [
            full_schema if t["function"]["name"] == tool_name else t
            for t in current_tools
        ]

    return None


def inject_phase1_model(
    routing_holder: Dict[str, Any], model: str,
) -> None:
    """Phase 2 出口注入 Phase 1 选定的模型"""
    decision = routing_holder.get("decision")
    if decision and not decision["arguments"].get("model"):
        decision["arguments"]["model"] = model
