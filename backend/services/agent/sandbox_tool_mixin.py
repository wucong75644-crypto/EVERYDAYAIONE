"""
代码执行沙盒工具 Mixin

从 tool_executor.py 拆出（500 行红线），承载：
- code_execute（沙盒执行、文件注册与失败观测）
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
        """在旧 AgentLoop 的安全沙盒中执行 Python 代码。"""
        import time as time_module

        from core.config import get_settings
        from services.agent.agent_result import AgentResult
        from services.sandbox.functions import build_sandbox_executor

        settings = get_settings()
        if not settings.sandbox_enabled:
            return AgentResult(
                summary="代码执行功能已关闭，请联系管理员启用",
                status="error",
                error_message="Feature disabled: sandbox_enabled=false",
                metadata={"retryable": False},
            )

        code = args.get("code", "")
        description = args.get("description", "")
        if not code:
            return AgentResult(
                summary="代码不能为空",
                status="error",
                error_message="Validation: code is required",
                metadata={"retryable": True},
            )

        started_ms = int(time_module.monotonic() * 1000)
        status = "success"
        result: AgentResult | str = ""
        try:
            timeout = settings.sandbox_timeout
            budget = getattr(self, "_budget", None)
            if budget is not None and hasattr(budget, "remaining"):
                timeout = min(timeout, max(budget.remaining, 5.0))

            from services.agent.file_path_cache import get_file_cache

            cache = get_file_cache(self.conversation_id)
            if not cache._staging_dir:
                staging_dir = self._get_staging_dir()
                if staging_dir:
                    cache.set_staging_dir(staging_dir)

            from services.sandbox.kernel_manager import get_kernel_manager

            executor = build_sandbox_executor(
                timeout=timeout,
                max_result_chars=settings.sandbox_max_result_chars,
                user_id=self.workspace_user_id,
                org_id=self.org_id,
                conversation_id=self.conversation_id,
                kernel_manager=get_kernel_manager(),
            )
            result = await executor.execute(code, description)
            if result.status == "success" and result.summary:
                self._register_files_from_output(result.summary)
            if result.is_failure:
                status = "timeout" if result.status == "timeout" else "failed"
            return result
        except Exception as error:
            status = "failed"
            result = AgentResult(
                summary=f"沙盒执行异常: {error}",
                status="error",
                error_message=str(error),
                metadata={"retryable": False},
            )
            return result
        finally:
            elapsed_ms = int(time_module.monotonic() * 1000) - started_ms
            result_text = (
                result.summary if isinstance(result, AgentResult) else str(result)
            )
            self._record_sandbox_metric(
                description=description,
                code=code,
                status=status,
                elapsed_ms=elapsed_ms,
                result_length=len(result_text),
            )
            if status == "failed":
                self._record_sandbox_knowledge(description, result_text)

    def _record_sandbox_metric(
        self,
        description: str,
        code: str,
        status: str,
        elapsed_ms: int,
        result_length: int,
    ) -> None:
        """异步记录沙盒执行指标，不阻塞工具结果。"""
        import asyncio

        from services.sandbox.functions import compute_code_hash

        try:
            from services.knowledge_metrics import record_metric

            asyncio.create_task(record_metric(
                db_source=self.db,
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
            ))
        except Exception as error:
            logger.debug(
                "SANDBOX_METRIC_RECORD_SKIPPED | exception_type={}",
                type(error).__name__,
            )

    def _record_sandbox_knowledge(
        self,
        description: str,
        error_result: str,
    ) -> None:
        """异步记录沙盒失败知识，不阻塞工具结果。"""
        import asyncio

        try:
            from services.knowledge_extractor import extract_and_save

            asyncio.create_task(extract_and_save(
                db_source=self.db,
                task_type="sandbox_execution",
                model_id="python_sandbox",
                status="failed",
                error_message=f"[{description}] {error_result[:500]}",
            ))
        except Exception as error:
            logger.debug(
                "SANDBOX_KNOWLEDGE_RECORD_SKIPPED | exception_type={}",
                type(error).__name__,
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

    def _register_files_from_output(self, stdout: str) -> None:
        """将沙盒输出中真实存在的文件注册到会话路径缓存。"""
        import os
        import re

        from services.agent.file_path_cache import get_file_cache

        workspace_dir = self._get_workspace_dir()
        if not workspace_dir:
            return
        data_exts = (
            r"\.(?:xlsx|xls|csv|tsv|parquet|pdf|docx|pptx|txt|json|png|jpg)"
        )
        file_pattern = re.compile(
            rf"['\"]([^'\"]*{data_exts})['\"]",
            re.IGNORECASE,
        )
        cache = get_file_cache(self.conversation_id)
        for match in file_pattern.finditer(stdout):
            filename = match.group(1)
            candidate = os.path.join(workspace_dir, filename)
            if os.path.exists(candidate):
                cache.register(
                    os.path.basename(filename),
                    workspace=os.path.realpath(candidate),
                )

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

    def _get_workspace_dir(self) -> str:
        """获取当前用户的工作区目录。"""
        try:
            from core.config import get_settings
            from core.workspace import resolve_workspace_dir

            settings = get_settings()
            return resolve_workspace_dir(
                settings.file_workspace_root,
                self.workspace_user_id,
                self.org_id,
            )
        except Exception:
            return ""
