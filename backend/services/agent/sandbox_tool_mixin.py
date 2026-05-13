"""
代码执行沙盒工具 Mixin

从 tool_executor.py 拆出（500 行红线），承载：
- code_execute（沙盒执行 + 图片/图表透传 + 文件注册）
- sandbox 指标记录（metric + knowledge）
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
        """在安全沙盒中执行 Python 代码"""
        import asyncio
        import time as _time

        from core.config import get_settings
        from services.agent.agent_result import AgentResult
        from services.sandbox.functions import (
            build_sandbox_executor,
            compute_code_hash,
        )

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

        start_ms = int(_time.monotonic() * 1000)
        status = "success"
        result = ""

        try:
            # sandbox 超时受 budget 约束（防止 sandbox 120s 但 budget 只剩 30s）
            _timeout = settings.sandbox_timeout
            _budget = getattr(self, "_budget", None)
            if _budget is not None and hasattr(_budget, "remaining"):
                _timeout = min(_timeout, max(_budget.remaining, 5.0))

            from services.sandbox.kernel_manager import get_kernel_manager
            executor = build_sandbox_executor(
                timeout=_timeout,
                max_result_chars=settings.sandbox_max_result_chars,
                user_id=self.user_id,
                org_id=self.org_id,
                conversation_id=self.conversation_id,
                kernel_manager=get_kernel_manager(),
            )
            result = await executor.execute(code, description)

            # 透传图片尺寸（沙盒读取的 PIL 宽高 → chat_handler 构建 image block）
            if hasattr(executor, "_image_dims") and executor._image_dims:
                if not hasattr(self, "_image_dims"):
                    self._image_dims = {}
                self._image_dims.update(executor._image_dims)

            # 透传 ECharts 配置（沙盒读取的 JSON → chat_handler 构建 chart block）
            if hasattr(executor, "_chart_options") and executor._chart_options:
                if not hasattr(self, "_chart_options"):
                    self._chart_options = {}
                self._chart_options.update(executor._chart_options)

            # 从 stdout 提取文件名注册到路径缓存（替代 file_list 的缓存注册）
            if result.status == "success" and result.summary:
                self._register_files_from_output(result.summary)

            # AgentResult 状态 → 指标状态
            if result.is_failure:
                status = "timeout" if result.status == "timeout" else "failed"

            return result
        except Exception as e:
            status = "failed"
            result = AgentResult(
                summary=f"沙盒执行异常: {e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": False},
            )
            return result
        finally:
            # Fire-and-forget: 记录执行指标
            elapsed_ms = int(_time.monotonic() * 1000) - start_ms
            _result_text = result.summary if isinstance(result, AgentResult) else str(result)
            self._record_sandbox_metric(
                description=description,
                code=code,
                status=status,
                elapsed_ms=elapsed_ms,
                result_length=len(_result_text),
            )

            # 失败时触发知识提取
            if status == "failed":
                self._record_sandbox_knowledge(description, _result_text)

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
                conn_ctx = await get_pg_connection()
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
            except Exception as e:
                logger.debug(f"Deleted files recording skipped | error={e}")

        try:
            asyncio.create_task(_do_record())
        except Exception:
            pass

    def _register_files_from_output(self, stdout: str) -> None:
        """从 code_execute 输出中提取文件名并注册到共享路径缓存。

        LLM 在沙盒中 os.listdir 发现的文件通过此方法注册，
        后续 file_delete / file_read 的路径解析继续工作。
        """
        import os
        import re

        from services.agent.file_path_cache import get_file_cache

        workspace_dir = self._get_workspace_dir()
        if not workspace_dir:
            return

        _DATA_EXTS = r"\.(?:xlsx|xls|csv|tsv|parquet|pdf|docx|pptx|txt|json|png|jpg)"
        _FILE_RE = re.compile(rf"['\"]([^'\"]*{_DATA_EXTS})['\"]", re.IGNORECASE)

        cache = get_file_cache(self.conversation_id)

        for m in _FILE_RE.finditer(stdout):
            filename = m.group(1)
            basename = os.path.basename(filename)
            candidate = os.path.join(workspace_dir, filename)
            if os.path.exists(candidate):
                cache.register(basename, os.path.realpath(candidate))

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
                cache.register(fr.filename, fr.path)
                return

        # 兜底：从 summary 文本中提取 staging 文件名
        import re
        _STAGING_RE = re.compile(r"STAGING_DIR\s*\+\s*'/([^']+)'")
        staging_dir = self._get_staging_dir()
        if not staging_dir:
            return

        cache = get_file_cache(self.conversation_id)
        for m in _STAGING_RE.finditer(result.summary):
            filename = m.group(1)
            abs_path = os.path.join(staging_dir, filename)
            if os.path.exists(abs_path):
                cache.register(filename, abs_path)

    def _get_staging_dir(self) -> str:
        """获取当前用户的 staging 目录"""
        try:
            from core.config import get_settings
            from core.workspace import resolve_staging_dir
            settings = get_settings()
            return resolve_staging_dir(
                settings.file_workspace_root, self.user_id, self.org_id,
                self.conversation_id or "default",
            )
        except Exception:
            return ""

    def _get_workspace_dir(self) -> str:
        """获取当前用户的 workspace 目录"""
        try:
            from core.config import get_settings
            from core.workspace import resolve_workspace_dir
            settings = get_settings()
            return resolve_workspace_dir(
                settings.file_workspace_root, self.user_id, self.org_id,
            )
        except Exception:
            return ""
