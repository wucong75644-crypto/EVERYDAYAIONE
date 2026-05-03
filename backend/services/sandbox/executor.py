"""
沙盒执行器

主进程负责：AST 验证、文件快照/上传检测、子进程生命周期管理。
子进程负责：chdir + exec 用户代码 + 返回结果（sandbox_worker.py）。
"""

import asyncio
import shutil
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
    ) -> None:
        self._timeout = timeout
        self._max_result_chars = max_result_chars
        self._output_dir = output_dir        # 沙盒输出目录（自动上传）
        self._staging_dir = staging_dir      # staging 数据目录
        self._workspace_dir = workspace_dir  # 用户 workspace 目录
        self._upload_fn = upload_fn          # 文件上传函数（注入）
        self._kernel_manager = kernel_manager  # KernelManager（有状态模式）
        self._conversation_id = conversation_id

    async def execute(self, code: str, description: str = "") -> AgentResult:
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

        # 2.5 备份已有的可上传文件（防止沙箱代码覆盖后丢失旧数据）
        file_backups = self._backup_existing_files()

        # 3. 执行代码（优先有状态 Kernel，fallback 无状态 subprocess）
        raw_result = await self._execute_code(code)

        logger.info(
            f"SandboxExecutor result | desc={description} | "
            f"result_len={len(raw_result)} | result={raw_result[:200]}"
        )

        # 4. 同名文件保护：覆盖检测 + Google Drive 风格重命名
        self._dedup_overwritten_files(file_backups)

        # 5. 自动检测生成的文件并上传（追加在截断后的文本末尾）
        file_results = await self._auto_upload_new_files()
        if file_results:
            raw_result = (raw_result or "") + "\n" + "\n".join(file_results)

        # 6. 包装为 AgentResult（根据子进程返回的前缀判断状态）
        if raw_result.startswith("❌"):
            return AgentResult(
                summary=raw_result.lstrip("❌ "),
                status="error",
                error_message=raw_result,
                metadata={"retryable": True},
            )
        if raw_result.startswith("⏱"):
            return AgentResult(
                summary=raw_result.lstrip("⏱ "),
                status="timeout",
                error_message=raw_result,
            )
        return AgentResult(summary=raw_result, status="success")

    async def _execute_code(self, code: str) -> str:
        """选择执行模式：有状态 Kernel 或无状态 subprocess"""
        if self._kernel_manager and self._conversation_id:
            try:
                kernel_ok = await self._kernel_manager.get_or_create(
                    self._conversation_id,
                    self._workspace_dir or "",
                    self._staging_dir or "",
                    self._output_dir or "",
                )
                if kernel_ok:
                    status, result = await self._kernel_manager.execute(
                        self._conversation_id, code, self._timeout,
                    )
                    return result
            except (KeyError, RuntimeError, OSError) as e:
                # Kernel 竞态死亡或启动失败，降级为无状态 subprocess
                logger.warning("Kernel 执行失败，降级为无状态 | error=%s", e)

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
            return (
                f"⏱ 代码执行超时（{self._timeout}秒）。\n"
                "建议：缩小查询范围、减少数据量、或分批处理。"
            )
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

    # --------------------------------------------------
    # Google Drive 风格同名文件保护
    # --------------------------------------------------

    def _backup_existing_files(self) -> dict[str, str]:
        """执行前备份已有的可上传文件，防止沙箱代码覆盖后丢失旧数据。

        Returns:
            {原始路径: 备份路径}，备份文件以 .dedup_bak 后缀存放在同目录。
        """
        backups: dict[str, str] = {}
        for d in self._upload_scan_dirs:
            dp = Path(d)
            if not dp.exists():
                continue
            for f in dp.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in self._AUTO_UPLOAD_EXTENSIONS:
                    continue
                backup = f.with_suffix(f.suffix + ".dedup_bak")
                shutil.copy2(f, backup)  # 保留 mtime
                backups[str(f)] = str(backup)
        if backups:
            logger.info(
                f"SandboxExecutor backup | count={len(backups)} | "
                f"files={[Path(p).name for p in backups]}"
            )
        return backups

    def _dedup_overwritten_files(self, backups: dict[str, str]) -> None:
        """执行后检测被覆盖的文件，Google Drive 风格重命名新文件、恢复旧文件。

        策略：新文件改名为 name (N).ext，旧文件恢复原名——保证历史 CDN URL 不失效。
        """
        for orig_path, backup_path in backups.items():
            orig = Path(orig_path)
            backup = Path(backup_path)
            if not backup.exists():
                continue
            if not orig.exists():
                # 原文件被删除（罕见），恢复备份
                backup.rename(orig)
                continue
            # 对比 mtime+size：不同 = 被覆盖
            orig_st = orig.stat()
            backup_st = backup.stat()
            if (orig_st.st_mtime, orig_st.st_size) != (
                backup_st.st_mtime, backup_st.st_size
            ):
                # 沙箱代码覆盖了同名文件 → 重命名新文件，恢复旧文件
                dedup_name = self._next_available_name(orig)
                orig.rename(dedup_name)   # 新内容 → name (N).ext
                backup.rename(orig)       # 旧内容 → 恢复原名
                logger.info(
                    f"SandboxExecutor dedup | "
                    f"old={orig.name} kept | new={dedup_name.name}"
                )
            else:
                # 未被覆盖，删除备份
                backup.unlink()

    @staticmethod
    def _next_available_name(path: Path) -> Path:
        """Google Drive 风格：name (1).ext, name (2).ext, ..."""
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        n = 1
        while True:
            candidate = parent / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
            n += 1

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
