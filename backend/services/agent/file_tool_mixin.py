"""
文件操作 + 社交爬虫工具 Mixin（聚合入口）

对齐 Claude 模式：
- file_search: 搜索/列目录/定位文件 → 返回 workspace 相对路径；
               命中单张图片时直接返回 FileReadResult(type=image) 多模态注入视觉模型
- file_analyze: Excel/CSV 结构化读取转 Parquet
- file_delete / restore_file: 删除/恢复文件（拆到 file_delete_mixin.py）
- social_crawler: 社交媒体爬虫（拆到 crawler_tool_mixin.py）

通过 Mixin 继承组合到 ToolExecutor。
依赖宿主类提供：self.user_id, self.org_id, self.conversation_id
"""
from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict

from loguru import logger

from services.agent.crawler_tool_mixin import CrawlerToolMixin
from services.agent.file_delete_mixin import FileDeleteMixin
from services.agent.file_describe_mixin import FileDescribeMixin


__all__ = ["FileToolMixin", "CrawlerToolMixin"]


class FileToolMixin(FileDescribeMixin, FileDeleteMixin):
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
            user_id=self.workspace_user_id,
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

        scope = str(args.get("scope") or "current").strip().lower()
        if getattr(self, "resource_manifest", None) is not None and scope != "workspace":
            return await self._search_manifest(executor, args)
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

    async def _search_manifest(
        self,
        executor: Any,
        args: Dict[str, Any],
    ) -> Any:
        """只检索当前任务冻结的资源，不扫描 Workspace。"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_id import compute_fid
        from services.agent.file_path_cache import get_file_cache

        path = str(args.get("path") or "").strip()
        keyword = str(args.get("keyword") or "").strip().lower()
        pattern = str(args.get("file_pattern") or "").strip()
        assets = list(self.resource_manifest.assets)
        if path:
            assets = [
                asset for asset in assets
                if asset.workspace_path == path or asset.name == path
            ]
        if keyword:
            assets = [
                asset for asset in assets
                if keyword in asset.name.lower()
                or keyword in asset.workspace_path.lower()
            ]
        if pattern:
            assets = [
                asset for asset in assets
                if fnmatch(asset.name, pattern)
                or fnmatch(asset.workspace_path, pattern)
            ]
        if not assets:
            return AgentResult(
                summary="当前任务资源中未找到匹配文件",
                status="empty",
                metadata={"resource_scope": "current"},
            )
        if path and len(assets) == 1:
            try:
                target = executor.resolve_safe_path(assets[0].workspace_path)
            except (FileNotFoundError, PermissionError, ValueError) as error:
                return AgentResult(
                    summary=f"当前任务文件不可用: {assets[0].name}",
                    status="error",
                    error_message=str(error),
                    metadata={"resource_scope": "current", "retryable": False},
                )
            return await self._describe_single_file(executor, str(target))

        cache = get_file_cache(self.conversation_id)
        lines = [f"当前任务资源 | 共 {len(assets)} 项", "─" * 50]
        for asset in assets:
            fid = compute_fid(self.org_id, asset.workspace_path)
            lines.append(
                f"  [{fid}] {asset.workspace_path}  "
                f"({executor._format_size(asset.size or 0)})"
            )
            try:
                target = executor.resolve_safe_path(asset.workspace_path)
                cache.register(asset.name, workspace=str(target))
                cache.register(asset.workspace_path, workspace=str(target))
            except (FileNotFoundError, PermissionError, ValueError):
                continue
        return AgentResult(
            summary="\n".join(lines),
            status="success",
            metadata={
                "resource_scope": "current",
                "asset_ids": [asset.asset_id for asset in assets],
            },
        )

    async def _list_directory(
        self, executor: Any, args: Dict[str, Any],
    ) -> Any:
        """列出目录内容，返回文件列表和 workspace 相对路径，注册到共享缓存"""
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
        from services.agent.file_id import compute_fid
        _org_id = getattr(self, "org_id", None)

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
            fid = compute_fid(_org_id, rel_path)
            lines.append(f"  [{fid}] {rel_path}  ({size_str})")
            cache.register(f["name"], workspace=f["abs_path"])
            cache.register(rel_path, workspace=f["abs_path"])

        if data.get("truncated"):
            lines.append("\n已达显示上限，部分条目未显示")

        lines.append("")
        lines.append(
            "在 code_execute 中用相对路径直接读取（沙盒 cwd=/workspace）；"
            "xlsx/csv 数据文件请先调 file_analyze 治理后用 pd.read_parquet('staging/x.parquet') 读"
        )

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
        from services.agent.file_id import compute_fid
        _org_id = getattr(self, "org_id", None)
        _file_re = re.compile(r"\s+\[文件\]\s+(\S+)")
        # 同时为每行 [文件] xxx 前插入 [fid_xxx]，方便 LLM 后续调工具
        annotated_lines: list[str] = []
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
                fid = compute_fid(_org_id, rel_path)
                # 替换 "[文件] rel_path" → "[文件] [fid_xxx] rel_path"
                annotated_lines.append(
                    line.replace(f"[文件] {rel_path}", f"[文件] [{fid}] {rel_path}", 1)
                )
            else:
                annotated_lines.append(line)

        lines = ["\n".join(annotated_lines)]
        lines.append("")
        lines.append(
            "在 code_execute 中用相对路径直接读取（沙盒 cwd=/workspace）；"
            "xlsx/csv 数据文件请先调 file_analyze 治理后用 pd.read_parquet('staging/x.parquet') 读"
        )

        return AgentResult(summary="\n".join(lines), status="success")

    # ================================================================
    # file_analyze：数据文件结构读取（Excel/CSV → prescan → Parquet）
    # ================================================================

    _ANALYZE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}

    async def _file_analyze(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """Analyze tabular files via the staged service.

        Security and timeout behavior remains explicit in the service:
        FileNotFoundError, PermissionError, retryable=False, wait_for,
        and _ENSURE_CACHE_TIMEOUT.
        """
        from services.agent.file_analysis_service import analyze_file

        return await analyze_file(self, executor, args, settings)
