"""
沙盒子进程 Worker

独立进程中执行用户代码，实现 cwd 隔离。
由 SandboxExecutor 通过 multiprocessing.Process(spawn) 启动。

通信协议：
  - 输入：函数参数 (code, workspace_dir, staging_dir, output_dir, timeout)
  - 输出：multiprocessing.Queue → (status, result_text)

安全措施：
  - os.chdir(workspace_dir)：进程级 cwd 隔离
  - resource.setrlimit：内存 2GB / CPU 时间限制
  - 精确清理敏感环境变量（黑名单前缀匹配）
  - AST 黑名单 + 运行时白名单（共享 sandbox_constants）
"""

import ast
import io
import math
import json
import sys
import time as _time
import traceback
from collections import Counter, defaultdict, OrderedDict
from datetime import datetime, timedelta, date, time as dt_time, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict

# 预导入数据分析库
try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    _PANDAS_AVAILABLE = False

try:
    import matplotlib as _mpl
    _mpl.use("Agg")
    _mpl.rcParams["font.sans-serif"] = [
        "WenQuanYi Micro Hei", "SimHei", "Noto Sans SC",
        "PingFang SC", "Microsoft YaHei", "DejaVu Sans",
    ]
    _mpl.rcParams["axes.unicode_minus"] = False
    import matplotlib.pyplot as _plt
    # 预热字体缓存（避免多进程并发写字体缓存文件）
    _mpl.font_manager.fontManager
    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    _mpl = None
    _plt = None
    _MATPLOTLIB_AVAILABLE = False

# 安全常量 — 唯一定义在 sandbox_constants.py，此处 import
from services.sandbox.sandbox_constants import (
    SAFE_BUILTINS,
    SENSITIVE_ENV_PREFIXES,
)
from services.sandbox.validators import truncate_result


# ============================================================
# 子进程环境准备
# ============================================================

def _clean_env():
    """清理敏感环境变量（精确前缀匹配，不误删无关变量）"""
    import os
    for key in list(os.environ.keys()):
        if any(key.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            del os.environ[key]


def _apply_resource_limits():
    """限制子进程资源（Linux only，macOS 部分限制不生效）"""
    try:
        import resource
        # 内存限制 2GB
        mem_limit = 2 * 1024 * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_limit, mem_limit))
        except (ValueError, OSError):
            pass  # macOS 不支持 RLIMIT_AS

        # 禁止创建子进程（防 fork bomb）
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        except (ValueError, OSError, AttributeError):
            pass  # 某些平台不支持
    except ImportError:
        pass  # Windows 无 resource 模块


def _build_sandbox_globals(workspace_dir: str, staging_dir: str, output_dir: str) -> Dict[str, Any]:
    """构建受限执行环境（在子进程中调用）"""
    import os as _os
    import builtins as _builtins

    g: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}

    # 标准库
    g["math"] = math
    g["json"] = json
    g["datetime"] = datetime
    g["timedelta"] = timedelta
    g["date"] = date
    g["time"] = dt_time
    g["timezone"] = timezone
    g["Decimal"] = Decimal
    g["ROUND_HALF_UP"] = ROUND_HALF_UP
    g["Counter"] = Counter
    g["defaultdict"] = defaultdict
    g["OrderedDict"] = OrderedDict

    # pandas
    if _PANDAS_AVAILABLE:
        g["pd"] = pd
        g["DataFrame"] = pd.DataFrame
        g["Series"] = pd.Series

    # matplotlib
    if _MATPLOTLIB_AVAILABLE:
        g["plt"] = _plt
        g["matplotlib"] = _mpl

    g["Path"] = Path

    # 目录路径
    if workspace_dir:
        g["WORKSPACE_DIR"] = workspace_dir
    if staging_dir:
        g["STAGING_DIR"] = staging_dir
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        g["OUTPUT_DIR"] = output_dir

    # workspace-scoped open（相对路径自动解析到 workspace）
    _ws_dir = workspace_dir

    def _scoped_open(path, mode="r", *args, **kwargs):
        path_str = str(path)
        if _ws_dir and not _os.path.isabs(path_str):
            path_str = _os.path.join(_ws_dir, path_str)
        resolved = _os.path.realpath(path_str)
        if _ws_dir:
            ws_real = _os.path.realpath(_ws_dir)
            if not resolved.startswith(ws_real + _os.sep) and resolved != ws_real:
                raise PermissionError(f"文件访问被拒绝：{path} 不在工作目录内")
        return _builtins.open(resolved, mode, *args, **kwargs)

    g["open"] = _scoped_open

    return g


# ============================================================
# 代码执行（同步，在子进程中运行）
# ============================================================

def _exec_code(code: str, sandbox_globals: Dict[str, Any], timeout: float) -> str:
    """执行代码，捕获 stdout + 最后表达式的值"""
    tree = ast.parse(code, mode="exec")

    # 不支持 await（子进程无 event loop）
    has_await = any(
        isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith))
        for node in ast.walk(tree)
    )
    if has_await:
        return "❌ 子进程沙盒不支持 async/await 语法"

    # stdout 重定向
    stdout_buffer = io.StringIO()
    sandbox_globals["print"] = lambda *args, **kwargs: print(
        *args, **kwargs, file=stdout_buffer,
    )

    # 超时 trace
    deadline = _time.monotonic() + timeout

    def _timeout_trace(frame, event, arg):
        if _time.monotonic() > deadline:
            raise TimeoutError("sandbox execution timeout")
        return _timeout_trace

    old_trace = sys.gettrace()
    sys.settrace(_timeout_trace)
    try:
        last_expr_value = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_node = tree.body.pop()
            if tree.body:
                exec(compile(tree, "<sandbox>", "exec"), sandbox_globals)
            expr_code = compile(
                ast.Expression(body=last_node.value), "<sandbox>", "eval",
            )
            last_expr_value = eval(expr_code, sandbox_globals)
        else:
            exec(compile(tree, "<sandbox>", "exec"), sandbox_globals)
    except TimeoutError:
        return f"⏱ 代码执行超时（{timeout}秒）。\n建议：缩小查询范围、减少数据量、或分批处理。"
    finally:
        sys.settrace(old_trace)
        if _MATPLOTLIB_AVAILABLE:
            _plt.close("all")

    # 组合输出
    stdout_text = stdout_buffer.getvalue()
    parts = []
    if stdout_text.strip():
        parts.append(stdout_text.rstrip())
    if last_expr_value is not None:
        parts.append(str(last_expr_value))

    if not parts:
        return "代码执行成功（无输出）"

    return "\n".join(parts)


# ============================================================
# Worker 入口函数（multiprocessing.Process target）
# ============================================================

def sandbox_worker_entry(
    result_queue,
    code: str,
    workspace_dir: str,
    staging_dir: str,
    output_dir: str,
    timeout: float,
    max_result_chars: int,
):
    """子进程入口：隔离环境中执行用户代码

    Args:
        result_queue: multiprocessing.Queue，写入 (status, result_text)
        code: 用户代码
        workspace_dir: 用户 workspace 绝对路径
        staging_dir: staging 数据目录
        output_dir: 输出目录
        timeout: 执行超时（秒）
        max_result_chars: 结果最大字符数
    """
    import os
    from services.sandbox.validators import validate_code

    try:
        # 1. 安全措施
        _clean_env()
        _apply_resource_limits()

        # 2. AST 验证（子进程内再验一次，防止绕过）
        error = validate_code(code)
        if error:
            result_queue.put(("error", f"❌ 代码验证失败:\n{error}"))
            return

        # 3. 切换到用户 workspace（进程级隔离，安全）
        if workspace_dir:
            os.makedirs(workspace_dir, exist_ok=True)
            os.chdir(workspace_dir)

        # 4. 构建沙盒环境
        sandbox_globals = _build_sandbox_globals(workspace_dir, staging_dir, output_dir)

        # 5. 执行代码
        result = _exec_code(code, sandbox_globals, timeout)

        # 6. 路径隐藏
        if result and output_dir:
            result = result.replace(output_dir, "下载")
        if result and workspace_dir:
            result = result.replace(workspace_dir, "工作区")

        # 7. 截断
        result = truncate_result(result, max_result_chars)

        result_queue.put(("ok", result))

    except Exception as e:
        tb = traceback.format_exc()
        tb_lines = tb.strip().split("\n")
        short_tb = "\n".join(tb_lines[-3:])
        result_queue.put(("error", f"❌ 执行错误:\n{short_tb}"))
