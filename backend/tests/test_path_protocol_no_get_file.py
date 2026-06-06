"""路径协议反退守护：确保所有给 LLM 的引导文案不再出现 get_file('xxx')。

Phase 1 (b38fc4d) 删除了沙盒里的 get_file() 函数,但工具回执文案残留
get_file('xxx') 字面值——LLM 跟着照写会触发 NameError。

本测试扫描整个 backend/services 源码,在工具结果格式化代码里 grep
get_file 字面,出现即失败。守护未来 contributor 加新工具时不再踩坑。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).parent.parent
_SERVICES_DIR = _BACKEND_ROOT / "services"

# get_file('xxx') 或 get_file("xxx") 的字面调用 — 这是 LLM 会看到的引导
_GET_FILE_LITERAL_RE = re.compile(r"""get_file\s*\(\s*['"]""")

# 测试/历史对比/废弃说明白名单(这些注释里的 get_file 是合法说明,不是引导文案)
_ALLOWED_PATHS = {
    "tests/",
    "scripts/",
    "external/",
}


def _is_allowed(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in _ALLOWED_PATHS)


def _is_doc_comment(line: str) -> bool:
    """是不是历史说明/废弃注释(允许保留)"""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    return any(
        keyword in stripped
        for keyword in ("已删", "已废弃", "废弃", "历史", "旧的", "兼容", "Phase 1")
    )


def test_no_get_file_literal_in_llm_facing_strings():
    """services/ 下源码不应出现 get_file('xxx') 字面调用。

    LLM 会原样看到这些字符串,然后照写到 code_execute 沙盒里 → NameError。
    """
    violations: list[tuple[str, int, str]] = []

    for py_file in _SERVICES_DIR.rglob("*.py"):
        rel_path = str(py_file.relative_to(_BACKEND_ROOT))
        if _is_allowed(rel_path):
            continue

        with py_file.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if not _GET_FILE_LITERAL_RE.search(line):
                    continue
                if _is_doc_comment(line):
                    continue
                violations.append((rel_path, lineno, line.rstrip()))

    if violations:
        msg = "\n".join(
            f"  {path}:{line}: {snippet}"
            for path, line, snippet in violations
        )
        pytest.fail(
            f"发现 {len(violations)} 处 get_file('xxx') 引导文案,LLM 照写会爆:\n{msg}\n\n"
            "修复:改成相对路径协议,如 pd.read_parquet('staging/x.parquet')。"
        )
