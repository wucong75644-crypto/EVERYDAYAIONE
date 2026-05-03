"""
文件操作 + 社交爬虫工具 Mixin

从 tool_executor.py 拆出（500 行红线），承载：
- file_read / file_write / file_edit / file_list / file_search / file_info
- social_crawler

通过 Mixin 继承组合到 ToolExecutor。
依赖宿主类提供：self.user_id, self.org_id, self.conversation_id
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger


class FileToolMixin:
    """文件操作工具 Mixin"""

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
        file_read 可能返回 FileReadResult（图片多模态）— 直接透传，不包装。
        其他工具返回 AgentResult。
        """
        from core.config import get_settings
        from services.agent.agent_result import AgentResult
        from services.file_executor import FileExecutor

        settings = get_settings()
        if not settings.file_workspace_enabled:
            return AgentResult(
                summary="文件操作功能已关闭，请联系管理员启用",
                status="error",
                error_message="Feature disabled: file_workspace_enabled=false",
                metadata={"retryable": False},
            )

        executor = FileExecutor(
            workspace_root=settings.file_workspace_root,
            user_id=self.user_id,
            org_id=self.org_id,
        )

        dispatch = {
            "file_read": executor.file_read,
            "file_write": executor.file_write,
            "file_edit": executor.file_edit,
        }

        func = dispatch.get(tool_name)
        if not func:
            return AgentResult(
                summary=f"Unknown file tool: {tool_name}",
                status="error",
                error_message=f"Unknown tool: {tool_name}",
                metadata={"retryable": False},
            )

        # file_read / file_edit 的 path 参数：先查缓存翻译
        if "path" in args and tool_name in ("file_read", "file_edit"):
            from services.agent.workspace_file_handles import get_file_cache
            cached = get_file_cache(self.conversation_id).resolve(args["path"])
            if cached:
                args = {**args, "path": cached}

        try:
            result = await func(**args)
            # FileReadResult（图片多模态）直接透传，不包装
            from services.file_executor import FileReadResult
            if isinstance(result, FileReadResult):
                return result
            # 普通 str 结果包装为 AgentResult
            return AgentResult(summary=result or "", status="success")
        except PermissionError as e:
            logger.warning(f"ToolExecutor file_dispatch | tool={tool_name} | perm_error={e}")
            return AgentResult(
                summary=f"权限不足: {e}",
                status="error",
                error_message=f"PermissionError: {e}",
                metadata={"retryable": False},
            )
        except Exception as e:
            from services.file_executor import FileOperationError
            if isinstance(e, FileOperationError):
                return AgentResult(
                    summary=str(e),
                    status="error",
                    error_message=str(e),
                    metadata={"retryable": True},
                )
            logger.error(f"ToolExecutor file_dispatch | tool={tool_name} | error={e}")
            return AgentResult(
                summary=f"文件操作失败: {e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

    async def _get_or_extract_metadata(self, abs_path: str) -> Optional[Dict]:
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

    # _file_list_with_metadata 和 _file_search_with_metadata 已删除
    # file_list/search 被 code_execute 内 os.listdir/walk 替代（见 TECH_沙盒OS开放与工具精简.md）


class CrawlerToolMixin:
    """社交媒体爬虫工具 Mixin"""

    async def _social_crawler(self, args: Dict[str, Any]) -> "AgentResult":
        """爬取社交媒体平台搜索结果"""
        from core.config import get_settings
        from services.agent.agent_result import AgentResult
        from services.crawler.service import CrawlerService

        settings = get_settings()
        if not settings.crawler_enabled:
            return AgentResult(
                summary="社交媒体爬虫功能未启用，请在 .env 中设置 CRAWLER_ENABLED=true",
                status="error",
                error_message="Feature disabled: crawler_enabled=false",
                metadata={"retryable": False},
            )

        service = CrawlerService()
        if not service.is_available():
            return AgentResult(
                summary=(
                    "社交媒体爬虫未安装，请运行以下命令安装：\n"
                    "cd backend/external && git clone https://github.com/NanmiCoder/MediaCrawler.git mediacrawler\n"
                    "cd mediacrawler && python3 -m venv venv && source venv/bin/activate\n"
                    "pip install -r requirements.txt && playwright install chromium"
                ),
                status="error",
                error_message="Crawler not installed",
                metadata={"retryable": False},
            )

        platform = args.get("platform", "xhs")
        keywords_str = args.get("keywords", "")
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if not keywords:
            return AgentResult(
                summary="搜索关键词不能为空",
                status="error",
                error_message="Validation: keywords is required",
                metadata={"retryable": True},
            )

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
            return AgentResult(
                summary=f"爬取失败：{result.error}",
                status="error",
                error_message=result.error,
                metadata={"retryable": True},
            )

        return AgentResult(
            summary=service.format_for_brain(result.items),
            status="success",
        )
