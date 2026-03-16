"""
通用代码执行沙盒

提供安全的 Python 代码执行环境，支持注册外部数据源函数。
与业务逻辑完全解耦：ERP/搜索/知识库等数据源作为插件注册。
"""

from services.sandbox.executor import SandboxExecutor

__all__ = ["SandboxExecutor"]
