"""
有状态 Kernel Worker 测试

通过 subprocess 启动 kernel_worker，验证：
  - stdin/stdout JSON-Line 协议
  - 变量跨调用保留
  - builtins/__import__/open 每次执行前重置
  - 安全拦截（import os、eval）
  - 超时处理 + 超时后变量保留
  - 空代码 / 协议错误处理
  - matplotlib / pandas 跨调用保留
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest


@pytest.fixture
def kernel_dirs():
    """创建临时目录，测试后清理"""
    workspace = tempfile.mkdtemp(prefix="kernel_test_ws_")
    staging = tempfile.mkdtemp(prefix="kernel_test_st_")
    output = tempfile.mkdtemp(prefix="kernel_test_out_")
    yield workspace, staging, output
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)


@pytest.fixture
def kernel_proc(kernel_dirs):
    """启动 kernel_worker 子进程，测试后关闭"""
    workspace, staging, output = kernel_dirs
    proc = subprocess.Popen(
        [sys.executable, "-m", "services.sandbox.kernel_worker",
         workspace, staging, output],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    # 等待 ready 信号
    ready_line = proc.stdout.readline()
    ready = json.loads(ready_line)
    assert ready["id"] == "__ready__"
    assert ready["status"] == "ok"
    yield proc
    # 清理
    if proc.poll() is None:
        proc.stdin.close()
        proc.wait(timeout=10)


def _send(proc, req: dict) -> dict:
    """向 kernel_worker 发送请求并等待响应"""
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "kernel_worker 未返回响应（进程可能已退出）"
    return json.loads(line)


# ============================================================
# 基础功能
# ============================================================

class TestBasicExecution:

    def test_simple_print(self, kernel_proc):
        r = _send(kernel_proc, {"id": "t1", "code": "print('hello')", "timeout": 10})
        assert r["status"] == "ok"
        assert r["result"] == "hello"
        assert r["id"] == "t1"
        assert "elapsed_ms" in r

    def test_expression_value(self, kernel_proc):
        r = _send(kernel_proc, {"id": "t2", "code": "1 + 2", "timeout": 10})
        assert r["status"] == "ok"
        assert "3" in r["result"]

    def test_empty_code(self, kernel_proc):
        r = _send(kernel_proc, {"id": "t3", "code": "", "timeout": 10})
        assert r["status"] == "error"

    def test_whitespace_code(self, kernel_proc):
        r = _send(kernel_proc, {"id": "t4", "code": "   \n  ", "timeout": 10})
        assert r["status"] == "error"


# ============================================================
# 有状态核心：变量跨调用保留
# ============================================================

class TestStatefulPersistence:

    def test_variable_persists(self, kernel_proc):
        _send(kernel_proc, {"id": "s1", "code": "x = 42", "timeout": 10})
        r = _send(kernel_proc, {"id": "s2", "code": "print(x)", "timeout": 10})
        assert r["status"] == "ok"
        assert "42" in r["result"]

    def test_function_persists(self, kernel_proc):
        _send(kernel_proc, {"id": "s3", "code": "def double(n): return n * 2", "timeout": 10})
        r = _send(kernel_proc, {"id": "s4", "code": "print(double(21))", "timeout": 10})
        assert r["status"] == "ok"
        assert "42" in r["result"]

    def test_pandas_dataframe_persists(self, kernel_proc):
        _send(kernel_proc, {
            "id": "s5",
            "code": "import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3], 'b': [4,5,6]})",
            "timeout": 10,
        })
        r = _send(kernel_proc, {"id": "s6", "code": "print(df['a'].sum())", "timeout": 10})
        assert r["status"] == "ok"
        assert "6" in r["result"]

    def test_multiple_variables(self, kernel_proc):
        _send(kernel_proc, {"id": "s7", "code": "a = 10\nb = 20\nc = a + b", "timeout": 10})
        r = _send(kernel_proc, {"id": "s8", "code": "print(a, b, c)", "timeout": 10})
        assert r["status"] == "ok"
        assert "10" in r["result"]
        assert "20" in r["result"]
        assert "30" in r["result"]

    def test_variable_mutation(self, kernel_proc):
        _send(kernel_proc, {"id": "s9", "code": "items = [1, 2, 3]", "timeout": 10})
        _send(kernel_proc, {"id": "s10", "code": "items.append(4)", "timeout": 10})
        r = _send(kernel_proc, {"id": "s11", "code": "print(len(items))", "timeout": 10})
        assert r["status"] == "ok"
        assert "4" in r["result"]


# ============================================================
# 安全：每次执行前重置
# ============================================================

class TestSecurityReset:

    def test_import_os_returns_scoped(self, kernel_proc):
        """import os 返回 scoped 版本，无 system 属性"""
        r = _send(kernel_proc, {"id": "sec1", "code": "import os\nprint(hasattr(os, 'system'))", "timeout": 10})
        assert r["status"] == "ok"
        assert "False" in r["result"]

    def test_import_subprocess_blocked(self, kernel_proc):
        r = _send(kernel_proc, {"id": "sec2", "code": "import subprocess", "timeout": 10})
        assert r["status"] == "error"

    def test_eval_blocked(self, kernel_proc):
        r = _send(kernel_proc, {"id": "sec3", "code": "eval('1+1')", "timeout": 10})
        assert r["status"] == "error"

    def test_blocked_import_doesnt_kill_kernel(self, kernel_proc):
        """安全拦截后 Kernel 继续工作，变量保留"""
        _send(kernel_proc, {"id": "sec4a", "code": "x = 99", "timeout": 10})
        _send(kernel_proc, {"id": "sec4b", "code": "import os", "timeout": 10})
        r = _send(kernel_proc, {"id": "sec4c", "code": "print(x)", "timeout": 10})
        assert r["status"] == "ok"
        assert "99" in r["result"]

    def test_builtins_reset_after_tampering(self, kernel_proc):
        """用户尝试篡改 __builtins__ 后，下次执行应被重置"""
        # 尝试注入 eval 到 builtins（在沙盒内 __builtins__ 是 dict）
        _send(kernel_proc, {
            "id": "sec5a",
            "code": "__builtins__['eval'] = lambda x: x",
            "timeout": 10,
        })
        # 下次执行前 builtins 被重置，eval 应该不存在
        r = _send(kernel_proc, {
            "id": "sec5b",
            "code": "eval('1+1')",
            "timeout": 10,
        })
        assert r["status"] == "error"

    def test_safe_import_works(self, kernel_proc):
        """白名单模块可以正常导入"""
        r = _send(kernel_proc, {
            "id": "sec6",
            "code": "import math\nprint(math.pi)",
            "timeout": 10,
        })
        assert r["status"] == "ok"
        assert "3.14" in r["result"]


# ============================================================
# 超时处理
# ============================================================

class TestTimeout:

    def test_infinite_loop_timeout(self, kernel_proc):
        r = _send(kernel_proc, {"id": "to1", "code": "while True: pass", "timeout": 2})
        assert r["status"] == "timeout"

    def test_variable_survives_timeout(self, kernel_proc):
        """超时后变量仍然保留"""
        _send(kernel_proc, {"id": "to2a", "code": "y = 123", "timeout": 10})
        _send(kernel_proc, {"id": "to2b", "code": "while True: pass", "timeout": 2})
        r = _send(kernel_proc, {"id": "to2c", "code": "print(y)", "timeout": 10})
        assert r["status"] == "ok"
        assert "123" in r["result"]


# ============================================================
# 文件操作
# ============================================================

class TestFileOperations:

    def test_write_and_read_file(self, kernel_proc, kernel_dirs):
        workspace, _, _ = kernel_dirs
        _send(kernel_proc, {
            "id": "f1",
            "code": "with open('test.txt', 'w') as f:\n    f.write('hello kernel')",
            "timeout": 10,
        })
        r = _send(kernel_proc, {
            "id": "f2",
            "code": "with open('test.txt') as f:\n    print(f.read())",
            "timeout": 10,
        })
        assert r["status"] == "ok"
        assert "hello kernel" in r["result"]
        # 验证文件确实在 workspace
        assert os.path.exists(os.path.join(workspace, "test.txt"))

    def test_access_outside_workspace_blocked(self, kernel_proc):
        r = _send(kernel_proc, {
            "id": "f3",
            "code": "open('/etc/passwd').read()",
            "timeout": 10,
        })
        assert r["status"] == "error"
        assert "拒绝" in r["result"] or "PermissionError" in r["result"]


# ============================================================
# 协议健壮性
# ============================================================

class TestProtocol:

    def test_missing_id(self, kernel_proc):
        r = _send(kernel_proc, {"code": "print(1)", "timeout": 10})
        assert r["status"] == "ok"
        assert r["id"] == "unknown"

    def test_missing_timeout_uses_default(self, kernel_proc):
        r = _send(kernel_proc, {"id": "p1", "code": "print('ok')"})
        assert r["status"] == "ok"

    def test_sequential_requests(self, kernel_proc):
        """连续发送多个请求，每个都能正确响应"""
        for i in range(10):
            r = _send(kernel_proc, {"id": f"seq_{i}", "code": f"print({i})", "timeout": 5})
            assert r["status"] == "ok"
            assert str(i) in r["result"]
            assert r["id"] == f"seq_{i}"

    def test_graceful_shutdown(self, kernel_proc):
        """关闭 stdin 后进程正常退出"""
        kernel_proc.stdin.close()
        exit_code = kernel_proc.wait(timeout=5)
        assert exit_code == 0
