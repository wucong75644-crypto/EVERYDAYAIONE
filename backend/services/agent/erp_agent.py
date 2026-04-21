"""
ERP 独立 Agent — 单域查询模式。

每次只查一个域的数据：参数提取 → 准入校验 → 部门 Agent 执行 → 返回结果。
跨域编排由主 Agent 负责（并行调多次 erp_agent + code_execute 合并）。
类型/常量见 erp_agent_types.py。

设计文档: docs/document/TECH_ERPAgent架构简化.md
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from utils.time_context import RequestContext

from services.agent.agent_result import AgentResult


# 有效数据域（不含 compute，计算由主 Agent 的 code_execute 负责）
_VALID_DOMAINS = frozenset({"warehouse", "purchase", "trade", "aftersale"})


def _error_result(summary: str, status: str = "error") -> AgentResult:
    """构建错误/异常 AgentResult 的快捷方式。"""
    return AgentResult(
        status=status, summary=summary,
        source="erp_agent", error_message=summary,
    )


class ERPAgent:
    """ERP 独立 Agent — 单域查询：参数提取 + 部门Agent执行 + 结果返回"""

    def __init__(
        self,
        db: Any,
        user_id: str,
        conversation_id: str,
        org_id: str,
        task_id: Optional[str] = None,
        request_ctx: Optional["RequestContext"] = None,
        budget: Optional["ExecutionBudget"] = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        self._budget = budget  # v6: 显式参数替代属性注入
        from utils.time_context import RequestContext
        self.request_ctx = request_ctx or RequestContext.build(
            user_id=user_id, org_id=org_id, request_id=task_id or "",
        )
        from services.agent.experience_recorder import ExperienceRecorder
        self._experience = ExperienceRecorder(org_id=org_id, writer="erp_agent")
        self._tokens_used: int = 0

    async def execute(
        self,
        task: str,
        conversation_context: str = "",
    ) -> AgentResult:
        """执行 ERP 单域查询。

        Args:
            task: 主 Agent 整理好的清晰任务描述
            conversation_context: 对话背景补充（可选）
        """
        # 合并为完整查询上下文
        query = task
        if conversation_context:
            query = f"{task}\n（背景：{conversation_context}）"

        # v6: Langfuse span
        from services.agent.observability.langfuse_integration import (
            create_trace, create_span,
        )
        create_span(
            create_trace(name="erp_agent", user_id=self.user_id),
            name="erp_agent.execute",
            metadata={"task": task[:200], "has_context": bool(conversation_context)},
        )

        if not self.org_id:
            return _error_result("当前账号未开通 ERP 功能，请联系管理员配置企业账号。")

        from core.config import get_settings
        _cfg_timeout = get_settings().dag_global_timeout
        # v6: budget.remaining 约束超时（取较小值）
        _timeout = (
            min(self._budget.remaining, _cfg_timeout)
            if self._budget else _cfg_timeout
        )
        _deadline = _time.monotonic() + _timeout
        try:
            return await asyncio.wait_for(
                self._execute(query, deadline=_deadline),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            return _error_result(
                f"查询超时（{_timeout:.0f}秒），请缩小查询范围后重试",
                status="timeout",
            )

    # ── 单域执行主流程 ──

    async def _execute(
        self, query: str, deadline: float,
    ) -> AgentResult:
        """参数提取 → 准入校验 → 实例化单个 Agent → 执行 → 结果处理。"""
        # ── Step 1: 参数提取（三级降级链）──
        extract_result = await self._extract_params(query)
        if extract_result is None:
            return _error_result("无法理解您的请求，请更具体地描述您要查询的内容")
        domain, params, degraded = extract_result

        # ── Step 2: 域白名单校验 ──
        if domain not in _VALID_DOMAINS:
            return _error_result(
                f"不支持的查询域 '{domain}'，可查询：库存/采购/订单/售后",
            )

        # ── Step 3: 准入校验（DB 验证 product_code / order_no）──
        from services.agent.plan_builder import (
            _fill_codes_for_params,
        )
        await _fill_codes_for_params(params, query, self.db, self.org_id)

        # ── Step 4: 实例化单个 DepartmentAgent ──
        agent = self._create_agent(domain)
        if agent is None:
            return _error_result(f"域 '{domain}' 无对应 Agent")

        # ── Step 5: 执行查询（带 deadline 协调）──
        remaining = deadline - _time.monotonic()
        if remaining < 5.0:
            return _error_result("查询预算不足，请缩小查询范围后重试")

        task_desc = query[:50]
        logger.info(
            f"ERPAgent execute | domain={domain} | "
            f"params={params} | remaining={remaining:.1f}s",
        )

        try:
            result = await asyncio.wait_for(
                agent.execute(task_desc, dag_mode=True, params=params),
                timeout=min(remaining, 30.0),
            )
        except asyncio.TimeoutError:
            # v6: 超时时检查子 Agent 是否有 partial 数据
            partial = getattr(agent, "_partial_rows", [])
            if partial:
                logger.warning(
                    f"ERPAgent {domain} timeout with {len(partial)} partial rows",
                )
                return _error_result(
                    f"{domain} 查询超时，返回已获取的 {len(partial)} 条部分数据",
                    status="partial",
                )
            logger.warning(f"ERPAgent {domain} timeout")
            return _error_result(
                f"{domain} 查询超时，请缩小查询范围后重试",
                status="timeout",
            )
        except Exception as e:
            logger.opt(exception=True).error(
                f"ERPAgent {domain} exception | query={query[:50]}",
            )
            is_known = isinstance(
                e, (ValueError, PermissionError, ConnectionError),
            )
            error_msg = (
                str(e) if is_known
                else f"内部错误，请联系管理员（{type(e).__name__}）"
            )
            return _error_result(f"{domain} 执行异常: {error_msg}")

        # ── Step 6+7: 结果处理 + 后处理 ──
        return self._build_result(result, query, domain, degraded, params)

    # ── 结果处理 ──

    def _build_result(
        self, result: Any, query: str, domain: str, degraded: bool,
        params: dict | None = None,
    ) -> "AgentResult":
        """Step 6+7: 文件注册 + 降级标记 + 经验记录 → AgentResult。"""
        from services.agent.agent_result import AgentResult
        from services.agent.tool_output import OutputFormat

        summary = result.summary or ""

        # ① SessionFileRegistry 注册（沙盒 read_file 依赖）
        if result.file_ref:
            from services.agent.session_file_registry import SessionFileRegistry
            registry = SessionFileRegistry()
            registry.register(domain, "execute", result.file_ref)
            # staging parquet 是中间产物（供 code_execute 转 Excel），
            # 不发 collected_files 给前端。用户最终下载的 Excel 由
            # code_execute → OUTPUT_DIR → auto_upload → [FILE] 链路生成。
            # ② staging 延迟清理
            asyncio.create_task(self._cleanup_staging_delayed())

        # ③ 经验记录（detail 含关键参数，供动态案例召回使用）
        if result.status == "error":
            asyncio.create_task(self._experience.record(
                "failure", query, [domain],
                f"单域失败：{summary[:200]}",
            ))
        else:
            detail = self._build_experience_detail(domain, params)
            asyncio.create_task(self._experience.record(
                "routing", query, [domain],
                detail, confidence=0.6,
            ))

        # ④ 构建 AgentResult（通信协议标准输出）
        status = "error" if result.status == "error" else "success"
        return AgentResult(
            status=status,
            summary=summary,
            file_ref=result.file_ref,
            data=result.data if result.format == OutputFormat.TABLE else None,
            columns=result.columns,
            source="erp_agent",
            tokens_used=self._tokens_used,
            confidence=0.6 if degraded else 1.0,
            error_message=summary if status == "error" else "",
        )

    @staticmethod
    def _build_experience_detail(
        domain: str, params: dict | None,
    ) -> str:
        """构建经验记录的 detail 字段（含关键参数，供动态案例召回）。"""
        if not params:
            return f"domain={domain}"
        parts = [f"domain={domain}"]
        mode = params.get("mode", "summary")
        parts.append(f"mode={mode}")
        if params.get("group_by"):
            parts.append(f"group_by={params['group_by']}")
        if params.get("platform"):
            parts.append(f"platform={params['platform']}")
        if params.get("fields"):
            parts.append(f"fields={params['fields']}")
        if params.get("product_code"):
            parts.append(f"product_code={params['product_code']}")
        return ", ".join(parts)

    # ── 工具描述自动生成（静态层）──

    @staticmethod
    def build_tool_description() -> str:
        """从 capability manifest 格式化为 5 段式描述文本。

        纯模板渲染，不含任何硬编码内容。
        改内容 → 改 get_capability_manifest()；改格式 → 改此方法。
        设计文档: docs/document/TECH_Agent能力通信架构.md §3.3.2
        """
        from services.agent.plan_builder import get_capability_manifest
        m = get_capability_manifest()

        # ① 功能定义
        lines = [m["summary"]]

        # ② 决策边界
        lines.append("\n使用场景：" + "；".join(m["use_when"]))
        dont = " / ".join(
            f"{d['场景']}→{d['替代']}" for d in m["dont_use_when"]
        )
        lines.append(f"不要用于：{dont}")

        # ③ 能力清单
        lines.append("\n能力：")
        lines.append(
            f"- 输出模式：{' / '.join(m['modes'])}（>200行自动导出文件）",
        )
        lines.append(f"- 分组统计：按{'/'.join(m['group_by'])}统计")
        lines.append(
            f"- 过滤：自动识别{'、'.join(m['platforms'])}、商品编码、订单号",
        )
        lines.append(
            f"- 时间列：{' / '.join(m['time_cols'])}（默认 doc_created_at）",
        )
        lines.append("- 异常数据：默认排除刷单，query 中写'包含刷单'则包含")

        # ③+ 可查询信息分类
        categories = m.get("field_categories", {})
        if categories:
            lines.append(f"- 可查询信息：{'/'.join(categories.keys())}")
            lines.append(
                "  （query 中提到具体信息如'备注''地址''快递单号'"
                "会自动返回对应字段）",
            )

        # ④ 返回说明
        lines.append("\n返回：")
        for r in m["returns"]:
            lines.append(f"- {r}")

        # ⑤ few-shot 示例
        lines.append("\nquery 示例：")
        for ex in m["examples"]:
            lines.append(f"· \"{ex['query']}\" → {ex['effect']}")

        return "\n".join(lines)

    # ── 参数提取（三级降级链）──

    async def _extract_params(
        self, query: str,
    ) -> tuple[str, dict, bool] | None:
        """从用户查询提取域和参数。

        三级降级链：
        1. LLM 结构化提取
        2. 关键词匹配降级
        3. abort（返回 None）

        Returns: (domain, params, degraded) 或 None
        """
        from services.agent.plan_builder import (
            VALID_DOMAINS as PB_VALID_DOMAINS,
            _DOMAIN_DOC_TYPES,
            _DOMAIN_DEFAULT_DOC_TYPE,
            _sanitize_params,
            quick_classify,
            _build_fallback_params,
            build_extract_prompt,
            parse_extract_response,
        )

        # ── 第一级：LLM 提取 ──
        try:
            domain, params = await self._llm_extract(query)
            # 参数宽容校验
            params = _sanitize_params(params)
            # L2 域路由冲突检测
            doc_type = params.get("doc_type")
            allowed = _DOMAIN_DOC_TYPES.get(domain)
            if doc_type and allowed and doc_type not in allowed:
                default = _DOMAIN_DEFAULT_DOC_TYPE.get(
                    domain, next(iter(allowed)),
                )
                logger.warning(
                    f"L2 域路由冲突: domain={domain} "
                    f"doc_type={doc_type} → {default}",
                )
                params["doc_type"] = default
            # L2 platform 补全
            self._fill_platform(params, query)
            return (domain, params, False)
        except Exception as e:
            logger.warning(f"LLM extract failed, falling back: {e}")

        # ── 第二级：关键词匹配降级 ──
        domain = quick_classify(query)
        if domain:
            params = _build_fallback_params(
                query, self.request_ctx, domain=domain,
            )
            self._fill_platform(params, query)
            return (domain, params, True)

        # ── 第三级：无法理解 ──
        return None

    async def _llm_extract(
        self, query: str,
    ) -> tuple[str, dict]:
        """调 LLM 提取域和参数。失败时抛异常，由调用方降级。"""
        from services.adapters.factory import create_chat_adapter
        from core.config import settings
        from services.agent.plan_builder import (
            build_extract_prompt,
            parse_extract_response,
        )

        # 构造时间字符串
        now = self.request_ctx.now
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        now_str = (
            f"{now.strftime('%Y-%m-%d %H:%M')} "
            f"{weekday[now.weekday()]}"
        )

        prompt = build_extract_prompt(query, now_str=now_str)
        messages = [
            {"role": "system", "content": "你是参数提取器，只返回JSON。"},
            {"role": "user", "content": prompt},
        ]

        adapter = create_chat_adapter(
            settings.agent_loop_model, org_id=self.org_id, db=self.db,
        )
        try:
            response = await adapter.chat_sync(messages=messages)
            self._tokens_used += getattr(response, "prompt_tokens", 0)
            self._tokens_used += getattr(response, "completion_tokens", 0)
            raw = getattr(response, "content", "") or ""
            return parse_extract_response(raw)
        finally:
            await adapter.close()

    @staticmethod
    def _fill_platform(params: dict, query: str) -> None:
        """L2 意图完整性：从用户查询文本补全 LLM 漏提取的 platform。"""
        if params.get("platform"):
            return  # AI 已提取，不覆盖

        from services.kuaimai.erp_unified_schema import PLATFORM_NORMALIZE
        cn_keys = [
            k for k in PLATFORM_NORMALIZE
            if not k.isascii() or k == "1688"
        ]
        matched: set[str] = set()
        for key in cn_keys:
            if key in query:
                matched.add(PLATFORM_NORMALIZE[key])

        if len(matched) == 1:
            params["platform"] = matched.pop()
            logger.info(
                f"L2 platform 补全: query={query!r} → "
                f"platform={params['platform']}",
            )
        elif len(matched) > 1:
            logger.warning(
                f"L2 platform 多匹配，不补全: query={query!r}, "
                f"matched={matched}",
            )

    def _create_agent(self, domain: str) -> Any:
        """按域名实例化对应的 DepartmentAgent。"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        from services.agent.departments.purchase_agent import PurchaseAgent
        from services.agent.departments.trade_agent import TradeAgent
        from services.agent.departments.aftersale_agent import AftersaleAgent

        agent_map = {
            "warehouse": WarehouseAgent,
            "purchase": PurchaseAgent,
            "trade": TradeAgent,
            "aftersale": AftersaleAgent,
        }
        cls = agent_map.get(domain)
        if cls is None:
            return None

        # 解析 staging_dir（与主 agent 的 code_execute 共享同一目录）
        staging_dir = None
        try:
            from core.config import get_settings
            from core.workspace import resolve_staging_dir
            _s = get_settings()
            staging_dir = resolve_staging_dir(
                _s.file_workspace_root,
                self.user_id,
                self.org_id,
                self.conversation_id or "default",
            )
        except Exception as e:
            logger.warning(f"resolve staging_dir failed: {e}")

        # v6: 传 budget.fork() 给子 Agent
        child_budget = self._budget.fork(max_turns=5) if self._budget else None
        return cls(
            db=self.db, org_id=self.org_id,
            request_ctx=self.request_ctx,
            staging_dir=staging_dir,
            budget=child_budget,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )

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
