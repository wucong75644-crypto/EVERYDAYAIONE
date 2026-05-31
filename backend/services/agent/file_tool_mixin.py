"""
文件操作 + 社交爬虫工具 Mixin

对齐 Claude 模式：
- file_search: 搜索/列目录/定位文件 → 返回 WORKSPACE_DIR 路径
- file_read: 仅图片视觉（多模态）
- restore_file: 从 staging 备份恢复（精确文件名匹配，不依赖 registry）

通过 Mixin 继承组合到 ToolExecutor。
依赖宿主类提供：self.user_id, self.org_id, self.conversation_id
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger


class FileToolMixin:
    """文件操作工具 Mixin"""

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
            if tool_name == "file_read":
                return await self._file_read_image(executor, args)
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
        """返回单个文件的基本信息和 WORKSPACE_DIR 路径，注册到共享缓存"""
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

        lines = [
            f"{name} ({size_str})",
            "",
            f"在 code_execute 中读取：path = get_file('{name}')",
        ]

        return AgentResult(summary="\n".join(lines), status="success")

    # ================================================================
    # file_delete：从缓存取路径 + 物理删除 + 记录 deleted_files
    # ================================================================

    async def _file_delete(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """file_delete：从共享缓存取精确路径，执行删除并记录到 deleted_files 表。

        tool_confirm 弹窗确认在 chat_tool_mixin 的 DANGEROUS 级别自动处理，
        执行到这里时用户已经点了确认。
        """
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        files = args.get("files") or []
        if isinstance(files, str):
            files = [files]
        if not files:
            return AgentResult(
                summary="未指定要删除的文件",
                status="error",
                error_message="files is empty",
                metadata={"retryable": True},
            )

        cache = get_file_cache(self.conversation_id)
        ws_root = str(Path(settings.file_workspace_root).resolve())

        deleted = []
        skipped = []
        for name in files:
            abs_path = cache.resolve(name, usage="delete")
            if not abs_path:
                # 缓存没有 → 尝试直接 resolve
                try:
                    target = executor.resolve_safe_path(name)
                    if target.is_file():
                        abs_path = str(target)
                except Exception:
                    pass
            if not abs_path or not os.path.isfile(abs_path):
                skipped.append(name)
                continue

            os.remove(abs_path)
            deleted.append((name, abs_path))
            logger.info(f"file_delete | path={name} | resolved={abs_path}")

        # 记录到 deleted_files 表（fire-and-forget）
        if deleted:
            deleted_meta = [
                {"raw": name, "resolved": ap} for name, ap in deleted
            ]
            self._record_deleted_files(deleted_meta)

        # 构建回复
        lines = []
        if deleted:
            lines.append(f"已删除 {len(deleted)} 个文件：")
            for name, _ in deleted:
                lines.append(f"  ✓ {name}")
        if skipped:
            lines.append(f"跳过 {len(skipped)} 个（不存在或未找到）：")
            for name in skipped:
                lines.append(f"  ✗ {name}")
        if not deleted and not skipped:
            lines.append("未执行任何删除操作")

        status = "success" if deleted else "error"
        return AgentResult(summary="\n".join(lines), status=status)

    # ================================================================
    # file_read：仅图片视觉
    # ================================================================

    async def _file_read_image(
        self, executor: Any, args: Dict[str, Any],
    ) -> Any:
        """file_read：仅处理图片文件，返回多模态 FileReadResult"""
        from services.agent.agent_result import AgentResult

        path = args.get("path", "")
        if not path:
            return AgentResult(
                summary="请指定文件路径", status="error",
                error_message="Validation: path is required",
                metadata={"retryable": True},
            )

        ext = Path(path).suffix.lower()
        _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

        if ext not in _IMAGE_EXTS:
            return AgentResult(
                summary=(
                    f"file_read 仅支持图片文件。"
                    f"其他文件请在 code_execute 中直接读取（openpyxl/pdfplumber/docx/open）。"
                ),
                status="error",
                error_message=f"Unsupported file type for file_read: {ext}",
                metadata={"retryable": False},
            )

        # 路径解析：优先从共享缓存取绝对路径，兜底 resolve_safe_path
        from services.agent.file_path_cache import get_file_cache
        resolved_path = get_file_cache(self.conversation_id).resolve(path, usage="analyze")
        if not resolved_path:
            try:
                resolved_path = str(executor.resolve_safe_path(path))
            except Exception:
                resolved_path = path

        try:
            result = await executor.file_read(path=resolved_path)
            from services.file_read_extensions import FileReadResult
            if isinstance(result, FileReadResult):
                return result
            return AgentResult(summary=result or "", status="success")
        except Exception as e:
            logger.error(f"file_read image | path={path} | resolved={resolved_path} | error={e}")
            return AgentResult(
                summary=f"图片读取失败: {e}", status="error",
                error_message=str(e), metadata={"retryable": True},
            )

    # ================================================================
    # restore_file：精确匹配备份文件名（不依赖 registry）
    # ================================================================

    async def _restore_file(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """从 OSS/CDN 恢复已删除的文件到 workspace。

        查 deleted_files 表找到 oss_object_key → 从 OSS 下载回原位置。
        删除后 30 天内可恢复（purge_after 之前）。
        """
        from services.agent.agent_result import AgentResult

        filename = args.get("filename", "").strip()
        if not filename:
            return AgentResult(
                summary="请指定要恢复的文件名",
                status="error",
                error_message="Validation: filename is required",
                metadata={"retryable": True},
            )

        # 查 deleted_files 表
        record = await self._find_deleted_record(filename)
        if not record:
            return AgentResult(
                summary=f"未找到「{filename}」的删除记录。文件可能未被删除，或已超过 30 天恢复期。",
                status="empty",
            )

        oss_key = record["oss_object_key"]
        rel_path = record["relative_path"]

        # 从 OSS 下载回 workspace
        # rel_path 已含 org 前缀（如 org/xxx/yyy/下载/file.xlsx），
        # 需用 _workspace_base（/mnt/nas-workspace）拼接为绝对路径，
        # 再走 resolve_safe_path 安全校验（确认在当前用户 _root 内）
        from pathlib import Path as _Path
        abs_path = str((_Path(executor._workspace_base) / rel_path).resolve())
        target_path = executor.resolve_safe_path(abs_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            import asyncio
            from services.oss_service import get_oss_service
            oss = get_oss_service()
            await asyncio.to_thread(
                oss.bucket.get_object_to_file, oss_key, str(target_path),
            )
        except Exception as e:
            logger.error(f"restore_file OSS download failed | key={oss_key} | error={e}")
            return AgentResult(
                summary=f"从 OSS 恢复「{filename}」失败: {e}",
                status="error",
                error_message=str(e),
            )

        # 标记 deleted_files 记录为已恢复
        await self._mark_restored(record["id"])

        logger.info(f"restore_file | file={filename} | oss_key={oss_key} | target={target_path}")

        return AgentResult(
            summary=f"已恢复「{filename}」到 {rel_path}",
            status="success",
        )

    async def _find_deleted_record(self, filename: str) -> dict | None:
        """从 deleted_files 表查找匹配的删除记录（未过期、未清理）"""
        try:
            from services.knowledge_config import get_pg_connection, is_kb_available
            if not is_kb_available():
                return None
            conn_ctx = await get_pg_connection()
            if conn_ctx is None:
                return None
            async with conn_ctx as conn:
                async with conn.cursor() as cur:
                    # 按 relative_path 精确匹配 或 文件名模糊匹配
                    await cur.execute(
                        """
                        SELECT id, relative_path, oss_object_key
                        FROM deleted_files
                        WHERE org_id = %(org_id)s
                          AND user_id = %(user_id)s
                          AND NOT purged
                          AND purge_after > now()
                          AND (relative_path = %(name)s
                               OR relative_path LIKE '%%/' || %(name)s)
                        ORDER BY deleted_at DESC
                        LIMIT 1
                        """,
                        {"org_id": self.org_id, "user_id": self.user_id, "name": filename},
                    )
                    row = await cur.fetchone()
                    if row:
                        return {"id": row[0], "relative_path": row[1], "oss_object_key": row[2]}
        except Exception as e:
            logger.warning(f"restore_file query failed | error={e}")
        return None

    async def _mark_restored(self, record_id: int) -> None:
        """标记 deleted_files 记录为已恢复（purged=TRUE，不再被清理任务处理）"""
        try:
            from services.knowledge_config import get_pg_connection, is_kb_available
            if not is_kb_available():
                return
            conn_ctx = await get_pg_connection()
            if conn_ctx is None:
                return
            async with conn_ctx as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE deleted_files SET purged = TRUE WHERE id = %s",
                        (record_id,),
                    )
        except Exception as e:
            logger.warning(f"restore_file mark restored failed | error={e}")

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
