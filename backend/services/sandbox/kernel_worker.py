"""
有状态沙盒 Kernel Worker（长驻 REPL 进程）

通过 stdin/stdout JSON-Line 协议与 KernelManager 通信。
sandbox_globals 在进程生命周期内持续存在，变量跨调用保留。

通信协议：
  - 输入（stdin）：{"id": "req_001", "code": "...", "timeout": 120}
  - 输出（stdout）：{"id": "req_001", "status": "ok|error|timeout", "result": "...", "elapsed_ms": 230}

安全措施（纵深防御）：
  - 外层：nsjail namespace + cgroups + chroot（由 KernelManager 负责）
  - 内层：复用 sandbox_worker.py 的 L1-L7 安全层
  - 有状态特有：每次执行前重置 __builtins__ / open / __import__（防跨调用篡改）
"""

import json
import sys
import time as _time
import traceback
from typing import Dict, Any, Optional

from services.sandbox.sandbox_worker import (
    _clean_env,
    _apply_resource_limits,
    _build_sandbox_globals,
    _exec_code,
    build_scoped_open,
)
from services.sandbox.sandbox_constants import (
    SAFE_BUILTINS,
)
from services.sandbox.validators import validate_code, truncate_result


def _setup_scoped_open(workspace_dir: str, staging_dir: str, output_dir: str):
    """构建带路径安全检查的 open 函数。

    委托 build_scoped_open()（sandbox_worker.py 统一定义），
    每次执行前重置，避免路径泄漏到下一个 conversation。
    """
    import builtins

    _original_open = builtins.open.__wrapped__ if hasattr(builtins.open, "__wrapped__") else builtins.open
    if not hasattr(_setup_scoped_open, "_original_open"):
        _setup_scoped_open._original_open = _original_open
    else:
        _original_open = _setup_scoped_open._original_open

    _global_scoped_open = build_scoped_open(
        workspace_dir, staging_dir, output_dir,
        original_open=_original_open,
    )

    return _global_scoped_open


def _reset_security(
    sandbox_globals: Dict[str, Any],
    scoped_open,
    scoped_os=None,
    scoped_shutil=None,
    scoped_import=None,
) -> None:
    """每次执行前重置安全关键项（防跨调用篡改）

    有状态 Kernel 中用户代码可能覆盖 __builtins__、open、os、shutil，
    必须在每次执行前重置到安全状态。
    """
    import builtins

    # 重置 builtins 白名单（copy 后注入 scoped_import）
    safe = SAFE_BUILTINS.copy()
    if scoped_import is not None:
        safe["__import__"] = scoped_import
    sandbox_globals["__builtins__"] = safe

    # 重置 open
    builtins.open = scoped_open
    sandbox_globals["open"] = scoped_open

    # 重置 os / shutil（防用户 del os 或 os = None）
    if scoped_os is not None:
        sandbox_globals["os"] = scoped_os
    if scoped_shutil is not None:
        sandbox_globals["shutil"] = scoped_shutil


def _hide_paths(result: str, output_dir: str, workspace_dir: str) -> str:
    """路径替换为变量名（LLM 可直接用 OUTPUT_DIR/WORKSPACE_DIR 引用文件）"""
    if result and output_dir:
        result = result.replace(output_dir, "OUTPUT_DIR")
    if result and workspace_dir:
        result = result.replace(workspace_dir, "WORKSPACE_DIR")
    return result


def _read_request() -> Optional[Dict[str, Any]]:
    """从 stdin 读取一行 JSON 请求，EOF 返回 None"""
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        return json.loads(line.strip())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # 协议错误：返回错误响应后继续
        sys.stderr.write(f"[kernel_worker] JSON 解析失败: {e}\n")
        return {"id": "__malformed__", "code": "", "timeout": 0, "_error": str(e)}


def _write_response(response: Dict[str, Any]) -> None:
    """向 stdout 写入一行 JSON 响应"""
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def kernel_main(workspace_dir: str, staging_dir: str, output_dir: str,
                max_result_chars: int = 8000) -> None:
    """Kernel Worker 主入口

    Args:
        workspace_dir: 工作区目录（jail 内路径，如 /workspace）
        staging_dir: staging 数据目录（jail 内路径，如 /staging）
        output_dir: 输出目录（jail 内路径，如 /output）
        max_result_chars: 结果最大字符数
    """
    import os

    # 1. 安全初始化（进程生命周期内只做一次）
    _clean_env()
    _apply_resource_limits()

    # 2. 切换到 workspace
    if workspace_dir:
        os.makedirs(workspace_dir, exist_ok=True)
        os.chdir(workspace_dir)
    if staging_dir:
        os.makedirs(staging_dir, exist_ok=True)

    # 3. 构建 scoped open（进程生命周期内复用同一个闭包）
    scoped_open = _setup_scoped_open(workspace_dir, staging_dir, output_dir)

    # 4. 替换 builtins.open + io.open
    import builtins
    import io as _io
    builtins.open = scoped_open
    _io.open = scoped_open  # 堵住 io.open 绕过沙盒的漏洞

    # 5. 构建沙盒 globals（变量跨调用保留）
    sandbox_globals = _build_sandbox_globals(workspace_dir, staging_dir, output_dir)

    # 取 scoped 引用（_reset_security 每次执行前重置用）
    _scoped_os = sandbox_globals.get("os")
    _scoped_shutil = sandbox_globals.get("shutil")
    _scoped_import = sandbox_globals["__builtins__"]["__import__"]

    # 6. 通知主进程 Kernel 就绪
    _write_response({"id": "__ready__", "status": "ok", "result": "kernel ready"})

    # 7. REPL 主循环
    while True:
        request = _read_request()
        if request is None:
            break  # stdin 关闭 = 主进程要求退出

        req_id = request.get("id", "unknown")

        # 协议错误处理
        if "_error" in request:
            _write_response({
                "id": req_id,
                "status": "error",
                "result": f"❌ 协议错误: {request['_error']}",
                "elapsed_ms": 0,
            })
            continue

        code = request.get("code", "")
        timeout = request.get("timeout", 120.0)
        confirm_delete = request.get("confirm_delete", [])

        if not code or not code.strip():
            _write_response({
                "id": req_id,
                "status": "error",
                "result": "❌ 代码不能为空",
                "elapsed_ms": 0,
            })
            continue

        start = _time.monotonic()

        try:
            # AST 预检（每次执行都验证）
            error = validate_code(code)
            if error:
                elapsed = int((_time.monotonic() - start) * 1000)
                _write_response({
                    "id": req_id,
                    "status": "error",
                    "result": f"❌ 代码验证失败:\n{error}",
                    "elapsed_ms": elapsed,
                })
                continue

            # 重置安全关键项（防跨调用篡改）
            _reset_security(
                sandbox_globals, scoped_open,
                _scoped_os, _scoped_shutil, _scoped_import,
            )

            # 设置本次执行允许删除的文件（每次都调用，确保上一轮不残留）
            if hasattr(_scoped_os, "_set_confirmed_deletes"):
                _scoped_os._set_confirmed_deletes(confirm_delete)

            # 执行代码（sandbox_globals 在进程内持续存在，变量保留）
            result = _exec_code(code, sandbox_globals, timeout)

            # 路径隐藏
            result = _hide_paths(result, output_dir, workspace_dir)

            # 截断
            result = truncate_result(result, max_result_chars)

            # 判断状态
            if result.startswith("⏱"):
                status = "timeout"
            elif result.startswith("❌"):
                status = "error"
            else:
                status = "ok"

            elapsed = int((_time.monotonic() - start) * 1000)
            _write_response({
                "id": req_id,
                "status": status,
                "result": result,
                "elapsed_ms": elapsed,
            })

        except Exception as e:
            elapsed = int((_time.monotonic() - start) * 1000)
            tb = traceback.format_exc()
            tb_lines = tb.strip().split("\n")
            short_tb = "\n".join(tb_lines[-3:])
            _write_response({
                "id": req_id,
                "status": "error",
                "result": f"❌ 执行错误:\n{short_tb}",
                "elapsed_ms": elapsed,
            })


if __name__ == "__main__":
    # 命令行启动：python kernel_worker.py <workspace_dir> <staging_dir> <output_dir> [max_result_chars]
    import sys as _sys
    if len(_sys.argv) < 4:
        print("Usage: kernel_worker.py <workspace_dir> <staging_dir> <output_dir> [max_result_chars]",
              file=_sys.stderr)
        _sys.exit(1)

    _workspace = _sys.argv[1]
    _staging = _sys.argv[2]
    _output = _sys.argv[3]
    _max_chars = int(_sys.argv[4]) if len(_sys.argv) > 4 else 8000

    kernel_main(_workspace, _staging, _output, _max_chars)
