"""
沙盒执行器

受限 exec 环境 + async 包装 + stdout 捕获 + 超时控制。
与业务逻辑完全解耦，通过 register() 注入外部数据源函数。
"""

import ast
import asyncio
import io
import math
import json
import sys
import time as _time
import traceback
from collections import Counter, defaultdict, OrderedDict
from datetime import datetime, timedelta, date, time as dt_time, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, Optional

from loguru import logger

from services.sandbox.validators import validate_code, truncate_result

# 预导入数据分析库（避免每次 exec 时的冷启动）
try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    _PANDAS_AVAILABLE = False

try:
    import matplotlib as _mpl
    _mpl.use("Agg")  # 无 GUI 后端，必须在 import pyplot 之前
    import matplotlib.pyplot as _plt
    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    _mpl = None
    _plt = None
    _MATPLOTLIB_AVAILABLE = False


# 运行时允许 import 的模块白名单（AST 黑名单之后的第二道防线）
_ALLOWED_IMPORT_MODULES = frozenset({
    # 数学/数据
    "math", "json", "decimal", "numbers", "fractions", "statistics",
    # 日期/时间（datetime 内部依赖 time）
    "datetime", "time", "calendar", "zoneinfo",
    # 文件路径（沙盒内受限使用，配合注入的文件函数）
    "pathlib",
    # 集合/迭代
    "collections", "itertools", "functools", "operator", "copy",
    # 字符串/正则
    "re", "string",
    # 类型/枚举
    "typing", "enum", "dataclasses", "abc",
    # IO（BytesIO 用于生成 Excel/CSV 等二进制文件）
    "io",
    # 数据分析
    "pandas", "numpy", "pyarrow",
    # 可视化（matplotlib Agg 后端，无 GUI）
    "matplotlib", "seaborn",
    # 图片处理
    "PIL",
    # 文档生成（PDF / Word / PPT / Excel 直接操作）
    "reportlab", "docx", "pptx", "openpyxl",
    # 高性能 Excel 读写引擎
    "calamine", "xlsxwriter",
    # 内部 C 扩展（被上述模块传递依赖）
    "_datetime", "_decimal", "_collections_abc", "_operator",
    "_functools", "_re", "_string", "_json", "_strptime",
})


def _restricted_import(
    name: str, globals: Any = None, locals: Any = None,
    fromlist: tuple = (), level: int = 0,
) -> Any:
    """受限 import — 仅允许白名单模块（AST 之后的第二道防线）"""
    top_module = name.split(".")[0]
    if top_module not in _ALLOWED_IMPORT_MODULES:
        raise ImportError(f"禁止导入模块: {name}")
    return __import__(name, globals, locals, fromlist, level)


# 沙盒内可用的安全内置函数白名单
_SAFE_BUILTINS = {
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
    # 受限 import（允许白名单模块的 from X import Y 语法）
    "__import__": _restricted_import,
}


class SandboxExecutor:
    """通用 Python 代码沙盒执行器"""

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
    ) -> None:
        self._timeout = timeout
        self._max_result_chars = max_result_chars
        self._registered_funcs: Dict[str, Callable] = {}
        self._output_dir = output_dir        # 沙盒输出目录（自动上传）
        self._staging_dir = staging_dir      # staging 数据目录（pd.read_parquet 用）
        self._workspace_dir = workspace_dir  # 用户 workspace 目录（只读，pd.read_excel 用）
        self._upload_fn = upload_fn          # 文件上传函数（注入）

    def register(self, name: str, func: Callable) -> None:
        """注册外部数据源函数（沙盒内可直接调用）"""
        self._registered_funcs[name] = func

    async def execute(self, code: str, description: str = "") -> str:
        """执行 Python 代码并返回结果文本

        Args:
            code: Python 代码（顶层可直接 await）
            description: 代码功能描述（日志用）

        Returns:
            执行结果文本（stdout 输出 + 最后一个表达式的值）
        """
        # 1. AST 安全验证
        error = validate_code(code)
        if error:
            return f"❌ 代码验证失败:\n{error}"

        logger.info(
            f"SandboxExecutor | desc={description} | "
            f"code_len={len(code)} | funcs={list(self._registered_funcs.keys())}"
        )

        # 2. 确保输出目录存在 + 快照现有文件（用于检测新生成的文件）
        self._clean_output_dir()
        self._snapshot_before = self._snapshot_output_files()

        # 3. 构建受限执行环境
        sandbox_globals = self._build_globals()

        # 4. 执行（带超时）
        try:
            result = await asyncio.wait_for(
                self._run_code(code, sandbox_globals),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return (
                f"⏱ 代码执行超时（{self._timeout}秒）。\n"
                "建议：缩小查询范围、减少数据量、或分批处理。"
            )
        except Exception as e:
            tb = traceback.format_exc()
            tb_lines = tb.strip().split("\n")
            short_tb = "\n".join(tb_lines[-3:])
            logger.warning(
                f"SandboxExecutor exec error | desc={description} | "
                f"error={short_tb[:200]}"
            )
            return f"❌ 执行错误:\n{short_tb}"
        finally:
            # 清理 matplotlib figure 防止内存泄露（用户代码可能忘记 plt.close）
            if _MATPLOTLIB_AVAILABLE:
                _plt.close("all")

        logger.info(
            f"SandboxExecutor result | desc={description} | "
            f"result_len={len(result)} | result={result[:200]}"
        )

        # 5. 先截断正文，再追加文件标记（文件标记不参与截断）
        # 对齐 Claude Code：文本输出和文件引用是分离的，文件不会被文本截断影响
        result = truncate_result(result, self._max_result_chars)

        # 6. 自动检测生成的文件并上传（追加在截断后的文本末尾）
        file_results = await self._auto_upload_new_files()
        if file_results:
            result = (result or "") + "\n" + "\n".join(file_results)

        return result

    def _build_globals(self) -> Dict[str, Any]:
        """构建每次执行独立的 globals 字典"""
        g: Dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}

        # 安全标准库模块
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

        # pandas（如果可用）
        if _PANDAS_AVAILABLE:
            g["pd"] = pd
            g["DataFrame"] = pd.DataFrame
            g["Series"] = pd.Series

        # matplotlib（如果可用，Agg 后端已在模块加载时设置）
        if _MATPLOTLIB_AVAILABLE:
            g["plt"] = _plt
            g["matplotlib"] = _mpl

        # 注册的外部数据源函数
        for name, func in self._registered_funcs.items():
            g[name] = func

        # 注入目录路径
        from pathlib import Path as _Path
        g["Path"] = _Path

        # WORKSPACE_DIR: 用户 workspace 目录（只读，pd.read_excel 用）
        if self._workspace_dir:
            g["WORKSPACE_DIR"] = self._workspace_dir

        # STAGING_DIR: staging 数据目录（pd.read_parquet 用）
        if self._staging_dir:
            g["STAGING_DIR"] = self._staging_dir

        # OUTPUT_DIR: 输出目录（写文件到这里自动上传）
        if self._output_dir:
            _Path(self._output_dir).mkdir(parents=True, exist_ok=True)
            g["OUTPUT_DIR"] = self._output_dir

        # workspace-scoped open — 相对路径自动解析到 workspace，绝对路径检查边界
        # 对标 OpenAI Code Interpreter：不限制函数，限制环境
        import builtins as _builtins
        import os as _os
        _ws_dir = self._workspace_dir

        def _scoped_open(path, mode="r", *args, **kwargs):
            path_str = str(path)
            if _ws_dir and not _os.path.isabs(path_str):
                path_str = _os.path.join(_ws_dir, path_str)
            resolved = _os.path.realpath(path_str)
            if _ws_dir:
                ws_real = _os.path.realpath(_ws_dir)
                if not resolved.startswith(ws_real + _os.sep) and resolved != ws_real:
                    raise PermissionError(
                        f"文件访问被拒绝：{path} 不在工作目录内"
                    )
            return _builtins.open(resolved, mode, *args, **kwargs)

        g["open"] = _scoped_open

        return g

    # ========================================
    # 自动文件检测与上传
    # ========================================

    def _clean_output_dir(self) -> None:
        """确保输出目录存在（不清空，下载文件夹是持久的）"""
        if not self._output_dir:
            return
        from pathlib import Path
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)

    def _snapshot_output_files(self) -> set:
        """快照输出目录现有文件（执行前调用，用于检测新生成的文件）"""
        if not self._output_dir:
            return set()
        from pathlib import Path
        output_path = Path(self._output_dir)
        if not output_path.exists():
            return set()
        return {f.name for f in output_path.iterdir() if f.is_file()}

    async def _auto_upload_new_files(self) -> list[str]:
        """扫描输出目录中的新文件并自动上传（保留源文件供工作区下载）"""
        if not self._output_dir or not self._upload_fn:
            return []

        from pathlib import Path
        output_path = Path(self._output_dir)
        if not output_path.exists():
            return []

        # 只处理本次执行新生成的文件（对比执行前快照）
        before = getattr(self, "_snapshot_before", set())
        results = []
        for f in output_path.iterdir():
            if not f.is_file():
                continue
            if f.name in before:
                continue  # 执行前就存在的文件，跳过
            if f.suffix.lower() not in self._AUTO_UPLOAD_EXTENSIONS:
                continue

            try:
                file_size = f.stat().st_size
                upload_result = await self._upload_fn(f.name, file_size)
                results.append(upload_result)
                # 保留源文件（用户从工作区"下载/"文件夹直接下载）
                logger.info(
                    f"SandboxExecutor auto-upload | file={f.name} | "
                    f"size={file_size}"
                )
            except Exception as e:
                logger.error(
                    f"SandboxExecutor auto-upload failed | file={f.name} | "
                    f"error={e}"
                )
                results.append(f"❌ 文件上传失败: {f.name} ({e})")

        return results

    async def _run_code(
        self, code: str, sandbox_globals: Dict[str, Any],
    ) -> str:
        """在受限环境中执行代码，捕获 stdout 和最后表达式的值"""
        # 检测顶层 await → 包装为 async 函数
        tree = ast.parse(code, mode="exec")
        has_await = any(
            isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith))
            for node in ast.walk(tree)
        )

        # 捕获 stdout
        stdout_buffer = io.StringIO()
        sandbox_globals["print"] = lambda *args, **kwargs: print(
            *args, **kwargs, file=stdout_buffer,
        )

        if has_await:
            result_value = await self._exec_async(code, sandbox_globals, tree)
        else:
            # 同步代码在线程池执行（防止 CPU-bound 死循环阻塞事件循环）
            loop = asyncio.get_running_loop()
            result_value = await loop.run_in_executor(
                None, self._exec_sync, code, sandbox_globals, tree,
            )

        # 组合输出：stdout + 最后表达式的值
        stdout_text = stdout_buffer.getvalue()
        parts = []
        if stdout_text.strip():
            parts.append(stdout_text.rstrip())
        if result_value is not None:
            parts.append(str(result_value))

        if not parts:
            return "代码执行成功（无输出）"

        return "\n".join(parts)

    def _exec_sync(
        self,
        code: str,
        sandbox_globals: Dict[str, Any],
        tree: ast.Module,
    ) -> Optional[Any]:
        """同步执行（无 await 的代码）

        使用 sys.settrace 逐行检查超时，确保死循环可被终止。
        """
        deadline = _time.monotonic() + self._timeout

        def _timeout_trace(frame, event, arg):
            if _time.monotonic() > deadline:
                raise TimeoutError("sandbox execution timeout")
            return _timeout_trace

        old_trace = sys.gettrace()
        sys.settrace(_timeout_trace)
        try:
            return self._exec_sync_inner(sandbox_globals, tree)
        except TimeoutError:
            raise asyncio.TimeoutError()
        finally:
            sys.settrace(old_trace)

    def _exec_sync_inner(
        self,
        sandbox_globals: Dict[str, Any],
        tree: ast.Module,
    ) -> Optional[Any]:
        """实际执行逻辑（从 _exec_sync 中拆出，供 settrace 保护）"""
        # 如果最后一个语句是表达式，单独提取其值
        last_expr_value = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            # 分离最后一个表达式
            last_node = tree.body.pop()
            # 先执行前面的语句
            if tree.body:
                exec(compile(tree, "<sandbox>", "exec"), sandbox_globals)
            # 再 eval 最后一个表达式
            expr_code = compile(
                ast.Expression(body=last_node.value),
                "<sandbox>", "eval",
            )
            last_expr_value = eval(expr_code, sandbox_globals)
        else:
            exec(compile(tree, "<sandbox>", "exec"), sandbox_globals)

        return last_expr_value

    async def _exec_async(
        self,
        code: str,
        sandbox_globals: Dict[str, Any],
        tree: ast.Module,
    ) -> Optional[Any]:
        """异步执行（含 await 的代码）— 包装为 async 函数"""
        code_lines = code.split("\n")

        # 使用 AST 判断最后一条语句是否为表达式（与 _exec_sync_inner 一致）
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last_node = tree.body[-1]
            # 用 AST 行号定位表达式起始行（1-based → 0-based）
            expr_start = last_node.lineno - 1
            code_lines[expr_start] = f"return {code_lines[expr_start]}"

        indented = "\n".join(f"    {line}" for line in code_lines)
        wrapper = f"async def __sandbox_main__():\n{indented}"
        exec(compile(wrapper, "<sandbox>", "exec"), sandbox_globals)
        return await sandbox_globals["__sandbox_main__"]()
