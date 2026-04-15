"""
沙盒安全验证器

AST 预验证 + 模块/函数黑名单 + 结果截断。
在代码执行前拦截危险操作，不依赖运行时检测。
"""

import ast
from typing import List, Optional

# 禁止导入的模块（文件系统/进程/网络/编译）
_BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil",
    "socket", "http", "urllib", "requests", "httpx",
    "ctypes", "importlib", "code", "codeop", "compileall",
    "multiprocessing", "threading", "signal", "resource",
    "pickle", "shelve", "marshal", "tempfile", "glob",
    "webbrowser", "ftplib", "smtplib", "telnetlib",
    "builtins", "__builtin__",
})

# 禁止调用的函数名
_BLOCKED_CALLS = frozenset({
    "eval", "exec", "compile", "execfile",
    "input", "breakpoint",
    # open 已从黑名单移除 — 运行时注入 workspace-scoped open（_build_globals），
    # 相对路径自动解析到用户 workspace，绝对路径检查边界，对标 OpenAI Code Interpreter
    "__import__", "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir",
    "exit", "quit",
})

# 代码长度上限（字符数）
MAX_CODE_LENGTH = 5000

# AST 节点数上限
MAX_AST_NODES = 500


def validate_code(code: str) -> Optional[str]:
    """验证代码安全性

    Args:
        code: 待验证的 Python 代码

    Returns:
        None 表示验证通过，否则返回错误描述
    """
    if not code or not code.strip():
        return "代码不能为空"

    if len(code) > MAX_CODE_LENGTH:
        return f"代码超过长度限制（{len(code)}/{MAX_CODE_LENGTH} 字符）"

    # 解析 AST
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"语法错误: {e.msg}（第{e.lineno}行）"

    # 检查节点数
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_AST_NODES:
        return f"代码复杂度超限（{node_count}/{MAX_AST_NODES} 个AST节点）"

    # 遍历 AST 检查危险操作
    errors: List[str] = []
    for node in ast.walk(tree):
        error = _check_node(node)
        if error:
            errors.append(error)
            if len(errors) >= 3:
                break

    if errors:
        return "安全检查未通过:\n" + "\n".join(f"- {e}" for e in errors)

    return None


def _check_node(node: ast.AST) -> Optional[str]:
    """检查单个 AST 节点"""
    # 拦截 import 语句
    if isinstance(node, ast.Import):
        for alias in node.names:
            top_module = alias.name.split(".")[0]
            if top_module in _BLOCKED_MODULES:
                return f"禁止导入模块: {alias.name}"

    if isinstance(node, ast.ImportFrom):
        if node.module:
            top_module = node.module.split(".")[0]
            if top_module in _BLOCKED_MODULES:
                return f"禁止导入模块: {node.module}"

    # 拦截危险函数调用
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _BLOCKED_CALLS:
            return f"禁止调用函数: {func.id}()"
        if isinstance(func, ast.Attribute) and func.attr in _BLOCKED_CALLS:
            return f"禁止调用方法: .{func.attr}()"

    # 拦截 __ 属性访问（防止元编程逃逸）
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            # 允许安全的 dunder（如 __len__、__str__）
            allowed_dunders = {
                "__len__", "__str__", "__repr__", "__iter__",
                "__next__", "__getitem__", "__contains__",
                "__enter__", "__exit__", "__init__",
                "__name__", "__doc__",
            }
            if node.attr not in allowed_dunders:
                return f"禁止访问属性: {node.attr}"

    return None


def truncate_result(text: str, max_chars: int = 8000) -> str:
    """截断执行结果

    Args:
        text: 原始输出文本
        max_chars: 最大字符数

    Returns:
        截断后的文本（超长时附加提示）
    """
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    remaining = len(text) - max_chars
    return f"{truncated}\n\n⚠ 输出过长，已截断（省略 {remaining} 字符）。建议缩小查询范围或分批处理。"
