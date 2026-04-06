"""沙盒执行器测试"""

import asyncio

import pytest

from services.sandbox.executor import SandboxExecutor


@pytest.fixture
def executor():
    return SandboxExecutor(timeout=5.0, max_result_chars=1000)


class TestBasicExecution:
    """基本执行功能"""

    @pytest.mark.asyncio
    async def test_simple_arithmetic(self, executor):
        result = await executor.execute("1 + 1", "简单加法")
        assert "2" in result

    @pytest.mark.asyncio
    async def test_print_output(self, executor):
        result = await executor.execute("print('hello')", "打印测试")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_multi_line_code(self, executor):
        code = "x = 10\ny = 20\nprint(x + y)"
        result = await executor.execute(code, "多行代码")
        assert "30" in result

    @pytest.mark.asyncio
    async def test_last_expression_returned(self, executor):
        code = "x = 42\nx"
        result = await executor.execute(code, "最后表达式")
        assert "42" in result

    @pytest.mark.asyncio
    async def test_no_output(self, executor):
        result = await executor.execute("x = 1", "无输出")
        assert "无输出" in result or "成功" in result

    @pytest.mark.asyncio
    async def test_print_and_expression(self, executor):
        code = "print('line1')\n42"
        result = await executor.execute(code, "混合输出")
        assert "line1" in result
        assert "42" in result


class TestWhitelistModules:
    """白名单模块测试"""

    @pytest.mark.asyncio
    async def test_math_module(self, executor):
        result = await executor.execute("math.sqrt(144)", "math模块")
        assert "12" in result

    @pytest.mark.asyncio
    async def test_json_module(self, executor):
        code = "json.dumps({'a': 1})"
        result = await executor.execute(code, "json模块")
        assert '"a"' in result

    @pytest.mark.asyncio
    async def test_datetime_available(self, executor):
        code = "str(datetime.now().year)"
        result = await executor.execute(code, "datetime")
        assert "202" in result  # 2025 or 2026

    @pytest.mark.asyncio
    async def test_decimal_available(self, executor):
        code = "str(Decimal('99.99') + Decimal('0.01'))"
        result = await executor.execute(code, "Decimal精度")
        assert "100.00" in result

    @pytest.mark.asyncio
    async def test_counter_available(self, executor):
        code = "str(Counter([1, 1, 2, 3]))"
        result = await executor.execute(code, "Counter")
        assert "1" in result

    @pytest.mark.asyncio
    async def test_pandas_available(self, executor):
        code = "df = pd.DataFrame({'a': [1, 2, 3]})\nstr(df['a'].sum())"
        result = await executor.execute(code, "pandas DataFrame")
        assert "6" in result


class TestSafeBuiltins:
    """安全内置函数测试"""

    @pytest.mark.asyncio
    async def test_len(self, executor):
        result = await executor.execute("len([1, 2, 3])", "len")
        assert "3" in result

    @pytest.mark.asyncio
    async def test_sum(self, executor):
        result = await executor.execute("sum([10, 20, 30])", "sum")
        assert "60" in result

    @pytest.mark.asyncio
    async def test_sorted(self, executor):
        result = await executor.execute("sorted([3, 1, 2])", "sorted")
        assert "[1, 2, 3]" in result

    @pytest.mark.asyncio
    async def test_range(self, executor):
        result = await executor.execute("list(range(5))", "range")
        assert "[0, 1, 2, 3, 4]" in result

    @pytest.mark.asyncio
    async def test_enumerate(self, executor):
        result = await executor.execute(
            "list(enumerate(['a', 'b']))", "enumerate"
        )
        assert "(0, 'a')" in result

    @pytest.mark.asyncio
    async def test_isinstance(self, executor):
        result = await executor.execute("isinstance(42, int)", "isinstance")
        assert "True" in result


class TestSecurityBlocking:
    """安全拦截测试"""

    @pytest.mark.asyncio
    async def test_import_os_blocked(self, executor):
        result = await executor.execute("import os\nos.listdir('.')", "危险导入")
        assert "验证失败" in result

    @pytest.mark.asyncio
    async def test_eval_blocked(self, executor):
        result = await executor.execute("eval('1+1')", "eval调用")
        assert "验证失败" in result

    @pytest.mark.asyncio
    async def test_open_blocked(self, executor):
        result = await executor.execute("open('/etc/passwd')", "open调用")
        assert "验证失败" in result

    @pytest.mark.asyncio
    async def test_dunder_escape_blocked(self, executor):
        result = await executor.execute(
            "x = [].__class__.__bases__", "元编程逃逸"
        )
        assert "验证失败" in result

    @pytest.mark.asyncio
    async def test_empty_code(self, executor):
        result = await executor.execute("", "空代码")
        assert "验证失败" in result


class TestAllowedImports:
    """允许的模块导入测试"""

    @pytest.mark.asyncio
    async def test_import_io_allowed(self, executor):
        """io 模块可导入 — BytesIO 用于生成 Excel/CSV"""
        result = await executor.execute(
            "import io\nbuf = io.BytesIO()\nbuf.write(b'hello')\nbuf.tell()",
            "io模块导入",
        )
        assert "5" in result

    @pytest.mark.asyncio
    async def test_import_json_allowed(self, executor):
        """json 模块可导入"""
        result = await executor.execute(
            "import json\njson.dumps({'a': 1})",
            "json模块导入",
        )
        assert '"a"' in result


class TestAsyncExecution:
    """异步执行测试"""

    @pytest.mark.asyncio
    async def test_await_registered_func(self):
        executor = SandboxExecutor(timeout=5.0)

        async def mock_query(action, **kwargs):
            return {"list": [{"id": 1}], "total": 1}

        executor.register("erp_query", mock_query)

        code = "data = await erp_query('shop_list')\nprint(data['total'])"
        result = await executor.execute(code, "异步调用")
        assert "1" in result

    @pytest.mark.asyncio
    async def test_multiple_await(self):
        executor = SandboxExecutor(timeout=5.0)

        async def mock_fetch(key):
            return {"value": key}

        executor.register("fetch", mock_fetch)

        code = (
            "a = await fetch('x')\n"
            "b = await fetch('y')\n"
            "print(a['value'], b['value'])"
        )
        result = await executor.execute(code, "多次await")
        assert "x" in result
        assert "y" in result


class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_syntax_error(self, executor):
        result = await executor.execute("def foo(", "语法错误")
        assert "验证失败" in result

    @pytest.mark.asyncio
    async def test_runtime_error(self, executor):
        result = await executor.execute("1 / 0", "除零错误")
        assert "执行错误" in result

    @pytest.mark.asyncio
    async def test_key_error(self, executor):
        result = await executor.execute("d = {}\nd['missing']", "KeyError")
        assert "执行错误" in result

    @pytest.mark.asyncio
    async def test_timeout(self):
        executor = SandboxExecutor(timeout=0.5)
        code = "import time\ntime.sleep(10)"
        # time 不在白名单，会被 AST 拦截（不是 timeout）
        # 改用循环
        code = "x = 0\nwhile True:\n    x += 1"
        result = await executor.execute(code, "死循环")
        assert "超时" in result


class TestResultTruncation:
    """结果截断测试"""

    @pytest.mark.asyncio
    async def test_long_output_truncated(self):
        executor = SandboxExecutor(timeout=5.0, max_result_chars=100)
        code = "print('x' * 500)"
        result = await executor.execute(code, "长输出")
        assert "已截断" in result

    @pytest.mark.asyncio
    async def test_short_output_not_truncated(self, executor):
        result = await executor.execute("print('short')", "短输出")
        assert "已截断" not in result


class TestRegisterFunctions:
    """函数注册测试"""

    @pytest.mark.asyncio
    async def test_register_sync_func(self):
        executor = SandboxExecutor(timeout=5.0)

        def add(a, b):
            return a + b

        executor.register("add", add)
        result = await executor.execute("add(3, 4)", "同步函数")
        assert "7" in result

    @pytest.mark.asyncio
    async def test_register_async_func(self):
        executor = SandboxExecutor(timeout=5.0)

        async def async_add(a, b):
            return a + b

        executor.register("async_add", async_add)
        result = await executor.execute(
            "result = await async_add(5, 6)\nresult", "异步函数"
        )
        assert "11" in result

    @pytest.mark.asyncio
    async def test_isolated_globals(self):
        """每次执行应使用独立的 globals"""
        executor = SandboxExecutor(timeout=5.0)
        await executor.execute("shared_var = 42", "设置变量")
        result = await executor.execute("shared_var", "读取变量")
        # 第二次执行不应看到第一次的变量
        assert "执行错误" in result or "NameError" in result
