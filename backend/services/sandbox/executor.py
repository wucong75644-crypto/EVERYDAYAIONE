"""
沙盒执行器

主进程负责：AST 验证、文件快照/上传检测、子进程生命周期管理。
子进程负责：chdir + exec 用户代码 + 返回结果（sandbox_worker.py）。
"""

import asyncio
import time as _time
from pathlib import Path
from typing import Callable, Dict, Optional

from loguru import logger

from services.agent.agent_result import AgentResult
from services.sandbox.validators import validate_code, truncate_result


class SandboxExecutor:
    """通用 Python 代码沙盒执行器"""

    # ECharts option JSON 最大字节数（超限降级为普通文件下载）
    _CHART_OPTION_MAX_BYTES = 512_000  # 500KB

    # 自动检测并上传的文件扩展名
    _AUTO_UPLOAD_EXTENSIONS = frozenset({
        ".xlsx", ".xls", ".csv", ".tsv",
        ".png", ".jpg", ".jpeg", ".svg", ".pdf",
        ".json", ".jsonl", ".txt",
        ".docx", ".pptx",
    })

    def __init__(
        self,
        timeout: float = 120.0,
        max_result_chars: int = 8000,
        output_dir: Optional[str] = None,
        staging_dir: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        upload_fn: Optional[Callable] = None,
        kernel_manager=None,
        conversation_id: str = "",
        skills_dir: str = "",
    ) -> None:
        self._timeout = timeout
        self._max_result_chars = max_result_chars
        self._output_dir = output_dir        # 沙盒输出目录（自动上传）
        self._staging_dir = staging_dir      # staging 数据目录
        self._workspace_dir = workspace_dir  # 用户 workspace 目录
        self._skills_dir = skills_dir        # 文件处理技能目录（只读）
        self._upload_fn = upload_fn          # 文件上传函数（注入）
        self._kernel_manager = kernel_manager  # KernelManager（有状态模式）
        self._conversation_id = conversation_id

    async def execute(
        self, code: str, description: str = "",
    ) -> AgentResult:
        """执行 Python 代码并返回结构化结果

        使用独立子进程执行（spawn），实现：
        - 进程级 cwd 隔离（os.chdir 到用户 workspace）
        - 真超时杀死（SIGTERM → SIGKILL）
        - 零状态污染（每次请求独立进程）

        主进程负责：AST 验证、文件快照、文件上传检测。
        子进程负责：chdir + exec 用户代码 + 返回结果文本。
        """
        # 1. AST 安全验证（主进程，快速拦截）
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

        # 2. 确保输出目录存在 + 快照现有文件（用于检测新生成的文件）
        self._clean_output_dir()
        self._snapshot_before = self._snapshot_output_files()

        # 3. 执行代码（优先有状态 Kernel，fallback 无状态 subprocess）
        raw_result = await self._execute_code(code)

        logger.info(
            f"SandboxExecutor result | desc={description} | "
            f"result_len={len(raw_result)} | result={raw_result[:200]}"
        )

        # 4. 执行成功时:扫 .echart.json 中转数据(staging,读完即删)
        #    再扫 output_dir 上传产物文件(下载/)
        is_error = raw_result.startswith("❌")
        is_timeout = raw_result.startswith("⏱")
        if not is_error and not is_timeout:
            self._scan_chart_options()  # staging 中转,读完即删
            file_results = await self._auto_upload_new_files()
            if file_results:
                raw_result = (raw_result or "") + "\n" + "\n".join(file_results)

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
        """Kernel 模式单一执行路径(无 subprocess 降级)
        Kernel 崩溃 → 销毁 → 重建 → 重试一次 → 仍失败报错
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
        """统一的错误返回格式(替代之前的 subprocess 降级路径)"""
        return f"❌ {msg}"

    def _clean_output_dir(self) -> None:
        """确保输出目录存在（不清空，下载文件夹是持久的）"""
        if not self._output_dir:
            return
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)

    @property
    def _upload_scan_dirs(self) -> list[str]:
        """auto_upload 监控的目录列表：仅 output_dir（host 路径，对应沙盒 '下载/'）。

        staging_dir（沙盒 'staging/'）是中间数据目录（parquet/json 等），
        不应推送给用户。工具描述已明确要求 LLM 把用户产出写到 '下载/'。
        """
        dirs: list[str] = []
        if self._output_dir:
            dirs.append(self._output_dir)
        return dirs

    def _snapshot_output_files(self) -> dict[str, tuple[float, int]]:
        """快照所有受监控目录的现有文件（执行前调用，用于检测新增或覆盖写入的文件）。

        返回 {dir/filename: (mtime, size)}，覆盖写入同名文件后 mtime/size 会变，
        对比即可检测到。
        """
        files: dict[str, tuple[float, int]] = {}
        for d in self._upload_scan_dirs:
            dp = Path(d)
            if dp.exists():
                for f in dp.iterdir():
                    if f.is_file():
                        st = f.stat()
                        files[f"{d}/{f.name}"] = (st.st_mtime, st.st_size)
        logger.info(f"SandboxExecutor snapshot | count={len(files)}")
        return files

    async def _auto_upload_new_files(self) -> list[str]:
        """扫描受监控目录中的新文件并自动上传（保留源文件供工作区下载）"""
        if not self._upload_fn:
            return []

        before: dict = getattr(self, "_snapshot_before", {})
        results = []

        for scan_dir in self._upload_scan_dirs:
            dir_path = Path(scan_dir)
            if not dir_path.exists():
                continue
            for f in dir_path.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in self._AUTO_UPLOAD_EXTENSIONS:
                    continue

                # 对比快照：新文件 OR 覆盖写入（mtime/size 变化）
                full_key = f"{scan_dir}/{f.name}"
                st = f.stat()
                old = before.get(full_key)
                if old and old == (st.st_mtime, st.st_size):
                    continue  # 未修改，跳过

                try:
                    upload_result = await self._upload_fn(f.name, st.st_size)
                    results.append(upload_result)
                    # 图片文件：读取宽高，存到实例供 image block 使用
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                        try:
                            from PIL import Image as _PILImage
                            with _PILImage.open(f) as _im:
                                if not hasattr(self, "_image_dims"):
                                    self._image_dims = {}
                                self._image_dims[f.name] = (_im.width, _im.height)
                        except Exception:
                            pass
                    # 路径协议:.echart.json 不再写到 output_dir,改到 staging
                    # 由 _scan_chart_options 单独扫描 + 即删,这里不再处理
                    logger.info(
                        f"SandboxExecutor auto-upload | file={f.name} | "
                        f"size={st.st_size} | new={old is None}"
                    )
                except Exception as e:
                    logger.error(
                        f"SandboxExecutor auto-upload failed | file={f.name} | "
                        f"error={e}"
                    )
                    results.append(f"❌ 文件上传失败: {f.name} ({e})")

        return results

    def _scan_chart_options(self) -> None:
        """扫描 staging 中的 .echart.json — 读取后立即删除(中转数据,不持久)。

        新协议:LLM 写图表配置到 "staging/x.echart.json"(相对路径),
        cwd=/workspace 解析到 host staging_dir。读完即删,避免污染
        用户 下载/ 目录(原 bug:文件留在 output_dir 持久化)。
        """
        if not self._staging_dir:
            return
        from pathlib import Path
        staging_path = Path(self._staging_dir)
        if not staging_path.exists():
            return
        for f in staging_path.iterdir():
            if not f.is_file() or not f.name.endswith(".echart.json"):
                continue
            try:
                content = f.read_text(encoding="utf-8")
                if len(content) <= self._CHART_OPTION_MAX_BYTES:
                    import json as _json
                    option = _json.loads(content)
                    if not hasattr(self, "_chart_options"):
                        self._chart_options = {}
                    self._chart_options[f.name] = option
                else:
                    logger.warning(
                        f"SandboxExecutor chart too large | "
                        f"file={f.name} | size={len(content)}"
                    )
                f.unlink()  # 中转数据,读完即删
            except Exception as e:
                logger.warning(
                    f"Chart option scan failed | file={f.name} | error={e}"
                )
