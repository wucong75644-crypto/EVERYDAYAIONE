"""ERP 编排 Agent — 计划提取 + 并行部门执行 + 结构化返回。"""
from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from services.agent.execution_budget import ExecutionBudget
    from utils.time_context import RequestContext

from services.agent.agent_result import AgentResult

_VALID_DOMAINS = frozenset({"warehouse", "purchase", "trade", "aftersale"})
_DOMAIN_LABEL = {"warehouse": "库存", "purchase": "采购", "trade": "订单", "aftersale": "售后"}


def _error_result(summary: str, status: str = "error") -> AgentResult:
    return AgentResult(status=status, summary=summary, source="erp_agent", error_message=summary)


@dataclass
class PlanStep:
    domain: str
    params: dict

@dataclass
class ExecutionPlan:
    steps: list[PlanStep]
    compute_hint: str | None = None
    degraded: bool = False


class ERPAgent:
    """ERP 编排 Agent — 计划提取 + 并行部门执行 + 结构化返回。"""

    def __init__(
        self, db: Any, user_id: str, conversation_id: str, org_id: str,
        task_id: Optional[str] = None, message_id: Optional[str] = None,
        request_ctx: Optional["RequestContext"] = None,
        budget: Optional["ExecutionBudget"] = None,
    ) -> None:
        self.db, self.user_id = db, user_id
        self.conversation_id, self.org_id = conversation_id, org_id
        self.task_id, self.message_id = task_id, message_id
        self._budget = budget
        from utils.time_context import RequestContext
        self.request_ctx = request_ctx or RequestContext.build(
            user_id=user_id, org_id=org_id, request_id=task_id or "",
        )
        from services.agent.experience_recorder import ExperienceRecorder
        self._experience = ExperienceRecorder(org_id=org_id, writer="erp_agent")
        self._tokens_used: int = 0
        self._thinking_parts: list[str] = []

    async def execute(self, task: str, conversation_context: str = "") -> AgentResult:
        """执行 ERP 查询任务。"""
        query = f"{task}\n（背景：{conversation_context}）" if conversation_context else task

        from services.agent.observability.langfuse_integration import create_trace, create_span
        create_span(
            create_trace(name="erp_agent", user_id=self.user_id),
            name="erp_agent.execute",
            metadata={"task": task[:200], "has_context": bool(conversation_context)},
        )

        if not self.org_id:
            return _error_result("当前账号未开通 ERP 功能，请联系管理员配置企业账号。")

        from core.config import get_settings
        _cfg_timeout = get_settings().dag_global_timeout
        _timeout = min(self._budget.remaining, _cfg_timeout) if self._budget else _cfg_timeout
        _deadline = _time.monotonic() + _timeout

        try:
            return await asyncio.wait_for(self._execute(query, deadline=_deadline), timeout=_timeout)
        except asyncio.TimeoutError:
            return _error_result(f"查询超时（{_timeout:.0f}秒），请缩小查询范围后重试", status="timeout")
        except Exception as e:
            logger.opt(exception=True).error(f"ERPAgent exception | query={query[:100]}")
            is_known = isinstance(e, (ValueError, PermissionError, ConnectionError))
            msg = str(e) if is_known else f"内部错误，请联系管理员（{type(e).__name__}）"
            return _error_result(f"执行异常: {msg}")

    async def _execute(self, query: str, deadline: float) -> AgentResult:
        """计划提取 → 并行部门执行 → 结果构建。"""
        await self._push_thinking("分析查询意图...")
        plan = await self._extract_plan(query)
        if plan is None:
            return _error_result("无法理解您的请求，请更具体地描述您要查询的内容")

        for step in plan.steps:
            if step.domain not in _VALID_DOMAINS:
                return _error_result(f"不支持的查询域 '{step.domain}'，可查询：库存/采购/订单/售后")

        from services.agent.plan_builder import _fill_codes_for_params
        for step in plan.steps:
            await _fill_codes_for_params(step.params, query, self.db, self.org_id)

        step_results = await self._execute_plan(plan, query, deadline)
        result = self._build_multi_result(step_results, plan, query)
        await self._push_thinking("完成")
        if self._thinking_parts:
            result.thinking_text = "\n".join(self._thinking_parts)
        return result

    async def _extract_plan(self, query: str) -> ExecutionPlan | None:
        """三级降级链：LLM 多域提取 → 关键词单域 → abort。"""
        from services.agent.plan_builder import (
            _DOMAIN_DOC_TYPES, _DOMAIN_DEFAULT_DOC_TYPE,
            _sanitize_params, fill_platform, quick_classify, _build_fallback_params,
        )

        # L1: LLM
        try:
            raw_steps, compute_hint = await self._llm_extract(query)
            steps = []
            for domain, params in raw_steps:
                params = _sanitize_params(params)
                doc_type = params.get("doc_type")
                allowed = _DOMAIN_DOC_TYPES.get(domain)
                if doc_type and allowed and doc_type not in allowed:
                    default = _DOMAIN_DEFAULT_DOC_TYPE.get(domain, next(iter(allowed)))
                    logger.warning(f"L2 域路由冲突: domain={domain} doc_type={doc_type} → {default}")
                    params["doc_type"] = default
                fill_platform(params, query)
                steps.append(PlanStep(domain=domain, params=params))
            return ExecutionPlan(steps=steps, compute_hint=compute_hint, degraded=False)
        except Exception as e:
            logger.warning(f"LLM extract failed, falling back: {e}")

        # L2: 关键词
        domain = quick_classify(query)
        if domain:
            params = _build_fallback_params(query, self.request_ctx, domain=domain)
            fill_platform(params, query)
            return ExecutionPlan(steps=[PlanStep(domain=domain, params=params)], degraded=True)

        return None

    async def _llm_extract(self, query: str) -> tuple[list[tuple[str, dict]], str | None]:
        """调 LLM 提取多域计划。失败时抛异常，由调用方降级。"""
        from services.adapters.factory import create_chat_adapter
        from core.config import get_settings
        from services.agent.plan_builder import build_multi_extract_prompt, parse_multi_extract_response

        now = self.request_ctx.now
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        now_str = f"{now.strftime('%Y-%m-%d %H:%M')} {weekday[now.weekday()]}"

        prompt = build_multi_extract_prompt(query, now_str=now_str)
        messages = [
            {"role": "system", "content": "你是参数提取器，只返回JSON。"},
            {"role": "user", "content": prompt},
        ]
        adapter = create_chat_adapter(get_settings().agent_loop_model, org_id=self.org_id, db=self.db)
        try:
            response = await adapter.chat_sync(messages=messages)
            self._tokens_used += getattr(response, "prompt_tokens", 0)
            self._tokens_used += getattr(response, "completion_tokens", 0)
            raw = getattr(response, "content", "") or ""
            return parse_multi_extract_response(raw)
        finally:
            await adapter.close()

    async def _execute_plan(self, plan: ExecutionPlan, query: str, deadline: float) -> list[tuple[str, Any]]:
        """并行执行所有 step，返回 [(domain, ToolOutput | Exception)]。"""
        async def run_step(step: PlanStep) -> tuple[str, Any]:
            await self._push_thinking(f"查询{_DOMAIN_LABEL.get(step.domain, step.domain)}数据...")
            agent = self._create_agent(step.domain)
            if agent is None:
                return (step.domain, ValueError(f"域 '{step.domain}' 无对应 Agent"))
            remaining = deadline - _time.monotonic()
            if remaining < 3.0:
                return (step.domain, asyncio.TimeoutError())
            logger.info(f"ERPAgent execute | domain={step.domain} | params={step.params} | remaining={remaining:.1f}s")
            try:
                result = await asyncio.wait_for(
                    agent.execute(query[:200], dag_mode=True, params=step.params),
                    timeout=min(remaining, 30.0),
                )
                return (step.domain, result)
            except asyncio.TimeoutError:
                logger.warning(f"ERPAgent {step.domain} timeout")
                return (step.domain, asyncio.TimeoutError())
            except Exception as e:
                logger.opt(exception=True).error(f"ERPAgent {step.domain} exception")
                return (step.domain, e)

        return list(await asyncio.gather(*[run_step(s) for s in plan.steps]))

    def _build_multi_result(
        self,
        step_results: list[tuple[str, Any]],
        plan: ExecutionPlan,
        query: str,
    ) -> AgentResult:
        """将并行执行结果聚合为单个 AgentResult。"""
        from services.agent.tool_output import OutputFormat

        successes: list[tuple[str, Any]] = []
        errors: list[str] = []

        for domain, result in step_results:
            if isinstance(result, Exception):
                label = _DOMAIN_LABEL.get(domain, domain)
                if isinstance(result, asyncio.TimeoutError):
                    errors.append(f"{label}查询超时")
                else:
                    errors.append(f"{label}查询失败: {result}")
            elif hasattr(result, "status") and str(result.status) == "error":
                label = _DOMAIN_LABEL.get(domain, domain)
                errors.append(f"{label}: {result.summary}")
            else:
                successes.append((domain, result))

        # 全部失败
        if not successes:
            return _error_result("；".join(errors) or "所有查询均失败")

        # 注册文件 + 经验记录（每个域匹配自己 step 的 params）
        step_params_map = {s.domain: s.params for s in plan.steps}
        for domain, result in successes:
            self._register_files(domain, result)
            self._record_experience(domain, query, result, step_params_map.get(domain))

        # staging 延迟清理
        asyncio.create_task(self._cleanup_staging_delayed())

        # 单步结果
        if len(successes) == 1:
            domain, result = successes[0]
            summary = result.summary or ""
            if errors:
                summary += f"\n\n⚠ {'; '.join(errors)}"
            status = "success"
            return AgentResult(
                status=status,
                summary=summary,
                file_ref=result.file_ref,
                data=result.data if result.format == OutputFormat.TABLE else None,
                columns=result.columns,
                source="erp_agent",
                tokens_used=self._tokens_used,
                confidence=0.6 if plan.degraded else 1.0,
                error_message="",
                metadata={"compute_hint": plan.compute_hint} if plan.compute_hint else {},
            )

        # 多步结果：合并 summary + file_ref 引用 + compute_hint
        parts = []
        file_refs = []
        all_file_ref_objs = []
        for domain, result in successes:
            label = _DOMAIN_LABEL.get(domain, domain)
            parts.append(f"【{label}】{result.summary}")
            if result.file_ref:
                file_refs.append({
                    "domain": domain,
                    "path": result.file_ref.path,
                    "filename": result.file_ref.filename,
                    "rows": result.file_ref.row_count,
                })
                all_file_ref_objs.append(result.file_ref)

        summary = "\n\n".join(parts)

        # 额外 file_ref 写入 summary（to_message_content 只输出 primary file_ref）
        if len(all_file_ref_objs) > 1:
            for fr in all_file_ref_objs[1:]:
                summary += (
                    f"\n\n[文件已存入 staging | "
                    f"读取: pd.read_parquet({fr.sandbox_ref}) | "
                    f"{fr.row_count}行 | {fr.format}]"
                )

        # compute_hint 写入 summary（让主 Agent LLM 直接看到关联计算提示）
        if plan.compute_hint:
            summary += f"\n\n[关联计算提示] {plan.compute_hint}"

        if errors:
            summary += f"\n\n⚠ {'; '.join(errors)}"

        primary_file_ref = all_file_ref_objs[0] if all_file_ref_objs else None

        metadata: dict[str, Any] = {}
        if plan.compute_hint:
            metadata["compute_hint"] = plan.compute_hint
        if file_refs:
            metadata["file_refs"] = file_refs

        return AgentResult(
            status="success",
            summary=summary,
            file_ref=primary_file_ref,
            source="erp_agent",
            tokens_used=self._tokens_used,
            confidence=0.6 if plan.degraded else 1.0,
            metadata=metadata,
        )

    def _register_files(self, domain: str, result: Any) -> None:
        if not getattr(result, "file_ref", None):
            return
        try:
            from services.agent.session_file_registry import SessionFileRegistry
            SessionFileRegistry().register(domain, "execute", result.file_ref)
        except Exception as e:
            logger.debug(f"File registry failed: {e}")

    def _record_experience(self, domain: str, query: str, result: Any, params: dict | None) -> None:
        if hasattr(result, "status") and str(result.status) == "error":
            asyncio.create_task(self._experience.record(
                "failure", query, [domain], f"单域失败：{(result.summary or '')[:200]}",
            ))
        else:
            detail = self._build_experience_detail(domain, params)
            asyncio.create_task(self._experience.record("routing", query, [domain], detail, confidence=0.6))

    @staticmethod
    def _build_experience_detail(domain: str, params: dict | None) -> str:
        if not params:
            return f"domain={domain}"
        parts = [f"domain={domain}", f"mode={params.get('mode', 'summary')}"]
        for k in ("group_by", "platform", "fields", "product_code"):
            if params.get(k):
                parts.append(f"{k}={params[k]}")
        return ", ".join(parts)

    async def _push_thinking(self, text: str) -> None:
        self._thinking_parts.append(f"→ {text}")
        if not self.task_id or not self.message_id:
            return
        try:
            from services.websocket_manager import ws_manager
            await ws_manager.send_to_user(self.user_id, {
                "type": "thinking_chunk", "task_id": self.task_id,
                "conversation_id": self.conversation_id,
                "message_id": self.message_id,
                "text": f"\n── ERP Agent ──\n→ {text}\n",
            })
        except Exception:
            pass

    def _create_agent(self, domain: str) -> Any:
        from services.agent.departments.warehouse_agent import WarehouseAgent
        from services.agent.departments.purchase_agent import PurchaseAgent
        from services.agent.departments.trade_agent import TradeAgent
        from services.agent.departments.aftersale_agent import AftersaleAgent

        cls = {"warehouse": WarehouseAgent, "purchase": PurchaseAgent,
               "trade": TradeAgent, "aftersale": AftersaleAgent}.get(domain)
        if cls is None:
            return None
        staging_dir = None
        try:
            from core.config import get_settings
            from core.workspace import resolve_staging_dir
            staging_dir = resolve_staging_dir(
                get_settings().file_workspace_root,
                self.user_id, self.org_id, self.conversation_id or "default",
            )
        except Exception as e:
            logger.warning(f"resolve staging_dir failed: {e}")
        child_budget = self._budget.fork(max_turns=5) if self._budget else None
        return cls(
            db=self.db, org_id=self.org_id, request_ctx=self.request_ctx,
            staging_dir=staging_dir, budget=child_budget,
            user_id=self.user_id, conversation_id=self.conversation_id,
        )

    @staticmethod
    def build_tool_description() -> str:
        """从 capability manifest 格式化为 5 段式描述文本。"""
        return _build_tool_description()

    # ── staging 清理 ──

    async def _cleanup_staging_delayed(self, delay: int = 900) -> None:
        """会话级 staging 延迟清理（15 分钟，覆盖 ~85% 的用户追问间隔）。"""
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
                    f"ERPAgent staging cleaned | dir={staging_dir}",
                )
        except Exception as e:
            logger.debug(f"ERPAgent staging cleanup failed | error={e}")


# ── 模块级辅助（从 ERPAgent 类中提取，减少类行数）──


def _build_tool_description() -> str:
    """从 capability manifest 格式化为 5 段式描述文本。"""
    from services.agent.plan_builder import get_capability_manifest
    m = get_capability_manifest()

    lines = [m["summary"]]
    lines.append("\n使用场景：" + "；".join(m["use_when"]))
    dont = " / ".join(
        f"{d['场景']}→{d['替代']}" for d in m["dont_use_when"]
    )
    lines.append(f"不要用于：{dont}")

    lines.append("\n能力：")
    lines.append(f"- 输出模式：{' / '.join(m['modes'])}（>200行自动导出文件）")
    lines.append(f"- 分组统计：按{'/'.join(m['group_by'])}统计")
    lines.append(f"- 过滤：自动识别{'、'.join(m['platforms'])}、商品编码、订单号")
    lines.append(f"- 时间列：{' / '.join(m['time_cols'])}（默认 doc_created_at）")
    lines.append("- 异常数据：默认排除刷单，query 中写'包含刷单'则包含")
    lines.append("- 跨域并行：可一次查询多个域数据（订单+售后等）")
    categories = m.get("field_categories", {})
    if categories:
        lines.append(f"- 可查询信息：{'/'.join(categories.keys())}")
        lines.append(
            "  （query 中提到具体信息如'备注''地址''快递单号'"
            "会自动返回对应字段）",
        )

    lines.append("\n返回：")
    for r in m["returns"]:
        lines.append(f"- {r}")

    lines.append("\nquery 示例：")
    for ex in m["examples"]:
        lines.append(f"· \"{ex['query']}\" → {ex['effect']}")

    return "\n".join(lines)
