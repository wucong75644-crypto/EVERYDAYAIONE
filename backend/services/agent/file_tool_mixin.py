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
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger

# 数据文件扩展名（走 DataQueryExecutor 或 excel_reader）
_DATA_EXTS = frozenset({".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".parquet"})


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

        # ── file_list：格式化 + 元数据 ──
        if tool_name == "file_list":
            try:
                return await self._file_list_with_metadata(executor, args)
            except PermissionError as e:
                return AgentResult(
                    summary=f"权限不足: {e}", status="error",
                    error_message=f"PermissionError: {e}",
                    metadata={"retryable": False},
                )
            except Exception as e:
                logger.error(f"ToolExecutor file_list | error={e}")
                return AgentResult(
                    summary=f"文件操作失败: {e}", status="error",
                    error_message=str(e), metadata={"retryable": False},
                )

        # ── file_search：搜索 + 元数据 ──
        if tool_name == "file_search":
            try:
                return await self._file_search_with_metadata(executor, args)
            except PermissionError as e:
                return AgentResult(
                    summary=f"权限不足: {e}", status="error",
                    error_message=f"PermissionError: {e}",
                    metadata={"retryable": False},
                )
            except Exception as e:
                logger.error(f"ToolExecutor file_search | error={e}")
                return AgentResult(
                    summary=f"文件操作失败: {e}", status="error",
                    error_message=str(e), metadata={"retryable": False},
                )

        # ── restore_file：从备份恢复 workspace 文件 ──
        if tool_name == "restore_file":
            try:
                return await self._restore_file(executor, args)
            except PermissionError as e:
                return AgentResult(
                    summary=f"权限不足: {e}", status="error",
                    error_message=f"PermissionError: {e}",
                    metadata={"retryable": False},
                )
            except Exception as e:
                logger.error(f"ToolExecutor restore_file | error={e}")
                return AgentResult(
                    summary=f"文件恢复失败: {e}", status="error",
                    error_message=str(e), metadata={"retryable": False},
                )

        # ── file_read：统一文件读取 ──
        if tool_name == "file_read":
            return await self._file_read_dispatch(executor, args, settings)

        return AgentResult(
            summary=f"Unknown file tool: {tool_name}",
            status="error",
            error_message=f"Unknown tool: {tool_name}",
            metadata={"retryable": False},
        )

    async def _file_read_dispatch(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """file_read 统一调度：数据文件走 DataQueryExecutor，其他走 FileExecutor。"""
        from services.agent.agent_result import AgentResult

        # path 参数：先查 FilePathCache 翻译
        path = args.get("path", "")
        if path:
            from services.agent.workspace_file_handles import get_file_cache
            cached = get_file_cache(self.conversation_id).resolve(path)
            if cached:
                args = {**args, "path": cached}
                path = cached

        ext = Path(path).suffix.lower() if path else ""

        # ── 数据文件分支：Excel/CSV/Parquet ──
        if ext in _DATA_EXTS:
            ft = "excel" if ext in (".xlsx", ".xls", ".xlsm") else "csv"
            return await self._file_read_data(args, settings, ft)

        # ── 其他文件：走 FileExecutor 原有逻辑 ──
        try:
            result = await executor.file_read(**{
                k: v for k, v in args.items()
                if k in ("path", "offset", "limit", "pages")
            })
            from services.file_read_extensions import FileReadResult
            if isinstance(result, FileReadResult):
                return result
            return AgentResult(summary=result or "", status="success")
        except PermissionError as e:
            logger.warning(f"ToolExecutor file_read | perm_error={e}")
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
                    summary=str(e), status="error",
                    error_message=str(e), metadata={"retryable": True},
                )
            logger.error(f"ToolExecutor file_read | error={e}")
            return AgentResult(
                summary=f"文件操作失败: {e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )

    async def _file_read_data(
        self, args: Dict[str, Any], settings: Any, file_type: str,
    ) -> Any:
        """数据文件读取：openpyxl 结构化预览 + Parquet 缓存。计算交给 code_execute + duckdb。"""
        from services.agent.agent_result import AgentResult
        from core.workspace import resolve_staging_dir

        staging_dir = resolve_staging_dir(
            settings.file_workspace_root,
            self.user_id, self.org_id, self.conversation_id,
        )

        # Excel → openpyxl 结构化预览 + Parquet 缓存
        if file_type == "excel":
            try:
                from services.agent.excel_reader import read_excel_structured
                result = await read_excel_structured(
                    args["path"], args.get("sheet"), staging_dir,
                )
                self._register_staging_files(result)
                _filename = Path(args["path"]).name
                # 创建 Parquet 缓存 + 附加 duckdb 查询指引
                cache_name = None
                cache_schema = ""
                try:
                    from services.agent.data_query_cache import ensure_parquet_cache
                    cache_path, _ = await ensure_parquet_cache(
                        args["path"], args.get("sheet"), staging_dir,
                    )
                    cache_name = Path(cache_path).name
                    try:
                        import duckdb as _dq
                        _con = _dq.connect(":memory:")
                        _escaped = cache_path.replace("'", "''")
                        pq_cols = _con.execute(
                            f"SELECT column_name, data_type FROM parquet_schema('{_escaped}')"
                        ).fetchall()
                        pq_rows = _con.execute(
                            f"SELECT num_rows::BIGINT FROM parquet_file_metadata('{_escaped}')"
                        ).fetchone()[0]
                        _con.close()
                        col_preview = ", ".join(f'"{n}"({t})' for n, t in pq_cols[:15])
                        if len(pq_cols) > 15:
                            col_preview += f" (+{len(pq_cols)-15}列)"
                        cache_schema = f" | {pq_rows}行 × {len(pq_cols)}列\n[列: {col_preview}]"
                    except Exception:
                        pass
                    result.summary += (
                        f"\n\n[staging 缓存] {cache_name}{cache_schema}"
                        f"\n\n数据查询（在 code_execute 中使用）:"
                        f"\n  import duckdb"
                        f"\n  path = STAGING_DIR + '/{cache_name}'"
                        f"\n  df = duckdb.sql(f\"SELECT * FROM read_parquet('{{path}}') LIMIT 20\").df()"
                    )
                except Exception:
                    pass
                # schema 注册：包含 staging 路径和列名（跨轮注入用）
                schema_text = f"{_filename}{cache_schema}"
                if cache_name:
                    schema_text += (
                        f"\n读取: path = STAGING_DIR + '/{cache_name}'"
                        f"\n      duckdb.sql(f\"SELECT * FROM read_parquet('{{path}}')\")"
                    )
                self._pending_schemas.append((
                    _filename, args["path"], schema_text,
                ))
                return result
            except Exception as e:
                logger.error(f"Excel structured read failed | error={e}")
                return AgentResult(
                    summary=f"Excel 读取失败: {e}",
                    status="error",
                    error_message=str(e),
                )

        # CSV / Parquet → DuckDB 快速 profile + 缓存
        try:
            from services.agent.data_query_cache import ensure_parquet_cache
            _filename = Path(args["path"]).name

            # CSV 需要转 Parquet 缓存；Parquet 直接用
            if file_type == "csv":
                cache_path, _ = await ensure_parquet_cache(
                    args["path"], None, staging_dir,
                )
            else:
                cache_path = args["path"]

            # DuckDB 快速 profile
            import duckdb as _dq
            _con = _dq.connect(":memory:")
            _escaped = cache_path.replace("'", "''")
            pq_cols = _con.execute(
                f"SELECT column_name, data_type FROM parquet_schema('{_escaped}')"
            ).fetchall()
            pq_rows = _con.execute(
                f"SELECT num_rows::BIGINT FROM parquet_file_metadata('{_escaped}')"
            ).fetchone()[0]
            preview = _con.execute(
                f"SELECT * FROM read_parquet('{_escaped}') LIMIT 5"
            ).fetchdf().to_string(index=False)
            _con.close()

            col_preview = ", ".join(f'"{n}"({t})' for n, t in pq_cols[:15])
            if len(pq_cols) > 15:
                col_preview += f" (+{len(pq_cols)-15}列)"
            cache_name = Path(cache_path).name

            summary = (
                f"{_filename} | {pq_rows:,}行 × {len(pq_cols)}列\n"
                f"[列: {col_preview}]\n\n"
                f"预览:\n{preview}\n\n"
                f"[staging 缓存] {cache_name}\n\n"
                f"数据查询（在 code_execute 中使用）:\n"
                f"  import duckdb\n"
                f"  path = STAGING_DIR + '/{cache_name}'\n"
                f"  df = duckdb.sql(f\"SELECT * FROM read_parquet('{{path}}') LIMIT 20\").df()"
            )
            self._pending_schemas.append((
                _filename, args["path"], summary[:500],
            ))
            return AgentResult(summary=summary, status="success")
        except Exception as e:
            logger.error(f"Data file read failed | error={e}")
            return AgentResult(
                summary=f"数据文件读取失败: {e}",
                status="error",
                error_message=str(e),
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

    async def _file_list_with_metadata(
        self, executor: 'Any', args: Dict[str, Any],
    ) -> 'AgentResult':
        """file_list + 元数据（每个文件附带结构信息和读取命令）"""
        from services.agent.agent_result import AgentResult
        from services.file_metadata_extractor import format_file_metadata_line

        data = await executor.file_list_entries(**args)

        if data["error"]:
            return AgentResult(
                summary=data["error"], status="error",
                error_message=data["error"], metadata={"retryable": False},
            )
        if not data["dirs"] and not data["files"]:
            return AgentResult(summary=f"目录为空: {data['path']}", status="empty")

        total = len(data["dirs"]) + len(data["files"])
        lines = [f"目录: {data['path']} | 共 {total} 项"]
        lines.append("─" * 60)
        for d in data["dirs"]:
            lines.append(f"  [目录] {d['name']}/\t\t{d['modified']}")

        from pathlib import Path as _Path
        from services.agent.workspace_file_handles import get_file_cache
        file_cache = get_file_cache(self.conversation_id)
        ws_root = _Path(executor.workspace_root)

        _MAX_METADATA = 5
        for i, f in enumerate(data["files"]):
            # 计算 workspace 相对路径（子目录文件带完整路径，如 "报表/月度汇总.xlsx"）
            try:
                rel_path = str(_Path(f["abs_path"]).relative_to(ws_root))
            except ValueError:
                rel_path = f["name"]
            # 注册两种 key：纯文件名 + 相对路径（都能命中缓存）
            file_cache.register(f["name"], f["abs_path"])
            file_cache.register(rel_path, f["abs_path"])
            if i < _MAX_METADATA:
                meta = await self._get_or_extract_metadata(f["abs_path"])
                line = format_file_metadata_line(
                    rel_path, f["abs_path"], f["size"], meta,
                )
            else:
                size_str = executor._format_size(f["size"])
                line = f"  {rel_path}\t{size_str}"
            lines.append(line)

        if data["truncated"]:
            lines.append("\n已达显示上限，部分条目未显示")

        return AgentResult(summary="\n".join(lines), status="success")

    async def _file_search_with_metadata(
        self, executor: 'Any', args: Dict[str, Any],
    ) -> 'AgentResult':
        """file_search + 元数据（搜到的文件附带结构信息）"""
        import re
        from services.file_metadata_extractor import format_file_metadata_line
        from services.agent.agent_result import AgentResult

        raw_result = await executor.file_search(**args)

        if "未找到" in raw_result or not raw_result.strip():
            return AgentResult(summary=raw_result or "未找到匹配文件", status="empty")

        lines = raw_result.split("\n")
        enhanced_lines = []
        metadata_count = 0
        _MAX_SEARCH_METADATA = 3

        from services.agent.workspace_file_handles import get_file_cache
        file_cache = get_file_cache(self.conversation_id)

        for line in lines:
            match = re.match(r"\s+\[文件\]\s+(\S+?)(?::\d+\s*\|.*)?$", line)
            if match:
                rel_path = match.group(1)
                try:
                    target = executor.resolve_safe_path(rel_path)
                    if target.is_file():
                        # 注册两种 key（对齐 file_list），所有搜索结果都注册
                        file_cache.register(target.name, str(target))
                        file_cache.register(rel_path, str(target))
                        if metadata_count < _MAX_SEARCH_METADATA:
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

        return AgentResult(summary="\n".join(enhanced_lines), status="success")

    async def _restore_file(
        self, executor: "Any", args: Dict[str, Any],
    ) -> "AgentResult":
        """从 session_file_registry 中查找备份并恢复到 workspace。

        路径安全：目标路径通过 FileExecutor.resolve_safe_path 校验，
        与 file_read/file_list/file_search 共用同一套安全基础设施。

        查找逻辑：registry 中 key 以 "backup:{filename}:" 开头的条目，
        取最新的（timestamp 降序），copy 回 workspace 原路径。
        """
        import shutil

        from services.agent.agent_result import AgentResult
        from services.agent.session_file_registry import get_conversation_registry

        filename = args.get("filename", "").strip()
        if not filename:
            return AgentResult(
                summary="请指定要恢复的文件名",
                status="error",
                error_message="Validation: filename is required",
                metadata={"retryable": True},
            )

        # 路径安全校验（realpath + workspace 白名单 + 符号链接 + 黑名单）
        # 拦截 "../"、绝对路径、符号链接等穿越攻击
        target_path = executor.resolve_safe_path(filename)

        # 从对话级 registry 查找备份
        registry = get_conversation_registry(self.conversation_id)
        prefix = f"backup:{filename}:"
        candidates = [
            (key, ref)
            for key, ref in registry.list_all()
            if key.startswith(prefix)
        ]

        if not candidates:
            return AgentResult(
                summary=f"未找到「{filename}」的备份。可能备份已过期（24小时有效期）或该文件未被修改过。",
                status="empty",
            )

        # 取最新的备份（key 末尾是 timestamp）
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_key, best_ref = candidates[0]

        # 验证备份文件存在
        if not best_ref.is_valid():
            # 备份文件已被清理，移除 registry 条目
            registry.remove(best_key)
            return AgentResult(
                summary=f"「{filename}」的备份文件已过期被清理，无法恢复。",
                status="error",
                error_message="Backup file expired",
            )

        # 恢复：copy 备份 → workspace 原路径
        shutil.copy2(best_ref.path, str(target_path))

        # 恢复后移除该备份条目（一次性使用）
        registry.remove(best_key)

        logger.info(
            f"ToolExecutor restore_file | file={filename} | "
            f"backup={best_ref.filename} | target={target_path}"
        )

        return AgentResult(
            summary=f"已恢复「{filename}」到修改前的版本。",
            status="success",
        )


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
