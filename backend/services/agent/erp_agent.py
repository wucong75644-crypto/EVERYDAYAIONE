"""
ERP 独立 Agent — 专用提示词 + 工具循环 + 安全护栏

类型/常量/工具函数见 erp_agent_types.py
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from utils.time_context import RequestContext

from services.agent.erp_agent_types import (
    ERPAgentResult,
    filter_erp_context,
    ERP_AGENT_DEADLINE as _ERP_AGENT_DEADLINE,
    MAX_ERP_TURNS,
)


class ERPAgent:
    """ERP 独立 Agent：专用提示词 + 同义词 + 工具过滤 + 独立循环 + 安全护栏"""

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str,
        task_id: Optional[str] = None,
        request_ctx: Optional["RequestContext"] = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        # 时间事实层：请求级 SSOT。入口未传时构造一个新的（向后兼容）。
        # 设计文档：docs/document/TECH_ERP时间准确性架构.md §4 / §6.2.4
        from utils.time_context import RequestContext
        self.request_ctx = request_ctx or RequestContext.build(
            user_id=user_id, org_id=org_id, request_id=task_id or "",
        )

    async def execute(
        self,
        query: str,
        parent_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> ERPAgentResult:
        """执行 ERP 查询"""
        total_tokens = 0
        tools_called: List[str] = []

        if not self.org_id:
            return ERPAgentResult(
                text="当前账号未开通 ERP 功能，请联系管理员配置企业账号。",
                status="error",
            )

        try:
            # 1. 同义词预处理 + 工具列表构建（ToolSearch 模式）
            all_tools, selected_tools = self._prepare_tools(query)

            # 2. 构建 messages（system_prompt + 时间注入 + 知识库 + 历史 + user query）
            messages = await self._build_messages(query, parent_messages)

            # 3. 创建 adapter
            from services.adapters.factory import create_chat_adapter
            from core.config import settings

            model_id = settings.agent_loop_model
            adapter = create_chat_adapter(
                model_id, org_id=self.org_id, db=self.db,
            )

            # 4. 创建工具执行器（透传 RequestContext，供时间相关工具使用）
            from services.agent.tool_executor import ToolExecutor
            executor = ToolExecutor(
                db=self.db, user_id=self.user_id,
                conversation_id=self.conversation_id,
                org_id=self.org_id,
                request_ctx=self.request_ctx,
            )

            # 5. 独立工具循环（从父 budget fork 或独立创建）
            from services.agent.execution_budget import ExecutionBudget
            _parent_budget = getattr(self, "_budget", None)
            if _parent_budget is not None:
                budget = _parent_budget.fork(max_turns=MAX_ERP_TURNS)
            else:
                budget = ExecutionBudget(
                    max_turns=MAX_ERP_TURNS,
                    max_wall_time=_ERP_AGENT_DEADLINE,
                )
            tool_loop, hook_ctx = self._build_tool_loop(
                adapter, executor, all_tools,
            )

            # 设置 staging 分流目录（兜底：正常由 chat_handler 继承，独立运行时自己设）
            from services.agent.tool_result_envelope import set_staging_dir
            from core.workspace import resolve_staging_dir
            from core.config import get_settings as _get_settings
            _erp_settings = _get_settings()
            set_staging_dir(resolve_staging_dir(
                _erp_settings.file_workspace_root,
                self.user_id, self.org_id, self.conversation_id,
            ))

            try:
                result = await tool_loop.run(
                    messages=messages,
                    selected_tools=selected_tools,
                    tools_called=tools_called,
                    hook_ctx=hook_ctx,
                    budget=budget,
                )
                text = result.text
                turns = result.turns
                total_tokens += result.total_tokens
            finally:
                await adapter.close()
                # 注意：ERPAgent 不 clear staging_dir，不清理 staging 文件
                # 清理权归最外层（chat_handler / scheduled_task_agent）

            # 判断是否被截断（text 中包含分流信号）
            from services.agent.tool_result_envelope import STAGED_MARKER
            is_truncated = STAGED_MARKER in (text or "")
            # 判断 status
            status = "success"
            if "未能生成完整结论" in text:
                status = "partial"
            elif is_truncated:
                status = "partial"

            # [F1/F2] 经验记录：成功记路由，失败记原因
            if status == "success" and tools_called:
                asyncio.create_task(self._record_agent_experience(
                    "routing", query, tools_called,
                    f"轮次：{turns}", budget, confidence=0.6,
                ))
            elif tools_called:
                asyncio.create_task(self._record_agent_experience(
                    "failure", query, tools_called,
                    f"失败原因：{text[:200]}", budget,
                ))

            return ERPAgentResult(
                text=text,
                full_text=text,
                status=status,
                tokens_used=total_tokens,
                turns_used=turns,
                tools_called=tools_called,
                is_truncated=is_truncated,
            )

        except Exception as e:
            logger.error(f"ERPAgent error | query={query[:50]} | error={e}")
            # [F2] 异常退出也记录失败记忆
            if tools_called:
                asyncio.create_task(self._record_agent_experience(
                    "failure", query, tools_called,
                    f"异常：{str(e)[:200]}", budget,
                ))
            return ERPAgentResult(
                text=f"ERP 查询出错：{e}。请稍后重试或换个方式提问。",
                full_text=str(e),
                status="error",
                tokens_used=total_tokens,
                tools_called=tools_called,
            )

    def _build_tool_loop(
        self,
        adapter: Any,
        executor: Any,
        all_tools: List[Dict[str, Any]],
    ) -> tuple:
        """装配 ToolLoopExecutor + HookContext（ERP 默认配置）

        返回 (tool_loop, hook_ctx)。所有 ERP 行为差异在这里集中表达：
        - LoopConfig：MAX_ERP_TURNS / MAX_TOTAL_TOKENS / TOOL_TIMEOUT
        - LoopStrategy：route_to_chat/ask_user 退出 + 工具自动扩展 + 强制先用工具
        - Hooks：进度推送 + 审计 + L4 时间校验 + 失败反思
        """
        from services.agent.tool_loop_executor import ToolLoopExecutor
        from services.agent.loop_types import (
            HookContext, LoopConfig, LoopStrategy,
        )
        from services.agent.loop_hooks import (
            AmbiguityDetectionHook,
            FailureReflectionHook,
            ProgressNotifyHook,
            TemporalValidatorHook,
            ToolAuditHook,
        )
        from services.agent.erp_agent_types import (
            MAX_ERP_TURNS, MAX_TOTAL_TOKENS, TOOL_TIMEOUT,
        )

        hook_ctx = HookContext(
            db=self.db,
            user_id=self.user_id,
            org_id=self.org_id,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            request_ctx=self.request_ctx,
        )

        tool_loop = ToolLoopExecutor(
            adapter=adapter,
            executor=executor,
            all_tools=all_tools,
            config=LoopConfig(
                max_turns=MAX_ERP_TURNS,
                max_tokens=MAX_TOTAL_TOKENS,
                tool_timeout=TOOL_TIMEOUT,
                no_synthesis_fallback_text=(
                    "ERP 查询过程中未能生成完整结论，"
                    "请重新提问或缩小查询范围。"
                ),
            ),
            strategy=LoopStrategy(
                exit_signals=frozenset({"route_to_chat", "ask_user"}),
                enable_tool_expansion=True,
            ),
            hooks=[
                ProgressNotifyHook(max_turns=MAX_ERP_TURNS),
                ToolAuditHook(),
                TemporalValidatorHook(),
                FailureReflectionHook(),
                AmbiguityDetectionHook(),
            ],
        )
        return tool_loop, hook_ctx

    def _prepare_tools(
        self, query: str,
    ) -> tuple:
        """同义词预处理 + ERP 工具列表构建（ToolSearch 模式）。

        返回 (all_tools, selected_tools)：
        - all_tools：本地 + 远程 ERP 全量工具（用于 ToolLoopExecutor 自动扩展）
        - selected_tools：初始可见集（本地工具 + 6 个固定核心工具）
        """
        from config.tool_registry import expand_synonyms
        from config.phase_tools import build_domain_tools

        expanded = expand_synonyms(query)
        logger.info(
            f"ERPAgent synonyms | query={query[:50]} | "
            f"expanded={sorted(expanded)[:5]}"
        )

        all_tools = build_domain_tools("erp")

        # 本地工具始终可见（毫秒级精确查询）
        # 远程 erp_* 工具隐藏（通过 erp_api_search 按需发现 + 自动扩展注入）
        _VISIBLE_PREFIXES = ("local_",)
        _VISIBLE_NAMES = {"erp_api_search", "code_execute",
                          "fetch_all_pages",
                          "trigger_erp_sync", "route_to_chat", "ask_user"}
        selected_tools = [
            t for t in all_tools
            if t["function"]["name"].startswith(_VISIBLE_PREFIXES)
            or t["function"]["name"] in _VISIBLE_NAMES
        ]
        logger.info(
            f"ERPAgent tools | visible={len(selected_tools)} | "
            f"hidden={len(all_tools) - len(selected_tools)} | "
            f"names={[t['function']['name'] for t in selected_tools]}"
        )
        return all_tools, selected_tools

    async def _build_messages(
        self,
        query: str,
        parent_messages: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """组装 ERPAgent 的 messages：system_prompt + 时间注入 + 知识库 + 历史 + user query"""
        from config.phase_tools import build_domain_prompt
        system_prompt = build_domain_prompt("erp")

        # 时间事实层 — 用 RequestContext 注入结构化的"今天"
        # 替代旧的 _time.strftime + _time.localtime（依赖 OS 时区，且只输出英文星期）
        # 设计文档：docs/document/TECH_ERP时间准确性架构.md §4 / §6.2.1
        time_injection = self.request_ctx.for_prompt_injection()

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": time_injection},
        ]

        # [Fix C] 独立获取知识库经验（和旧架构一样）
        knowledge_items = await self._fetch_knowledge(query)
        if knowledge_items:
            knowledge_text = "\n".join(
                f"- {k['title']}: {k['content']}" for k in knowledge_items
            )
            messages.append({
                "role": "system",
                "content": f"你已掌握的经验知识：\n{knowledge_text}",
            })

        # 注入筛选后的对话历史
        if parent_messages:
            context = filter_erp_context(parent_messages)
            if len(context) > 10:
                context = context[-10:]
            messages.extend(context)

        messages.append({"role": "user", "content": query})
        return messages

    async def _fetch_knowledge(self, query: str) -> Optional[list]:
        """[Fix C] 独立获取知识库经验（ERP 专业经验）

        min_confidence=0.6 屏蔽未被命中验证过的初始自动经验（routing/failure 起步
        confidence=0.5/0.6），形成"必须被命中至少一次才能反哺自己"的闸门，
        防止 ERPAgent 自学习的 garbage-in/garbage-out 飞轮污染。
        """
        try:
            from services.knowledge_service import search_relevant
            return await search_relevant(
                query=query, limit=3, min_confidence=0.6, org_id=self.org_id,
            )
        except Exception as e:
            logger.debug(f"ERPAgent knowledge fetch skipped | error={e}")
            return None

    # ── F1/F2 经验积累参数（per-node_type 独立配额）──
    # routing: 成功路由路径，写入频繁 → 给 400 配额
    # failure: 失败教训，写入稀缺 → 给 200 配额（rare 边界 case 更值得保留）
    _ROUTING_PATTERN_MAX = 400
    _FAILURE_PATTERN_MAX = 200

    # 业务域白名单：从 tool_name 推断 subcategory，便于后续按域聚合分析
    # 收敛命名空间避免 stock/inventory/库存 三种写法散落
    _BUSINESS_DOMAINS = frozenset({
        "stock", "order", "product", "purchase",
        "aftersale", "warehouse", "info", "general",
    })

    @classmethod
    def _infer_business_domain(cls, tools_called: List[str]) -> str:
        """从 tools_called 列表推断业务域，作为知识节点的 subcategory。

        命名规范：local_*_query / erp_*_query / local_*_identify / local_*_stats
        匹配规则：取首个能识别的工具，提取中间业务名 → 归一化 → 白名单匹配。
        无匹配返回 'general'，确保任何工具列表都能落入合法 subcategory。
        """
        import re

        # 同义归一（保持白名单内的术语一致性）
        normalize = {
            "aftersales": "aftersale",
            "trade": "order",
            "inventory": "stock",
            "basic": "info",
        }
        pattern = re.compile(
            r"^(?:local_|erp_)([a-z]+?)(?:_query|_identify|_stats|_flow)?$"
        )
        for tool in tools_called:
            m = pattern.match(tool)
            if not m:
                continue
            domain = normalize.get(m.group(1), m.group(1))
            if domain in cls._BUSINESS_DOMAINS:
                return domain
        return "general"

    async def _record_agent_experience(
        self, record_type: str, query: str, tools_called: List[str],
        detail: str, budget: Optional[Any] = None,
        confidence: float = 0.5,
    ) -> None:
        """[F1/F2] 记录路由经验或失败记忆到知识库。

        Args:
            record_type: "routing" (成功) 或 "failure" (失败)
                内部映射到 node_type=routing_pattern / failure_pattern。
                category 固定 "experience"，subcategory 从 tools_called 推断。
        """
        try:
            from services.knowledge_service import add_knowledge

            if record_type == "routing":
                node_type = "routing_pattern"
                max_count = self._ROUTING_PATTERN_MAX
                prefix = "查询路由"
            elif record_type == "failure":
                node_type = "failure_pattern"
                max_count = self._FAILURE_PATTERN_MAX
                prefix = "查询失败"
            else:
                logger.error(
                    f"ERPAgent _record_agent_experience: unknown "
                    f"record_type={record_type!r} (must be routing/failure)"
                )
                return

            elapsed = f"{budget.elapsed:.1f}s" if budget else "N/A"
            unique_tools = list(dict.fromkeys(tools_called))
            domain = self._infer_business_domain(unique_tools)

            await add_knowledge(
                category="experience",
                subcategory=domain,
                node_type=node_type,
                title=f"{prefix}：{query[:30]}",
                content=(
                    f"查询：{query}\n"
                    f"路径：{' → '.join(unique_tools)}\n"
                    f"{detail}\n耗时：{elapsed}"
                ),
                # source 必须在 PG CHECK 白名单内（auto/seed/manual/aggregated）。
                # ERPAgent 经验属于自动写入，用 "auto"；
                # 通过 metadata.writer 区分"哪个 Agent 写的"，便于运维聚合。
                metadata={
                    "writer": "erp_agent",
                    "record_type": record_type,
                    "tools": unique_tools,
                },
                source="auto",
                confidence=confidence,
                scope="org",
                org_id=self.org_id,
                max_per_node_type=max_count,
            )
        except ValueError as e:
            # schema 违反必须显眼报告（不应发生 — 内部 hardcoded 合法值）
            logger.error(
                f"ERPAgent {record_type} experience schema violation | "
                f"error={e}"
            )
        except Exception as e:
            logger.debug(
                f"ERPAgent {record_type} experience save failed | error={e}"
            )

    async def _cleanup_staging_delayed(self, delay: int = 300) -> None:
        """会话级 staging 延迟清理：等待后删除本次会话的 staging 文件

        延迟 5 分钟，避免 Agent 超时退出但 code_execute 还没读到 staging 文件。
        """
        import shutil
        from pathlib import Path
        from core.config import get_settings

        try:
            await asyncio.sleep(delay)
            settings = get_settings()
            from core.workspace import resolve_staging_dir
            staging_dir = Path(resolve_staging_dir(
                settings.file_workspace_root,
                self.user_id, self.org_id, self.conversation_id,
            ))
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
                logger.info(
                    f"ERPAgent staging cleaned | dir={staging_dir}"
                )
        except Exception as e:
            logger.debug(f"ERPAgent staging cleanup failed | error={e}")
