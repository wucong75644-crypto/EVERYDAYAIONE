"""
同步工具执行器

执行 Agent Loop / ChatHandler 工具循环中的工具。
异常不在此处 catch — 调用方统一处理并回传大脑。

拆分结构：
- tool_executor.py (本文件): 核心调度 + 通用工具
- media_tool_executor.py: 图片/视频生成 (MediaToolMixin)
- erp_tool_executor.py: ERP远程/本地调度 (ErpToolMixin)
"""

from typing import Any, Callable, Coroutine, Dict

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
        self._handlers: Dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
            "get_conversation_context": self._get_conversation_context,
            "search_knowledge": self._search_knowledge,
            "social_crawler": self._social_crawler,
            "erp_api_search": self._erp_api_search,
            "code_execute": self._code_execute,
            "web_search": self._web_search,
            "generate_image": self._generate_image,
            "generate_video": self._generate_video,
            "erp_agent": self._erp_agent,
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

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """执行同步工具，返回结果文本

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
        from services.sandbox.functions import sandbox_web_search
        query = args.get("query", "").strip()
        if not query:
            return "搜索查询不能为空"
        return await sandbox_web_search(query)

    # ========================================
    # ERP Agent（独立 Agent 作为工具调用）
    # ========================================

    async def _erp_agent(self, args: Dict[str, Any]) -> str:
        """ERP 独立 Agent：接收用户问题，内部运行工具循环，返回结论"""
        from services.agent.erp_agent import ERPAgent

        query = args.get("query", "").strip()
        if not query:
            return "请输入 ERP 相关问题"

        agent = ERPAgent(
            db=self.db,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            task_id=getattr(self, "_task_id", None),
            request_ctx=self.request_ctx,  # 时间事实层透传 (B16)
        )

        # 传递父 budget（fork 机制：子消耗回写父）
        _parent_budget = getattr(self, "_budget", None)
        if _parent_budget is not None:
            agent._budget = _parent_budget

        # 传入父 Agent 的 messages 上下文（如果有）
        parent_messages = getattr(self, "_parent_messages", None)

        result = await agent.execute(query, parent_messages=parent_messages)

        # 记录 token 消耗（供 ChatHandler 统一扣费）
        self._erp_agent_tokens = getattr(self, "_erp_agent_tokens", 0)
        self._erp_agent_tokens += result.tokens_used

        # 提取 ERP Agent 结果中的 [FILE] 标记 → 透传到主 Agent 的 _pending_file_parts
        # ERP Agent 内部 code_execute 生成的文件会产生 [FILE] 标记，
        # 需要在这里提取，否则会被 LLM 改写成 markdown 链接丢失
        import re
        _file_re = re.compile(r'\[FILE\](.*?)\|(.*?)\|(.*?)\|(.*?)\[/FILE\]')
        _file_matches = _file_re.findall(result.text)
        if _file_matches:
            from schemas.message import FilePart
            _pending = getattr(self, "_pending_file_parts", None)
            if _pending is not None:
                for url, name, mime_type, size in _file_matches:
                    _pending.append(FilePart(
                        url=url, name=name, mime_type=mime_type, size=int(size),
                    ))
            # 从文本中移除 [FILE] 标记（防止 LLM 改写）
            result_text = _file_re.sub(
                lambda m: f"📎 文件已生成: {m.group(2)}", result.text,
            )
        else:
            result_text = result.text

        # 保存原始展示文本 + 文件信息（供 ChatHandler 推送 content_block_add）
        self._erp_display_text = result_text
        logger.debug(
            f"ERP display text set | len={len(result_text)} | "
            f"files={len(_file_matches) if _file_matches else 0}"
        )
        self._erp_display_files = [
            {"url": url, "name": name, "mime_type": mime_type, "size": int(size)}
            for url, name, mime_type, size in _file_matches
        ] if _file_matches else []

        # erp_agent 结果截断（给主 LLM 看的精简版）
        from services.agent.tool_result_envelope import wrap_erp_agent_result
        return wrap_erp_agent_result(result_text)

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
        import json
        import time as _time
        from pathlib import Path

        from core.config import get_settings
        from services.sandbox.functions import erp_query_all

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

        # 复用 erp_query_all 的翻页逻辑
        result = await erp_query_all(
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

        # 存 staging 文件
        staging_dir = Path(settings.file_workspace_root) / "staging" / (
            self.conversation_id or "default"
        )
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

        rel_path = f"staging/{self.conversation_id or 'default'}/{filename}"
        file_size_kb = staging_path.stat().st_size / 1024

        # 预览前3条
        preview = df.head(3).to_string(index=False, max_colwidth=30)

        warning = ""
        if result.get("warning"):
            warning = f"\n⚠ {result['warning']}"

        return (
            f"[数据已暂存] {rel_path}\n"
            f"共 {len(items)} 条记录（Parquet格式，{file_size_kb:.0f}KB），"
            f"耗时 {elapsed:.1f}秒。{warning}\n"
            f"如需处理请调 code_execute，"
            f"用 df = pd.read_parquet(STAGING_DIR + '/{filename}') 读取。\n\n"
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

            executor = build_sandbox_executor(
                timeout=_timeout,
                max_result_chars=settings.sandbox_max_result_chars,
                user_id=self.user_id,
                org_id=self.org_id,
                conversation_id=self.conversation_id,
            )
            result = await executor.execute(code, description)

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
    ) -> Callable[..., Coroutine[Any, Any, str]]:
        """为指定文件工具创建handler"""
        async def handler(args: Dict[str, Any]) -> str:
            return await self._file_dispatch(tool_name, args)
        return handler

    async def _file_dispatch(
        self, tool_name: str, args: Dict[str, Any],
    ) -> str:
        """文件工具统一调度"""
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

        dispatch = {
            "file_read": executor.file_read,
            "file_write": executor.file_write,
            "file_list": executor.file_list,
            "file_search": executor.file_search,
            "file_info": executor.file_info,
        }

        func = dispatch.get(tool_name)
        if not func:
            return f"Unknown file tool: {tool_name}"

        try:
            return await func(**args)
        except PermissionError as e:
            logger.warning(f"ToolExecutor file_dispatch | tool={tool_name} | perm_error={e}")
            return f"权限不足: {e}"
        except Exception as e:
            logger.error(f"ToolExecutor file_dispatch | tool={tool_name} | error={e}")
            return f"文件操作失败: {e}"

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
