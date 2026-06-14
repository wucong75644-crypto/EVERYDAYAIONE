"""文件删除 + 恢复工具 Mixin。

依赖宿主类提供：
- self.user_id, self.org_id, self.conversation_id
- self._record_deleted_files(...)（来自 SandboxToolMixin）
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from loguru import logger


class FileDeleteMixin:
    """文件删除 + 恢复工具 Mixin"""

    # ================================================================
    # file_delete：从共享缓存取精确路径 + 物理删除 + 记录 deleted_files
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

        cache = get_file_cache(self.conversation_id)

        # 优先 file_ids（fid 协议），兜底 files（老协议）
        file_ids = args.get("file_ids") or []
        files = args.get("files") or []
        if isinstance(file_ids, str):
            file_ids = [file_ids]
        if isinstance(files, str):
            files = [files]
        if not file_ids and not files:
            return AgentResult(
                summary="未指定要删除的文件",
                status="error",
                error_message="file_ids 或 files 至少传一个",
                metadata={"retryable": True},
            )

        # fid 优先解析
        if file_ids:
            from services.agent.file_id import (
                is_valid_fid, resolve_fid_to_workspace,
            )
            _org_id = getattr(self, "org_id", None)
            for fid in file_ids:
                if not is_valid_fid(fid):
                    return AgentResult(
                        summary=f"file_id 格式错误: {fid}",
                        status="error",
                        error_message=f"file_id 必须 fid_xxx 格式，你传的是 {fid!r}",
                        metadata={"retryable": True},
                    )
                ws = resolve_fid_to_workspace(fid, _org_id, cache)
                if not ws:
                    return AgentResult(
                        summary=f"未找到 file_id={fid}",
                        status="error",
                        error_message=f"file_id={fid} 在当前对话附件里找不到",
                        metadata={"retryable": True},
                    )
                files.append(ws)

        deleted = []
        skipped = []
        for name in files:
            if os.path.isabs(name) and os.path.isfile(name):
                abs_path = name  # 已经是 fid 解析出来的 abs 路径
            else:
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
        abs_path = str((Path(executor._workspace_base) / rel_path).resolve())
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
