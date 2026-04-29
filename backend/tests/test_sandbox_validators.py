"""沙盒安全验证器测试"""

import pytest

from services.sandbox.validators import (
    MAX_AST_NODES,
    MAX_CODE_LENGTH,
    validate_code,
    truncate_result,
)


class TestValidateCode:
    """validate_code 测试"""

    def test_empty_code(self):
        assert validate_code("") is not None
        assert validate_code("   ") is not None

    def test_valid_simple_code(self):
        assert validate_code("x = 1 + 1") is None

    def test_valid_math(self):
        assert validate_code("import math\nresult = math.sqrt(16)") is None

    def test_valid_json(self):
        assert validate_code("import json\nd = json.loads('{\"a\": 1}')") is None

    def test_valid_datetime(self):
        assert validate_code("from datetime import datetime\nnow = datetime.now()") is None

    def test_valid_pandas(self):
        code = "import pandas as pd\ndf = pd.DataFrame({'a': [1, 2, 3]})"
        assert validate_code(code) is None

    def test_valid_decimal(self):
        code = "from decimal import Decimal\nx = Decimal('99.99') + Decimal('0.01')"
        assert validate_code(code) is None

    def test_valid_collections(self):
        code = "from collections import Counter\nc = Counter([1, 1, 2])"
        assert validate_code(code) is None

    # === 安全拦截 ===

    def test_blocked_import_os(self):
        result = validate_code("import os")
        assert result is not None
        assert "os" in result

    def test_blocked_import_subprocess(self):
        result = validate_code("import subprocess")
        assert result is not None
        assert "subprocess" in result

    def test_blocked_from_os(self):
        result = validate_code("from os import listdir")
        assert result is not None
        assert "os" in result

    def test_blocked_import_sys(self):
        result = validate_code("import sys")
        assert result is not None
        assert "sys" in result

    def test_blocked_import_socket(self):
        result = validate_code("import socket")
        assert result is not None
        assert "socket" in result

    def test_blocked_import_httpx(self):
        result = validate_code("import httpx")
        assert result is not None
        assert "httpx" in result

    def test_blocked_import_requests(self):
        result = validate_code("import requests")
        assert result is not None
        assert "requests" in result

    def test_blocked_call_eval(self):
        result = validate_code("eval('1+1')")
        assert result is not None
        assert "eval" in result

    def test_blocked_call_exec(self):
        result = validate_code("exec('x=1')")
        assert result is not None
        assert "exec" in result

    def test_open_passes_ast_validation(self):
        """open() 不再被 AST 阻止（安全由运行时 _scoped_open 保证）"""
        result = validate_code("f = open('/etc/passwd')")
        assert result is None

    def test_blocked_call___import__(self):
        result = validate_code("__import__('os')")
        assert result is not None
        assert "__import__" in result

    def test_blocked_dunder_class(self):
        result = validate_code("x = [].__class__.__bases__")
        assert result is not None
        assert "__bases__" in result

    def test_blocked_dunder_subclasses(self):
        result = validate_code("x = ().__class__.__subclasses__()")
        assert result is not None
        assert "__subclasses__" in result

    def test_allowed_dunder_len(self):
        """__len__ 等安全 dunder 应允许"""
        assert validate_code("class Foo:\n    def __len__(self): return 0") is None

    def test_allowed_dunder_str(self):
        assert validate_code("class Foo:\n    def __str__(self): return ''") is None

    # === 限制 ===

    def test_code_too_long(self):
        code = "x = 1\n" * (MAX_CODE_LENGTH // 5)
        result = validate_code(code)
        assert result is not None
        assert "长度限制" in result

    def test_syntax_error(self):
        result = validate_code("def foo(")
        assert result is not None
        assert "语法错误" in result

    def test_multiple_blocked_imports(self):
        """多个违规只报前 3 个"""
        code = "import os\nimport sys\nimport subprocess\nimport socket"
        result = validate_code(code)
        assert result is not None
        # 至少检测到 3 个
        assert result.count("禁止导入模块") >= 3


class TestTruncateResult:
    """truncate_result 测试"""

    def test_short_text_unchanged(self):
        text = "hello world"
        assert truncate_result(text, 100) == text

    def test_exact_limit(self):
        text = "a" * 100
        assert truncate_result(text, 100) == text

    def test_truncation(self):
        text = "a" * 200
        result = truncate_result(text, 100)
        assert len(result) > 100  # 截断文本 + 提示
        assert "已截断" in result
        assert "100" in result  # 省略字符数

    def test_truncation_message_has_suggestion(self):
        text = "a" * 10000
        result = truncate_result(text, 8000)
        assert "缩小查询范围" in result

    def test_default_limit_is_50000(self):
        """默认上限对标 Claude DEFAULT_MAX_RESULT_SIZE_CHARS=50000"""
        # 49999 字符：不截断
        text = "a" * 49999
        assert truncate_result(text) == text

        # 50001 字符：截断
        text_over = "a" * 50001
        result = truncate_result(text_over)
        assert "已截断" in result
        assert "1" in result  # 省略 1 字符
