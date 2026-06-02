"""
沙盒安全常量 — 主进程和子进程共享的唯一定义

运行时 import 策略 + builtins 白名单，由 executor.py 和 sandbox_worker.py
共同引用。修改此文件会同时影响两端，确保安全规则一致。

# Import 策略：黑名单模式（对齐 OpenAI Code Interpreter / Pyodide）
默认放行所有 Python 标准库 + 已装第三方库；仅黑名单显式拦危险模块。

理由：白名单维护成本指数级（每次第三方库踩到一个传递依赖标准库就要补一个，
duckdb 已经踩了 inspect / sys / types 三次）。黑名单收敛快，且 nsjail
namespace+cgroup 已经做了进程级隔离，Python 层只需要拦"能逃逸 nsjail
的模块"（ctypes/pickle 等）和"沙盒环境内没意义的模块"（network/subprocess
—— nsjail 已隔离但深度防御）。

# 例外（os / shutil / pathlib）
这些有副作用的文件系统模块走 _scoped 注入：import 时拿到的是
scoped_os / scoped_shutil 等包装版本，路径走白名单校验。
"""

from typing import Any

# ============================================================
# 运行时禁止 import 的危险模块（黑名单）
# ============================================================

BLOCKED_IMPORT_MODULES = frozenset({
    # ── C ABI / 任意内存（可绕过 Python 沙盒）──
    "ctypes", "_ctypes", "cffi",
    # ── 反序列化 RCE（pickle gadget 可执行任意代码）──
    "pickle", "_pickle", "marshal", "shelve",
    # ── 进程 / 信号 ──
    "subprocess", "multiprocessing", "_multiprocessing",
    "signal", "pty", "posix",
    # ── 网络（沙盒无网，深度防御）──
    "socket", "_socket", "ssl", "_ssl",
    "select", "selectors", "asyncore", "asynchat",
    "urllib", "urllib3", "http", "httplib", "xmlrpc",
    "ftplib", "smtplib", "poplib", "imaplib", "nntplib", "telnetlib",
    "socketserver", "wsgiref",
    "requests", "httpx", "aiohttp", "websockets",
    # ── 动态加载 / 执行任意代码（Python 层逃逸）──
    "importlib", "_importlib_external", "_imp",
    "runpy", "code", "codeop",
    "compileall", "py_compile", "zipimport",
    "pkgutil", "modulefinder",
    # ── GUI / 浏览器 ──
    "tkinter", "_tkinter", "turtle", "turtledemo",
    "webbrowser", "idlelib", "curses", "_curses",
    # ── 平台后门 / 低级 IO ──
    "fcntl", "termios", "tty", "syslog",
    "nis", "crypt", "spwd", "_crypt",
    # ── 调试 / 性能分析（拖慢沙盒，无业务价值）──
    "pdb", "bdb", "cProfile", "profile", "trace",
    # ── 已知逃逸 vector ──
    "readline",  # 可改 builtins 行为
    "builtins", "__builtin__",  # 直接 import builtins 可绕过 SAFE_BUILTINS 限制
    # ── 并发（沙盒应单线程，避免 deadline trace 失效）──
    "threading", "_thread",
    # ── 系统资源（极少正当用途，多用作 fingerprint）──
    "resource",
})


# 历史保留：旧白名单常量（黑名单模式下不再使用，保留空集合兼容
# 旧 import 路径，未来彻底清理）
ALLOWED_IMPORT_MODULES = frozenset()


def make_restricted_import(scoped_modules: dict | None = None):
    """构建受限 import 函数（黑名单策略）

    Args:
        scoped_modules: {"os": scoped_os, "shutil": scoped_shutil, "pathlib": ...}
                        scoped 模块拿包装版（路径校验），其他默认放行。

    决策树：
      1. 模块在 _scoped 里 → 返回 scoped 版本（os/shutil/pathlib 等）
      2. 模块在 BLOCKED_IMPORT_MODULES → 拒绝
      3. 其他 → 放行（标准库工具 + 已装第三方库都自动可用）
    """
    _scoped = scoped_modules or {}

    def _restricted_import(
        name: str, globals: Any = None, locals: Any = None,
        fromlist: tuple = (), level: int = 0,
    ) -> Any:
        top = name.split(".")[0]

        # 1. scoped 模块（os / shutil / pathlib 等）走包装版
        if top in _scoped:
            mod = _scoped[top]
            # from os.path import join → fromlist 非空，返回子模块
            if name == "os.path" and fromlist:
                return mod.path
            # import os / import os.path（无 fromlist）→ 返回顶层
            return mod

        # 2. 黑名单：危险模块拒绝
        if top in BLOCKED_IMPORT_MODULES:
            raise ImportError(f"禁止导入模块: {name}（沙盒安全策略）")

        # 3. 其他默认放行（标准库工具 + 已装第三方库）
        return __import__(name, globals, locals, fromlist, level)

    return _restricted_import


# 向后兼容：无 scoped 模块时保持原有行为
restricted_import = make_restricted_import()


# ============================================================
# 沙盒内可用的安全内置函数白名单
# ============================================================

SAFE_BUILTINS = {
    # 类型转换
    "int": int, "float": float, "str": str, "bool": bool,
    "list": list, "dict": dict, "tuple": tuple, "set": set,
    "frozenset": frozenset, "bytes": bytes, "bytearray": bytearray,
    # 数学/聚合
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "len": len, "pow": pow, "divmod": divmod,
    # 迭代
    "range": range, "enumerate": enumerate, "zip": zip,
    "map": map, "filter": filter, "sorted": sorted, "reversed": reversed,
    # 字符串/格式化
    "format": format, "repr": repr, "chr": chr, "ord": ord,
    # 逻辑
    "all": all, "any": any, "isinstance": isinstance, "issubclass": issubclass,
    "type": type, "hasattr": hasattr,
    # 打印（重定向到 StringIO）
    "print": print,
    # 异常类型（允许 try-except）
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "AttributeError": AttributeError,
    "ZeroDivisionError": ZeroDivisionError, "RuntimeError": RuntimeError,
    "StopIteration": StopIteration, "ImportError": ImportError,
    # None/True/False
    "None": None, "True": True, "False": False,
    # 受限 import
    "__import__": restricted_import,
}


# ============================================================
# 子进程敏感环境变量清理黑名单（精确匹配前缀）
# ============================================================

# 只清理已知的敏感变量前缀，不做模糊子串匹配，防止误删
# ============================================================
# 超时提示模板（executor / sandbox_worker / kernel_manager 共用）
# ============================================================

TIMEOUT_MESSAGE = (
    "⏱ 代码执行超时（{timeout}秒）。\n"
    "单次处理量过大，请拆分为多次 code_execute 调用：\n"
    "1. 先统计总量（len(files) / len(df)）\n"
    "2. 按批次处理，确保单批在时限内完成\n"
    "3. 变量跨调用保留，可累积中间结果"
)


SENSITIVE_ENV_PREFIXES = (
    "OPENAI_API_",
    "ANTHROPIC_API_",
    "DASHSCOPE_API_",
    "DASHSCOPE_",
    "GOOGLE_API_",
    "AWS_SECRET_",
    "AWS_ACCESS_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "SUPABASE_",
    "OSS_ACCESS_KEY",
    "OSS_SECRET",
    "KUAIMAI_",
    "WECHAT_",
    "WECOM_",
    "CLERK_SECRET",
    "JWT_SECRET",
    "SECRET_KEY",
)
