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
# 文件名纠错（模块级，供 builtins.open 和 sandbox globals 共用）
# ============================================================

def _find_similar_file_global(target_path: str, workspace_dir: str) -> str:
    """文件找不到时，搜索同目录下相似文件名，返回绝对路径

    策略1：去掉空格/连字符/下划线后完全匹配（利润表 - xxx vs 利润表-xxx）
    策略2：同扩展名 + 文件名前缀包含关系（至少前 6 字符匹配）

    返回：匹配文件的绝对路径，或空字符串
    """
    import os as _os
    target_name = _os.path.basename(target_path)
    target_dir = _os.path.dirname(target_path)
    if not target_dir or not _os.path.isdir(target_dir):
        target_dir = _os.path.realpath(workspace_dir)
    if not _os.path.isdir(target_dir):
        return ""
    try:
        entries = _os.listdir(target_dir)
    except OSError:
        return ""
    # 策略1：归一化后完全匹配
    normalized = target_name.replace(" ", "").replace("-", "").replace("_", "").lower()
    for entry in entries:
        entry_normalized = entry.replace(" ", "").replace("-", "").replace("_", "").lower()
        if entry_normalized == normalized and entry != target_name:
            return _os.path.join(target_dir, entry)
    # 策略2：同扩展名 + 文件名前缀包含关系
    target_stem = _os.path.splitext(target_name)[0].replace(" ", "").lower()
    target_ext = _os.path.splitext(target_name)[1].lower()
    for entry in entries:
        entry_ext = _os.path.splitext(entry)[1].lower()
        entry_stem = _os.path.splitext(entry)[0].replace(" ", "").lower()
        if entry_ext == target_ext and (target_stem[:6] in entry_stem or entry_stem[:6] in target_stem):
            return _os.path.join(target_dir, entry)
    return ""


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

    # pandas（对标 Claude Code L2 行数限制：默认 nrows=2000）
    if _PANDAS_AVAILABLE:
        _DEFAULT_NROWS = 2000

        def _wrap_pd_reader(original_fn):
            """包装 pd.read_excel/read_csv，默认 nrows=2000"""
            import functools

            @functools.wraps(original_fn)
            def wrapper(*args, **kwargs):
                # 用户未指定 nrows 时，默认限制 2000 行
                if "nrows" not in kwargs:
                    kwargs["nrows"] = _DEFAULT_NROWS
                # 用户显式传 nrows=None → 全读（移除限制）
                elif kwargs["nrows"] is None:
                    del kwargs["nrows"]
                return original_fn(*args, **kwargs)
            return wrapper

        # 创建 pandas 命名空间代理（不污染真实 pd 模块）
        class _PandasProxy:
            """代理 pd 模块，拦截 read_* 函数加默认 nrows 限制"""
            def __init__(self, real_pd):
                self._pd = real_pd
                self.read_excel = _wrap_pd_reader(real_pd.read_excel)
                self.read_csv = _wrap_pd_reader(real_pd.read_csv)
                # read_parquet 不限制（列式存储，按列读取不吃内存）

            def __getattr__(self, name):
                return getattr(self._pd, name)

        g["pd"] = _PandasProxy(pd)
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

    # sandbox globals 的 open 直接用 builtins.open
    # builtins.open 已在 sandbox_worker_entry 中被替换为 _global_scoped_open
    # 统一处理：路径解析 + 安全检查 + 文件名纠错
    # pandas/docx/pptx/PyPDF2 等库内部调用的 open() 也自动受益
    g["open"] = _builtins.open

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

        # 3.5 替换 builtins.open 为带路径纠错 + 安全检查的版本
        # 这是唯一的拦截点——pandas/docx/pptx/PyPDF2/pathlib 最终都调 builtins.open
        # 在这里统一处理，所有入口自动受益
        if workspace_dir:
            import builtins
            _original_open = builtins.open
            _ws_dir = workspace_dir

            # 安全白名单：workspace + staging + output + 系统临时目录
            import tempfile as _tempfile
            _allowed_prefixes = [os.path.realpath(_ws_dir)]
            if staging_dir:
                _allowed_prefixes.append(os.path.realpath(staging_dir))
            if output_dir:
                _allowed_prefixes.append(os.path.realpath(output_dir))
            # 系统临时目录（xlsxwriter/openpyxl 等库写 xlsx 需要临时文件）
            _allowed_prefixes.append(os.path.realpath(_tempfile.gettempdir()))
            # 只读系统文件白名单（库读取 mime.types/timezone 等元数据需要）
            # 只允许具体文件，不开放整个目录（避免 /etc/passwd 等敏感文件泄露）
            # macOS: /etc → /private/etc 符号链接
            _readonly_system_files = frozenset({
                "/etc/apache2/mime.types",
                "/private/etc/apache2/mime.types",
                "/etc/mime.types",
                "/usr/share/misc/mime.types",
                "/usr/share/zoneinfo",  # timezone 数据目录
            })

            def _global_scoped_open(path, mode="r", *args, **kwargs):
                path_str = str(path)
                # 相对路径解析到 workspace
                if not os.path.isabs(path_str):
                    path_str = os.path.join(_ws_dir, path_str)
                resolved = os.path.realpath(path_str)
                # 安全检查：只允许访问白名单目录
                _in_whitelist = any(
                    resolved.startswith(prefix + os.sep) or resolved == prefix
                    for prefix in _allowed_prefixes
                )
                if not _in_whitelist:
                    # 只读系统文件：仅允许读模式 + 文件在白名单中
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
                # 文件不存在时自动纠错（suggestion 是绝对路径，直接用）
                if "r" in mode and not os.path.exists(resolved):
                    suggestion = _find_similar_file_global(resolved, _ws_dir)
                    if suggestion and os.path.exists(suggestion):
                        # logger 可能在子进程中不可用，用 print 到 stderr
                        import sys as _sys
                        print(f"[sandbox] 文件名自动纠正: {path} → {os.path.basename(suggestion)}", file=_sys.stderr)
                        return _original_open(suggestion, mode, *args, **kwargs)
                    msg = f"文件不存在: {path}"
                    if suggestion:
                        msg += f"。你是否要找: {os.path.basename(suggestion)}？"
                    raise FileNotFoundError(msg)
                return _original_open(resolved, mode, *args, **kwargs)

            builtins.open = _global_scoped_open

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
