"""
代码执行沙盒工具 Mixin

从 tool_executor.py 拆出（500 行红线），承载：
- 旧 code_execute 的明确拒绝
- workspace 备份注册（供 restore_file 查找）

通过 Mixin 继承组合到 ToolExecutor。
依赖宿主类提供：self.user_id, self.org_id, self.conversation_id
"""
from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from services.agent.agent_result import AgentResult

from loguru import logger


class SandboxToolMixin:
    """代码执行沙盒工具 Mixin"""

    async def _code_execute(self, args: Dict[str, Any]) -> "AgentResult":
        """Reject legacy direct execution; persistent Action Runtime owns it."""
        from services.agent.agent_result import AgentResult
        return AgentResult(
            summary="代码执行正在迁移到安全任务运行时，当前入口不可用",
            status="error",
            error_message="CODE_EXECUTE_REQUIRES_ACTION_RUNTIME",
            metadata={"retryable": False},
        )

    def _record_deleted_files(self, deleted_meta: list[dict]) -> None:
        """Fire-and-forget 记录文件删除事件到 deleted_files 表（OSS 30 天延迟清理）"""
        import asyncio

        async def _do_record():
            try:
                from pathlib import Path as _Path
                from core.config import get_settings
                from services.knowledge_config import get_pg_connection, is_kb_available

                if not is_kb_available():
                    return
                conn_ctx = await get_pg_connection(self.db)
                if conn_ctx is None:
                    return

                settings = get_settings()
                ws_root = str(_Path(settings.file_workspace_root).resolve())

                async with conn_ctx as conn:
                    async with conn.cursor() as cur:
                        for item in deleted_meta:
                            resolved = item["resolved"]
                            if resolved.startswith(ws_root):
                                rel = resolved[len(ws_root):].lstrip("/")
                            else:
                                rel = item["raw"]
                            await cur.execute(
                                """
                                INSERT INTO deleted_files
                                    (org_id, user_id, relative_path, oss_object_key, purge_after)
                                VALUES
                                    (%(org_id)s, %(user_id)s, %(rel)s, %(oss_key)s,
                                     now() + interval '30 days')
                                """,
                                {
                                    "org_id": self.org_id,
                                    "user_id": self.user_id,
                                    "rel": rel,
                                    "oss_key": f"workspace/{rel}",
                                },
                            )
            except Exception as error:
                logger.debug(
                    "DELETED_FILE_RECORD_FAILED | exception_type={}",
                    type(error).__name__,
                )

        try:
            asyncio.create_task(_do_record())
        except Exception:
            pass

    def _register_staging_files(self, result: "AgentResult") -> None:
        """从工具结果中提取 staging 文件路径，注册到共享路径缓存。"""
        import os

        from services.agent.file_path_cache import get_file_cache

        if not result or not result.summary:
            return

        # 从 file_ref 注册（结构化路径，最可靠）
        if hasattr(result, "file_ref") and result.file_ref:
            fr = result.file_ref
            if fr.path and os.path.exists(fr.path):
                cache = get_file_cache(self.conversation_id)
                # ERP 产出：parquet 就是源文件，两个地址相同
                cache.register(fr.filename, workspace=fr.path, parquet=fr.path)
                return

        # 兜底:从 summary 文本中提取 staging 文件名(同时兼容新旧格式)
        # 新格式: staging/x.parquet (相对路径)
        # 旧格式: STAGING_DIR + '/x.parquet' (保留正则,向后兼容历史 messages)
        import re
        _STAGING_RE = re.compile(
            r"(?:STAGING_DIR\s*\+\s*'/|['\"]staging/)([^'\"]+)['\"]?"
        )
        staging_dir = self._get_staging_dir()
        if not staging_dir:
            return

        cache = get_file_cache(self.conversation_id)
        for m in _STAGING_RE.finditer(result.summary):
            filename = m.group(1)
            abs_path = os.path.join(staging_dir, filename)
            if os.path.exists(abs_path):
                # staging 文件：两个地址相同
                cache.register(filename, workspace=abs_path, parquet=abs_path)

    def _get_staging_dir(self) -> str:
        """获取当前用户的 staging 目录"""
        try:
            from core.config import get_settings
            from core.workspace import resolve_staging_dir
            settings = get_settings()
            return resolve_staging_dir(
                settings.file_workspace_root,
                self.workspace_user_id,
                self.org_id,
                self.conversation_id or "default",
            )
        except Exception:
            return ""
