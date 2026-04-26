"""
subprocess_export + _format_progress 单元测试

覆盖：services/kuaimai/erp_export_subprocess.py
- subprocess_export: 子进程调用/超时/错误
- _format_progress: 各 phase 格式化
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


from services.kuaimai.erp_export_subprocess import _format_progress, subprocess_export


# ── _format_progress ────────────────────────────────

class TestFormatProgress:

    def test_connect_phase(self):
        assert _format_progress({"phase": "connect"}) == "正在连接数据库..."

    def test_export_phase_small(self):
        result = _format_progress({"phase": "export", "size_kb": 500, "elapsed": 5})
        assert "500KB" in result
        assert "5s" in result

    def test_export_phase_large(self):
        result = _format_progress({"phase": "export", "size_kb": 2048, "elapsed": 10})
        assert "2.0MB" in result
        assert "10s" in result

    def test_done_phase_small(self):
        result = _format_progress({
            "phase": "done", "row_count": 15000, "size_kb": 500, "elapsed": 30,
        })
        assert "15,000" in result
        assert "500KB" in result

    def test_done_phase_large(self):
        result = _format_progress({
            "phase": "done", "row_count": 100000, "size_kb": 5120, "elapsed": 60,
        })
        assert "100,000" in result
        assert "5.0MB" in result

    def test_error_phase_returns_none(self):
        assert _format_progress({"phase": "error", "message": "OOM"}) is None

    def test_unknown_phase_returns_none(self):
        assert _format_progress({"phase": "unknown"}) is None

    def test_empty_dict_returns_none(self):
        assert _format_progress({}) is None

    def test_export_zero_values(self):
        result = _format_progress({"phase": "export", "size_kb": 0, "elapsed": 0})
        assert "0KB" in result


# ── subprocess_export ────────────────────────────────

def _fake_settings(**overrides):
    """构造 mock settings。"""
    s = MagicMock()
    s.export_subprocess_timeout = overrides.get("timeout", 120)
    s.database_url = "postgresql://fake/db"
    s.duckdb_memory_limit = "256MB"
    s.duckdb_threads = 2
    return s


class TestSubprocessExport:

    @pytest.mark.asyncio
    async def test_success_returns_result(self):
        """正常导出返回 row_count/size_kb。"""
        expected = {"row_count": 1000, "size_kb": 50.5, "path": "/tmp/out.parquet"}

        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout.read = AsyncMock(return_value=json.dumps(expected).encode())
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with patch("services.kuaimai.erp_export_subprocess.get_settings",
                    return_value=_fake_settings()), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await subprocess_export(
                "SELECT 1", "/tmp/out.parquet",
            )

        assert result["row_count"] == 1000
        assert result["size_kb"] == 50.5

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        """超时时 kill 子进程并抛 TimeoutError。"""
        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.stdout.read = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("services.kuaimai.erp_export_subprocess.get_settings",
                    return_value=_fake_settings(timeout=5)), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(TimeoutError, match="timed out"):
                await subprocess_export("SELECT 1", "/tmp/out.parquet")

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_with_stderr(self):
        """子进程 exit code != 0 时抛 RuntimeError 并附带 stderr。"""
        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout.read = AsyncMock(return_value=b"")
        mock_proc.returncode = 1

        stderr_lines = [
            b'{"phase":"connect"}\n',
            b'{"phase":"error","message":"OOM"}\n',
            b"Traceback (most recent call last):\n",
            b"  RuntimeError: Out of memory\n",
            b"",
        ]
        readline_iter = iter(stderr_lines)
        mock_proc.stderr.readline = AsyncMock(side_effect=lambda: next(readline_iter))
        mock_proc.wait = AsyncMock()

        with patch("services.kuaimai.erp_export_subprocess.get_settings",
                    return_value=_fake_settings()), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="exit=1"):
                await subprocess_export("SELECT 1", "/tmp/out.parquet")

    @pytest.mark.asyncio
    async def test_push_thinking_receives_progress(self):
        """push_thinking 回调收到格式化的进度文案。"""
        expected = {"row_count": 100, "size_kb": 5.0, "path": "/tmp/out.parquet"}
        thinking_msgs = []

        async def fake_thinking(text):
            thinking_msgs.append(text)

        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        async def delayed_stdout_read():
            """让 stderr task 先跑完再返回 stdout。"""
            await asyncio.sleep(0.05)
            return json.dumps(expected).encode()

        mock_proc.stdout.read = delayed_stdout_read
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        stderr_lines = [
            b'{"phase":"connect"}\n',
            b'{"phase":"export","size_kb":500,"elapsed":5}\n',
            b'{"phase":"done","row_count":100,"size_kb":5.0,"elapsed":3}\n',
            b"",
        ]
        readline_iter = iter(stderr_lines)
        mock_proc.stderr.readline = AsyncMock(side_effect=lambda: next(readline_iter))

        with patch("services.kuaimai.erp_export_subprocess.get_settings",
                    return_value=_fake_settings()), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await subprocess_export(
                "SELECT 1", "/tmp/out.parquet",
                push_thinking=fake_thinking,
            )

        assert any("正在连接" in m for m in thinking_msgs)
        assert any("500KB" in m for m in thinking_msgs)

    @pytest.mark.asyncio
    async def test_passes_timeout_minus_5_to_subprocess(self):
        """子进程内部 timeout 应比外层少 5s。"""
        expected = {"row_count": 0, "size_kb": 0, "path": "/tmp/out.parquet"}

        captured_stdin = []

        mock_proc = AsyncMock()
        mock_proc.stdin = AsyncMock()
        mock_proc.stdin.write = MagicMock(side_effect=lambda d: captured_stdin.append(d))
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout.read = AsyncMock(return_value=json.dumps(expected).encode())
        mock_proc.stderr.readline = AsyncMock(return_value=b"")
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        with patch("services.kuaimai.erp_export_subprocess.get_settings",
                    return_value=_fake_settings(timeout=120)), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await subprocess_export("SELECT 1", "/tmp/out.parquet")

        params = json.loads(captured_stdin[0])
        assert params["timeout"] == 115  # 120 - 5
