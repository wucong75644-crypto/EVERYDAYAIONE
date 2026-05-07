"""
沙盒子进程 Worker 测试

测试 sandbox_worker.py 中的函数（直接在测试进程中调用，不 spawn 子进程）。
验证：沙盒环境构建、代码执行、安全限制、环境变量清理。
"""

import builtins
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

import gc
import multiprocessing as mp
import resource

_original_open = builtins.open
_queues_to_cleanup: list = []


def _make_queue():
    """创建 Queue 并注册清理，防止 pipe fd 泄漏污染后续测试"""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    _queues_to_cleanup.append(q)
    return q


@pytest.fixture(autouse=True)
def _restore_sandbox_side_effects():
    """恢复沙盒测试的副作用：
    - builtins.open（_build_sandbox_globals 会覆盖）
    - os.environ（_clean_env 会删除 DATABASE_URL 等）
    - multiprocessing Queue pipe fd（显式关闭）
    """
    env_snapshot = os.environ.copy()
    cwd_snapshot = os.getcwd()
    _queues_to_cleanup.clear()
    # 阻止 _apply_resource_limits 在测试进程中生效（设 NPROC=0 不可逆）
    import unittest.mock as _mock
    with _mock.patch(
        "services.sandbox.sandbox_worker._apply_resource_limits", lambda: None
    ):
        yield
    builtins.open = _original_open
    os.environ.clear()
    os.environ.update(env_snapshot)
    os.chdir(cwd_snapshot)
    for q in _queues_to_cleanup:
        try:
            q.close()
            q.join_thread()
        except Exception:
            pass
    _queues_to_cleanup.clear()
    for child in mp.active_children():
        child.join(timeout=2)
    gc.collect()


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
        # os/shutil 已移到白名单（运行时走 scoped_os），其他仍禁止
        for mod in ["sys", "subprocess", "socket", "pickle"]:
            assert mod not in ALLOWED_IMPORT_MODULES
        # os/shutil 在白名单（由 make_restricted_import + scoped_os 保障安全）
        for mod in ["os", "os.path", "shutil"]:
            assert mod in ALLOWED_IMPORT_MODULES

    def test_restricted_import_allows_os_in_whitelist(self):
        """向后兼容：无 scoped 模块时 os 在白名单中，返回真实 os（非生产路径）"""
        mod = restricted_import("os")
        assert hasattr(mod, "path")

    def test_make_restricted_import_returns_scoped(self):
        """make_restricted_import 带 scoped 模块时，import os 返回 scoped 实例"""
        from services.sandbox.sandbox_constants import make_restricted_import

        class _FakeOS:
            class path:
                join = staticmethod(lambda *a: "/".join(a))
        fake = _FakeOS()
        scoped = make_restricted_import({"os": fake})
        result = scoped("os")
        assert result is fake
        assert not hasattr(result, "system")

    def test_make_restricted_import_fromlist_os_path(self):
        """from os.path import join → fromlist 非空时返回 os.path 子模块"""
        from services.sandbox.sandbox_constants import make_restricted_import
        import os as real_os

        class _FakeOS:
            path = real_os.path
        fake = _FakeOS()
        scoped = make_restricted_import({"os": fake})
        result = scoped("os.path", fromlist=("join",))
        assert result is real_os.path

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

    def test_open_delegates_to_builtins(self, tmp_path):
        """sandbox globals 的 open 委托给 builtins.open（安全检查在 worker_entry 层统一处理）"""
        import builtins
        g = _build_sandbox_globals(str(tmp_path), "", "")
        assert g["open"] is builtins.open

    def test_builtins_are_restricted(self, tmp_path):
        g = _build_sandbox_globals(str(tmp_path), "", "")
        # copy 后的 dict（含 scoped_import），不再 is SAFE_BUILTINS
        builtins = g["__builtins__"]
        assert isinstance(builtins, dict)
        assert "print" in builtins
        assert callable(builtins["__import__"])

    def test_scoped_os_injected(self, tmp_path):
        """_build_sandbox_globals 注入 scoped_os/shutil"""
        g = _build_sandbox_globals(str(tmp_path), "", "")
        assert "os" in g
        assert "shutil" in g
        assert not hasattr(g["os"], "system")
        assert hasattr(g["os"], "listdir")


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

    def test_read_workspace_file_via_worker(self, tmp_path):
        """通过 open() 读取 workspace 文件（需走 worker_entry 完整链路）"""
        (tmp_path / "data.txt").write_text("test content")
        q = _make_queue()
        sandbox_worker_entry(
            q, "print(open('data.txt').read())",
            str(tmp_path), "", "", 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "test content" in result


# ============================================================
# sandbox_worker_entry 集成测试（通过 Queue 通信）
# ============================================================


class TestSandboxWorkerEntry:
    """Worker 入口集成测试"""

    def test_normal_execution(self, tmp_path):
        """正常执行返回 ok"""
        q = _make_queue()

        sandbox_worker_entry(
            q, "1 + 1",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "2" in result

    def test_error_execution(self, tmp_path):
        """运行时错误由外层 catch，返回 error"""
        q = _make_queue()

        sandbox_worker_entry(
            q, "1 / 0",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "error"
        assert "执行错误" in result

    def test_validation_failure(self, tmp_path):
        """AST 验证失败返回 error（用 sys 测试，os 已放行）"""
        q = _make_queue()

        sandbox_worker_entry(
            q, "import sys",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "error"
        assert "验证失败" in result

    def test_path_hidden_in_result(self, tmp_path):
        """真实路径在结果中被替换"""
        q = _make_queue()

        sandbox_worker_entry(
            q, "print(WORKSPACE_DIR)",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "ok"
        assert str(tmp_path) not in result
        assert "WORKSPACE_DIR" in result

    def test_chdir_to_workspace(self, tmp_path):
        """子进程 chdir 到 workspace，相对路径可读"""
        (tmp_path / "hello.txt").write_text("chdir works")

        q = _make_queue()

        sandbox_worker_entry(
            q, "print(open('hello.txt').read())",
            str(tmp_path), "", "", 5.0, 1000,
        )

        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "chdir works" in result


# ============================================================
# PandasProxy 测试（nrows 默认限制）
# ============================================================


class TestPandasProxy:
    """pandas 读取默认 nrows=2000 限制"""

    def test_default_nrows_injected(self, tmp_path):
        """未指定 nrows 时自动加 nrows=2000"""
        g = _build_sandbox_globals(str(tmp_path), "", "")
        proxy = g.get("pd")
        if proxy is None:
            pytest.skip("pandas not installed")
        # proxy.read_csv 应该是 wrapped 版本
        import pandas as _pd
        assert proxy.read_csv is not _pd.read_csv  # type: ignore
        assert proxy.read_excel is not _pd.read_excel  # type: ignore

    def test_passthrough_other_attrs(self, tmp_path):
        """非 read_* 属性透传到真实 pd"""
        g = _build_sandbox_globals(str(tmp_path), "", "")
        proxy = g.get("pd")
        if proxy is None:
            pytest.skip("pandas not installed")
        import pandas as _pd
        assert proxy.DataFrame is _pd.DataFrame  # type: ignore
        assert proxy.Series is _pd.Series  # type: ignore

    def test_read_csv_default_nrows(self, tmp_path):
        """read_csv 默认 nrows=2000"""
        try:
            import pandas
        except ImportError:
            pytest.skip("pandas not installed")

        # 创建超过 2000 行的 CSV
        lines = ["value"] + [str(i) for i in range(3000)]
        (tmp_path / "big.csv").write_text("\n".join(lines))

        g = _build_sandbox_globals(str(tmp_path), "", "")
        code = (
            "df = pd.read_csv('big.csv')\n"
            "print(len(df))"
        )
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = _exec_code(code, g, timeout=10.0)
        finally:
            os.chdir(old_cwd)
        assert "2000" in result

    def test_read_csv_explicit_nrows_none(self, tmp_path):
        """显式 nrows=None 全读"""
        try:
            import pandas
        except ImportError:
            pytest.skip("pandas not installed")

        lines = ["value"] + [str(i) for i in range(3000)]
        (tmp_path / "big.csv").write_text("\n".join(lines))

        g = _build_sandbox_globals(str(tmp_path), "", "")
        code = (
            "df = pd.read_csv('big.csv', nrows=None)\n"
            "print(len(df))"
        )
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = _exec_code(code, g, timeout=10.0)
        finally:
            os.chdir(old_cwd)
        assert "3000" in result

    def test_read_csv_explicit_nrows_100(self, tmp_path):
        """显式 nrows=100 按用户指定"""
        try:
            import pandas
        except ImportError:
            pytest.skip("pandas not installed")

        lines = ["value"] + [str(i) for i in range(3000)]
        (tmp_path / "big.csv").write_text("\n".join(lines))

        g = _build_sandbox_globals(str(tmp_path), "", "")
        code = (
            "df = pd.read_csv('big.csv', nrows=100)\n"
            "print(len(df))"
        )
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = _exec_code(code, g, timeout=10.0)
        finally:
            os.chdir(old_cwd)
        assert "100" in result


# ============================================================
# findSimilarFile + scoped_open 文件建议测试
# ============================================================


class TestFindSimilarFile:
    """文件名纠错测试（_find_similar_file_global 模块级函数）"""

    def test_similar_file_space_difference(self, tmp_path):
        """空格差异能匹配（'利润表 - xxx' vs '利润表-xxx'）"""
        from services.sandbox.sandbox_worker import _find_similar_file_global
        (tmp_path / "利润表-数据.txt").write_text("data")

        result = _find_similar_file_global(
            str(tmp_path / "利润表 - 数据.txt"), str(tmp_path),
        )
        assert result  # 应找到相似文件
        assert "利润表-数据.txt" in result

    def test_similar_file_underscore_difference(self, tmp_path):
        """下划线差异能匹配"""
        from services.sandbox.sandbox_worker import _find_similar_file_global
        (tmp_path / "report_2026.csv").write_text("data")

        result = _find_similar_file_global(
            str(tmp_path / "report-2026.csv"), str(tmp_path),
        )
        assert result
        assert "report_2026.csv" in result

    def test_no_similar_file(self, tmp_path):
        """没有相似文件时返回空"""
        from services.sandbox.sandbox_worker import _find_similar_file_global

        result = _find_similar_file_global(
            str(tmp_path / "completely_different.txt"), str(tmp_path),
        )
        assert result == ""

    def test_exact_file_via_worker(self, tmp_path):
        """通过 sandbox_worker_entry 完整链路测试精确文件名读取"""
        (tmp_path / "data.txt").write_text("hello from worker")
        q = _make_queue()

        sandbox_worker_entry(
            q, "print(open('data.txt').read())",
            str(tmp_path), "", "", 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "hello from worker" in result

    def test_auto_correct_via_worker(self, tmp_path):
        """通过完整链路测试文件名自动纠错"""
        (tmp_path / "利润表-数据.xlsx").write_bytes(b"fake xlsx")
        q = _make_queue()

        # AI 拼错的文件名（多了空格）
        sandbox_worker_entry(
            q, "f = open('利润表 - 数据.xlsx', 'rb')\nprint(len(f.read()))\nf.close()",
            str(tmp_path), "", "", 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "9" in result  # b"fake xlsx" = 9 bytes

    def test_outside_workspace_blocked_via_worker(self, tmp_path):
        """通过完整链路测试路径穿越拦截"""
        q = _make_queue()

        sandbox_worker_entry(
            q, "open('/etc/passwd').read()",
            str(tmp_path), "", "", 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert "文件访问被拒绝" in result or "PermissionError" in result

    def test_output_dir_variable_write(self, tmp_path):
        """Agent 用 OUTPUT_DIR 变量写文件"""
        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        q = _make_queue()

        sandbox_worker_entry(
            q,
            "f = open(OUTPUT_DIR + '/test.txt', 'w')\nf.write('hello')\nf.close()\nprint('ok')",
            str(tmp_path), "", str(output_dir), 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "ok" in result
        assert (output_dir / "test.txt").read_text() == "hello"

    def test_staging_dir_variable_write(self, tmp_path):
        """Agent 用 STAGING_DIR 变量写文件"""
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        q = _make_queue()

        sandbox_worker_entry(
            q,
            "f = open(STAGING_DIR + '/mid.txt', 'w')\nf.write('mid')\nf.close()\nprint('ok')",
            str(tmp_path), str(staging_dir), "", 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert (staging_dir / "mid.txt").read_text() == "mid"

    def test_output_dir_write_read_roundtrip(self, tmp_path):
        """通过 OUTPUT_DIR 写入后可读回"""
        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        q = _make_queue()

        sandbox_worker_entry(
            q,
            (
                "f = open(OUTPUT_DIR + '/data.txt', 'w')\nf.write('roundtrip')\nf.close()\n"
                "f2 = open(OUTPUT_DIR + '/data.txt')\nprint(f2.read())\nf2.close()"
            ),
            str(tmp_path), "", str(output_dir), 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "roundtrip" in result

    def test_fallback_search_output_dir(self, tmp_path):
        """文件在 OUTPUT_DIR 里，Agent 用相对路径读取 → 自动搜到"""
        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        (output_dir / "报表_2026-04-20至04-26.csv").write_text("a,b\n1,2")
        q = _make_queue()

        # Agent 用相对路径，workspace 根目录找不到 → fallback 搜 OUTPUT_DIR
        sandbox_worker_entry(
            q, "print(open('报表_2026-04-20至04-26.csv').read())",
            str(tmp_path), "", str(output_dir), 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "1,2" in result

    def test_fallback_search_output_dir_fuzzy(self, tmp_path):
        """文件在 OUTPUT_DIR + 文件名有空格差异 → 模糊纠错搜到"""
        output_dir = tmp_path / "下载"
        output_dir.mkdir()
        # 实际文件名无空格
        (output_dir / "汇总_2026-04-20至04-26.csv").write_text("x,y\n3,4")
        q = _make_queue()

        # Agent 文件名多了空格（"至"前后）
        sandbox_worker_entry(
            q, "print(open('汇总_2026-04-20 至 04-26.csv').read())",
            str(tmp_path), "", str(output_dir), 5.0, 1000,
        )
        status, result = q.get(timeout=5)
        assert status == "ok"
        assert "3,4" in result
