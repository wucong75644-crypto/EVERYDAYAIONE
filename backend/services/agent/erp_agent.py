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

from services.agent.erp_agent_types import ERPAgentResult


# 有效数据域（不含 compute，计算由主 Agent 的 code_execute 负责）
_VALID_DOMAINS = frozenset({"warehouse", "purchase", "trade", "aftersale"})


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
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.task_id = task_id
        from utils.time_context import RequestContext
        self.request_ctx = request_ctx or RequestContext.build(
            user_id=user_id, org_id=org_id, request_id=task_id or "",
        )
        from services.agent.experience_recorder import ExperienceRecorder
        self._experience = ExperienceRecorder(org_id=org_id, writer="erp_agent")
        self._tokens_used: int = 0

    async def execute(
        self,
        query: str,
        **_kwargs: Any,
    ) -> ERPAgentResult:
        """执行 ERP 单域查询。

        **_kwargs 兼容旧调用方传 parent_messages（已无用，不报错）。
        """
        if not self.org_id:
            return ERPAgentResult(
                text="当前账号未开通 ERP 功能，请联系管理员配置企业账号。",
                status="error",
            )

        from core.config import get_settings
        _timeout = get_settings().dag_global_timeout
        _deadline = _time.monotonic() + _timeout
        try:
            return await asyncio.wait_for(
                self._execute(query, deadline=_deadline),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            return ERPAgentResult(
                text=f"查询超时（{_timeout:.0f}秒），请缩小查询范围后重试",
                status="error",
            )

    # ── 单域执行主流程 ──

    async def _execute(
        self, query: str, deadline: float,
    ) -> ERPAgentResult:
        """参数提取 → 准入校验 → 实例化单个 Agent → 执行 → 结果处理。"""
        # ── Step 1: 参数提取（三级降级链）──
        extract_result = await self._extract_params(query)
        if extract_result is None:
            return ERPAgentResult(
                text="无法理解您的请求，请更具体地描述您要查询的内容",
                status="error",
            )
        domain, params, degraded = extract_result

        # ── Step 2: 域白名单校验 ──
        if domain not in _VALID_DOMAINS:
            return ERPAgentResult(
                text=f"不支持的查询域 '{domain}'，"
                     f"可查询：库存/采购/订单/售后",
                status="error",
            )

        # ── Step 3: 准入校验（DB 验证 product_code / order_no）──
        from services.agent.plan_builder import (
            _fill_codes_for_params,
        )
        await _fill_codes_for_params(params, query, self.db, self.org_id)

        # ── Step 4: 实例化单个 DepartmentAgent ──
        agent = self._create_agent(domain)
        if agent is None:
            return ERPAgentResult(
                text=f"域 '{domain}' 无对应 Agent",
                status="error",
            )

        # ── Step 5: 执行查询（带 deadline 协调）──
        remaining = deadline - _time.monotonic()
        if remaining < 5.0:
            return ERPAgentResult(
                text="查询预算不足，请缩小查询范围后重试",
                status="error",
            )

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
            logger.warning(f"ERPAgent {domain} timeout")
            return ERPAgentResult(
                text=f"{domain} 查询超时，请缩小查询范围后重试",
                status="error",
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
            return ERPAgentResult(
                text=f"{domain} 执行异常: {error_msg}",
                status="error",
            )

        # ── Step 6+7: 结果处理 + 后处理 ──
        return self._build_result(result, query, domain, degraded)

    # ── 结果处理 ──

    def _build_result(
        self, result: Any, query: str, domain: str, degraded: bool,
    ) -> ERPAgentResult:
        """Step 6+7: 文件注册 + 降级标记 + 经验记录 → ERPAgentResult。"""
        from services.agent.tool_output import OutputStatus

        summary = result.summary or ""
        collected_files: list[dict[str, Any]] = []

        # file_ref 注册到 SessionFileRegistry
        if result.file_ref:
            from services.agent.session_file_registry import SessionFileRegistry
            registry = SessionFileRegistry()
            registry.register(domain, "execute", result.file_ref)
            collected_files.append({
                "url": result.file_ref.path,
                "name": result.file_ref.filename,
                "mime_type": "application/octet-stream",
                "size": result.file_ref.size_bytes,
            })
            asyncio.create_task(self._cleanup_staging_delayed())

        # 降级标记
        if degraded:
            summary = "⚠ 简化查询模式（关键词匹配，非AI分析）\n\n" + summary

        # 经验记录
        if result.status == OutputStatus.ERROR:
            asyncio.create_task(self._experience.record(
                "failure", query, [domain],
                f"单域失败：{summary[:200]}",
            ))
        else:
            asyncio.create_task(self._experience.record(
                "routing", query, [domain],
                "单域查询", confidence=0.6,
            ))

        status = "error" if result.status == OutputStatus.ERROR else "success"
        return ERPAgentResult(
            text=summary, full_text=summary,
            status=status, tokens_used=self._tokens_used,
            tools_called=[domain], collected_files=collected_files,
        )

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
        return cls(
            db=self.db, org_id=self.org_id,
            request_ctx=self.request_ctx,
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
