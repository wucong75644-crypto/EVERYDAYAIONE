"""DuckDB 子进程导出（内存隔离）

从 erp_unified_query.py 拆出，保持引擎文件 < 500 行。
父进程通过 subprocess 执行 core.export_worker，通信协议见 export_worker.py。
"""

from __future__ import annotations

import asyncio
import json as _json
import sys as _sys
from pathlib import Path
from typing import Any

from core.config import get_settings


async def subprocess_export(
    query: str,
    output_path: str,
    push_thinking: Any = None,
) -> dict[str, int | float | str]:
    """子进程执行 DuckDB 导出，内存隔离，实时进度推送。

    Returns:
        export_worker 的 stdout JSON：{"row_count": N, "size_kb": F, ...}
    Raises:
        TimeoutError: 子进程超时
        RuntimeError: 子进程非零退出
    """
    settings = get_settings()
    timeout = settings.export_subprocess_timeout

    params = _json.dumps({
        "query": query,
        "output_path": output_path,
        "pg_url": settings.database_url,
        "memory_limit": settings.duckdb_memory_limit,
        "threads": settings.duckdb_threads,
        "timeout": timeout - 5,
    })

    proc = await asyncio.create_subprocess_exec(
        _sys.executable, "-m", "core.export_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[2]),  # backend/
    )

    proc.stdin.write(params.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    # stderr 完整收集：JSON 进度行推 thinking，所有行保留供错误诊断
    stderr_lines: list[str] = []

    async def _consume_stderr() -> None:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            stderr_lines.append(text)
            if push_thinking:
                try:
                    progress = _json.loads(text)
                    msg = _format_progress(progress)
                    if msg:
                        await push_thinking(msg)
                except (ValueError, KeyError):
                    pass  # 非进度行（traceback 等），已收集在 stderr_lines

    stderr_task = asyncio.create_task(_consume_stderr())
    try:
        stdout = await asyncio.wait_for(
            proc.stdout.read(), timeout=timeout,
        )
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"Export subprocess timed out after {timeout}s"
        )
    finally:
        # 确保 stderr 消费任务完整结束，不中断正在执行的 push_thinking
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass

    if proc.returncode != 0:
        detail = "\n".join(stderr_lines[-10:])
        raise RuntimeError(
            f"Export subprocess failed (exit={proc.returncode}):\n{detail}"
        )

    return _json.loads(stdout.decode())


def _format_progress(progress: dict) -> str | None:
    """将子进程进度 JSON 格式化为 thinking 文案。"""
    phase = progress.get("phase")
    if phase == "connect":
        return "正在连接数据库..."
    if phase == "export":
        size = progress.get("size_kb", 0)
        elapsed = progress.get("elapsed", 0)
        if size > 1024:
            return f"正在导出数据... {size / 1024:.1f}MB 已写入（{elapsed:.0f}s）"
        return f"正在导出数据... {size:.0f}KB 已写入（{elapsed:.0f}s）"
    if phase == "done":
        rows = progress.get("row_count", 0)
        size = progress.get("size_kb", 0)
        elapsed = progress.get("elapsed", 0)
        if size > 1024:
            return f"导出完成：{rows:,} 行，{size / 1024:.1f}MB（{elapsed:.0f}s）"
        return f"导出完成：{rows:,} 行，{size:.0f}KB（{elapsed:.0f}s）"
    return None
