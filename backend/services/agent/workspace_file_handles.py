"""
统一文件句柄注册表 — 对标 OpenAI Code Interpreter file_id 机制。

解决的问题：
  LLM 抄写长路径（中文/空格/192个sheet名）极易出错。
  本模块给所有文件（workspace + staging）统一分配短句柄（F1, F2...），
  LLM 全程只用句柄，系统层替换为真实路径。

生命周期：一个对话一个实例（挂在 ToolExecutor 上）。

注册来源：
  - file_list 发现 workspace 文件 → ToolExecutor._file_list_with_handles 注册
  - 工具产出 staging 文件 → ToolLoopExecutor._register_result_files 注册

翻译入口（唯一）：
  - ToolExecutor._file_dispatch 翻译 "F1" → 绝对路径（file 工具）
  - ToolExecutor._code_execute 注入 FILES 字典（沙盒）
"""
from __future__ import annotations

import re

_HANDLE_RE = re.compile(r"^F(\d+)$", re.IGNORECASE)


class WorkspaceFileHandles:
    """会话级 workspace 文件句柄映射。

    线程安全说明：单个对话串行执行工具调用，无并发写入。
    """

    __slots__ = ("_path_to_handle", "_handle_to_path", "_handle_to_name", "_counter")

    def __init__(self) -> None:
        self._path_to_handle: dict[str, str] = {}   # abs_path → "F1"
        self._handle_to_path: dict[str, str] = {}   # "F1" → abs_path
        self._handle_to_name: dict[str, str] = {}   # "F1" → filename
        self._counter: int = 0

    # ----------------------------------------------------------
    # 注册
    # ----------------------------------------------------------

    def register(self, abs_path: str, filename: str = "") -> str:
        """注册文件，返回句柄。重复路径返回已有句柄。

        Args:
            abs_path: 文件绝对路径
            filename: 显示用文件名（可选，默认从路径提取）

        Returns:
            句柄字符串，如 "F1"
        """
        existing = self._path_to_handle.get(abs_path)
        if existing:
            return existing

        self._counter += 1
        handle = f"F{self._counter}"
        self._path_to_handle[abs_path] = handle
        self._handle_to_path[handle] = abs_path
        self._handle_to_name[handle] = filename or abs_path.rsplit("/", 1)[-1]
        return handle

    # ----------------------------------------------------------
    # 解析
    # ----------------------------------------------------------

    def resolve(self, handle_or_path: str) -> str | None:
        """解析句柄 → 绝对路径。非句柄返回 None。

        大小写不敏感：f1 / F1 均可。
        """
        key = handle_or_path.strip().upper()
        return self._handle_to_path.get(key)

    def get_filename(self, handle: str) -> str | None:
        """获取句柄对应的文件名。"""
        return self._handle_to_name.get(handle.strip().upper())

    @staticmethod
    def is_handle(value: str) -> bool:
        """判断字符串是否为合法句柄格式（F1, F2, ...）。"""
        return bool(_HANDLE_RE.match(value.strip()))

    # ----------------------------------------------------------
    # 沙盒注入
    # ----------------------------------------------------------

    def to_sandbox_dict(self) -> dict[str, str]:
        """生成供沙盒注入的 FILES 字典。

        Returns:
            {"F1": "/mnt/.../file.xlsx", "F2": "/mnt/.../data.csv", ...}
        """
        return dict(self._handle_to_path)

    # ----------------------------------------------------------
    # 信息
    # ----------------------------------------------------------

    @property
    def count(self) -> int:
        return self._counter

    def __len__(self) -> int:
        return self._counter

    def __bool__(self) -> bool:
        return self._counter > 0

    def __repr__(self) -> str:
        items = ", ".join(
            f"{h}: {n}" for h, n in self._handle_to_name.items()
        )
        return f"WorkspaceFileHandles({items})"
