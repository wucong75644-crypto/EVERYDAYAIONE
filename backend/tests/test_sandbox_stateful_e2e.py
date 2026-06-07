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
    """带 KernelManager 的有状态执行器(沙盒 IO 统一协议:emit 由 tool_loop_executor 处理)"""
    ex = SandboxExecutor(
        timeout=30.0,
        max_result_chars=8000,
        workspace_dir=ws["workspace"],
        staging_dir=ws["staging"],
        output_dir=ws["output"],
        kernel_manager=km,
        conversation_id="test_conv_001",
    )
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
    async def test_import_os_scoped(self, stateful_executor):
        """import os 返回 scoped 版本（无 system 属性）"""
        result = await stateful_executor.execute(
            "import os\nprint(hasattr(os, 'system'))", "os测试",
        )
        assert "False" in result.summary

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
    async def test_file_write_works(self, stateful_executor, ws):
        """有状态模式下文件写入 + emit_file 协议正常工作。
        新协议:LLM 调 emit_file('下载/x.json') 由 tool_loop_executor 接管上传。
        沙盒主进程不再扫描 output_dir 兜底(对齐 Jupyter/OpenAI 行业标准)。
        """
        code = (
            "with open('下载/report.json', 'w') as f:\n"
            "    f.write('{\"ok\": true}')\n"
            "emit_file('下载/report.json')\n"
            "print('done')"
        )
        result = await stateful_executor.execute(code, "写文件并 emit")
        assert "done" in result.summary
        # 沙盒末尾产 [EMIT] marker,由 tool_loop_executor 处理(此处不验证 upload)
        assert "[EMIT]" in result.summary or "report.json" in result.summary

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
# 路径协议:subprocess 降级路径已删除(Phase 1 路径协议重构)
# TestExceptionDegradation / TestDegradation 测试已删除的旧路径
# Kernel 不可用现在直接返回错误,不再 fallback
# ============================================================


# ============================================================
# 沙盒删除拦截（有状态 Kernel）
# ============================================================

class TestStatefulDeleteBlocked:

    @pytest.mark.asyncio
    async def test_remove_in_kernel_always_blocked(self, stateful_executor, ws):
        """Kernel 模式：沙盒内 os.remove 统一禁止"""
        Path(ws["workspace"], "temp.txt").write_text("data")
        result = await stateful_executor.execute(
            "import os\nos.remove('temp.txt')", "删除",
        )
        assert "沙盒内禁止直接删除文件" in result.summary
        assert Path(ws["workspace"], "temp.txt").exists()
