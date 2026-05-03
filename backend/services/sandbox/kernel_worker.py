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
    _find_similar_file_global,
)
from services.sandbox.sandbox_constants import (
    SAFE_BUILTINS,
    restricted_import,
)
from services.sandbox.validators import validate_code, truncate_result


def _setup_scoped_open(workspace_dir: str, staging_dir: str, output_dir: str):
    """构建带路径安全检查 + 文件名纠错的 open 函数

    返回 _global_scoped_open 闭包，用于替换 builtins.open。
    与 sandbox_worker.py 中的逻辑一致，但提取为独立函数以便每次执行前重置。
    """
    import os
    import builtins
    import tempfile as _tempfile

    _original_open = builtins.open.__wrapped__ if hasattr(builtins.open, "__wrapped__") else builtins.open
    # 首次调用时保存原始 open
    if not hasattr(_setup_scoped_open, "_original_open"):
        _setup_scoped_open._original_open = _original_open
    else:
        _original_open = _setup_scoped_open._original_open

    _ws_dir = workspace_dir

    # 安全白名单
    _allowed_prefixes = [os.path.realpath(_ws_dir)]
    if staging_dir:
        _allowed_prefixes.append(os.path.realpath(staging_dir))
    if output_dir:
        _allowed_prefixes.append(os.path.realpath(output_dir))
    _allowed_prefixes.append(os.path.realpath(_tempfile.gettempdir()))

    _readonly_system_files = frozenset({
        "/etc/apache2/mime.types",
        "/private/etc/apache2/mime.types",
        "/etc/mime.types",
        "/usr/share/misc/mime.types",
        "/usr/share/zoneinfo",
    })

    def _global_scoped_open(path, mode="r", *args, **kwargs):
        path_str = str(path)
        if not os.path.isabs(path_str):
            path_str = os.path.join(_ws_dir, path_str)
        resolved = os.path.realpath(path_str)

        _in_whitelist = any(
            resolved.startswith(prefix + os.sep) or resolved == prefix
            for prefix in _allowed_prefixes
        )
        if not _in_whitelist:
            _is_readonly_system = (
                "r" in mode
                and "w" not in mode
                and "a" not in mode
                and (
                    resolved in _readonly_system_files
                    or any(resolved.startswith(f + "/") for f in _readonly_system_files)
                )
            )
            if not _is_readonly_system:
                raise PermissionError(f"文件访问被拒绝：{path} 不在允许的目录内")

        # 文件不存在时自动纠错：当前目录 → OUTPUT_DIR → STAGING_DIR
        if "r" in mode and not os.path.exists(resolved):
            _basename = os.path.basename(resolved)
            suggestion = _find_similar_file_global(resolved, _ws_dir)
            if not suggestion:
                for _fallback_dir in (output_dir, staging_dir):
                    if not _fallback_dir:
                        continue
                    _alt = os.path.join(_fallback_dir, _basename)
                    if os.path.exists(_alt):
                        suggestion = _alt
                        break
                    _alt_suggestion = _find_similar_file_global(_alt, _ws_dir)
                    if _alt_suggestion:
                        suggestion = _alt_suggestion
                        break
            if suggestion and os.path.exists(suggestion):
                print(
                    f"[sandbox] 文件名自动纠正: {path} → {os.path.basename(suggestion)}",
                    file=sys.stderr,
                )
                return _original_open(suggestion, mode, *args, **kwargs)
            msg = f"文件不存在: {path}"
            if suggestion:
                msg += f"。你是否要找: {os.path.basename(suggestion)}？"
            raise FileNotFoundError(msg)

        return _original_open(resolved, mode, *args, **kwargs)

    return _global_scoped_open


def _reset_security(
    sandbox_globals: Dict[str, Any],
    scoped_open,
) -> None:
    """每次执行前重置安全关键项（防跨调用篡改）

    有状态 Kernel 中用户代码可能覆盖 __builtins__、open、__import__，
    必须在每次执行前重置到安全状态。

    注意：不能替换 builtins.__import__，因为 restricted_import 内部调用
    __import__() 会解析到 builtins.__import__，造成无限递归。
    restricted_import 仅通过 sandbox_globals["__builtins__"] 注入沙盒作用域。
    """
    import builtins

    # 重置 builtins 白名单（SAFE_BUILTINS 是 dict，必须 copy）
    # 这已包含 "__import__": restricted_import，沙盒内 import 走白名单
    sandbox_globals["__builtins__"] = SAFE_BUILTINS.copy()

    # 重置 open（sandbox_globals 和 builtins 两个入口都要重置）
    # builtins.open 必须替换：pandas/docx 等库内部调用 builtins.open
    builtins.open = scoped_open
    sandbox_globals["open"] = scoped_open


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

    # 3. 构建 scoped open（进程生命周期内复用同一个闭包）
    scoped_open = _setup_scoped_open(workspace_dir, staging_dir, output_dir)

    # 4. 替换 builtins.open
    import builtins
    builtins.open = scoped_open

    # 5. 构建沙盒 globals（变量跨调用保留）
    sandbox_globals = _build_sandbox_globals(workspace_dir, staging_dir, output_dir)

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
            _reset_security(sandbox_globals, scoped_open)

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
