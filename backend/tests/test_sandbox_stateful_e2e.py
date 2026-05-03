"""
有状态沙盒 E2E 集成测试

完整链路：SandboxExecutor → KernelManager → kernel_worker
验证：有状态执行、降级无状态、文件上传、变量保留。
"""

import asyncio
import os
from pathlib import Path

import pytest

from services.sandbox.executor import SandboxExecutor
from services.sandbox.kernel_manager import KernelManager


@pytest.fixture
def ws(tmp_path):
    """用户 workspace（含 output/staging 子目录）"""
    output = tmp_path / "下载"
    staging = tmp_path / "staging" / "conv_001"
    output.mkdir(parents=True)
    staging.mkdir(parents=True)
    return {
        "workspace": str(tmp_path),
        "output": str(output),
        "staging": str(staging),
    }


@pytest.fixture
async def km():
    """创建并启动 KernelManager"""
    manager = KernelManager()
    await manager.start()
    yield manager
    await manager.shutdown()


@pytest.fixture
def stateful_executor(ws, km):
    """带 KernelManager 的有状态执行器"""
    uploaded = []

    async def mock_upload(filename, size):
        uploaded.append(filename)
        return f"✅ {filename}"

    ex = SandboxExecutor(
        timeout=30.0,
        max_result_chars=8000,
        workspace_dir=ws["workspace"],
        staging_dir=ws["staging"],
        output_dir=ws["output"],
        upload_fn=mock_upload,
        kernel_manager=km,
        conversation_id="test_conv_001",
    )
    ex._uploaded = uploaded
    return ex


# ============================================================
# 有状态核心：变量跨调用保留
# ============================================================

class TestStatefulExecution:

    @pytest.mark.asyncio
    async def test_variable_persists_across_calls(self, stateful_executor):
        """变量在多次 execute() 之间保留"""
        await stateful_executor.execute("x = 42", "定义变量")
        result = await stateful_executor.execute("print(x * 2)", "使用变量")
        assert "84" in result.summary

    @pytest.mark.asyncio
    async def test_dataframe_persists(self, stateful_executor):
        """DataFrame 跨调用保留"""
        await stateful_executor.execute(
            "import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3]})",
            "创建 DataFrame",
        )
        result = await stateful_executor.execute("print(df['a'].sum())", "聚合")
        assert "6" in result.summary

    @pytest.mark.asyncio
    async def test_function_persists(self, stateful_executor):
        """自定义函数跨调用保留"""
        await stateful_executor.execute("def greet(name): return f'hi {name}'", "定义函数")
        result = await stateful_executor.execute("print(greet('world'))", "调用函数")
        assert "hi world" in result.summary

    @pytest.mark.asyncio
    async def test_error_preserves_state(self, stateful_executor):
        """执行错误不影响已有变量"""
        await stateful_executor.execute("saved = 'important'", "保存")
        await stateful_executor.execute("1/0", "错误")  # 除零错误
        result = await stateful_executor.execute("print(saved)", "恢复")
        assert "important" in result.summary

    @pytest.mark.asyncio
    async def test_timeout_preserves_state(self, stateful_executor):
        """超时不影响已有变量（超时只中断当前执行，不杀 Kernel）"""
        await stateful_executor.execute("keeper = 123", "保存")
        stateful_executor._timeout = 2
        await stateful_executor.execute("while True: pass", "超时")
        stateful_executor._timeout = 30
        result = await stateful_executor.execute("print(keeper)", "恢复")
        assert "123" in result.summary


# ============================================================
# 安全：builtins 重置
# ============================================================

class TestStatefulSecurity:

    @pytest.mark.asyncio
    async def test_import_os_blocked(self, stateful_executor):
        result = await stateful_executor.execute("import os", "拦截")
        assert result.is_failure

    @pytest.mark.asyncio
    async def test_builtins_tamper_reset(self, stateful_executor):
        """用户篡改 builtins 后，下次执行被重置"""
        await stateful_executor.execute(
            "__builtins__['eval'] = lambda x: x", "篡改",
        )
        result = await stateful_executor.execute("eval('1+1')", "验证")
        assert result.is_failure


# ============================================================
# 文件上传集成
# ============================================================

class TestStatefulFileUpload:

    @pytest.mark.asyncio
    async def test_file_upload_works(self, stateful_executor, ws):
        """有状态模式下文件上传仍然正常"""
        code = (
            "with open(OUTPUT_DIR + '/report.json', 'w') as f:\n"
            "    f.write('{\"ok\": true}')\n"
            "print('done')"
        )
        result = await stateful_executor.execute(code, "写文件")
        assert "done" in result.summary
        assert "report.json" in stateful_executor._uploaded

    @pytest.mark.asyncio
    async def test_file_persists_in_workspace(self, stateful_executor, ws):
        """有状态模式下写入 workspace 的文件跨调用可访问"""
        await stateful_executor.execute(
            "with open('memo.txt', 'w') as f: f.write('remember this')",
            "写文件",
        )
        result = await stateful_executor.execute(
            "print(open('memo.txt').read())", "读文件",
        )
        assert "remember this" in result.summary


# ============================================================
# 降级：KernelManager 不可用时走无状态
# ============================================================

class TestExceptionDegradation:
    """Kernel 执行中异常时自动降级为无状态 subprocess"""

    @pytest.mark.asyncio
    async def test_kernel_keyerror_fallback(self, ws, km):
        """Kernel 死亡导致 KeyError 时降级为无状态 subprocess"""
        ex = SandboxExecutor(
            timeout=10.0,
            workspace_dir=ws["workspace"],
            staging_dir=ws["staging"],
            output_dir=ws["output"],
            kernel_manager=km,
            conversation_id="test_crash",
        )
        # 先创建 Kernel
        await ex.execute("x = 1", "init")
        # 强制杀死 Kernel 进程
        kernel = km._kernels["test_crash"]
        kernel.process.kill()
        await kernel.process.wait()
        # 下次执行应降级为无状态 subprocess（不抛异常）
        result = await ex.execute("print(42)", "降级执行")
        assert "42" in result.summary

    @pytest.mark.asyncio
    async def test_kernel_runtime_error_fallback(self, ws):
        """KernelManager 启动失败（RuntimeError）时降级"""
        from unittest.mock import AsyncMock

        mock_km = AsyncMock()
        mock_km.get_or_create = AsyncMock(side_effect=RuntimeError("spawn failed"))

        ex = SandboxExecutor(
            timeout=10.0,
            workspace_dir=ws["workspace"],
            staging_dir=ws["staging"],
            output_dir=ws["output"],
            kernel_manager=mock_km,
            conversation_id="test_fail",
        )
        result = await ex.execute("print(99)", "降级测试")
        assert "99" in result.summary


class TestDegradation:

    @pytest.mark.asyncio
    async def test_no_kernel_manager_fallback(self, ws):
        """kernel_manager=None 时降级为无状态 subprocess"""
        ex = SandboxExecutor(
            timeout=10.0,
            workspace_dir=ws["workspace"],
            staging_dir=ws["staging"],
            output_dir=ws["output"],
            kernel_manager=None,  # 无 KernelManager
            conversation_id="test",
        )
        result = await ex.execute("print(42)", "降级测试")
        assert "42" in result.summary

    @pytest.mark.asyncio
    async def test_no_conversation_id_fallback(self, ws, km):
        """conversation_id 为空时降级为无状态"""
        ex = SandboxExecutor(
            timeout=10.0,
            workspace_dir=ws["workspace"],
            staging_dir=ws["staging"],
            output_dir=ws["output"],
            kernel_manager=km,
            conversation_id="",  # 空 conversation_id
        )
        result = await ex.execute("print(42)", "降级测试")
        assert "42" in result.summary
