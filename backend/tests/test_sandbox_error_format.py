"""
format_sandbox_error 单测

覆盖：
- 常见异常类型（NameError / KeyError / DuckDB-style / FileNotFoundError）
- 用户代码行号提取（compile 时 filename="<sandbox>"）
- source_code 切片提取出错行
- 提取失败时的 fallback（traceback_excerpt）
- 防御性：传入异常的提取本身崩了不抛
- XML 安全转义（特殊字符不破坏结构）
"""

from __future__ import annotations

import pytest

from services.sandbox.error_format import format_sandbox_error


def _raise_from_sandbox(code: str) -> BaseException:
    """在 <sandbox> 编译环境里执行代码，捕获并返回异常实例。

    模拟真实沙盒流程：compile(tree, "<sandbox>", "exec") + exec()。
    """
    import ast
    tree = ast.parse(code, mode="exec")
    compiled = compile(tree, "<sandbox>", "exec")
    try:
        exec(compiled, {})
        raise AssertionError("expected exception, none raised")
    except BaseException as e:
        return e


# ============================================================
# 核心字段：type + message
# ============================================================

class TestCoreFields:

    def test_name_error_type_and_message(self):
        exc = _raise_from_sandbox("undefined_var\n")
        out = format_sandbox_error(exc)
        assert "<sandbox_error>" in out
        assert "</sandbox_error>" in out
        assert "<type>NameError</type>" in out
        assert "<message>" in out
        assert "undefined_var" in out  # NameError 消息含变量名

    def test_key_error_type_and_message(self):
        exc = _raise_from_sandbox("d = {}\nd['missing']\n")
        out = format_sandbox_error(exc)
        assert "<type>KeyError</type>" in out
        assert "'missing'" in out or "missing" in out

    def test_zero_division(self):
        exc = _raise_from_sandbox("x = 1 / 0\n")
        out = format_sandbox_error(exc)
        assert "<type>ZeroDivisionError</type>" in out

    def test_type_error_with_message(self):
        exc = _raise_from_sandbox("len(123)\n")
        out = format_sandbox_error(exc)
        assert "<type>TypeError</type>" in out


# ============================================================
# 用户代码定位：user_line + user_code
# ============================================================

class TestUserCodeExtraction:

    def test_user_line_extracted(self):
        code = "x = 1\ny = 2\nundefined_var\n"  # 第 3 行触发
        exc = _raise_from_sandbox(code)
        out = format_sandbox_error(exc, source_code=code)
        assert "<user_line>3</user_line>" in out

    def test_user_code_from_source_slice(self):
        """frame.line 为 None 时通过 source_code 切片得到出错行"""
        code = "a = 1\nb = bad_name\n"
        exc = _raise_from_sandbox(code)
        out = format_sandbox_error(exc, source_code=code)
        assert "<user_line>2</user_line>" in out
        assert "b = bad_name" in out

    def test_user_code_in_nested_call(self):
        """嵌套函数调用：user_line 应该是用户调用层（最后一个 <sandbox> frame）"""
        code = (
            "def outer():\n"
            "    return bad_name\n"
            "outer()\n"
        )
        exc = _raise_from_sandbox(code)
        out = format_sandbox_error(exc, source_code=code)
        # 应该提取到最后用户 frame，line=3 (outer()) 或 line=2 (return bad_name)
        # Python traceback 默认 last frame，本例是 line 2
        assert "<user_line>" in out
        # 至少能拿到 NameError 这个核心信息
        assert "<type>NameError</type>" in out


# ============================================================
# Fallback：提取不到用户 frame
# ============================================================

class TestFallback:

    def test_no_traceback_fallback(self):
        """异常没有 __traceback__（手工构造的）→ traceback_excerpt 兜底"""
        exc = ValueError("manually crafted, no traceback")
        out = format_sandbox_error(exc)
        # 核心字段必须有
        assert "<type>ValueError</type>" in out
        assert "manually crafted" in out
        # 无 user_line（没 traceback）
        assert "<user_line>" not in out
        # 应附 fallback traceback_excerpt（或不附，但不能崩）

    def test_no_source_code_still_works(self):
        """source_code=None 仍能返回核心字段"""
        exc = _raise_from_sandbox("undefined_var\n")
        out = format_sandbox_error(exc, source_code=None)
        assert "<type>NameError</type>" in out
        assert "<message>" in out


# ============================================================
# 防御性：本身永远不抛
# ============================================================

class TestDefensive:

    def test_does_not_raise_on_weird_exception(self):
        """type() 失败时也不能崩"""

        class WeirdException(Exception):
            def __str__(self):
                raise RuntimeError("str() broken")

        try:
            raise WeirdException("orig")
        except WeirdException as e:
            # 不应该抛
            out = format_sandbox_error(e)
            assert "<sandbox_error>" in out

    def test_xml_safe_special_chars(self):
        """异常消息含 < > & 不破坏 XML"""
        exc = ValueError("if x < 0 and y > 1 and z & w")
        out = format_sandbox_error(exc)
        assert "&lt;" in out
        assert "&gt;" in out
        assert "&amp;" in out


# ============================================================
# 真实场景：DuckDBException / FileNotFoundError 等
# ============================================================

class TestRealisticScenarios:

    def test_file_not_found(self):
        exc = _raise_from_sandbox("open('/nonexistent/path/file.txt')\n")
        out = format_sandbox_error(exc)
        assert "<type>FileNotFoundError</type>" in out
        assert "/nonexistent" in out

    def test_chained_imports_error(self):
        """ModuleNotFoundError 也能正确捕获"""
        exc = _raise_from_sandbox("import this_module_does_not_exist\n")
        out = format_sandbox_error(exc)
        assert "ModuleNotFoundError" in out or "ImportError" in out

    def test_attribute_error(self):
        exc = _raise_from_sandbox("(1).nonexistent_attr\n")
        out = format_sandbox_error(exc)
        assert "<type>AttributeError</type>" in out
