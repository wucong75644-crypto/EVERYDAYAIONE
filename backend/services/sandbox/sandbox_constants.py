"""
沙盒安全常量 — 主进程和子进程共享的唯一定义

运行时白名单（import + builtins），由 executor.py 和 sandbox_worker.py 共同引用。
修改此文件会同时影响两端，确保安全规则一致。
"""

from typing import Any

# ============================================================
# 运行时允许 import 的模块白名单（AST 黑名单之后的第二道防线）
# ============================================================

ALLOWED_IMPORT_MODULES = frozenset({
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
    # 文档读写（PDF / Word / PPT / Excel）
    "reportlab", "docx", "pptx", "openpyxl",
    "PyPDF2",  # PDF 读取（服务器已安装 3.0.1）
    # 高性能 Excel 读写引擎
    "calamine", "xlsxwriter",
    # 内部 C 扩展（被上述模块传递依赖）
    "_datetime", "_decimal", "_collections_abc", "_operator",
    "_functools", "_re", "_string", "_json", "_strptime",
})


def restricted_import(
    name: str, globals: Any = None, locals: Any = None,
    fromlist: tuple = (), level: int = 0,
) -> Any:
    """受限 import — 仅允许白名单模块（AST 之后的第二道防线）"""
    top_module = name.split(".")[0]
    if top_module not in ALLOWED_IMPORT_MODULES:
        raise ImportError(f"禁止导入模块: {name}")
    return __import__(name, globals, locals, fromlist, level)


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
