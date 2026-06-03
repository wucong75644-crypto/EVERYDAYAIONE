"""
文件操作 + 社交爬虫工具 Mixin（聚合入口）

对齐 Claude 模式：
- file_search: 搜索/列目录/定位文件 → 返回 WORKSPACE_DIR 路径；
               命中单张图片时直接返回 FileReadResult(type=image) 多模态注入视觉模型
- file_analyze: Excel/CSV 结构化读取转 Parquet
- file_delete / restore_file: 删除/恢复文件（拆到 file_delete_mixin.py）
- social_crawler: 社交媒体爬虫（拆到 crawler_tool_mixin.py）

通过 Mixin 继承组合到 ToolExecutor。
依赖宿主类提供：self.user_id, self.org_id, self.conversation_id
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict

from loguru import logger

from services.agent.crawler_tool_mixin import CrawlerToolMixin
from services.agent.file_delete_mixin import FileDeleteMixin


__all__ = ["FileToolMixin", "CrawlerToolMixin"]


class FileToolMixin(FileDeleteMixin):
    """文件操作工具 Mixin（搜索 + 分析 + 删除恢复继承自 FileDeleteMixin）"""

    def _make_file_handler(
        self, tool_name: str,
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        """为指定文件工具创建 handler"""
        async def handler(args: Dict[str, Any]) -> Any:
            return await self._file_dispatch(tool_name, args)
        return handler

    async def _file_dispatch(
        self, tool_name: str, args: Dict[str, Any],
    ) -> Any:
        """文件工具统一调度"""
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

        try:
            if tool_name == "file_search":
                return await self._file_search(executor, args, settings)
            if tool_name == "file_analyze":
                return await self._file_analyze(executor, args, settings)
            if tool_name == "file_delete":
                return await self._file_delete(executor, args, settings)
            if tool_name == "restore_file":
                return await self._restore_file(executor, args, settings)
        except PermissionError as e:
            return AgentResult(
                summary=f"权限不足: {e}", status="error",
                error_message=f"PermissionError: {e}",
                metadata={"retryable": False},
            )
        except Exception as e:
            logger.error(f"ToolExecutor {tool_name} | error={e}")
            return AgentResult(
                summary=f"文件操作失败: {e}", status="error",
                error_message=str(e), metadata={"retryable": False},
            )

        return AgentResult(
            summary=f"Unknown file tool: {tool_name}",
            status="error",
            error_message=f"Unknown tool: {tool_name}",
            metadata={"retryable": False},
        )

    # ================================================================
    # file_search：搜索/列目录/定位文件（纯搜索，不做转换）
    # ================================================================

    async def _file_search(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """file_search 实现：搜索/列目录/定位文件，返回路径。不做 Parquet 转换。"""
        from services.agent.agent_result import AgentResult

        path = args.get("path", "")
        keyword = args.get("keyword", "")
        file_pattern = args.get("file_pattern", "")

        # ── 判断模式：指向单文件 vs 列目录/搜索 ──
        if path and not keyword and not file_pattern:
            try:
                target = executor.resolve_safe_path(path)
            except Exception as e:
                return AgentResult(
                    summary=f"路径无效: {path} ({e})",
                    status="error",
                    error_message=str(e),
                    metadata={"retryable": True},
                )
            if target.is_file():
                return await self._describe_single_file(executor, str(target))
            if target.is_dir():
                return await self._list_directory(executor, args)
            return AgentResult(
                summary=f"未找到文件或目录: {path}",
                status="error",
                error_message=f"Path not found: {path}",
                metadata={"retryable": True},
            )

        # ── 有 keyword/file_pattern → 搜索模式 ──
        if keyword or file_pattern:
            return await self._search_files(executor, args)

        # ── 无参数 → 列出根目录 ──
        return await self._list_directory(executor, args)

    async def _list_directory(
        self, executor: Any, args: Dict[str, Any],
    ) -> Any:
        """列出目录内容，返回文件列表和 WORKSPACE_DIR 路径，注册到共享缓存"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        data = await executor.file_list_entries(**{
            k: v for k, v in args.items() if k in ("path", "show_hidden")
        })

        if data["error"]:
            return AgentResult(
                summary=data["error"], status="error",
                error_message=data["error"], metadata={"retryable": False},
            )
        if not data["dirs"] and not data["files"]:
            return AgentResult(summary=f"目录为空: {data['path']}", status="empty")

        total = len(data["dirs"]) + len(data["files"])
        lines = [f"目录: {data['path']} | 共 {total} 项"]
        lines.append("─" * 50)

        cache = get_file_cache(self.conversation_id)

        for d in data["dirs"]:
            lines.append(f"  [目录] {d['name']}/")

        for f in data["files"]:
            size_str = executor._format_size(f["size"])
            try:
                rel_path = str(Path(f["abs_path"]).relative_to(
                    Path(executor.workspace_root)
                ))
            except ValueError:
                rel_path = f["name"]
            lines.append(f"  {rel_path}  ({size_str})")
            cache.register(f["name"], workspace=f["abs_path"])
            cache.register(rel_path, workspace=f["abs_path"])

        if data.get("truncated"):
            lines.append("\n已达显示上限，部分条目未显示")

        lines.append("")
        lines.append("在 code_execute 中用 get_file('文件名') 获取路径")

        return AgentResult(summary="\n".join(lines), status="success")

    async def _search_files(
        self, executor: Any, args: Dict[str, Any],
    ) -> Any:
        """搜索文件，返回结果列表，注册到共享缓存"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        raw_result = await executor.file_search(**{
            k: v for k, v in args.items()
            if k in ("keyword", "path", "search_content", "file_pattern")
        })

        if "未找到" in raw_result or not raw_result.strip():
            return AgentResult(summary=raw_result or "未找到匹配文件", status="empty")

        # 从搜索结果中提取文件路径并注册到缓存，收集编号
        cache = get_file_cache(self.conversation_id)
        _file_re = re.compile(r"\s+\[文件\]\s+(\S+)")
        for line in raw_result.split("\n"):
            m = _file_re.match(line)
            if m:
                rel_path = m.group(1).split(":")[0]
                try:
                    target = executor.resolve_safe_path(rel_path)
                    if target.is_file():
                        cache.register(target.name, workspace=str(target))
                        cache.register(rel_path, workspace=str(target))
                except Exception:
                    pass

        lines = [raw_result]
        lines.append("")
        lines.append("在 code_execute 中用 get_file('文件名') 获取路径")

        return AgentResult(summary="\n".join(lines), status="success")

    # ================================================================
    # file_analyze：数据文件结构读取（Excel/CSV → prescan → Parquet）
    # ================================================================

    _ANALYZE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}

    async def _file_analyze(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """读取 Excel/CSV 文件结构，自动转 Parquet 缓存，返回元数据。"""
        import time
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache
        from services.agent.data_query_cache import ensure_parquet_cache
        from services.agent.file_meta import read_file_meta, format_file_view
        from core.workspace import resolve_staging_dir

        path = args.get("path", "")
        if not path:
            return AgentResult(
                summary="请提供文件路径", status="error",
                error_message="path is required",
                metadata={"retryable": True},
            )

        # 路径解析：缓存(workspace) → resolve_safe_path
        cache = get_file_cache(self.conversation_id)
        abs_path = cache.resolve(path, usage="analyze")
        if not abs_path:
            try:
                target = executor.resolve_safe_path(path)
                abs_path = str(target)
            except Exception as e:
                return AgentResult(
                    summary=f"文件不存在: {path}",
                    status="error",
                    error_message=str(e),
                    metadata={"retryable": True},
                )

        if not os.path.isfile(abs_path):
            return AgentResult(
                summary=f"文件不存在: {path}",
                status="error",
                error_message=f"Not a file: {abs_path}",
                metadata={"retryable": True},
            )

        # 扩展名检查
        ext = ("." + abs_path.rsplit(".", 1)[-1].lower()) if "." in abs_path else ""
        if ext not in self._ANALYZE_EXTENSIONS:
            return AgentResult(
                summary=f"file_analyze 仅支持 Excel/CSV 文件，当前文件类型: {ext}",
                status="error",
                error_message=f"Unsupported extension: {ext}",
                metadata={"retryable": False},
            )

        # prescan → parquet 转换
        start = time.monotonic()
        staging_dir = resolve_staging_dir(
            settings.file_workspace_root,
            self.user_id,
            getattr(self, "org_id", None),
            self.conversation_id,
        )
        # 确保 cache 有 staging_dir（用户没上传文件时 chat_context_mixin 不会设置）
        if not cache._staging_dir:
            cache.set_staging_dir(staging_dir)

        try:
            cache_path, sheet_names = await ensure_parquet_cache(
                abs_path, None, staging_dir,
            )
        except ValueError as e:
            # 空文件等
            return AgentResult(
                summary=str(e), status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )
        except Exception as e:
            return AgentResult(
                summary=f"文件解析失败: {e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

        elapsed = round(time.monotonic() - start, 2)

        # 读取元数据 → 格式化
        meta = read_file_meta(cache_path)
        file_view = format_file_view(meta) if meta else f"文件已转为 Parquet: {cache_path}"

        # 注册到路径缓存：workspace + parquet 分开写
        name = Path(abs_path).name
        cache.register(name, workspace=abs_path)
        try:
            rel_path = str(Path(abs_path).relative_to(Path(executor.workspace_root)))
            cache.register(rel_path, workspace=abs_path)
        except ValueError:
            pass
        # 设置 parquet 路径（后续 get_file usage="code" 返回 parquet）
        cache.set_parquet(name, cache_path)
        # 标记已分析（跨轮持久，驱动下一轮 <attachments> status 切换为"已分析"）
        cache.set_analyzed(name, True)

        # 构建返回内容
        # 删除"## 后续操作"重复段 — get_file/duckdb 用法已在 code_execute 工具描述里
        lines = [file_view]
        if sheet_names and len(sheet_names) > 1:
            lines.append("")
            lines.append(f"Sheet 列表: {', '.join(sheet_names)}")

        logger.info(
            f"file_analyze OK | {name} | "
            f"{meta.summary.get('row_count', '?') if meta else '?'}×"
            f"{meta.summary.get('col_count', '?') if meta else '?'} | "
            f"{elapsed}s"
        )

        return AgentResult(summary="\n".join(lines), status="success")

    async def _describe_single_file(
        self, executor: Any, abs_path: str,
    ) -> Any:
        """返回单个文件的基本信息和 WORKSPACE_DIR 路径，注册到共享缓存。

        命中图片时返回 FileReadResult(type="image")，让 chat_handler 在下一轮
        把 image_url 多模态块注入 messages —— 多模态模型直接看到图，
        无需再走 file_read（P1 file_search 多模态化）。
        """
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        name = Path(abs_path).name
        size = os.path.getsize(abs_path)
        size_str = self._fmt_size(size)

        try:
            rel_path = str(Path(abs_path).relative_to(Path(executor.workspace_root)))
        except ValueError:
            rel_path = name

        # 注册到共享缓存拿编号
        cache = get_file_cache(self.conversation_id)
        cache.register(name, workspace=abs_path)
        cache.register(rel_path, workspace=abs_path)

        # ── 图片：返回 FileReadResult(type="image") 走多模态注入 ──
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        _IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        if ext in _IMG_EXTS:
            from schemas.multimodal import FileReadResult
            cdn_url = executor.get_cdn_url(rel_path) if hasattr(executor, "get_cdn_url") else ""
            if cdn_url:
                return FileReadResult(
                    type="image",
                    text=f"{name} ({size_str}) — 图片已注入视觉，可直接观察。",
                    image_url=cdn_url,
                )
            # OSS URL 拿不到时退回文本结果（极少见），保持原行为
            logger.warning(f"file_search image | no CDN URL for {abs_path}")

        lines = [
            f"{name} ({size_str})",
            "",
            f"在 code_execute 中读取：path = get_file('{name}')",
        ]

        return AgentResult(summary="\n".join(lines), status="success")

    # ================================================================
    # 工具函数
    # ================================================================

    @staticmethod
    def _fmt_size(size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
