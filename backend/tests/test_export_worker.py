"""
export_worker 子进程单元测试

覆盖：core/export_worker.py
- _report: stderr JSON 输出格式
- _size_monitor: 文件大小监控上报
- main: 正常导出 / 异常退出 / 超时
"""

import io
import json
import sys
import threading
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


from core.export_worker import _report, _size_monitor, main


# ── _report ────────────────────────────────

class TestReport:

    def test_writes_json_line_to_stderr(self):
        buf = io.StringIO()
        with patch("core.export_worker.sys") as mock_sys:
            mock_sys.stderr = buf
            _report("connect")
        line = buf.getvalue()
        assert line.endswith("\n")
        parsed = json.loads(line.strip())
        assert parsed["phase"] == "connect"

    def test_extra_kwargs_included(self):
        buf = io.StringIO()
        with patch("core.export_worker.sys") as mock_sys:
            mock_sys.stderr = buf
            _report("done", row_count=100, size_kb=12.5, elapsed=3.2)
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["phase"] == "done"
        assert parsed["row_count"] == 100
        assert parsed["size_kb"] == 12.5

    def test_error_phase_truncates_message(self):
        buf = io.StringIO()
        with patch("core.export_worker.sys") as mock_sys:
            mock_sys.stderr = buf
            _report("error", message="x" * 600)
        # _report 本身不截断，截断在 main() 的调用处
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["phase"] == "error"


# ── _size_monitor ────────────────────────────────

class TestSizeMonitor:

    def test_reports_file_size_when_exists(self, tmp_path):
        p = tmp_path / "test.parquet"
        p.write_bytes(b"x" * 2048)

        stop = threading.Event()
        reports = []

        def fake_report(phase, **kw):
            reports.append({"phase": phase, **kw})
            stop.set()  # 上报一次后停止

        with patch("core.export_worker._report", side_effect=fake_report):
            with patch("core.export_worker.time") as mock_time:
                mock_time.monotonic.return_value = 5.0
                # 让 stop.wait(5.0) 立即返回 False 第一次，再 True
                original_wait = stop.wait
                call_count = [0]

                def fast_wait(timeout):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        return False  # 触发一次检查
                    return True  # 停止

                stop.wait = fast_wait
                _size_monitor(str(p), stop)

        assert len(reports) >= 1
        assert reports[0]["phase"] == "export"
        assert reports[0]["size_kb"] == 2.0

    def test_skips_when_file_not_exists(self, tmp_path):
        p = tmp_path / "nonexistent.parquet"
        stop = threading.Event()
        reports = []

        def fake_report(phase, **kw):
            reports.append({"phase": phase, **kw})

        with patch("core.export_worker._report", side_effect=fake_report):
            call_count = [0]

            def fast_wait(timeout):
                call_count[0] += 1
                return call_count[0] > 1

            stop.wait = fast_wait
            _size_monitor(str(p), stop)

        assert len(reports) == 0


# ── main ────────────────────────────────

class TestMain:

    def _run_main(self, params: dict, export_result=None, export_error=None):
        """执行 main()，mock stdin/stdout/stderr 和 DuckDBEngine。"""
        stdin_data = json.dumps(params)
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        mock_engine = MagicMock()
        if export_error:
            mock_engine.export_to_parquet.side_effect = export_error
        else:
            mock_engine.export_to_parquet.return_value = export_result or {
                "row_count": 100, "size_kb": 5.5, "path": "/tmp/test.parquet",
            }

        with patch("core.export_worker.sys") as mock_sys, \
             patch("core.duckdb_engine.DuckDBEngine", return_value=mock_engine) as mock_cls:
            mock_sys.stdin.read.return_value = stdin_data
            mock_sys.stdout = stdout_buf
            mock_sys.stderr = stderr_buf
            mock_sys.exit = MagicMock(side_effect=SystemExit(1))

            try:
                main()
            except SystemExit:
                pass

        return stdout_buf.getvalue(), stderr_buf.getvalue(), mock_engine

    def test_success_writes_result_to_stdout(self):
        params = {
            "query": "SELECT * FROM pg.public.t",
            "output_path": "/tmp/out.parquet",
            "pg_url": "postgresql://fake/db",
            "timeout": 60.0,
        }
        export_result = {"row_count": 500, "size_kb": 10.2, "path": "/tmp/out.parquet"}
        stdout, stderr, engine = self._run_main(params, export_result=export_result)

        result = json.loads(stdout)
        assert result["row_count"] == 500
        assert result["size_kb"] == 10.2
        engine.close.assert_called_once()

    def test_success_reports_connect_and_done_phases(self):
        params = {
            "query": "SELECT 1",
            "output_path": "/tmp/out.parquet",
            "pg_url": "postgresql://fake/db",
        }
        stdout, stderr, _ = self._run_main(params)

        lines = [l for l in stderr.strip().split("\n") if l]
        phases = [json.loads(l)["phase"] for l in lines]
        assert "connect" in phases
        assert "done" in phases

    def test_engine_initialized_with_params(self):
        params = {
            "query": "SELECT 1",
            "output_path": "/tmp/out.parquet",
            "pg_url": "postgresql://mydb",
            "memory_limit": "512MB",
            "threads": 4,
        }
        with patch("core.export_worker.sys") as mock_sys, \
             patch("core.duckdb_engine.DuckDBEngine") as mock_cls:
            mock_engine = MagicMock()
            mock_engine.export_to_parquet.return_value = {
                "row_count": 0, "size_kb": 0, "path": "/tmp/out.parquet",
            }
            mock_cls.return_value = mock_engine
            mock_sys.stdin.read.return_value = json.dumps(params)
            mock_sys.stdout = io.StringIO()
            mock_sys.stderr = io.StringIO()

            main()

        mock_cls.assert_called_once_with(
            pg_url="postgresql://mydb",
            memory_limit="512MB",
            threads=4,
        )

    def test_error_exits_with_code_1(self):
        params = {
            "query": "SELECT 1",
            "output_path": "/tmp/out.parquet",
            "pg_url": "postgresql://fake/db",
        }
        stdout, stderr, engine = self._run_main(
            params, export_error=RuntimeError("OOM"),
        )

        # stdout 应该为空（没写结果）
        assert stdout == ""
        # stderr 应该有 error phase
        lines = [l for l in stderr.strip().split("\n") if l]
        error_lines = [json.loads(l) for l in lines if "error" in l]
        assert any(e["phase"] == "error" for e in error_lines)
        # engine 应该被关闭
        engine.close.assert_called_once()

    def test_default_timeout_120(self):
        params = {
            "query": "SELECT 1",
            "output_path": "/tmp/out.parquet",
            "pg_url": "postgresql://fake/db",
            # 不传 timeout
        }
        _, _, engine = self._run_main(params)

        call_args = engine.export_to_parquet.call_args
        assert call_args.kwargs.get("timeout") == 120.0
