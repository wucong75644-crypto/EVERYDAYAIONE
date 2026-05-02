"""
Kernel 进程池管理器

按 conversation_id 分配持久 Python 进程，变量跨调用保留。
空闲超时回收，最大进程数限制，超出降级为无状态 subprocess。

生命周期：
  - main.py lifespan 启动时初始化（start）
  - main.py lifespan 关闭时清理（shutdown）
  - cleanup_idle 定时任务每 60 秒扫描回收

通信协议：
  - 通过 stdin/stdout JSON-Line 与 kernel_worker.py 交互
  - 详见 kernel_worker.py 文档
"""

import asyncio
import json
import logging
import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# nsjail 路径查找
_NSJAIL_PATH: Optional[str] = shutil.which("nsjail")

# 模块级单例（main.py lifespan 中初始化）
_instance: Optional["KernelManager"] = None


def get_kernel_manager() -> Optional["KernelManager"]:
    """获取全局 KernelManager 实例（未初始化时返回 None → 降级无状态）"""
    return _instance


def set_kernel_manager(manager: Optional["KernelManager"]) -> None:
    """设置全局 KernelManager 实例（main.py lifespan 调用）"""
    global _instance
    _instance = manager


@dataclass
class Kernel:
    """单个 Kernel 实例"""
    conversation_id: str
    process: asyncio.subprocess.Process
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)
    # 宿主机路径（给 executor 文件上传用）
    host_workspace: str = ""
    host_staging: str = ""
    host_output: str = ""


def _make_pdeathsig_fn():
    """创建 preexec_fn：父进程死亡时子进程收 SIGTERM（Linux only）"""
    if platform.system() != "Linux":
        return None
    try:
        import ctypes
        import signal
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
        _PR_SET_PDEATHSIG = 1

        def _set_pdeathsig():
            _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)

        return _set_pdeathsig
    except (OSError, AttributeError):
        return None


class KernelManager:
    """Kernel 进程池管理器"""

    MAX_KERNELS = 4           # 最大同时存活 Kernel 数
    IDLE_TIMEOUT = 1200.0     # 空闲 20 分钟回收
    MAX_LIFETIME = 1800.0     # 最大存活 30 分钟强制重建
    READY_TIMEOUT = 10.0      # Kernel 启动就绪超时
    CLEANUP_INTERVAL = 60.0   # 清理扫描间隔

    def __init__(self, nsjail_cfg: Optional[str] = None):
        """
        Args:
            nsjail_cfg: nsjail 配置文件路径（None 则不使用 nsjail）
        """
        self._kernels: Dict[str, Kernel] = {}
        self._nsjail_cfg = nsjail_cfg
        self._cleanup_task: Optional[asyncio.Task] = None
        self._pdeathsig_fn = _make_pdeathsig_fn()
        self._started = False
        # backend 目录（kernel_worker 作为模块启动时需要 cwd）
        self._backend_dir = str(Path(__file__).resolve().parent.parent.parent)

    async def start(self) -> None:
        """启动清理定时任务"""
        if self._started:
            return
        self._started = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("KernelManager started | max_kernels=%d idle_timeout=%ds",
                     self.MAX_KERNELS, int(self.IDLE_TIMEOUT))

    async def shutdown(self) -> None:
        """关闭所有 Kernel 并停止清理任务"""
        self._started = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # 并发关闭所有 Kernel
        conv_ids = list(self._kernels.keys())
        if conv_ids:
            await asyncio.gather(
                *(self._destroy_kernel(cid) for cid in conv_ids),
                return_exceptions=True,
            )
        logger.info("KernelManager shutdown | destroyed=%d kernels", len(conv_ids))

    def active_count(self) -> int:
        """当前活跃 Kernel 数"""
        return len(self._kernels)

    async def get_or_create(
        self,
        conversation_id: str,
        workspace_dir: str,
        staging_dir: str,
        output_dir: str,
    ) -> bool:
        """获取或创建 Kernel

        Args:
            conversation_id: 对话 ID
            workspace_dir: 宿主机 workspace 路径
            staging_dir: 宿主机 staging 路径
            output_dir: 宿主机 output 路径

        Returns:
            True = Kernel 可用，False = 降级为无状态
        """
        # 复用已有 Kernel
        kernel = self._kernels.get(conversation_id)
        if kernel and self._is_alive(kernel):
            kernel.last_active = time.monotonic()
            return True

        # 已死亡的 Kernel 清理
        if kernel:
            await self._destroy_kernel(conversation_id)

        # 尝试创建新 Kernel
        if len(self._kernels) >= self.MAX_KERNELS:
            # 驱逐最久空闲的
            evicted = await self._evict_idle()
            if not evicted:
                logger.warning("KernelManager 降级为无状态 | active=%d max=%d conv=%s",
                               len(self._kernels), self.MAX_KERNELS, conversation_id[:8])
                return False

        try:
            kernel = await self._spawn_kernel(
                conversation_id, workspace_dir, staging_dir, output_dir,
            )
            self._kernels[conversation_id] = kernel
            logger.info("Kernel 创建成功 | conv=%s active=%d",
                        conversation_id[:8], len(self._kernels))
            return True
        except Exception as e:
            logger.error("Kernel 创建失败，降级为无状态 | conv=%s error=%s",
                         conversation_id[:8], e)
            return False

    async def execute(
        self,
        conversation_id: str,
        code: str,
        timeout: float,
    ) -> Tuple[str, str]:
        """向 Kernel 发送代码并等待结果

        Args:
            conversation_id: 对话 ID
            code: 用户代码
            timeout: 执行超时（秒）

        Returns:
            (status, result) — status: "ok" | "error" | "timeout"

        Raises:
            KeyError: conversation_id 对应的 Kernel 不存在
        """
        kernel = self._kernels.get(conversation_id)
        if not kernel or not self._is_alive(kernel):
            if kernel:
                await self._destroy_kernel(conversation_id)
            raise KeyError(f"Kernel 不存在或已死亡: {conversation_id[:8]}")

        async with kernel.lock:
            kernel.last_active = time.monotonic()
            return await self._send_and_recv(kernel, code, timeout)

    async def destroy(self, conversation_id: str) -> None:
        """销毁指定 Kernel"""
        await self._destroy_kernel(conversation_id)

    # ── 内部方法 ──

    def _is_alive(self, kernel: Kernel) -> bool:
        """检查 Kernel 进程是否存活"""
        return kernel.process.returncode is None

    async def _spawn_kernel(
        self,
        conversation_id: str,
        workspace_dir: str,
        staging_dir: str,
        output_dir: str,
    ) -> Kernel:
        """启动 Kernel 子进程"""
        cmd = self._build_command(workspace_dir, staging_dir, output_dir)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._backend_dir,
            preexec_fn=self._pdeathsig_fn,
        )

        # 等待 ready 信号
        try:
            ready_line = await asyncio.wait_for(
                process.stdout.readline(), timeout=self.READY_TIMEOUT,
            )
            ready = json.loads(ready_line)
            if ready.get("status") != "ok":
                raise RuntimeError(f"Kernel ready 失败: {ready}")
        except (asyncio.TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            process.kill()
            await process.wait()
            raise RuntimeError(f"Kernel 启动超时或协议错误: {e}") from e

        kernel = Kernel(
            conversation_id=conversation_id,
            process=process,
            host_workspace=workspace_dir,
            host_staging=staging_dir,
            host_output=output_dir,
        )
        return kernel

    def _build_command(
        self, workspace_dir: str, staging_dir: str, output_dir: str,
    ) -> list:
        """构建启动命令（nsjail 或裸 python）"""
        if self._nsjail_cfg and _NSJAIL_PATH:
            return [
                _NSJAIL_PATH, "--config", self._nsjail_cfg,
                "-B", f"{workspace_dir}:/workspace",
                "-B", f"{staging_dir}:/staging",
                "-B", f"{output_dir}:/output",
                "--", "/venv/bin/python3", "-u",
                "-m", "services.sandbox.kernel_worker",
                "/workspace", "/staging", "/output",
            ]
        # 无 nsjail：直接启动（保留 L1-L7 安全层）
        return [
            sys.executable, "-u",
            "-m", "services.sandbox.kernel_worker",
            workspace_dir, staging_dir, output_dir,
        ]

    async def _send_and_recv(
        self, kernel: Kernel, code: str, timeout: float,
    ) -> Tuple[str, str]:
        """发送代码并等待结果"""
        req_id = f"req_{int(time.monotonic() * 1000)}"
        request = {"id": req_id, "code": code, "timeout": timeout}
        request_line = json.dumps(request, ensure_ascii=False) + "\n"

        try:
            kernel.process.stdin.write(request_line.encode())
            await kernel.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return "error", "❌ Kernel 进程已断开，环境已重置"

        # 等待响应（timeout + 缓冲时间）
        try:
            response_line = await asyncio.wait_for(
                kernel.process.stdout.readline(),
                timeout=timeout + 10,
            )
        except asyncio.TimeoutError:
            return "timeout", f"⏱ Kernel 响应超时（{timeout}秒）"

        if not response_line:
            return "error", "❌ Kernel 进程已退出，环境已重置"

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError:
            return "error", f"❌ Kernel 返回无效 JSON: {response_line[:200]}"

        return response.get("status", "error"), response.get("result", "")

    async def _destroy_kernel(self, conversation_id: str) -> None:
        """安全销毁 Kernel"""
        kernel = self._kernels.pop(conversation_id, None)
        if not kernel:
            return

        if self._is_alive(kernel):
            try:
                kernel.process.stdin.close()
                # 等待进程正常退出
                await asyncio.wait_for(kernel.process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                # 强制杀死
                try:
                    kernel.process.kill()
                    await kernel.process.wait()
                except (ProcessLookupError, OSError):
                    pass

        logger.info("Kernel 已销毁 | conv=%s lifetime=%.0fs",
                    conversation_id[:8], time.monotonic() - kernel.created_at)

    async def _evict_idle(self) -> bool:
        """驱逐最久空闲的 Kernel，返回是否成功"""
        if not self._kernels:
            return False

        # 按 last_active 升序排序，驱逐最久未活跃的
        oldest_id = min(self._kernels, key=lambda k: self._kernels[k].last_active)
        await self._destroy_kernel(oldest_id)
        return True

    async def _cleanup_loop(self) -> None:
        """定时清理空闲和超龄 Kernel"""
        while self._started:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("KernelManager cleanup 异常 | error=%s", e)

    async def _cleanup_idle(self) -> None:
        """清理空闲和超龄 Kernel"""
        now = time.monotonic()
        to_destroy = []

        for conv_id, kernel in list(self._kernels.items()):
            if not self._is_alive(kernel):
                to_destroy.append((conv_id, "dead"))
            elif now - kernel.last_active > self.IDLE_TIMEOUT:
                to_destroy.append((conv_id, "idle"))
            elif now - kernel.created_at > self.MAX_LIFETIME:
                to_destroy.append((conv_id, "expired"))

        for conv_id, reason in to_destroy:
            await self._destroy_kernel(conv_id)
            logger.info("Kernel 自动回收 | conv=%s reason=%s active=%d",
                        conv_id[:8], reason, len(self._kernels))
