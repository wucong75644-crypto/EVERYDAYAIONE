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
    TIMEOUT_MESSAGE,
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


def build_scoped_open(
    workspace_dir: str, staging_dir: str, output_dir: str,
    original_open=None,
    skills_dir: str = "",
):
    """构建带路径安全检查 + 文件名纠错的 open 函数。

    sandbox_worker 和 kernel_worker 共用此逻辑，避免两处维护。
    返回 scoped_open 函数，调用方赋值给 builtins.open。

    注意：不做虚拟路径别名（/staging/ /output/），LLM 统一用
    STAGING_DIR/OUTPUT_DIR 变量（真实绝对路径），任何库都能直接写。
    """
    import os
    import tempfile as _tempfile

    if original_open is None:
        import builtins
        original_open = builtins.open

    _ws_dir = workspace_dir

    # 安全白名单：workspace + staging + output + skills(只读) + 系统临时目录
    _allowed_prefixes = [os.path.realpath(_ws_dir)]
    if staging_dir:
        _allowed_prefixes.append(os.path.realpath(staging_dir))
    if output_dir:
        _allowed_prefixes.append(os.path.realpath(output_dir))
    if skills_dir:
        _allowed_prefixes.append(os.path.realpath(skills_dir))
    _allowed_prefixes.append(os.path.realpath(_tempfile.gettempdir()))

    # 只读系统文件白名单（库读取 mime.types/timezone 等元数据需要）
    _readonly_system_files = frozenset({
        "/etc/apache2/mime.types",
        "/private/etc/apache2/mime.types",
        "/etc/mime.types",
        "/usr/share/misc/mime.types",
        "/usr/share/zoneinfo",
    })

    def _scoped_open(path, mode="r", *args, **kwargs):
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
                    _alt_suggestion = _find_similar_file_global(
                        _alt, _fallback_dir,
                    )
                    if _alt_suggestion:
                        suggestion = _alt_suggestion
                        break
            if suggestion and os.path.exists(suggestion):
                import sys as _sys
                print(
                    f"[sandbox] 文件名自动纠正: {path} → "
                    f"{os.path.basename(suggestion)}",
                    file=_sys.stderr,
                )
                return original_open(suggestion, mode, *args, **kwargs)
            msg = f"文件不存在: {path}"
            if suggestion:
                msg += f"。你是否要找: {os.path.basename(suggestion)}？"
            raise FileNotFoundError(msg)
        return original_open(resolved, mode, *args, **kwargs)

    return _scoped_open


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

        # 限制子进程/线程数量（防 fork bomb，但允许库创建工作线程）
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        except (ValueError, OSError, AttributeError):
            pass  # 某些平台不支持
    except ImportError:
        pass  # Windows 无 resource 模块


def _build_sandbox_globals(workspace_dir: str, staging_dir: str, output_dir: str, skills_dir: str = "") -> Dict[str, Any]:
    """构建受限执行环境（在子进程中调用）"""
    import builtins as _builtins
    from services.sandbox.scoped_os import (
        build_scoped_os, build_scoped_shutil, build_scoped_pathlib,
    )
    from services.sandbox.sandbox_constants import make_restricted_import

    # 构建 scoped os/shutil/pathlib（路径白名单 + 操作限制）
    scoped_os, check_path = build_scoped_os(workspace_dir, staging_dir, output_dir)
    scoped_shutil = build_scoped_shutil(check_path)
    scoped_pathlib = build_scoped_pathlib(scoped_os)
    scoped_import = make_restricted_import({
        "os": scoped_os, "shutil": scoped_shutil, "pathlib": scoped_pathlib,
    })

    # builtins 必须 copy 后替换 __import__（不污染全局 SAFE_BUILTINS）
    safe = SAFE_BUILTINS.copy()
    safe["__import__"] = scoped_import
    g: Dict[str, Any] = {"__builtins__": safe}

    # 注入 scoped os/shutil
    g["os"] = scoped_os
    g["shutil"] = scoped_shutil

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

    # pandas 读取增强管道
    # 对标行业标准：底层自动处理数据格式，提示词只描述环境能力
    if _PANDAS_AVAILABLE:
        _DEFAULT_NROWS = 2000
        _SIZE_WARN_BYTES = 500 * 1024 * 1024  # 500MB

        def _post_clean(df):
            """POST-READ 清洗：空 Unnamed 列 + 尾部空行"""
            # 合并单元格拆开后产生的全空 Unnamed 列（ERP 导出常见）
            unnamed_empty = [
                c for c in df.columns
                if str(c).startswith("Unnamed:") and df[c].isna().all()
            ]
            if unnamed_empty:
                df.drop(columns=unnamed_empty, inplace=True)
            # 尾部全空行
            while len(df) > 0 and df.iloc[-1].isna().all():
                df = df.iloc[:-1]
            return df

        def _check_file_size(file_arg):
            """PRE-READ 文件大小预检"""
            import os as _os
            try:
                path = str(file_arg)
                if _os.path.isfile(path):
                    size = _os.path.getsize(path)
                    if size > _SIZE_WARN_BYTES:
                        print(f"\u26a0\ufe0f 文件 {size // 1048576}MB，建议用 duckdb 直接聚合 Parquet。")
            except Exception:
                pass

        def _nrows_read(original_fn, args, kwargs):
            """统一的 nrows 截断 + 清洗 + 提示逻辑"""
            if "nrows" not in kwargs:
                kwargs["nrows"] = _DEFAULT_NROWS
                result = original_fn(*args, **kwargs)
                result = _post_clean(result)
                if len(result) >= _DEFAULT_NROWS:
                    print(
                        f"\u26a0\ufe0f 数据已截断到前 {_DEFAULT_NROWS} 行。"
                        f"如需全量分析，用 duckdb 直接聚合 Parquet 或传 nrows=None。"
                    )
                return result
            if kwargs["nrows"] is None:
                del kwargs["nrows"]
            result = original_fn(*args, **kwargs)
            return _post_clean(result)

        def _wrap_pd_read_excel(original_fn):
            """read_excel 管道：预检 → 引擎 → sheet纠错 → 表头检测 → 读取 → 清洗 → 截断提示"""
            import functools

            @functools.wraps(original_fn)
            def wrapper(*args, **kwargs):
                file_arg = args[0] if args else kwargs.get("io") or kwargs.get("filepath_or_buffer")
                # ① 文件大小预检
                if file_arg is not None:
                    _check_file_size(file_arg)
                # ② 引擎选择：未指定 → fastexcel（calamine 同速，混合类型不崩）
                if "engine" not in kwargs:
                    kwargs["engine"] = "calamine"
                # ②b sheet 名模糊匹配（防全角/半角/空格等不精确匹配）
                sheet_arg = kwargs.get("sheet_name")
                if isinstance(sheet_arg, str) and file_arg is not None:
                    try:
                        import fastexcel as _fe
                        from services.agent.data_query_cache import fuzzy_match_sheet
                        _reader = _fe.read_excel(str(file_arg))
                        kwargs["sheet_name"] = fuzzy_match_sheet(sheet_arg, _reader.sheet_names)
                    except Exception:
                        pass
                # ③ 表头检测：未指定 header → 自动检测
                if "header" not in kwargs and file_arg is not None:
                    try:
                        from services.agent.data_query_cache import detect_header_row
                        probe = original_fn(
                            file_arg, header=None, nrows=20,
                            engine=kwargs.get("engine", "calamine"),
                        )
                        header_row = detect_header_row(probe.values.tolist())
                        if header_row > 0:
                            kwargs["header"] = header_row
                    except Exception:
                        pass
                # ④⑤⑥ 读取 + 清洗 + 截断
                return _nrows_read(original_fn, args, kwargs)
            return wrapper

        def _wrap_pd_read_csv(original_fn):
            """read_csv 管道：预检 → 编码检测 → 分隔符检测 → 读取 → 清洗 → 截断提示"""
            import functools

            @functools.wraps(original_fn)
            def wrapper(*args, **kwargs):
                file_arg = args[0] if args else kwargs.get("filepath_or_buffer")
                # ① 文件大小预检
                if file_arg is not None:
                    _check_file_size(file_arg)
                # ② 编码检测：未指定 → chardet 自动检测
                if "encoding" not in kwargs and file_arg is not None:
                    try:
                        from services.agent.data_query_cache import detect_encoding
                        enc = detect_encoding(str(file_arg))
                        if enc.lower() not in ("utf-8", "ascii"):
                            kwargs["encoding"] = enc
                    except Exception:
                        pass
                # ③ 分隔符检测：未指定 → csv.Sniffer
                if "sep" not in kwargs and "delimiter" not in kwargs and file_arg is not None:
                    try:
                        import csv as _csv
                        enc = kwargs.get("encoding", "utf-8")
                        with open(str(file_arg), encoding=enc, errors="ignore") as _f:
                            sample = _f.read(8192)
                        dialect = _csv.Sniffer().sniff(sample)
                        if dialect.delimiter != ",":
                            kwargs["sep"] = dialect.delimiter
                    except Exception:
                        pass
                # ④⑤⑥ 读取 + 清洗 + 截断（含 UnicodeDecodeError 降级 GBK）
                try:
                    return _nrows_read(original_fn, args, kwargs)
                except UnicodeDecodeError:
                    if "encoding" not in kwargs:
                        kwargs["encoding"] = "gbk"
                        return _nrows_read(original_fn, args, kwargs)
                    raise
            return wrapper

        class _PandasProxy:
            """pd 代理：Excel 表头/引擎 + CSV 编码/分隔符 + nrows 截断 + 数据清洗"""
            def __init__(self, real_pd):
                self._pd = real_pd
                self.read_excel = _wrap_pd_read_excel(real_pd.read_excel)
                self.read_csv = _wrap_pd_read_csv(real_pd.read_csv)

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

    # 路径协议:cwd=workspace,AI 用相对路径"上传/x" "下载/x" "staging/x"
    # 不再注入 OUTPUT_DIR/STAGING_DIR/WORKSPACE_DIR 等字符串变量
    # output_dir 目录(workspace/下载/)仍需主进程创建,供 _auto_upload_new_files 扫描
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    # DuckDB 磁盘模式预注入：数据全程在磁盘，内存只做缓存
    # AI 直接用 duckdb.sql() 即可，不需要自己配置连接
    # 关键：DuckDB 在容器内无法正确读 cgroup（issue #15080），
    # 必须显式 SET memory_limit，否则 DuckDB 会默认按宿主机 80% 内存自适应。
    # 沙盒 cgroup 上限 4GB（deploy/sandbox.cfg），DuckDB 给 3GB，留 1GB 给 Python + pandas。
    if staging_dir:
        try:
            import duckdb as _duckdb
            _db_path = str(Path(staging_dir) / ".duckdb.db")
            _temp_dir = str(Path(staging_dir) / ".duckdb_temp")
            Path(_temp_dir).mkdir(parents=True, exist_ok=True)
            _con = _duckdb.connect(_db_path)
            _con.execute("SET memory_limit = '3GB'")
            _con.execute(f"SET temp_directory = '{_temp_dir}'")
            # 注入为默认连接：duckdb.sql() 自动使用这个连接
            _duckdb.default_connection = _con
            g["duckdb"] = _duckdb
        except Exception:
            pass  # duckdb 不可用时降级，不影响其他功能

    # sandbox globals 的 open 直接用 builtins.open
    # builtins.open 已在 sandbox_worker_entry 中被替换为 _global_scoped_open
    # 统一处理：路径解析 + 安全检查 + 文件名纠错
    # pandas/docx/pptx/pdfplumber 等库内部调用的 open() 也自动受益
    g["open"] = _builtins.open

    # 路径协议:AI 用相对路径 + attachments XML 给完整字符串,无需 get_file 兜底
    # 长对话上下文丢失时,AI 主动调 file_search 工具(Agent 层)探索

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
        return TIMEOUT_MESSAGE.format(timeout=timeout)
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

