"""
同步工具执行器

执行 Agent Loop / ChatHandler 工具循环中的工具。
异常不在此处 catch — 调用方统一处理并回传大脑。

拆分结构：
- tool_executor.py (本文件): 核心调度 + 通用工具
- media_tool_executor.py: 图片/视频生成 (MediaToolMixin)
- erp_tool_executor.py: ERP远程/本地调度 (ErpToolMixin)
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from services.agent.agent_result import AgentResult

from loguru import logger

from config.erp_local_tools import ERP_LOCAL_TOOLS
from config.erp_tools import ERP_SYNC_TOOLS
from config.file_tools import FILE_INFO_TOOLS
from services.agent.erp_tool_executor import ErpToolMixin
from services.handlers.mixins.credit_mixin import CreditMixin
from services.media_tool_executor import MediaToolMixin


class ToolExecutor(MediaToolMixin, ErpToolMixin, CreditMixin):
    """同步工具执行器"""

    def __init__(
        self, db, user_id: str, conversation_id: str,
        org_id: str | None = None,
        request_ctx=None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
        # 时间事实层 — 请求级 SSOT，由 ERPAgent 透传
        # 设计文档：docs/document/TECH_ERP时间准确性架构.md §6.2.4 (B16)
        self.request_ctx = request_ctx
        # schema 收集协议（复用 _pending_file_parts 模式）
        # 工具方法写入 → chat_tool_mixin 统一消费 → registry.register
        # 元素: (filename, abs_path, schema_text)
        self._pending_schemas: list[tuple[str, str, str]] = []
        self._handlers: Dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
            "get_conversation_context": self._get_conversation_context,
            "search_knowledge": self._search_knowledge,
            "social_crawler": self._social_crawler,
            "erp_api_search": self._erp_api_search,
            "code_execute": self._code_execute,
            "web_search": self._web_search,
            "generate_image": self._generate_image,
            "generate_video": self._generate_video,
            "data_query": self._data_query,
            "erp_agent": self._erp_agent,
            "erp_analyze": self._erp_analyze,
            "manage_scheduled_task": self._manage_scheduled_task,
        }
        # 注册文件操作工具
        for tool_name in FILE_INFO_TOOLS:
            self._handlers[tool_name] = self._make_file_handler(tool_name)
        # 散客不注册 ERP 工具（散客无 ERP 功能）
        if org_id is not None:
            self._handlers["fetch_all_pages"] = self._fetch_all_pages
            for tool_name in ERP_SYNC_TOOLS:
                self._handlers[tool_name] = self._make_erp_handler(tool_name)
            for tool_name in ERP_LOCAL_TOOLS:
                self._handlers[tool_name] = self._make_local_handler(tool_name)

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
        handler = self._handlers.get(tool_name)
        if not handler:
            raise ValueError(f"Unknown sync tool: {tool_name}")
        return await handler(arguments)

    # ========================================
    # 通用工具实现
    # ========================================

    async def _get_conversation_context(self, args: Dict[str, Any]) -> str:
        """获取近期对话记录（含图片 URL）"""
        from services.message_service import MessageService

        limit = min(args.get("limit", 10), 20)

        service = MessageService(self.db)
        result = await service.get_messages(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            limit=limit,
            org_id=self.org_id,
        )

        messages = result.get("messages", [])
        if not messages:
            return "当前对话暂无历史消息"

        lines = []
        for msg in reversed(messages):  # 从旧到新
            role = msg.get("role", "unknown")
            content_parts = msg.get("content", [])
            text_parts = []
            image_urls = []

            for part in content_parts:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image":
                        url = part.get("url", "")
                        if url:
                            image_urls.append(url)

            line = f"[{role}] {' '.join(text_parts)}"
            if image_urls:
                line += f" [图片: {', '.join(image_urls)}]"
            lines.append(line)

        context_text = "\n".join(lines)
        logger.debug(
            f"get_conversation_context result | conv={self.conversation_id} "
            f"| msg_count={len(messages)} | len={len(context_text)} "
            f"| preview={context_text[:500]}"
        )
        return context_text

    async def _search_knowledge(self, args: Dict[str, Any]) -> str:
        """查询 AI 知识库"""
        from services.knowledge_service import search_relevant

        query = args.get("query", "")
        if not query:
            return "查询关键词不能为空"

        items = await search_relevant(query=query, limit=5, org_id=self.org_id)
        if not items:
            return f"知识库中未找到与「{query}」相关的经验"

        lines = []
        for item in items:
            title = item.get("title", "")
            content = item.get("content", "")
            lines.append(f"- {title}: {content}")

        return "\n".join(lines)

    # ========================================
    # 互联网搜索
    # ========================================

    async def _web_search(self, args: Dict[str, Any]) -> str:
        """搜索互联网获取实时信息"""
        from services.intent_router import IntentRouter

        query = args.get("query", "").strip()
        if not query:
            return "搜索查询不能为空"

        router = IntentRouter()
        try:
            result = await router.execute_search(
                query=query, user_text=query, system_prompt=None,
            )
            return result or f"搜索「{query}」未找到相关结果"
        finally:
            await router.close()

    # ========================================
    # 数据查询工具
    # ========================================

    async def _data_query(self, args: Dict[str, Any]) -> "AgentResult":
        """查询 staging 文件或工作区数据文件"""
        from core.config import get_settings
        from services.agent.data_query_executor import DataQueryExecutor

        settings = get_settings()
        executor = DataQueryExecutor(
            user_id=self.user_id,
            org_id=self.org_id,
            conversation_id=self.conversation_id,
            workspace_root=settings.file_workspace_root,
        )

        result = await executor.execute(
            file=args.get("file", ""),
            sql=args.get("sql"),
            export=args.get("export"),
            sheet=args.get("sheet"),
        )

        # schema 收集 → chat_tool_mixin 统一消费注册到 registry
        if executor.last_file_meta:
            self._pending_schemas.append(executor.last_file_meta)

        return result

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
            f"ERPAgent dispatch | task={task[:300]} | "
            f"context_len={len(conversation_context)} | "
            f"context_preview={conversation_context[:200] if conversation_context else '(empty)'}"
        )

        # v6: budget 通过构造函数传递（替代属性注入 hack）
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
        )

        result = await agent.execute(
            task, conversation_context=conversation_context,
        )

        # 返回 AgentResult，文件通道/ask_user/display/token 由 ChatToolMixin 统一处理
        return result

    async def _erp_analyze(self, args: Dict[str, Any]) -> AgentResult:
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

        logger.info(f"ERPAgent analyze | task={task[:200]}")

        agent = ERPAgent(
            db=self.db,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            request_ctx=self.request_ctx,
        )

        return await agent.analyze(task, conversation_context=conversation_context)

    # ========================================
    # 定时任务管理（聊天内创建/查看/修改）
    # ========================================

    async def _manage_scheduled_task(self, args: Dict[str, Any]):
        """聊天内管理定时任务：返回 FormBlockResult 或文本 str

        FormBlockResult 与 AgentResult 平级，chat_tool_mixin 用 isinstance 分发：
        - FormBlockResult → content_block_add 推送表单到前端
        - str → 普通文本（列表/确认/错误）
        """
        from services.scheduler.chat_task_manager import ChatTaskManager, FormBlockResult

        if not self.org_id:
            return "此功能仅企业用户可用，请先加入企业。"

        action = (args.get("action") or "").strip()
        if not action:
            return "请指定操作：create / list / update / pause / resume / delete"

        manager = ChatTaskManager(self.db, self.user_id, self.org_id)
        result = await manager.handle(action, args)

        if result.get("type") == "form":
            return FormBlockResult(
                form=result,
                llm_hint=f"已向用户展示{result.get('title', '表单')}，等待用户确认。不要重复展示表单内容。",
            )

        return result.get("text", str(result))

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

    async def _fetch_all_pages(self, args: Dict[str, Any]) -> str:
        """包装任意 erp_* 远程查询工具，自动翻页拉取全部数据并存 staging"""
        import asyncio
        import time as _time
        from pathlib import Path

        from core.config import get_settings

        tool_name = args.get("tool", "")
        action = args.get("action", "")
        params = args.get("params") or {}
        page_size = max(args.get("page_size", 100), 20)  # 最小20
        max_pages = min(args.get("max_pages", 200), 500)  # 上限500

        if not tool_name or not action:
            return "❌ 必须指定 tool 和 action 参数"

        # 获取 ERP dispatcher
        dispatcher = await self._get_erp_dispatcher()
        if isinstance(dispatcher, str):
            return dispatcher  # 错误信息

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
            return f"❌ 翻页查询失败: {result['error']}"

        items = result.get("list", [])
        if not items:
            return f"查询结果为空（{tool_name}:{action}）"

        # 存 staging 文件（用户级隔离）
        from core.workspace import resolve_staging_dir, resolve_staging_rel_path

        _conv = self.conversation_id or "default"
        staging_dir = Path(resolve_staging_dir(
            settings.file_workspace_root, self.user_id, self.org_id, _conv,
        ))
        staging_dir.mkdir(parents=True, exist_ok=True)

        import pandas as _pd

        ts = int(_time.time())
        safe_tool = tool_name.replace("/", "_").replace("..", "_")
        safe_action = action.replace("/", "_").replace("..", "_")
        filename = f"{safe_tool}_{safe_action}_{ts}.parquet"
        staging_path = staging_dir / filename

        # Parquet 写入（类型/null/日期零解析问题）
        df = _pd.DataFrame(items)
        df.to_parquet(staging_path, index=False, engine="pyarrow")

        rel_path = resolve_staging_rel_path(conversation_id=_conv, filename=filename)
        file_size_kb = staging_path.stat().st_size / 1024

        # 预览前3条
        preview = df.head(3).to_string(index=False, max_colwidth=30)

        warning = ""
        if result.get("warning"):
            warning = f"\n⚠ {result['warning']}"

        # schema 收集：列名+类型+行数
        col_parts = [f"{c}({str(df[c].dtype)})" for c in df.columns]
        schema_text = (
            f"{filename} | {len(items):,}行 × {len(df.columns)}列\n"
            f"列: {', '.join(col_parts)}"
        )
        self._pending_schemas.append((filename, str(staging_path), schema_text))

        return (
            f"[数据已暂存] {rel_path}\n"
            f"共 {len(items)} 条记录（Parquet格式，{file_size_kb:.0f}KB），"
            f"耗时 {elapsed:.1f}秒。{warning}\n"
            f"如需处理请调 data_query，"
            f"用 data_query(file=\"{filename}\", sql=\"SELECT ... FROM data\") 查询。\n\n"
            f"前3条预览：\n{preview}"
        )

    # ========================================
    # 代码执行沙盒
    # ========================================

    async def _code_execute(self, args: Dict[str, Any]) -> str:
        """在安全沙盒中执行 Python 代码"""
        import asyncio
        import time as _time

        from core.config import get_settings
        from services.sandbox.functions import (
            build_sandbox_executor,
            compute_code_hash,
        )

        settings = get_settings()
        if not settings.sandbox_enabled:
            return "代码执行功能已关闭，请联系管理员启用"

        code = args.get("code", "")
        description = args.get("description", "")
        if not code:
            return "代码不能为空"

        start_ms = int(_time.monotonic() * 1000)
        status = "success"
        result = ""

        try:
            # sandbox 超时受 budget 约束（防止 sandbox 120s 但 budget 只剩 30s）
            _timeout = settings.sandbox_timeout
            _budget = getattr(self, "_budget", None)
            if _budget is not None and hasattr(_budget, "remaining"):
                _timeout = min(_timeout, max(_budget.remaining, 5.0))

            from services.sandbox.kernel_manager import get_kernel_manager
            executor = build_sandbox_executor(
                timeout=_timeout,
                max_result_chars=settings.sandbox_max_result_chars,
                user_id=self.user_id,
                org_id=self.org_id,
                conversation_id=self.conversation_id,
                kernel_manager=get_kernel_manager(),
            )
            result = await executor.execute(code, description)

            # 透传图片尺寸（沙盒读取的 PIL 宽高 → chat_handler 构建 image block）
            if hasattr(executor, "_image_dims") and executor._image_dims:
                if not hasattr(self, "_image_dims"):
                    self._image_dims = {}
                self._image_dims.update(executor._image_dims)

            # 判断执行状态
            if result.startswith("❌"):
                status = "failed"
            elif result.startswith("⏱"):
                status = "timeout"

            return result
        except Exception as e:
            status = "failed"
            result = f"沙盒执行异常: {e}"
            return result
        finally:
            # Fire-and-forget: 记录执行指标
            elapsed_ms = int(_time.monotonic() * 1000) - start_ms
            self._record_sandbox_metric(
                description=description,
                code=code,
                status=status,
                elapsed_ms=elapsed_ms,
                result_length=len(result),
            )

            # 失败时触发知识提取
            if status == "failed":
                self._record_sandbox_knowledge(description, result)

    def _record_sandbox_metric(
        self,
        description: str,
        code: str,
        status: str,
        elapsed_ms: int,
        result_length: int,
    ) -> None:
        """Fire-and-forget 记录沙盒执行指标"""
        import asyncio

        from services.sandbox.functions import compute_code_hash

        try:
            from services.knowledge_metrics import record_metric
            asyncio.create_task(
                record_metric(
                    task_type="sandbox_execution",
                    model_id="python_sandbox",
                    status=status,
                    cost_time_ms=elapsed_ms,
                    params={
                        "description": description,
                        "code_hash": compute_code_hash(code),
                        "code_length": len(code),
                        "result_length": result_length,
                    },
                    user_id=self.user_id,
                    org_id=self.org_id,
                )
            )
        except Exception as e:
            logger.debug(f"Sandbox metric recording skipped | error={e}")

    @staticmethod
    def _record_sandbox_knowledge(description: str, error_result: str) -> None:
        """Fire-and-forget 记录沙盒失败知识"""
        import asyncio

        try:
            from services.knowledge_extractor import extract_and_save
            asyncio.create_task(
                extract_and_save(
                    task_type="sandbox_execution",
                    model_id="python_sandbox",
                    status="failed",
                    error_message=f"[{description}] {error_result[:500]}",
                )
            )
        except Exception as e:
            logger.debug(f"Sandbox knowledge recording skipped | error={e}")

    # ========================================
    # 文件操作工具
    # ========================================

    def _make_file_handler(
        self, tool_name: str,
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        """为指定文件工具创建handler

        file_read 可能返回 FileReadResult（图片多模态），
        由 ChatHandler 工具结果处理逻辑识别并注入 image_url。
        """
        async def handler(args: Dict[str, Any]) -> Any:
            return await self._file_dispatch(tool_name, args)
        return handler

    async def _file_dispatch(
        self, tool_name: str, args: Dict[str, Any],
    ) -> Any:
        """文件工具统一调度（直接用文件名/相对路径）

        file_list 和 file_search 返回结果自动附带文件元数据。
        元数据通过 per-message 缓存（_metadata_cache）避免重复提取。
        file_read 可能返回 FileReadResult（图片多模态）。
        """
        from core.config import get_settings
        from services.file_executor import FileExecutor

        settings = get_settings()
        if not settings.file_workspace_enabled:
            return "文件操作功能已关闭，请联系管理员启用"

        executor = FileExecutor(
            workspace_root=settings.file_workspace_root,
            user_id=self.user_id,
            org_id=self.org_id,
        )

        # ── file_list：格式化 + 元数据 ──
        if tool_name == "file_list":
            try:
                return await self._file_list_with_metadata(executor, args)
            except PermissionError as e:
                return f"权限不足: {e}"
            except Exception as e:
                logger.error(f"ToolExecutor file_list | error={e}")
                return f"文件操作失败: {e}"

        # ── file_search：搜索 + 元数据 ──
        if tool_name == "file_search":
            try:
                return await self._file_search_with_metadata(executor, args)
            except PermissionError as e:
                return f"权限不足: {e}"
            except Exception as e:
                logger.error(f"ToolExecutor file_search | error={e}")
                return f"文件操作失败: {e}"

        dispatch = {
            "file_read": executor.file_read,
            "file_write": executor.file_write,
            "file_edit": executor.file_edit,
        }

        func = dispatch.get(tool_name)
        if not func:
            return f"Unknown file tool: {tool_name}"

        # file_read / file_edit 的 path 参数：先查缓存翻译
        if "path" in args and tool_name in ("file_read", "file_edit"):
            from services.agent.workspace_file_handles import get_file_cache
            cached = get_file_cache(self.conversation_id).resolve(args["path"])
            if cached:
                args = {**args, "path": cached}

        try:
            return await func(**args)
        except PermissionError as e:
            logger.warning(f"ToolExecutor file_dispatch | tool={tool_name} | perm_error={e}")
            return f"权限不足: {e}"
        except Exception as e:
            logger.error(f"ToolExecutor file_dispatch | tool={tool_name} | error={e}")
            return f"文件操作失败: {e}"

    async def _get_or_extract_metadata(self, abs_path: str) -> 'Optional[Dict]':
        """获取文件元数据（带 per-message 缓存 + 线程池执行）

        缓存挂在 ToolExecutor 实例上（_metadata_cache），
        工具循环结束后自动 GC。
        IO 阻塞操作（openpyxl 读文件等）在线程池中执行，不阻塞 event loop。
        """
        import os
        from services.file_metadata_extractor import extract_file_metadata

        cache = getattr(self, "_metadata_cache", None)
        if cache is None:
            cache = {}
            self._metadata_cache = cache

        # 缓存命中：路径 + mtime 匹配
        cached = cache.get(abs_path)
        if cached is not None:
            try:
                current_mtime = os.path.getmtime(abs_path)
                if cached[0] == current_mtime:
                    return cached[1]
            except OSError:
                pass

        # 在线程池中提取（防阻塞 event loop）
        try:
            loop = asyncio.get_running_loop()
            meta = await asyncio.wait_for(
                loop.run_in_executor(None, extract_file_metadata, abs_path),
                timeout=3.0,
            )
            mtime = os.path.getmtime(abs_path)
            cache[abs_path] = (mtime, meta)
            return meta
        except Exception:
            return None

    async def _file_list_with_metadata(
        self, executor: 'Any', args: Dict[str, Any],
    ) -> str:
        """file_list + 元数据（每个文件附带结构信息和读取命令）"""
        from services.file_metadata_extractor import format_file_metadata_line

        data = await executor.file_list_entries(**args)

        if data["error"]:
            return data["error"]
        if not data["dirs"] and not data["files"]:
            return f"目录为空: {data['path']}"

        total = len(data["dirs"]) + len(data["files"])
        lines = [f"目录: {data['path']} | 共 {total} 项"]
        lines.append("─" * 60)
        for d in data["dirs"]:
            lines.append(f"  [目录] {d['name']}/\t\t{d['modified']}")

        # 文件：注册路径缓存 + 提取元数据（最多 _MAX_METADATA 个，防慢）
        from services.agent.workspace_file_handles import get_file_cache
        file_cache = get_file_cache(self.conversation_id)

        _MAX_METADATA = 5
        for i, f in enumerate(data["files"]):
            file_cache.register(f["name"], f["abs_path"])
            if i < _MAX_METADATA:
                meta = await self._get_or_extract_metadata(f["abs_path"])
                line = format_file_metadata_line(
                    f["name"], f["abs_path"], f["size"], meta,
                )
            else:
                size_str = executor._format_size(f["size"])
                line = f"  {f['name']}\t{size_str}"
            lines.append(line)

        if data["truncated"]:
            lines.append(f"\n已达显示上限，部分条目未显示")

        return "\n".join(lines)

    async def _file_search_with_metadata(
        self, executor: 'Any', args: Dict[str, Any],
    ) -> str:
        """file_search + 元数据（搜到的文件附带结构信息）"""
        import re
        from services.file_metadata_extractor import format_file_metadata_line

        # 先执行原始搜索
        raw_result = await executor.file_search(**args)

        # 如果搜索无结果或报错，直接返回
        if "未找到" in raw_result or not raw_result.strip():
            return raw_result

        # 从搜索结果中提取文件路径，对前 3 个文件追加元数据
        lines = raw_result.split("\n")
        enhanced_lines = []
        metadata_count = 0
        _MAX_SEARCH_METADATA = 3

        from services.agent.workspace_file_handles import get_file_cache
        file_cache = get_file_cache(self.conversation_id)

        for line in lines:
            # 搜索结果格式：  [文件] 相对路径  或  [文件] 相对路径:行号 | 预览
            match = re.match(r"\s+\[文件\]\s+(\S+?)(?::\d+\s*\|.*)?$", line)
            if match and metadata_count < _MAX_SEARCH_METADATA:
                rel_path = match.group(1)
                try:
                    target = executor.resolve_safe_path(rel_path)
                    if target.is_file():
                        file_cache.register(target.name, str(target))
                        meta = await self._get_or_extract_metadata(str(target))
                        if meta:
                            enhanced_line = format_file_metadata_line(
                                target.name, str(target), target.stat().st_size, meta,
                            )
                            enhanced_lines.append(enhanced_line)
                            metadata_count += 1
                            continue
                except Exception:
                    pass

            enhanced_lines.append(line)

        return "\n".join(enhanced_lines)

    # ========================================
    # 社交媒体爬虫工具
    # ========================================

    async def _social_crawler(self, args: Dict[str, Any]) -> str:
        """爬取社交媒体平台搜索结果"""
        from core.config import get_settings
        from services.crawler.service import CrawlerService

        settings = get_settings()
        if not settings.crawler_enabled:
            return "社交媒体爬虫功能未启用，请在 .env 中设置 CRAWLER_ENABLED=true"

        service = CrawlerService()
        if not service.is_available():
            return (
                "社交媒体爬虫未安装，请运行以下命令安装：\n"
                "cd backend/external && git clone https://github.com/NanmiCoder/MediaCrawler.git mediacrawler\n"
                "cd mediacrawler && python3 -m venv venv && source venv/bin/activate\n"
                "pip install -r requirements.txt && playwright install chromium"
            )

        platform = args.get("platform", "xhs")
        keywords_str = args.get("keywords", "")
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if not keywords:
            return "搜索关键词不能为空"

        max_results = min(args.get("max_results", 10), 30)
        crawl_type = args.get("crawl_type", "search")

        logger.info(
            f"ToolExecutor social_crawler | platform={platform} "
            f"| keywords={keywords_str} | max={max_results}"
        )

        result = await service.execute(
            platform=platform,
            keywords=keywords,
            max_notes=max_results,
            crawl_type=crawl_type,
        )

        if result.error:
            return f"爬取失败：{result.error}"

        return service.format_for_brain(result.items)
