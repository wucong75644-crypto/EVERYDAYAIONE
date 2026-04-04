"""
ERP 独立 Agent

将 ERP 从"工具"升级为"独立 Agent"，封装旧架构 Phase2 的精华：
- 专用系统提示词（ERP_ROUTING_PROMPT）
- 同义词预处理（expand_synonyms）
- 工具预过滤（3 级选择算法）
- 独立工具循环（max 5 轮）
- WebSocket 进度推送

被 ChatHandler 作为一个工具调用，内部独立运行，返回纯文本结论。
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ============================================================
# ERP Agent 结果
# ============================================================

@dataclass
class ERPAgentResult:
    """ERP Agent 执行结果"""
    text: str                       # 结论文本（给主 Agent 的精简版）
    full_text: str = ""             # 完整文本（给用户的详细版）
    tokens_used: int = 0            # 消耗的总 tokens
    turns_used: int = 0             # 内部轮次数
    tools_called: List[str] = field(default_factory=list)  # 调用过的工具名


# ============================================================
# 上下文筛选
# ============================================================

def filter_erp_context(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从主 Agent 的 messages 中筛选 ERP 相关上下文

    筛选规则：
    - user 消息：全部保留（用户说的每句话都可能有 ERP 上下文）
    - assistant + erp_agent 工具调用：保留
    - assistant 其他工具调用（generate_image 等）：跳过
    - tool 结果：保留（简化处理）
    - system 消息：跳过（ERP Agent 有自己的系统提示词）
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        if role == "user":
            result.append(msg)
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                result.append(msg)  # 纯文字回复保留
            elif any(
                tc.get("function", {}).get("name") == "erp_agent"
                for tc in tool_calls
            ):
                result.append(msg)  # ERP 相关保留
        elif role == "tool":
            result.append(msg)
    return result


# ============================================================
# ERP Agent 核心
# ============================================================

MAX_ERP_TURNS = 5


class ERPAgent:
    """ERP 独立 Agent：专用提示词 + 同义词 + 工具过滤 + 独立循环"""

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str,
        task_id: Optional[str] = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id

    async def execute(
        self,
        query: str,
        parent_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> ERPAgentResult:
        """执行 ERP 查询

        Args:
            query: 用户原始问题
            parent_messages: 主 Agent 的 messages（用于上下文筛选）

        Returns:
            ERPAgentResult 包含结论文本和 token 消耗
        """
        total_tokens = 0
        tools_called: List[str] = []

        # 散客保护：无 org_id 不能使用 ERP 功能
        if not self.org_id:
            return ERPAgentResult(
                text="当前账号未开通 ERP 功能，请联系管理员配置企业账号。",
            )

        try:
            # 1. 同义词预处理
            from config.tool_registry import expand_synonyms
            expanded = expand_synonyms(query)
            logger.info(
                f"ERPAgent synonyms | query={query[:50]} | "
                f"expanded={sorted(expanded)[:5]}"
            )

            # 2. 构建 ERP 工具列表 + 预过滤
            from config.phase_tools import build_domain_tools
            from services.tool_selector import select_and_filter_tools

            all_tools = build_domain_tools("erp")
            selected_tools = await select_and_filter_tools(
                "erp", query, all_tools,
            )
            # 过滤掉 ERP Agent 内不需要的 always_include 工具
            # 只保留 route_to_chat / ask_user 作为退出工具
            _ERP_EXCLUDE = {"code_execute", "erp_api_search",
                            "search_knowledge", "get_conversation_context"}
            selected_tools = [
                t for t in selected_tools
                if t["function"]["name"] not in _ERP_EXCLUDE
            ]
            logger.info(
                f"ERPAgent tools selected | "
                f"total={len(all_tools)} | selected={len(selected_tools)} | "
                f"names={[t['function']['name'] for t in selected_tools[:5]]}"
            )

            # 3. 构建 messages（ERP 专用提示词 + 筛选后的上下文）
            from config.phase_tools import build_domain_prompt
            system_prompt = build_domain_prompt("erp")

            import time as _time
            now_str = _time.strftime("%Y-%m-%d %H:%M %A", _time.localtime())

            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"当前时间：{now_str}"},
            ]

            # 注入筛选后的对话历史
            if parent_messages:
                context = filter_erp_context(parent_messages)
                # 限制历史长度（最近 10 条消息）
                if len(context) > 10:
                    context = context[-10:]
                messages.extend(context)

            messages.append({"role": "user", "content": query})

            # 4. 创建 adapter（支持企业 BYOK）
            from services.adapters.factory import create_chat_adapter
            from core.config import settings

            model_id = settings.agent_loop_model
            adapter = create_chat_adapter(
                model_id, org_id=self.org_id, db=self.db,
            )

            # 5. 创建工具执行器
            from services.tool_executor import ToolExecutor
            executor = ToolExecutor(
                db=self.db, user_id=self.user_id,
                conversation_id=self.conversation_id,
                org_id=self.org_id,
            )

            # 6. 独立工具循环
            try:
                text, tokens, turns = await self._run_tool_loop(
                    adapter, executor, messages, selected_tools, tools_called,
                )
                total_tokens += tokens
            finally:
                await adapter.close()

            # 7. 构建结果
            return ERPAgentResult(
                text=self._make_summary(text),
                full_text=text,
                tokens_used=total_tokens,
                turns_used=turns,
                tools_called=tools_called,
            )

        except Exception as e:
            logger.error(f"ERPAgent error | query={query[:50]} | error={e}")
            return ERPAgentResult(
                text=f"ERP 查询出错：{e}。请稍后重试或换个方式提问。",
                full_text=str(e),
                tokens_used=total_tokens,
                turns_used=0,
                tools_called=tools_called,
            )

    async def _run_tool_loop(
        self,
        adapter: Any,
        executor: Any,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
    ) -> tuple:
        """内部工具循环：调 LLM → 执行工具 → 结果回 LLM → 重复

        Returns:
            (accumulated_text, total_tokens, turns_used)
        """
        accumulated_text = ""
        total_tokens = 0

        for turn in range(MAX_ERP_TURNS):
            await self._notify_progress(turn + 1, "thinking")

            # 流式调 LLM
            tc_acc: Dict[int, Dict[str, Any]] = {}
            turn_text = ""
            turn_tokens = 0

            async for chunk in adapter.stream_chat(
                messages=messages, tools=selected_tools,
            ):
                if chunk.content:
                    turn_text += chunk.content
                if chunk.tool_calls:
                    for tc_delta in chunk.tool_calls:
                        idx = tc_delta.index
                        if idx not in tc_acc:
                            tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        entry = tc_acc[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.name:
                            entry["name"] = tc_delta.name
                        if tc_delta.arguments_delta:
                            entry["arguments"] += tc_delta.arguments_delta
                if chunk.prompt_tokens or chunk.completion_tokens:
                    turn_tokens = (chunk.prompt_tokens or 0) + (chunk.completion_tokens or 0)

            total_tokens += turn_tokens

            if not tc_acc:
                accumulated_text = turn_text
                break

            # 执行工具调用
            completed = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))
            accumulated_text = await self._execute_tools(
                completed, executor, messages, selected_tools,
                tools_called, turn_text, turn + 1,
            )

            # 检查是否已退出
            if any(tc["name"] in ("route_to_chat", "ask_user") for tc in completed):
                break

            logger.info(f"ERPAgent turn {turn + 1} | tools={[tc['name'] for tc in completed]}")

        return accumulated_text, total_tokens, min(turn + 1, MAX_ERP_TURNS)

    async def _execute_tools(
        self,
        completed: List[Dict[str, Any]],
        executor: Any,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
        turn_text: str,
        turn: int,
    ) -> str:
        """执行一轮工具调用，返回累积文本"""
        # assistant 消息塞进 messages
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
        asst_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in completed
        ]
        messages.append(asst_msg)

        accumulated = turn_text
        for tc in completed:
            tool_name = tc["name"]
            tools_called.append(tool_name)

            # 路由工具：退出循环
            if tool_name in ("route_to_chat", "ask_user"):
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                if tool_name == "route_to_chat":
                    accumulated = turn_text or args.get("system_prompt", "")
                else:
                    accumulated = args.get("message", turn_text)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "OK"})
                break

            await self._notify_progress(turn, tool_name)

            # 解析参数并执行
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            try:
                result = await executor.execute(tool_name, args)
            except Exception as e:
                logger.error(f"ERPAgent tool error | tool={tool_name} | error={e}")
                result = f"工具执行失败: {e}"

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            accumulated = result

            # 自动扩展：AI 需要不在列表中的工具
            current = {t["function"]["name"] for t in selected_tools}
            if tool_name not in current and tool_name not in ("route_to_chat", "ask_user"):
                from config.chat_tools import get_tools_by_names
                extra = get_tools_by_names({tool_name}, org_id=self.org_id)
                selected_tools.extend(extra)
                logger.info(f"ERPAgent tool expansion | added={tool_name}")

        return accumulated

    def _make_summary(self, full_text: str, max_chars: int = 500) -> str:
        """将完整结果压缩为精简结论（给 messages 的版本）"""
        if not full_text or len(full_text) <= max_chars:
            return full_text
        return (
            full_text[:max_chars]
            + f"\n\n（以上为摘要，共{len(full_text)}字符）"
        )

    async def _notify_progress(self, turn: int, tool_name: str) -> None:
        """通过 WebSocket 发送进度通知"""
        if not self.task_id:
            return
        try:
            from schemas.websocket import build_agent_step
            from services.websocket_manager import ws_manager
            msg = build_agent_step(
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                status="running",
                turn=turn,
                task_id=self.task_id,
            )
            await ws_manager.send_to_task_subscribers(self.task_id, msg)
        except Exception:
            pass  # 进度通知失败不影响主流程
