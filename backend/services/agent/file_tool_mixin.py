"""
文件操作 + 社交爬虫工具 Mixin

对齐 Claude 模式：
- file_search: 搜索/定位文件 → 大 Excel/CSV 转 Parquet → 写 manifest → 返回路径
- file_read: 仅图片视觉（多模态）
- restore_file: 从 staging 备份恢复（精确文件名匹配，不依赖 registry）

通过 Mixin 继承组合到 ToolExecutor。
依赖宿主类提供：self.user_id, self.org_id, self.conversation_id
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger

# 数据文件扩展名（触发 Parquet 转换）
_DATA_EXTS = frozenset({".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".parquet"})

# 安全文件名：只保留 ASCII 字母/数字/下划线/连字符/点
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.\-]")


def _sanitize_filename(name: str, idx: int) -> str:
    """将原始文件名转换为安全的 ASCII 文件名。

    中文/特殊字符全部移除，保留 ASCII 字母数字，加序号避免冲突。
    统一输出 .parquet 扩展名。
    例：sales_2024.xlsx → sales2024_001.parquet
        销售数据.xlsx → file_001.parquet（中文全移除后为空，用 file 兜底）
    """
    ext = Path(name).suffix.lower()
    stem = Path(name).stem
    safe = _SAFE_NAME_RE.sub("", stem)
    if not safe:
        safe = "file"
    # 截断避免过长
    safe = safe[:30]
    return f"{safe}_{idx:03d}.parquet"


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
    # file_search：搜索/定位 + Parquet 转换 + manifest
    # ================================================================

    async def _file_search(
        self, executor: Any, args: Dict[str, Any], settings: Any,
    ) -> Any:
        """file_search 实现：搜索文件 → 大数据文件转 Parquet → 写 manifest → 返回路径"""
        from services.agent.agent_result import AgentResult
        from core.workspace import resolve_staging_dir

        path = args.get("path", "")
        keyword = args.get("keyword", "")
        file_pattern = args.get("file_pattern", "")

        staging_dir = resolve_staging_dir(
            settings.file_workspace_root,
            self.user_id, self.org_id, self.conversation_id,
        )
        Path(staging_dir).mkdir(parents=True, exist_ok=True)

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
                return await self._prepare_single_file(
                    executor, str(target), staging_dir,
                )
            if target.is_dir():
                return await self._list_directory(executor, args, staging_dir)
            # 路径既非文件也非目录
            return AgentResult(
                summary=f"未找到文件或目录: {path}",
                status="error",
                error_message=f"Path not found: {path}",
                metadata={"retryable": True},
            )

        # ── 有 keyword/file_pattern → 搜索模式 ──
        if keyword or file_pattern:
            return await self._search_files(executor, args, staging_dir)

        # ── 无参数 → 列出根目录 ──
        return await self._list_directory(executor, args, staging_dir)

    async def _list_directory(
        self, executor: Any, args: Dict[str, Any], staging_dir: str,
    ) -> Any:
        """列出目录内容，对数据文件自动转 Parquet"""
        from services.agent.agent_result import AgentResult

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

        # 文件路径缓存：注册到会话级共享缓存，供 code_execute/file_delete 等工具使用
        from services.agent.file_path_cache import get_file_cache
        _cache = get_file_cache(self.conversation_id)

        for d in data["dirs"]:
            lines.append(f"  [目录] {d['name']}/")

        # 收集数据文件做批量转换（限制单次最多 5 个，避免列目录时大量转换阻塞）
        _MAX_AUTO_CONVERT = 5
        _MIN_CONVERT_SIZE = 1024  # 小于 1KB 的跳过（空文件/模板）
        data_files = []
        skipped_data_count = 0
        for f in data["files"]:
            size_str = executor._format_size(f["size"])
            try:
                rel_path = str(Path(f["abs_path"]).relative_to(
                    Path(executor.workspace_root)
                ))
            except ValueError:
                rel_path = f["name"]
            lines.append(f"  {rel_path}  ({size_str})")
            _cache.register(rel_path, f["abs_path"])

            ext = Path(f["name"]).suffix.lower()
            if ext in _DATA_EXTS and f["size"] >= _MIN_CONVERT_SIZE:
                if len(data_files) < _MAX_AUTO_CONVERT:
                    data_files.append(f)
                else:
                    skipped_data_count += 1

        # 批量转 Parquet + 写 manifest
        if data_files:
            manifest_entries = await self._batch_prepare_parquet(
                data_files, staging_dir,
            )
            if manifest_entries:
                lines.append("")
                lines.append(f"[staging] {len(manifest_entries)} 个数据文件已转 Parquet：")
                for entry in manifest_entries:
                    lines.append(
                        f"  {entry['original']} → {entry['parquet']} "
                        f"({entry['rows']:,}行 × {entry['cols']}列)"
                    )
                lines.append("")
                lines.append("在 code_execute 中直接使用：")
                for entry in manifest_entries:
                    lines.append(
                        f"  duckdb.sql(\"SELECT * FROM read_parquet("
                        f"STAGING_DIR + '/{entry['parquet']}') LIMIT 20\")"
                    )

        if skipped_data_count > 0:
            lines.append(
                f"\n还有 {skipped_data_count} 个数据文件未自动转换，"
                "需要时用 file_search(path=\"文件名\") 逐个准备。"
            )

        if data.get("truncated"):
            lines.append("\n已达显示上限，部分条目未显示")

        return AgentResult(summary="\n".join(lines), status="success")

    async def _search_files(
        self, executor: Any, args: Dict[str, Any], staging_dir: str,
    ) -> Any:
        """搜索文件并对数据文件自动转 Parquet"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        raw_result = await executor.file_search(**{
            k: v for k, v in args.items()
            if k in ("keyword", "path", "search_content", "file_pattern")
        })

        if "未找到" in raw_result or not raw_result.strip():
            return AgentResult(summary=raw_result or "未找到匹配文件", status="empty")

        # 从搜索结果中提取文件路径
        _cache = get_file_cache(self.conversation_id)
        data_files = []
        _file_re = re.compile(r"\s+\[文件\]\s+(\S+)")
        for line in raw_result.split("\n"):
            m = _file_re.match(line)
            if m:
                rel_path = m.group(1).split(":")[0]  # 去掉行号后缀
                try:
                    target = executor.resolve_safe_path(rel_path)
                    if target.is_file():
                        _cache.register(rel_path, str(target))
                        ext = target.suffix.lower()
                        if ext in _DATA_EXTS:
                            data_files.append({
                                "name": target.name,
                                "abs_path": str(target),
                                "size": target.stat().st_size,
                            })
                except Exception:
                    pass

        lines = [raw_result]

        if data_files:
            manifest_entries = await self._batch_prepare_parquet(
                data_files, staging_dir,
            )
            if manifest_entries:
                lines.append("")
                lines.append(f"[staging] {len(manifest_entries)} 个数据文件已转 Parquet：")
                for entry in manifest_entries:
                    lines.append(
                        f"  {entry['original']} → {entry['parquet']} "
                        f"({entry['rows']:,}行 × {entry['cols']}列)"
                    )
                lines.append("")
                lines.append("在 code_execute 中直接使用：")
                for entry in manifest_entries:
                    lines.append(
                        f"  duckdb.sql(\"SELECT * FROM read_parquet("
                        f"STAGING_DIR + '/{entry['parquet']}') LIMIT 20\")"
                    )

        return AgentResult(summary="\n".join(lines), status="success")

    async def _prepare_single_file(
        self, executor: Any, abs_path: str, staging_dir: str,
    ) -> Any:
        """准备单个文件：数据文件转 Parquet + manifest，其他文件返回路径信息"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        ext = Path(abs_path).suffix.lower()
        name = Path(abs_path).name

        # 注册到共享缓存
        try:
            rel_path = str(Path(abs_path).relative_to(Path(executor.workspace_root)))
        except ValueError:
            rel_path = name
        get_file_cache(self.conversation_id).register(rel_path, abs_path)

        if ext not in _DATA_EXTS:
            # 非数据文件：返回基本信息 + workspace 路径
            size = os.path.getsize(abs_path)
            size_str = self._fmt_size(size)
            lines = [f"{name} ({size_str})"]
            lines.append("")
            lines.append("在 code_execute 中直接读取：")
            if ext in (".pdf",):
                lines.append(f"  import pdfplumber")
                lines.append(f"  pdf = pdfplumber.open(WORKSPACE_DIR + '/{name}')")
            elif ext in (".docx",):
                lines.append(f"  from docx import Document")
                lines.append(f"  doc = Document(WORKSPACE_DIR + '/{name}')")
            else:
                lines.append(f"  with open(WORKSPACE_DIR + '/{name}') as f:")
                lines.append(f"      content = f.read()")
            return AgentResult(summary="\n".join(lines), status="success")

        # 数据文件：转 Parquet + 写 manifest
        manifest_entries = await self._batch_prepare_parquet(
            [{"name": name, "abs_path": abs_path, "size": os.path.getsize(abs_path)}],
            staging_dir,
        )

        if not manifest_entries:
            return AgentResult(
                summary=f"{name}: Parquet 转换失败",
                status="error",
            )

        entry = manifest_entries[0]
        lines = [
            f"{name} → {entry['parquet']} ({entry['rows']:,}行 × {entry['cols']}列)",
            "",
            "在 code_execute 中直接使用：",
            f"  duckdb.sql(\"SELECT * FROM read_parquet(STAGING_DIR + '/{entry['parquet']}') LIMIT 20\")",
        ]

        return AgentResult(summary="\n".join(lines), status="success")

    # ================================================================
    # Parquet 转换 + manifest
    # ================================================================

    async def _batch_prepare_parquet(
        self, files: list[dict], staging_dir: str,
    ) -> list[dict]:
        """批量将数据文件转 Parquet 并写入 manifest。

        Args:
            files: [{"name": str, "abs_path": str, "size": int}]
            staging_dir: staging 目录绝对路径

        Returns:
            manifest entries: [{"original": str, "parquet": str, "rows": int, "cols": int}]
        """
        from services.agent.data_query_cache import ensure_parquet_cache

        manifest_path = os.path.join(staging_dir, "_manifest.json")

        # 读取现有 manifest（增量更新）
        existing_manifest = {"files": []}
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    existing_manifest = json.load(f)
            except Exception:
                pass

        existing_by_original = {
            e["original"]: e for e in existing_manifest.get("files", [])
        }
        # 用于生成唯一序号
        next_idx = len(existing_manifest.get("files", [])) + 1

        new_entries = []
        for file_info in files:
            name = file_info["name"]
            abs_path = file_info["abs_path"]
            ext = Path(name).suffix.lower()

            # 已在 manifest 中且 Parquet 文件存在 → 跳过
            if name in existing_by_original:
                pq_name = existing_by_original[name]["parquet"]
                if os.path.exists(os.path.join(staging_dir, pq_name)):
                    new_entries.append(existing_by_original[name])
                    continue

            try:
                if ext == ".parquet":
                    # Parquet 文件直接 copy 到 staging
                    safe_name = _sanitize_filename(name, next_idx)
                    dst = os.path.join(staging_dir, safe_name)
                    if not os.path.exists(dst):
                        shutil.copy2(abs_path, dst)
                    rows, cols = self._parquet_shape(dst)
                else:
                    # Excel/CSV → Parquet
                    cache_path, _ = await ensure_parquet_cache(
                        abs_path, None, staging_dir,
                    )
                    # 重命名为安全文件名
                    safe_name = _sanitize_filename(name, next_idx)
                    dst = os.path.join(staging_dir, safe_name)
                    if cache_path != dst:
                        if os.path.exists(dst):
                            os.remove(dst)
                        shutil.move(cache_path, dst)
                    rows, cols = self._parquet_shape(dst)

                entry = {
                    "original": name,
                    "parquet": safe_name,
                    "rows": rows,
                    "cols": cols,
                }
                new_entries.append(entry)
                next_idx += 1
            except Exception as e:
                logger.warning(f"Parquet conversion failed | file={name} | error={e}")

        # 合并并写入 manifest
        all_entries = list(existing_by_original.values())
        # 用 new_entries 覆盖同名条目
        seen = set()
        merged = []
        for entry in new_entries:
            merged.append(entry)
            seen.add(entry["original"])
        for entry in all_entries:
            if entry["original"] not in seen:
                merged.append(entry)

        manifest = {"files": merged, "updated_at": int(time.time())}
        # atomic write：先写临时文件再 rename，防并发覆盖丢条目
        tmp_path = manifest_path + f".tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, manifest_path)  # 原子替换（POSIX 保证）

        return new_entries

    @staticmethod
    def _parquet_shape(path: str) -> tuple[int, int]:
        """读取 Parquet 文件的行列数"""
        try:
            import duckdb as _dq
            _con = _dq.connect(":memory:")
            _escaped = path.replace("'", "''")
            rows = _con.execute(
                f"SELECT num_rows::BIGINT FROM parquet_file_metadata('{_escaped}')"
            ).fetchone()[0]
            cols = len(_con.execute(
                f"SELECT column_name FROM parquet_schema('{_escaped}')"
            ).fetchall())
            _con.close()
            return rows, cols
        except Exception:
            return 0, 0

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
            abs_path = cache.resolve(name)
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

        try:
            result = await executor.file_read(path=path)
            from services.file_read_extensions import FileReadResult
            if isinstance(result, FileReadResult):
                return result
            return AgentResult(summary=result or "", status="success")
        except Exception as e:
            logger.error(f"file_read image | error={e}")
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
        target_path = executor.resolve_safe_path(rel_path)
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
                          AND NOT purged
                          AND purge_after > now()
                          AND (relative_path = %(name)s
                               OR relative_path LIKE '%%/' || %(name)s)
                        ORDER BY deleted_at DESC
                        LIMIT 1
                        """,
                        {"org_id": self.org_id, "name": filename},
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
