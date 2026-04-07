"""
ERP 独立 Agent — 专用提示词 + 工具循环 + 安全护栏

类型/常量/工具函数见 erp_agent_types.py
"""

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from services.agent.erp_agent_types import (
    ERPAgentResult,
    filter_erp_context,
    TOOL_TIMEOUT as _TOOL_TIMEOUT,
    MAX_TOTAL_TOKENS as _MAX_TOTAL_TOKENS,
    ERP_AGENT_DEADLINE as _ERP_AGENT_DEADLINE,
    MAX_ERP_TURNS,
    is_context_length_error as _is_context_length_error,
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
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        # [B4] 会话级读工具缓存（key=tool_name+args_hash → result, TTL 5分钟）
        self._query_cache: Dict[str, tuple] = {}  # {cache_key: (result, timestamp)}

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
            # 1. 同义词预处理
            from config.tool_registry import expand_synonyms
            expanded = expand_synonyms(query)
            logger.info(
                f"ERPAgent synonyms | query={query[:50]} | "
                f"expanded={sorted(expanded)[:5]}"
            )

            # 2. 构建工具列表（ToolSearch 模式：本地可见 + 远程按需发现）
            from config.phase_tools import build_domain_tools

            all_tools = build_domain_tools("erp")
            self._all_tools = all_tools  # 保存全量，供自动扩展用

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

            # 3. 构建 messages
            from config.phase_tools import build_domain_prompt
            system_prompt = build_domain_prompt("erp")

            import time as _time
            now_str = _time.strftime("%Y-%m-%d %H:%M %A", _time.localtime())

            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"当前时间：{now_str}"},
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

            # 4. 创建 adapter
            from services.adapters.factory import create_chat_adapter
            from core.config import settings

            model_id = settings.agent_loop_model
            adapter = create_chat_adapter(
                model_id, org_id=self.org_id, db=self.db,
            )

            # 5. 创建工具执行器
            from services.agent.tool_executor import ToolExecutor
            executor = ToolExecutor(
                db=self.db, user_id=self.user_id,
                conversation_id=self.conversation_id,
                org_id=self.org_id,
            )

            # 6. 独立工具循环（带全局时间预算）
            from services.agent.execution_budget import ExecutionBudget
            budget = ExecutionBudget(_ERP_AGENT_DEADLINE)

            try:
                text, tokens, turns = await self._run_tool_loop(
                    adapter, executor, messages, selected_tools, tools_called,
                    budget=budget,
                )
                total_tokens += tokens
            finally:
                await adapter.close()
                # 会话级 staging 延迟清理（5分钟后删除，防止 Agent 还没调 code_execute 就被清了）
                asyncio.create_task(self._cleanup_staging_delayed())

            # 判断是否被截断（text 中包含截断信号）
            is_truncated = "⚠ 输出已截断" in text
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

    async def _fetch_knowledge(self, query: str) -> Optional[list]:
        """[Fix C] 独立获取知识库经验（ERP 专业经验）"""
        try:
            from services.knowledge_service import search_relevant
            return await search_relevant(query=query, limit=3, org_id=self.org_id)
        except Exception as e:
            logger.debug(f"ERPAgent knowledge fetch skipped | error={e}")
            return None

    async def _run_tool_loop(
        self,
        adapter: Any,
        executor: Any,
        messages: List[Dict[str, Any]],
        selected_tools: List[Dict[str, Any]],
        tools_called: List[str],
        budget: Any = None,
    ) -> tuple:
        """内部工具循环，含安全护栏"""
        accumulated_text = ""
        total_tokens = 0
        is_llm_synthesis = False  # 标记最终结果是否为 LLM 合成
        empty_turns = 0  # 连续空响应计数
        # [Fix G] 循环检测
        recent_calls: List[str] = []
        # [B6] 上下文恢复最多尝试 1 次，防止反复压缩浪费 API 调用
        context_recovery_used = False

        # [B2/B6] 提前导入压缩函数（避免循环内重复 import 语句）
        from services.handlers.context_compressor import estimate_tokens, enforce_budget

        for turn in range(MAX_ERP_TURNS):
            # [B3] 全局时间预算检查
            if budget and not budget.check_or_log(f"turn={turn + 1}"):
                break

            # [Fix H] Token 预算检查
            if total_tokens >= _MAX_TOTAL_TOKENS:
                logger.warning(
                    f"ERPAgent token budget exceeded | used={total_tokens}"
                )
                break

            # [B2] 上下文压缩：超 70% token 预算时主动压缩 messages
            estimated = estimate_tokens(messages)
            budget_70 = int(_MAX_TOTAL_TOKENS * 0.7)
            if estimated > budget_70:
                logger.info(
                    f"ERPAgent context compress | tokens={estimated} | "
                    f"budget_70={budget_70}"
                )
                enforce_budget(messages, budget_70)

            # [E1+E2] 推送进度（含已完成工具列表 + 耗时 + 预估）
            _estimated = len(selected_tools) * 3 if turn == 0 else None  # 首轮粗估
            await self._notify_progress(
                turn + 1, "thinking",
                tools_called=tools_called, budget=budget,
                estimated_s=_estimated,
            )

            tc_acc: Dict[int, Dict[str, Any]] = {}
            turn_text = ""
            turn_tokens = 0

            # [Fix I] 显式传 temperature=0.1
            # [B6] 上下文恢复：捕获上下文超限异常 → 压缩 → 重试一次
            try:
                async for chunk in adapter.stream_chat(
                    messages=messages, tools=selected_tools,
                    temperature=0.1,
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
            except Exception as stream_err:
                if _is_context_length_error(stream_err) and not context_recovery_used:
                    context_recovery_used = True
                    logger.warning(
                        f"ERPAgent context_length_exceeded | turn={turn + 1} | "
                        f"attempting recovery (one-shot)"
                    )
                    enforce_budget(messages, int(_MAX_TOTAL_TOKENS * 0.5))
                    messages.append({
                        "role": "user",
                        "content": "上下文过长已自动压缩。请直接继续当前任务，不要重复已完成的步骤。",
                    })
                    continue  # 重试当前轮（仅此一次）
                raise  # 非上下文错误 或 已恢复过一次，正常冒泡

            total_tokens += turn_tokens

            if not tc_acc:
                if not tools_called:
                    # 还没调过任何工具就想输出文字 — 强制继续循环
                    empty_turns += 1
                    logger.info(
                        f"ERPAgent skip empty turn #{empty_turns} | "
                        f"text={turn_text[:50] if turn_text else '(empty)'}"
                    )
                    if empty_turns >= 2:
                        # 连续 2 次空响应，有文字则作为最终输出
                        if turn_text:
                            accumulated_text = turn_text
                            is_llm_synthesis = True
                        break
                    if turn_text:
                        messages.append({"role": "assistant", "content": turn_text})
                    continue
                # 调过工具后输出纯文字 — 这是干净的合成结果
                accumulated_text = turn_text
                is_llm_synthesis = True
                break

            completed = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))

            # [Fix G] 循环检测：连续 3 次相同调用中止
            call_key = "|".join(
                f"{tc['name']}:{hashlib.md5(tc['arguments'].encode()).hexdigest()[:6]}"
                for tc in completed
            )
            recent_calls.append(call_key)
            if len(recent_calls) >= 3 and len(set(recent_calls[-3:])) == 1:
                logger.warning(f"ERPAgent loop detected | call={call_key}")
                break

            accumulated_text = await self._execute_tools(
                completed, executor, messages, selected_tools,
                tools_called, turn_text, turn + 1, budget=budget,
            )

            # route_to_chat / ask_user 是退出信号
            if any(tc["name"] in ("route_to_chat", "ask_user") for tc in completed):
                # ask_user 的结论在 accumulated（args.message），不依赖 turn_text
                has_ask_user = any(tc["name"] == "ask_user" for tc in completed)
                is_llm_synthesis = has_ask_user or bool(turn_text)
                break

            logger.info(f"ERPAgent turn {turn + 1} | tools={[tc['name'] for tc in completed]}")

        # 非正常退出（token超限/循环检测/max turns）且结果不是 LLM 合成的
        if not is_llm_synthesis:
            logger.warning(
                f"ERPAgent exited without synthesis | "
                f"raw_len={len(accumulated_text)} | turns={turn + 1}"
            )
            # 不尝试再调 LLM（上下文过长可能产出错误结论），
            # 返回明确提示让主 Agent 告知用户
            accumulated_text = "ERP 查询过程中未能生成完整结论，请重新提问或缩小查询范围。"

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
        budget: Any = None,
    ) -> str:
        """执行一轮工具调用"""
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

            if tool_name in ("route_to_chat", "ask_user"):
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}
                if tool_name == "route_to_chat":
                    # 优先用 LLM 本轮合成的文字，而不是 system_prompt（可能是原始数据）
                    accumulated = turn_text if turn_text else ""
                else:
                    accumulated = args.get("message", turn_text)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "OK"})
                break

            await self._notify_progress(turn, tool_name, tools_called=tools_called, budget=budget)

            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError as e:
                logger.warning(f"ERPAgent bad JSON | tool={tool_name} | error={e}")
                result = f"工具参数JSON格式错误: {e}，请检查参数格式"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                accumulated = result
                continue

            # [B4] 读工具缓存命中检查
            import time as _audit_time
            _audit_start = _audit_time.monotonic()
            _is_cached = False
            _audit_status = "success"

            cached = self._cache_get(tool_name, args)
            if cached is not None:
                logger.info(f"ERPAgent cache hit | tool={tool_name}")
                result = cached
                _is_cached = True
            else:
                # [Fix F + B3] 超时控制（动态：min(单工具上限, 剩余预算)）
                tool_timeout = (
                    budget.tool_timeout(_TOOL_TIMEOUT) if budget
                    else _TOOL_TIMEOUT
                )
                try:
                    result = await asyncio.wait_for(
                        executor.execute(tool_name, args),
                        timeout=tool_timeout,
                    )
                    # [B4] 只缓存读工具的成功结果
                    self._cache_put(tool_name, args, result)
                except asyncio.TimeoutError:
                    logger.warning(f"ERPAgent tool timeout | tool={tool_name} | timeout={tool_timeout:.1f}s")
                    result = f"工具执行超时（{int(tool_timeout)}秒），请缩小查询范围"
                    _audit_status = "timeout"
                except Exception as e:
                    logger.error(f"ERPAgent tool error | tool={tool_name} | error={e}")
                    result = f"工具执行失败: {e}"
                    _audit_status = "error"

            _audit_elapsed = int((_audit_time.monotonic() - _audit_start) * 1000)

            # ERP Agent 内部截断+信号（防止单条结果撑爆上下文）
            from services.agent.tool_result_envelope import wrap_for_erp_agent
            _is_truncated = len(result) > 3000 if result else False
            result = wrap_for_erp_agent(tool_name, result)

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            accumulated = result

            # [C1] 审计日志（fire-and-forget）
            self._emit_tool_audit(
                tool_name, tc["id"], turn, args, len(result),
                _audit_elapsed, _audit_status, _is_cached, _is_truncated,
            )

            # [A2] 失败反思：工具返回错误时，注入 system message 引导模型分析原因
            # 只匹配工具错误框架生成的固定前缀，不匹配业务数据中的"错误"/"失败"
            _error_prefixes = (
                "工具执行失败:", "工具执行超时", "工具参数JSON格式错误:",
                "❌", "Traceback",
            )
            if result.startswith(_error_prefixes) or "Error:" in result[:100]:
                messages.append({
                    "role": "system",
                    "content": (
                        f"工具 {tool_name} 返回了错误。请分析原因后选择："
                        f"1) 换参数重试 2) 换工具 3) 用 ask_user 向用户确认"
                    ),
                })

            # 自动扩展：千问调了隐藏的远程工具 → 从全量列表动态注入（去重）
            current = {t["function"]["name"] for t in selected_tools}
            if tool_name not in current and tool_name not in ("route_to_chat", "ask_user"):
                all_map = {t["function"]["name"]: t for t in self._all_tools}
                if tool_name in all_map:
                    selected_tools.append(all_map[tool_name])
                    logger.info(f"ERPAgent tool injected | {tool_name}")
                    current.add(tool_name)  # 防止多轮重复注入
                else:
                    # 不在 ERP 全量列表中（可能是其他域工具），尝试从 chat_tools 获取
                    from config.chat_tools import get_tools_by_names
                    extra = get_tools_by_names({tool_name}, org_id=self.org_id)
                    selected_tools.extend(extra)
                logger.info(f"ERPAgent tool expansion | added={tool_name}")

        return accumulated

    async def _notify_progress(
        self, turn: int, tool_name: str,
        tools_called: Optional[List[str]] = None,
        budget: Optional[Any] = None,
        estimated_s: Optional[int] = None,
    ) -> None:
        """通过 WebSocket 发送进度通知（含进度比例/耗时/已完成工具）"""
        if not self.task_id:
            return
        try:
            from schemas.websocket import build_agent_step
            from services.task_stream import publish as stream_publish
            msg = build_agent_step(
                conversation_id=self.conversation_id,
                tool_name=tool_name,
                status="running",
                turn=turn,
                task_id=self.task_id,
                max_turns=MAX_ERP_TURNS,
                elapsed_s=budget.elapsed if budget else None,
                tools_completed=list(dict.fromkeys(tools_called)) if tools_called else None,
                estimated_s=estimated_s,
            )
            await stream_publish(self.task_id, self.user_id, msg)
        except Exception as e:
            logger.debug(f"ERPAgent progress notify failed | turn={turn} | error={e}")

    _CACHE_TTL = 300.0  # 5 分钟
    _CACHE_MAX_ENTRIES = 50  # 最多缓存 50 条，防止内存膨胀
    _CACHE_MAX_VALUE_CHARS = 8000  # 单条结果超过此大小不缓存

    # 只缓存读工具（从 chat_tools 的 _CONCURRENT_SAFE_TOOLS 判断）
    @staticmethod
    def _is_cacheable(tool_name: str) -> bool:
        from config.chat_tools import is_concurrency_safe
        return is_concurrency_safe(tool_name)

    def _cache_key(self, tool_name: str, args: Dict[str, Any]) -> str:
        sorted_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return f"{tool_name}:{hashlib.md5(sorted_args.encode()).hexdigest()}"

    def _cache_get(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        if not self._is_cacheable(tool_name):
            return None
        import time as _time
        key = self._cache_key(tool_name, args)
        entry = self._query_cache.get(key)
        if entry is None:
            return None
        if (_time.monotonic() - entry[1]) < self._CACHE_TTL:
            return entry[0]
        # 过期条目删除，释放空间
        del self._query_cache[key]
        return None

    def _emit_tool_audit(
        self, tool_name: str, tool_call_id: str, turn: int,
        args: Dict[str, Any], result_length: int, elapsed_ms: int,
        status: str, is_cached: bool = False, is_truncated: bool = False,
    ) -> None:
        """[C1] fire-and-forget 审计日志"""
        from services.agent.tool_audit import (
            ToolAuditEntry, build_args_hash, record_tool_audit,
        )
        asyncio.create_task(record_tool_audit(self.db, ToolAuditEntry(
            task_id=self.task_id or "", conversation_id=self.conversation_id,
            user_id=self.user_id, org_id=self.org_id or "",
            tool_name=tool_name, tool_call_id=tool_call_id,
            turn=turn, args_hash=build_args_hash(args),
            result_length=result_length, elapsed_ms=elapsed_ms,
            status=status, is_cached=is_cached, is_truncated=is_truncated,
        )))

    def _cache_put(self, tool_name: str, args: Dict[str, Any], result: str) -> None:
        if not self._is_cacheable(tool_name):
            return
        # 大结果不缓存，防止内存膨胀
        if len(result) > self._CACHE_MAX_VALUE_CHARS:
            return
        # 条目上限，满了跳过（简单策略，单次请求内缓存不会太多）
        if len(self._query_cache) >= self._CACHE_MAX_ENTRIES:
            return
        import time as _time
        key = self._cache_key(tool_name, args)
        self._query_cache[key] = (result, _time.monotonic())

    _EXPERIENCE_MAX_PER_CATEGORY = 500  # 每个 category 最多保留 500 条，淘汰 confidence 最低的

    async def _record_agent_experience(
        self, category: str, query: str, tools_called: List[str],
        detail: str, budget: Optional[Any] = None,
        confidence: float = 0.5,
    ) -> None:
        """[F1/F2] 记录路由经验或失败记忆到知识库（通用方法，含 per-category 淘汰）"""
        try:
            from services.knowledge_service import add_knowledge
            elapsed = f"{budget.elapsed:.1f}s" if budget else "N/A"
            unique_tools = list(dict.fromkeys(tools_called))
            prefix = "查询路由" if category == "routing" else "查询失败"
            await add_knowledge(
                category=category,
                node_type="experience",
                title=f"{prefix}：{query[:30]}",
                content=(
                    f"查询：{query}\n"
                    f"路径：{' → '.join(unique_tools)}\n"
                    f"{detail}\n耗时：{elapsed}"
                ),
                source="erp_agent",
                confidence=confidence,
                scope="org",
                org_id=self.org_id,
                max_per_category=self._EXPERIENCE_MAX_PER_CATEGORY,
            )
        except Exception as e:
            logger.debug(f"ERPAgent {category} experience save failed | error={e}")

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
            staging_dir = (
                Path(settings.file_workspace_root)
                / "staging"
                / (self.conversation_id or "default")
            )
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
                logger.info(
                    f"ERPAgent staging cleaned | dir={staging_dir}"
                )
        except Exception as e:
            logger.debug(f"ERPAgent staging cleanup failed | error={e}")
