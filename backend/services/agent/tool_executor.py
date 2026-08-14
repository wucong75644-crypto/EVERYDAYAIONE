"""
同步工具执行器

执行 Agent Loop / ChatHandler 工具循环中的工具。
异常不在此处 catch — 调用方统一处理并回传大脑。

通用调度位于本文件，领域执行逻辑由各 ToolMixin 承担。
"""

from __future__ import annotations
from typing import AbstractSet, Any, Callable, Coroutine, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from services.agent.agent_result import AgentResult

from loguru import logger
from config.erp_local_tools import ERP_LOCAL_TOOLS
from config.erp_tools import ERP_SYNC_TOOLS
from config.file_tools import FILE_INFO_TOOLS
from services.agent.erp_tool_executor import ErpToolMixin
from services.agent.artifact_tool_mixin import ArtifactToolMixin
from services.agent.memory_tool_mixin import MemoryToolMixin
from services.agent.evidence_tool_mixin import EvidenceToolMixin
from services.agent.conversation_tool_mixin import ConversationToolMixin
from services.agent.file_tool_mixin import CrawlerToolMixin, FileToolMixin
from services.agent.knowledge_tool_mixin import KnowledgeToolMixin
from services.agent.sandbox_tool_mixin import SandboxToolMixin
from services.agent.runtime_media_tool_mixin import RuntimeMediaToolMixin
from services.handlers.mixins.credit_mixin import CreditMixin
from services.media_tool_executor import MediaToolMixin
class ToolExecutor(
    ArtifactToolMixin, MemoryToolMixin, ConversationToolMixin,
    FileToolMixin, CrawlerToolMixin,
    RuntimeMediaToolMixin, MediaToolMixin, EvidenceToolMixin,
    ErpToolMixin, KnowledgeToolMixin, SandboxToolMixin,
    CreditMixin,
):
    """同步工具执行器"""

    def __init__(
        self, db, user_id: str, conversation_id: str,
        org_id: str | None = None,
        request_ctx=None,
        workspace_user_id: str | None = None,
        resource_manifest=None, runtime_state=None,
        personal_context_allowed: bool = True,
        allowed_tools: AbstractSet[str] | None = None,
        runtime_action_executor: Any | None = None,
        input_message_id: str | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.workspace_user_id = workspace_user_id or user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        self._runtime_action_executor = runtime_action_executor
        self._input_message_id = input_message_id
        self._task_id = task_id
        self._message_id = message_id
        self.resource_manifest, self.runtime_state = resource_manifest, runtime_state
        # 时间事实层 — 请求级 SSOT，由 ERPAgent 透传
        # 设计文档：docs/document/TECH_ERP时间准确性架构.md §6.2.4 (B16)
        self.request_ctx = request_ctx
        self._pending_schemas: list = []  # 已废弃，保留空列表兼容 chat_tool_mixin.clear()
        self._handlers: Dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
            "get_conversation_context": self._get_conversation_context,
            "search_knowledge": self._search_knowledge,
            "social_crawler": self._social_crawler,
            "erp_api_search": self._erp_api_search,
            "code_execute": self._code_execute,
            "web_search": self._web_search,
            "generate_image": self._generate_image,
            "generate_video": self._generate_video,
            # 数据查询：file_analyze → code_execute + duckdb（沿用沙盒查询能力）
            "erp_agent": self._erp_agent,
            "erp_analyze": self._erp_analyze,
            "manage_scheduled_task": self._manage_scheduled_task,
            "image_agent": self._image_agent,
            "evidence_search": self._evidence_search,
            "evidence_get": self._evidence_get,
            "artifact_search": self._artifact_search, "artifact_get": self._artifact_get,
            "artifact_read": self._artifact_read,
        }
        if personal_context_allowed:
            self._handlers.update({
                "memory_search": self._memory_search,
                "memory_get": self._memory_get,
            })
        for tool_name in FILE_INFO_TOOLS:
            self._handlers[tool_name] = self._make_file_handler(tool_name)
        if org_id is not None:
            self._handlers["fetch_all_pages"] = self._fetch_all_pages
            for tool_name in ERP_SYNC_TOOLS:
                self._handlers[tool_name] = self._make_erp_handler(tool_name)
            for tool_name in ERP_LOCAL_TOOLS:
                self._handlers[tool_name] = self._make_local_handler(tool_name)
        if self.workspace_user_id != self.user_id:
            self._handlers.pop("get_conversation_context", None)
            self._handlers.pop("manage_scheduled_task", None)
        if allowed_tools is not None:
            self._handlers = {
                name: handler
                for name, handler in self._handlers.items()
                if name in allowed_tools
            }

    def has_handler(self, tool_name: str) -> bool:
        """检查工具是否有已注册的 handler（兜底扩充用）"""
        return tool_name in self._handlers

    async def execute(self, tool_name: str, arguments: Dict[str, Any]):
        """执行同步工具，返回 ToolOutput 或 str。

        底层工具返回 ToolOutput 时直接透传，
        ToolLoopExecutor 负责统一处理（转 content + 注入 timestamp）。

        Raises:
            ValueError: 未知工具名
            Exception: 工具执行异常（由调用方 catch 后回传大脑）
        """
        from config.chat_tools import SafetyLevel, get_safety_level
        from services.tool_confirmation.preview import registered_preview_tools

        handler = self._handlers.get(tool_name)
        if not handler:
            raise ValueError("UNKNOWN_TOOL_HANDLER")
        safety = get_safety_level(tool_name)
        has_preview = tool_name in registered_preview_tools()
        if (safety == SafetyLevel.SAFE and has_preview) or (
            safety != SafetyLevel.SAFE and not has_preview
        ):
            raise ValueError("TOOL_REGISTRY_INCONSISTENT")
        return await handler(arguments)

    # ========================================
    # 互联网搜索（Gemini + Google Search Grounding）
    # ========================================

    async def _web_search(self, args: Dict[str, Any]) -> "AgentResult":
        """搜索互联网获取实时信息（Gemini Google Search Grounding）"""
        from services.agent.agent_result import AgentResult
        from services.agent.web_search_engine import search_with_grounding

        query = args.get("query", "").strip()
        if not query:
            return AgentResult(
                summary="搜索查询不能为空",
                status="error",
                error_message="Validation: query is required",
                metadata={"retryable": True},
            )

        result = await search_with_grounding(query)
        if not result:
            return AgentResult(
                summary=f"搜索「{query}」未找到相关结果",
                status="empty",
            )
        return AgentResult(summary=result["content"], status="success", metadata={
            "sources": result.get("sources", []),
            "search_queries": result.get("search_queries", []),
        })

    # ========================================
    # 数据查询工具
    # ========================================

    # 数据查询统一走 file_analyze → code_execute + duckdb（沙盒内直接 SQL Parquet）

    # ========================================
    # ERP Agent（独立 Agent 作为工具调用）
    # ========================================

    async def _erp_agent(self, args: Dict[str, Any]) -> AgentResult:
        """ERP 独立 Agent：接收用户问题，内部运行工具循环，返回结论"""
        from services.agent.erp_agent import ERPAgent

        # 输入协议：task + conversation_context（向后兼容旧 query）
        task = (args.get("task") or args.get("query", "")).strip()
        if not task:
            from services.agent.agent_result import AgentResult as _AR
            return _AR(status="error", summary="请输入 ERP 相关问题")
        conversation_context = args.get("conversation_context", "")

        logger.info(
            "ERPAgent dispatch | "
            f"user_id={self.user_id} | org_id={self.org_id} | "
            f"task_id={getattr(self, '_task_id', None)} | tool=erp_agent"
        )

        # v6: budget 通过构造函数显式传递
        _parent_budget = getattr(self, "_budget", None)
        agent = ERPAgent(
            db=self.db,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            task_id=getattr(self, "_task_id", None),
            message_id=getattr(self, "_message_id", None),
            request_ctx=self.request_ctx,
            budget=_parent_budget,
            workspace_user_id=self.workspace_user_id,
        )

        result = await agent.execute(
            task, conversation_context=conversation_context,
        )

        # staging 文件注册到共享路径缓存（其他工具可直接用文件名引用）
        self._register_staging_files(result)

        # 返回 AgentResult，文件通道/display/token 由 ChatToolMixin 统一处理
        return result

    async def _image_agent(self, args: Dict[str, Any]) -> "AgentResult":
        """电商图片 Agent：单张图片生成（三重自动注入）。

        自动注入：
        1. image_urls — 当前消息的用户上传图片
        2. style_directive — 会话级全局风格（从 DB 读）
        3. history_images — 历史生成图片（从消息 FilePart 查）
        """
        # === 三重自动注入（LLM 不需要传这些参数）===

        # 注入1：用户上传的图片
        if not args.get("image_urls"):
            args["image_urls"] = getattr(self, "_current_message_images", [])

        # 注入2：全局风格（从 DB 读取）
        if not args.get("style_directive"):
            try:
                row = self.db.table("conversations").select(
                    "image_style_directive"
                ).eq("id", self.conversation_id).maybe_single().execute()
                if row and row.data and row.data.get("image_style_directive"):
                    args["style_directive"] = row.data["image_style_directive"]
            except Exception as exc:
                logger.warning(
                    "image_agent style lookup failed | "
                    f"user_id={self.user_id} | org_id={self.org_id} | "
                    "tool=image_agent | error_code=IMAGE_STYLE_LOOKUP_FAILED | "
                    f"exception_type={type(exc).__name__}"
                )

        # 注入3：历史生成图片（供修改引用）
        if not args.get("history_images"):
            args["history_images"] = self._get_conversation_image_parts()

        runtime_args = dict(args)
        runtime_args.setdefault("prompt", runtime_args.get("task", ""))
        runtime_args["image_urls"] = runtime_args.get("image_urls", [])
        runtime_args["style_directive"] = runtime_args.get("style_directive", "")
        runtime_args["history_images"] = runtime_args.get("history_images", [])
        return await self._execute_runtime_media_action("generate_image", runtime_args)

    def _get_conversation_image_parts(self) -> list[dict]:
        """从会话消息历史中提取已生成的图片 FilePart（供修改引用）。"""
        try:
            rows = self.db.table("messages").select("content").eq(
                "conversation_id", self.conversation_id,
            ).eq("role", "assistant").order(
                "created_at", desc=True,
            ).limit(20).execute()

            images: list[dict] = []
            for row in (rows.data or []):
                for part in (row.get("content") or []):
                    if isinstance(part, dict) and part.get("type") == "file":
                        mime = part.get("mime_type") or ""
                        if mime.startswith("image/"):
                            images.append({"url": part["url"], "name": part.get("name", "")})
            return images
        except Exception as exc:
            logger.warning(
                "image_agent history lookup failed | "
                f"user_id={self.user_id} | org_id={self.org_id} | "
                "tool=image_agent | error_code=IMAGE_HISTORY_LOOKUP_FAILED | "
                f"exception_type={type(exc).__name__}"
            )
            return []

    async def _erp_analyze(self, args: Dict[str, Any]) -> "AgentResult":
        """ERP 分析接口：只分析不执行，返回结构化任务拆解。

        主 Agent 在计划模式下调用，获取 ERP 查询的步骤、域、参数、依赖关系。
        不查数据库、不调 API，只跑 PlanBuilder LLM 提取。
        """
        from services.agent.erp_agent import ERPAgent

        task = (args.get("task") or args.get("query", "")).strip()
        if not task:
            from services.agent.agent_result import AgentResult as _AR
            return _AR(status="error", summary="请输入要分析的 ERP 查询")
        conversation_context = args.get("conversation_context", "")

        logger.info(
            "ERPAgent analyze | "
            f"user_id={self.user_id} | org_id={self.org_id} | "
            f"tool=erp_analyze"
        )

        agent = ERPAgent(
            db=self.db,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            request_ctx=self.request_ctx,
            workspace_user_id=self.workspace_user_id,
        )

        return await agent.analyze(task, conversation_context=conversation_context)

    # ========================================
    # 定时任务管理（聊天内创建/查看/修改）
    # ========================================

    async def _manage_scheduled_task(self, args: Dict[str, Any]):
        """聊天内管理定时任务：返回 FormBlockResult 或 AgentResult

        FormBlockResult 与 AgentResult 平级，chat_tool_mixin 用 isinstance 分发：
        - FormBlockResult → content_block_add 推送表单到前端
        - AgentResult → 普通结构化结果
        """
        from services.agent.agent_result import AgentResult
        from services.scheduler.chat_task_manager import ChatTaskManager, FormBlockResult

        if not self.org_id:
            return AgentResult(
                summary="此功能仅企业用户可用，请先加入企业。",
                status="error",
                error_message="Permission: org_id required",
                metadata={"retryable": False},
            )

        action = (args.get("action") or "").strip()
        if not action:
            return AgentResult(
                summary="请指定操作：create / list / update / pause / resume / delete",
                status="error",
                error_message="Validation: action is required",
                metadata={"retryable": True},
            )

        manager = ChatTaskManager(self.db, self.user_id, self.org_id)
        result = await manager.handle(action, args)

        if result.get("type") == "form":
            return FormBlockResult(
                form=result,
                llm_hint=f"已向用户展示{result.get('title', '表单')}，等待用户确认。不要重复展示表单内容。",
            )

        return AgentResult(
            summary=result.get("text", str(result)),
            status="success",
        )

    # ========================================
    # 搜索工具（按需发现 API/模型文档）
    # ========================================

    async def _erp_api_search(self, args: Dict[str, Any]) -> str:
        """搜索 ERP API 操作和参数文档"""
        from services.kuaimai.api_search import search_erp_api
        query = args.get("query", "").strip()
        if not query:
            return "请输入搜索关键词"
        return search_erp_api(query)

    # ========================================
    # 全量翻页工具（独立可组合工具）
    # ========================================

    async def _fetch_all_pages(self, args: Dict[str, Any]) -> "AgentResult":
        """包装任意 erp_* 远程查询工具，自动翻页拉取全部数据并存 staging"""
        import asyncio
        import time as _time
        from pathlib import Path

        from core.config import get_settings
        from services.agent.agent_result import AgentResult

        tool_name = args.get("tool", "")
        action = args.get("action", "")
        params = args.get("params") or {}
        page_size = max(args.get("page_size", 100), 20)  # 最小20
        max_pages = min(args.get("max_pages", 200), 500)  # 上限500

        if not tool_name or not action:
            return AgentResult(
                summary="必须指定 tool 和 action 参数",
                status="error",
                error_message="Validation: tool and action required",
                metadata={"retryable": True},
            )

        # 获取 ERP dispatcher
        dispatcher = await self._get_erp_dispatcher()
        if isinstance(dispatcher, AgentResult):
            return dispatcher

        settings = get_settings()
        semaphore = asyncio.Semaphore(
            settings.sandbox_api_concurrency,
        )

        start = _time.monotonic()

        from services.agent.erp_pagination import paginate_erp

        result = await paginate_erp(
            tool_name, action, {**params, "page_size": page_size},
            max_pages=max_pages,
            _dispatcher=dispatcher,
            _semaphore=semaphore,
        )

        elapsed = _time.monotonic() - start

        if "error" in result and not result.get("list"):
            return AgentResult(
                summary=f"翻页查询失败: {result['error']}",
                status="error",
                error_message=result["error"],
                metadata={"retryable": True},
            )

        items = result.get("list", [])
        if not items:
            return AgentResult(
                summary=f"查询结果为空（{tool_name}:{action}）",
                status="empty",
            )

        # 存 staging 文件（用户级隔离）
        from core.workspace import resolve_staging_dir

        _conv = self.conversation_id or "default"
        staging_dir = Path(resolve_staging_dir(
            settings.file_workspace_root,
            self.workspace_user_id,
            self.org_id,
            _conv,
        ))

        import pandas as _pd

        ts = int(_time.time())
        safe_tool = tool_name.replace("/", "_").replace("..", "_")
        safe_action = action.replace("/", "_").replace("..", "_")
        filename = f"{safe_tool}_{safe_action}_{ts}.parquet"
        staging_path = staging_dir / filename

        # Parquet 写入（类型/null/日期零解析问题）
        df = _pd.DataFrame(items)
        df.to_parquet(staging_path, index=False, engine="pyarrow")

        file_size_kb = staging_path.stat().st_size / 1024

        # 预览前3条
        preview = df.head(3).to_string(index=False, max_colwidth=30)

        warning = ""
        if result.get("warning"):
            warning = f"\n⚠ {result['warning']}"

        col_parts = [f"{c}({str(df[c].dtype)})" for c in df.columns]

        # 列 schema 摘要（截取前15列）
        col_schema = ", ".join(col_parts[:15])
        if len(col_parts) > 15:
            col_schema += f" (+{len(col_parts)-15}列)"

        # 注册到路径缓存
        from services.agent.file_path_cache import get_file_cache
        _cache = get_file_cache(self.conversation_id)
        # fetch_all_pages 产出：parquet 就是源文件
        _cache.register(filename, workspace=str(staging_path), parquet=str(staging_path))

        return AgentResult(
            summary=(
                f"[数据已暂存] {filename}\n"
                f"共 {len(items)} 条记录（Parquet格式，{file_size_kb:.0f}KB），"
                f"耗时 {elapsed:.1f}秒。{warning}\n"
                f"代码处理: duckdb.sql(\"SELECT * FROM 'staging/{filename}'\") | "
                f"{len(items)}行 × {len(df.columns)}列\n"
                f"[列: {col_schema}]\n\n"
                f"前3条预览：\n{preview}"
            ),
            status="success",
        )

    # 代码执行沙盒：继承自 SandboxToolMixin (sandbox_tool_mixin.py)
    # 文件操作工具 + 社交爬虫：继承自 FileToolMixin / CrawlerToolMixin (file_tool_mixin.py)
