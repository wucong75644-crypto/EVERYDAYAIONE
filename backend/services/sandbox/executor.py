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

        # 4. 执行成功时才上传生成的文件（失败的半成品不交付给用户）
        is_error = raw_result.startswith("❌")
        is_timeout = raw_result.startswith("⏱")
        if not is_error and not is_timeout:
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
        """选择执行模式：有状态 Kernel 或无状态 subprocess

        崩溃恢复策略（对标 Jupyter autorestart）：
        Kernel 崩溃 → 销毁 → 重建 → 重试一次 → 再失败降级 subprocess
        """
        if self._kernel_manager and self._conversation_id:
            for attempt in range(2):  # 最多 2 次：首次 + 崩溃重试
                try:
                    kernel_ok = await self._kernel_manager.get_or_create(
                        self._conversation_id,
                        self._workspace_dir or "",
                        self._staging_dir or "",
                        self._output_dir or "",
                        skills_dir=self._skills_dir,
                    )
                    if not kernel_ok:
                        break  # 池满且无法驱逐，直接降级

                    status, result = await self._kernel_manager.execute(
                        self._conversation_id, code, self._timeout,
                    )

                    if status != "crashed":
                        return result

                    # Kernel 崩溃：销毁后重试一次
                    if attempt == 0:
                        logger.warning("Kernel 崩溃，尝试重建 | conv={}",
                                       self._conversation_id[:8])
                        await self._kernel_manager.destroy(self._conversation_id)
                        continue
                    # 第二次仍崩溃，降级
                    logger.warning("Kernel 重建后仍崩溃，降级为无状态 | conv={}",
                                   self._conversation_id[:8])
                    break

                except (KeyError, RuntimeError, OSError) as e:
                    logger.warning("Kernel 执行失败，降级为无状态 | error=%s", e)
                    break

        # 降级：无状态 subprocess
        return await self._run_in_subprocess(code)

    async def _run_in_subprocess(self, code: str) -> str:
        """在独立子进程中执行代码（spawn 隔离）

        通信协议：子进程通过 Queue 返回 (status, result_text)。
        超时策略：SIGTERM → 5s → SIGKILL。
        """
        import multiprocessing as mp
        import os
        import signal

        from services.sandbox.sandbox_worker import sandbox_worker_entry

        ctx = mp.get_context("spawn")
        result_queue = ctx.Queue()

        proc = ctx.Process(
            target=sandbox_worker_entry,
            args=(
                result_queue,
                code,
                self._workspace_dir or "",
                self._staging_dir or "",
                self._output_dir or "",
                self._timeout,
                self._max_result_chars,
                self._skills_dir,
            ),
        )
        proc.start()
        logger.debug(f"Sandbox subprocess started | pid={proc.pid}")

        try:
            # 在线程池中等待结果（不阻塞 event loop）
            loop = asyncio.get_running_loop()
            status, result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._wait_for_result, result_queue, proc,
                    self._timeout,
                ),
                timeout=self._timeout + 10,  # 留 10s 余量给进程启动
            )
            if status == "error":
                logger.warning(f"Sandbox subprocess error | pid={proc.pid}")
            return result

        except asyncio.TimeoutError:
            logger.warning(
                f"Sandbox subprocess timeout | pid={proc.pid} | "
                f"timeout={self._timeout}s"
            )
            # 优雅退出：SIGTERM → 5s → SIGKILL
            self._kill_process(proc)
            from services.sandbox.sandbox_constants import TIMEOUT_MESSAGE
            return TIMEOUT_MESSAGE.format(timeout=self._timeout)
        except Exception as e:
            logger.error(f"Sandbox subprocess failed | pid={proc.pid} | error={e}")
            self._kill_process(proc)
            return f"❌ 沙盒进程异常: {e}"
        finally:
            # 确保进程退出 + 清理 Queue
            if proc.is_alive():
                self._kill_process(proc)
            else:
                proc.join(timeout=3)
            try:
                result_queue.close()
                result_queue.join_thread()
            except Exception:
                pass

    @staticmethod
    def _wait_for_result(result_queue, proc, timeout: float) -> tuple:
        """阻塞等待子进程结果（在线程池中调用）"""
        import queue as _queue
        try:
            return result_queue.get(timeout=timeout + 5)  # 比子进程 timeout 多留 5s
        except _queue.Empty:
            # 子进程没写结果就退了
            exitcode = proc.exitcode
            if exitcode is not None and exitcode < 0:
                import signal
                sig_name = signal.Signals(-exitcode).name
                return ("error", f"❌ 沙盒进程被信号终止: {sig_name}")
            return ("error", "❌ 沙盒进程无响应（可能 OOM 或崩溃）")

    @staticmethod
    def _kill_process(proc):
        """优雅杀死子进程：SIGTERM → 5s → SIGKILL"""
        import os
        import signal

        if not proc.is_alive():
            proc.join(timeout=3)
            return

        # 先 SIGTERM（给代码清理机会）
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        proc.join(timeout=5)

        # 还没死就 SIGKILL
        if proc.is_alive():
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.join(timeout=3)

    # ========================================
    # 自动文件检测与上传
    # ========================================

    def _clean_output_dir(self) -> None:
        """确保输出目录存在（不清空，下载文件夹是持久的）"""
        if not self._output_dir:
            return
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)

    @property
    def _upload_scan_dirs(self) -> list[str]:
        """auto_upload 监控的目录列表：仅 OUTPUT_DIR。

        STAGING_DIR 是中间数据目录（parquet/json 等），不应推送给用户。
        工具描述已明确要求 LLM 将用户产出写到 OUTPUT_DIR。
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
                    # .echart.json 文件：读取 ECharts option，存到实例供 chart block 使用
                    if f.name.endswith(".echart.json"):
                        try:
                            _content = f.read_text(encoding="utf-8")
                            if len(_content) <= self._CHART_OPTION_MAX_BYTES:
                                import json as _json
                                _option = _json.loads(_content)
                                if not hasattr(self, "_chart_options"):
                                    self._chart_options = {}
                                self._chart_options[f.name] = _option
                            else:
                                logger.warning(
                                    f"SandboxExecutor chart too large | "
                                    f"file={f.name} | size={len(_content)}"
                                )
                        except Exception as _ce:
                            logger.warning(
                                f"SandboxExecutor chart parse failed | "
                                f"file={f.name} | error={_ce}"
                            )
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
