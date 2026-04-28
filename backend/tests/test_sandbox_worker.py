"""
沙盒子进程 Worker 测试

测试 sandbox_worker.py 中的函数（直接在测试进程中调用，不 spawn 子进程）。
验证：沙盒环境构建、代码执行、安全限制、环境变量清理。
"""

import os
import pytest
from pathlib import Path

from services.sandbox.sandbox_worker import (
    _build_sandbox_globals,
    _clean_env,
    _exec_code,
    sandbox_worker_entry,
)
from services.sandbox.sandbox_constants import (
    SAFE_BUILTINS,
    ALLOWED_IMPORT_MODULES,
    SENSITIVE_ENV_PREFIXES,
    restricted_import,
)


# ============================================================
# sandbox_constants 测试
# ============================================================


class TestSandboxConstants:
    """共享安全常量测试"""

    def test_safe_builtins_has_essential_funcs(self):
        for name in ["int", "float", "str", "len", "sum", "range", "print"]:
            assert name in SAFE_BUILTINS

    def test_safe_builtins_blocks_dangerous(self):
        """不包含危险内置函数"""
        for name in ["eval", "exec", "compile", "__import__"]:
            # __import__ 存在但是 restricted 版本
            if name == "__import__":
                assert SAFE_BUILTINS["__import__"] is restricted_import
            else:
                assert name not in SAFE_BUILTINS

    def test_allowed_modules_includes_data_libs(self):
        for mod in ["pandas", "numpy", "matplotlib", "json", "math"]:
            assert mod in ALLOWED_IMPORT_MODULES

    def test_allowed_modules_excludes_dangerous(self):
        for mod in ["os", "sys", "subprocess", "socket", "pickle"]:
            assert mod not in ALLOWED_IMPORT_MODULES

    def test_restricted_import_blocks_os(self):
        with pytest.raises(ImportError, match="禁止导入"):
            restricted_import("os")

    def test_restricted_import_allows_json(self):
        mod = restricted_import("json")
        assert mod.__name__ == "json"

    def test_sensitive_env_prefixes_are_tuples(self):
        assert isinstance(SENSITIVE_ENV_PREFIXES, tuple)
        assert len(SENSITIVE_ENV_PREFIXES) > 0


# ============================================================
# _clean_env 测试
# ============================================================


class TestCleanEnv:
    """敏感环境变量清理测试"""

    def test_removes_matching_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dk-test")
        monkeypatch.setenv("SAFE_VAR", "keep-me")

        _clean_env()

        assert "OPENAI_API_KEY" not in os.environ
        assert "DASHSCOPE_API_KEY" not in os.environ
        assert os.environ.get("SAFE_VAR") == "keep-me"

    def test_does_not_remove_unrelated_vars(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/root")
        monkeypatch.setenv("LANG", "en_US.UTF-8")

        _clean_env()

        assert "PATH" in os.environ
        assert "HOME" in os.environ
        assert "LANG" in os.environ

    def test_precise_prefix_match(self, monkeypatch):
        """不做模糊子串匹配，MONKEY_KEY 不会被误删"""
        monkeypatch.setenv("MONKEY_KEY", "banana")
        monkeypatch.setenv("MY_TOKEN_TRACKER", "v1")

        _clean_env()

        assert os.environ.get("MONKEY_KEY") == "banana"
        assert os.environ.get("MY_TOKEN_TRACKER") == "v1"


# ============================================================
# _build_sandbox_globals 测试
# ============================================================


class TestBuildSandboxGlobals:
    """沙盒环境构建测试"""

    def test_has_essential_modules(self, tmp_path):
        g = _build_sandbox_globals(str(tmp_path), "", "")
        assert "math" in g
        assert "json" in g
        assert "datetime" in g
        assert "Decimal" in g
        assert "Path" in g

    def test_has_pandas_if_available(self, tmp_path):
        g = _build_sandbox_globals(str(tmp_path), "", "")
        # pandas 可能未安装，跳过
        try:
            import pandas
            assert "pd" in g
            assert "DataFrame" in g
        except ImportError:
            pass

    def test_workspace_dir_injected(self, tmp_path):
        g = _build_sandbox_globals(str(tmp_path), "", "")
        assert g["WORKSPACE_DIR"] == str(tmp_path)

    def test_staging_dir_injected(self, tmp_path):
        staging = str(tmp_path / "staging")
        g = _build_sandbox_globals("", staging, "")
        assert g["STAGING_DIR"] == staging

    def test_output_dir_injected_and_created(self, tmp_path):
        output = str(tmp_path / "下载")
        g = _build_sandbox_globals("", "", output)
        assert g["OUTPUT_DIR"] == output
        assert Path(output).exists()

    def test_scoped_open_resolves_relative_to_workspace(self, tmp_path):
        """相对路径自动解析到 workspace"""
        (tmp_path / "test.txt").write_text("workspace file")
        g = _build_sandbox_globals(str(tmp_path), "", "")

        result = g["open"]("test.txt", "r").read()
        assert result == "workspace file"

    def test_scoped_open_blocks_outside_workspace(self, tmp_path):
        """访问 workspace 外路径被拦截"""
        g = _build_sandbox_globals(str(tmp_path), "", "")

        with pytest.raises(PermissionError, match="文件访问被拒绝"):
            g["open"]("/etc/passwd", "r")

    def test_builtins_are_restricted(self, tmp_path):
        g = _build_sandbox_globals(str(tmp_path), "", "")
        assert g["__builtins__"] is SAFE_BUILTINS


# ============================================================
# _exec_code 测试
# ============================================================


class TestExecCode:
    """代码执行测试（直接调用，不经子进程）"""

    def _make_globals(self, tmp_path):
        return _build_sandbox_globals(str(tmp_path), "", "")

    def test_simple_expression(self, tmp_path):
        g = self._make_globals(tmp_path)
        result = _exec_code("1 + 1", g, timeout=5.0)
        assert "2" in result

    def test_print_output(self, tmp_path):
        g = self._make_globals(tmp_path)
        result = _exec_code("print('hello')", g, timeout=5.0)
        assert "hello" in result

    def test_last_expression_captured(self, tmp_path):
        g = self._make_globals(tmp_path)
        result = _exec_code("x = 42\nx", g, timeout=5.0)
        assert "42" in result

    def test_no_output(self, tmp_path):
        g = self._make_globals(tmp_path)
        result = _exec_code("x = 1", g, timeout=5.0)
        assert "成功" in result

    def test_runtime_error(self, tmp_path):
        """运行时异常由 _exec_code 抛出，sandbox_worker_entry 外层 catch"""
        g = self._make_globals(tmp_path)
        with pytest.raises(ZeroDivisionError):
            _exec_code("1 / 0", g, timeout=5.0)

    def test_timeout_on_infinite_loop(self, tmp_path):
        g = self._make_globals(tmp_path)
        result = _exec_code("while True: pass", g, timeout=0.5)
        assert "超时" in result

    def test_async_not_supported(self, tmp_path):
        g = self._make_globals(tmp_path)
        result = _exec_code("await some_func()", g, timeout=5.0)
        assert "不支持" in result

    def test_read_workspace_file(self, tmp_path):
        """通过 open() 读取 workspace 文件"""
        (tmp_path / "data.txt").write_text("test content")
        g = self._make_globals(tmp_path)
        result = _exec_code("print(open('data.txt').read())", g, timeout=5.0)
        assert "test content" in result


# ============================================================
# sandbox_worker_entry 集成测试（通过 Queue 通信）
# ============================================================


class TestSandboxWorkerEntry:
    """Worker 入口集成测试"""

    def test_normal_execution(self, tmp_path):
        """正常执行返回 ok"""
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        sandbox_worker_entry(
            q, "1 + 1",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "2" in result

    def test_error_execution(self, tmp_path):
        """运行时错误由外层 catch，返回 error"""
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        sandbox_worker_entry(
            q, "1 / 0",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "error"
        assert "执行错误" in result

    def test_validation_failure(self, tmp_path):
        """AST 验证失败返回 error"""
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        sandbox_worker_entry(
            q, "import os",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "error"
        assert "验证失败" in result

    def test_path_hidden_in_result(self, tmp_path):
        """真实路径在结果中被替换"""
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        sandbox_worker_entry(
            q, "print(WORKSPACE_DIR)",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "ok"
        assert str(tmp_path) not in result
        assert "工作区" in result

    def test_chdir_to_workspace(self, tmp_path):
        """子进程 chdir 到 workspace，相对路径可读"""
        (tmp_path / "hello.txt").write_text("chdir works")

        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        sandbox_worker_entry(
            q, "print(open('hello.txt').read())",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "chdir works" in result
