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
from services.erp_tool_executor import ErpToolMixin
from services.handlers.mixins.credit_mixin import CreditMixin
from services.media_tool_executor import MediaToolMixin


class ToolExecutor(MediaToolMixin, ErpToolMixin, CreditMixin):
    """同步工具执行器"""

    def __init__(self, db, user_id: str, conversation_id: str, org_id: str | None = None) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.org_id = org_id
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
        from services.erp_agent import ERPAgent

        query = args.get("query", "").strip()
        if not query:
            return "请输入 ERP 相关问题"

        agent = ERPAgent(
            db=self.db,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            task_id=getattr(self, "_task_id", None),
        )

        # 传入父 Agent 的 messages 上下文（如果有）
        parent_messages = getattr(self, "_parent_messages", None)

        result = await agent.execute(query, parent_messages=parent_messages)

        # 记录 token 消耗（供 ChatHandler 统一扣费）
        self._erp_agent_tokens = getattr(self, "_erp_agent_tokens", 0)
        self._erp_agent_tokens += result.tokens_used

        return result.text

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

        # 获取 ERP dispatcher（企业用户才有，散客无 ERP 沙盒函数）
        erp_dispatcher = None
        if self.org_id:
            dispatcher = await self._get_erp_dispatcher()
            erp_dispatcher = dispatcher if not isinstance(dispatcher, str) else None

        start_ms = int(_time.monotonic() * 1000)
        status = "success"
        result = ""

        try:
            executor = build_sandbox_executor(
                dispatcher=erp_dispatcher,
                api_concurrency=settings.sandbox_api_concurrency,
                timeout=settings.sandbox_timeout,
                max_result_chars=settings.sandbox_max_result_chars,
                max_pages=settings.sandbox_max_pages,
                user_id=self.user_id,
                org_id=self.org_id,
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
            if erp_dispatcher is not None:
                await erp_dispatcher.close()

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
