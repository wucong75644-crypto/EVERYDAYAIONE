"""
沙盒执行器

主进程负责: AST 验证 + 子进程生命周期管理 + 漏 emit 告警。
子进程负责: chdir + exec 用户代码 + 返回结果(sandbox_worker.py)。

产物协议: emit_chart/file/image/table (沙盒 IO 统一协议)。
LLM 漏调 emit_xxx 时主进程不再兜底上传(对齐行业标准
OpenAI/Anthropic/Jupyter),改为 WARNING 日志暴露漏调率。
"""

import time as _time
from pathlib import Path
from typing import Optional

from loguru import logger

from services.agent.agent_result import AgentResult
from services.sandbox.validators import validate_code


# 用于漏 emit 告警的常见产物扩展名
_PRODUCT_EXTS = frozenset({
    ".xlsx", ".xls", ".csv", ".tsv",
    ".png", ".jpg", ".jpeg", ".svg", ".pdf",
    ".docx", ".pptx",
})


class SandboxExecutor:
    """通用 Python 代码沙盒执行器"""

    def __init__(
        self,
        timeout: float = 120.0,
        max_result_chars: int = 8000,
        output_dir: Optional[str] = None,
        staging_dir: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        kernel_manager=None,
        conversation_id: str = "",
        skills_dir: str = "",
    ) -> None:
        self._timeout = timeout
        self._max_result_chars = max_result_chars
        self._output_dir = output_dir        # 沙盒输出目录("下载/")
        self._staging_dir = staging_dir      # staging 数据目录(中间产物)
        self._workspace_dir = workspace_dir  # 用户 workspace 目录
        self._skills_dir = skills_dir        # 文件处理技能目录(只读)
        self._kernel_manager = kernel_manager  # KernelManager(有状态模式)
        self._conversation_id = conversation_id

    async def execute(
        self, code: str, description: str = "",
    ) -> AgentResult:
        """执行 Python 代码并返回结构化结果。

        产物通过 emit_chart/file/image/table 协议返回。
        result.summary 含 [EMIT] marker,由 tool_loop_executor 解析填进
        AgentResult.emit_payloads(本函数不直接产生 emit_payloads)。
        """
        # 1. AST 安全验证(主进程,快速拦截)
        error = validate_code(code)
        if error:
            return AgentResult(
                summary=f"代码验证失败:\n{error}",
                status="error",
                error_message=error,
                metadata={"retryable": True},
            )

        logger.info(
            f"SandboxExecutor | desc={description} | "
            f"code_len={len(code)} | subprocess=spawn"
        )

        # 2. 快照 output_dir(执行后比对,漏 emit 时打 WARNING)
        snapshot_before = self._snapshot_output_dir()

        # 3. 执行代码
        raw_result = await self._execute_code(code)

        logger.info(
            f"SandboxExecutor result | desc={description} | "
            f"result_len={len(raw_result)} | result={raw_result[:200]}"
        )

        is_error = raw_result.startswith("❌")
        is_timeout = raw_result.startswith("⏱")

        # 4. 执行成功时:检查 output_dir 是否有新文件但 LLM 没 emit
        #    (不上传,只告警 - 对齐 Jupyter/OpenAI 行业标准)
        if not is_error and not is_timeout:
            self._warn_missed_emit(snapshot_before, raw_result)

        if is_error:
            return AgentResult(
                summary=raw_result.lstrip("❌ "),
                status="error",
                error_message=raw_result,
                metadata={"retryable": True},
            )
        if is_timeout:
            return AgentResult(
                summary=raw_result.lstrip("⏱ "),
                status="timeout",
                error_message=raw_result,
            )

        return AgentResult(summary=raw_result, status="success")

    async def _execute_code(self, code: str) -> str:
        """Kernel 模式单一执行路径(无 subprocess 降级)。
        Kernel 崩溃 → 销毁 → 重建 → 重试一次 → 仍失败报错。
        """
        if not (self._kernel_manager and self._conversation_id):
            return self._format_error(
                "沙盒服务未就绪,请稍后重试", retryable=True,
            )

        for attempt in range(2):
            try:
                kernel_ok = await self._kernel_manager.get_or_create(
                    self._conversation_id,
                    self._workspace_dir or "",
                    self._staging_dir or "",
                    self._output_dir or "",
                    skills_dir=self._skills_dir,
                )
                if not kernel_ok:
                    return self._format_error(
                        "沙盒资源紧张,请稍后重试", retryable=True,
                    )

                status, result = await self._kernel_manager.execute(
                    self._conversation_id, code, self._timeout,
                )

                if status != "crashed":
                    return result

                if attempt == 0:
                    logger.warning("Kernel 崩溃,尝试重建 | conv={}",
                                   self._conversation_id[:8])
                    await self._kernel_manager.destroy(self._conversation_id)
                    continue
                return self._format_error(
                    "沙盒执行异常,请稍后重试", retryable=True,
                )

            except (KeyError, RuntimeError, OSError) as e:
                logger.warning("Kernel 执行失败 | error=%s", e)
                return self._format_error(
                    f"沙盒执行失败: {e}", retryable=True,
                )

        return self._format_error("沙盒不可用", retryable=True)

    @staticmethod
    def _format_error(msg: str, retryable: bool = True) -> str:
        return f"❌ {msg}"

    def _snapshot_output_dir(self) -> dict[str, tuple[float, int]]:
        """快照 output_dir 现有文件 (执行前调用,用于漏 emit 告警)。"""
        files: dict[str, tuple[float, int]] = {}
        if not self._output_dir:
            return files
        dp = Path(self._output_dir)
        if dp.exists():
            for f in dp.iterdir():
                if f.is_file():
                    st = f.stat()
                    files[f.name] = (st.st_mtime, st.st_size)
        return files

    def _warn_missed_emit(
        self,
        snapshot_before: dict[str, tuple[float, int]],
        raw_result: str,
    ) -> None:
        """检查 output_dir 是否有新文件但 LLM 没在 result 里 emit。
        不上传,只打 WARNING 暴露漏调率(对齐 Jupyter/OpenAI/Anthropic 行业标准:
        无兜底扫描,LLM 必须显式声明产物)。
        """
        if not self._output_dir:
            return
        dp = Path(self._output_dir)
        if not dp.exists():
            return

        missed: list[str] = []
        for f in dp.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in _PRODUCT_EXTS:
                continue
            st = f.stat()
            old = snapshot_before.get(f.name)
            if old and old == (st.st_mtime, st.st_size):
                continue  # 未变化
            # 新增或覆盖,且 raw_result 没 emit 这个文件
            if f.name not in raw_result:
                missed.append(f.name)

        if missed:
            logger.warning(
                f"[MISSED_EMIT] LLM 漏调 emit_file/emit_image | "
                f"files={missed} | conv={self._conversation_id[:8]}"
            )
