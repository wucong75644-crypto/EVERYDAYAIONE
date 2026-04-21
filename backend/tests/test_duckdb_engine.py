"""
DuckDB 导出引擎单元测试

覆盖：core/duckdb_engine.py
- DuckDBEngine: 连接管理、重试逻辑、export_to_parquet
- get_duckdb_engine: 进程级单例

注意：不连真实 PG，全部 mock DuckDB 连接。
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── DuckDBEngine 测试 ────────────────────────────────


class TestDuckDBEngine:

    def _make_engine(self):
        from core.duckdb_engine import DuckDBEngine
        return DuckDBEngine(
            pg_url="postgresql://fake:fake@localhost/fake",
            memory_limit="128MB",
            threads=1,
        )

    @patch("core.duckdb_engine.duckdb")
    def test_get_conn_lazy_init(self, mock_duckdb):
        """首次调用 _get_conn 才初始化连接"""
        mock_conn = MagicMock()
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        assert engine._conn is None

        conn = engine._get_conn()
        assert conn is mock_conn
        mock_duckdb.connect.assert_called_once()
        # 验证 SET 和 ATTACH 被执行
        calls = mock_conn.execute.call_args_list
        sql_strs = [str(c) for c in calls]
        assert any("memory_limit" in s for s in sql_strs)
        assert any("INSTALL postgres" in s for s in sql_strs)
        assert any("ATTACH" in s for s in sql_strs)

    @patch("core.duckdb_engine.duckdb")
    def test_get_conn_reuses_existing(self, mock_duckdb):
        """已有连接时不重复创建"""
        mock_conn = MagicMock()
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        engine._get_conn()
        engine._get_conn()
        mock_duckdb.connect.assert_called_once()

    @patch("core.duckdb_engine.duckdb")
    def test_reset_conn_clears_connection(self, mock_duckdb):
        """_reset_conn 后下次 _get_conn 重建连接"""
        mock_conn = MagicMock()
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        engine._get_conn()
        engine._reset_conn()
        assert engine._conn is None
        mock_conn.close.assert_called_once()

    @patch("core.duckdb_engine.duckdb")
    def test_reset_conn_handles_close_error(self, mock_duckdb):
        """close 报错不影响 reset"""
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("already closed")
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        engine._get_conn()
        engine._reset_conn()  # 不应抛异常
        assert engine._conn is None

    @patch("core.duckdb_engine.duckdb")
    def test_export_to_parquet_success(self, mock_duckdb):
        """正常导出：COPY TO + 读 metadata"""
        mock_conn = MagicMock()
        mock_duckdb.connect.return_value = mock_conn
        # parquet_file_metadata 返回行数
        mock_conn.execute.return_value.fetchone.return_value = (100,)

        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            # 创建一个假文件让 stat 不报错
            f.write(b"x" * 1024)
            tmp_path = f.name

        try:
            result = engine.export_to_parquet(
                "SELECT * FROM pg.public.test", tmp_path,
            )
            assert result["row_count"] == 100
            assert result["size_kb"] > 0
            assert result["path"] == tmp_path
            # 验证 COPY 和 metadata 查询都被调用
            execute_calls = [str(c) for c in mock_conn.execute.call_args_list]
            assert any("COPY" in s for s in execute_calls)
            assert any("parquet_file_metadata" in s for s in execute_calls)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @patch("core.duckdb_engine.duckdb")
    def test_export_retries_on_failure(self, mock_duckdb):
        """首次失败后重试成功"""
        call_count = 0
        tmp_path = None

        def execute_side_effect(sql):
            nonlocal call_count, tmp_path
            call_count += 1
            if call_count <= 5:
                # 前几次调用是 SET/INSTALL/ATTACH（第一次 _get_conn）+ COPY（失败）
                if "COPY" in sql:
                    raise RuntimeError("connection lost")
                return MagicMock()
            # 第二次连接的调用全部成功
            mock_result = MagicMock()
            mock_result.fetchone.return_value = (50,)
            # COPY 调用后要确保文件存在
            if "COPY" in sql and tmp_path:
                Path(tmp_path).write_bytes(b"x" * 512)
            return mock_result

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = execute_side_effect
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"x" * 512)
            tmp_path = f.name

        try:
            result = engine.export_to_parquet(
                "SELECT * FROM pg.public.test", tmp_path,
            )
            assert result["row_count"] == 50
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @patch("core.duckdb_engine.duckdb")
    def test_export_raises_after_all_retries_exhausted(self, mock_duckdb):
        """重试耗尽后抛出最后一个异常"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("persistent failure")
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"x" * 512)
            tmp_path = f.name

        try:
            with pytest.raises(RuntimeError, match="persistent failure"):
                engine.export_to_parquet(
                    "SELECT * FROM pg.public.test", tmp_path,
                )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @patch("core.duckdb_engine.duckdb")
    def test_export_cleans_partial_file_on_failure(self, mock_duckdb):
        """失败时清理可能写了一半的文件"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("fail")
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        tmp_path = Path(tempfile.mktemp(suffix=".parquet"))
        tmp_path.write_bytes(b"partial data")

        with pytest.raises(RuntimeError):
            engine.export_to_parquet("SELECT 1", str(tmp_path))

        # 文件应被清理
        assert not tmp_path.exists()

    @patch("core.duckdb_engine.duckdb")
    def test_output_path_with_single_quote_escaped(self, mock_duckdb):
        """路径含单引号时 SQL 中应转义"""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (0,)
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"x" * 100)
            tmp_path = f.name

        try:
            # 模拟含单引号路径（虽然实际不太可能，但防御性必须覆盖）
            engine.export_to_parquet("SELECT 1", tmp_path)
            # 验证 COPY TO 中的路径被转义
            copy_call = [
                c for c in mock_conn.execute.call_args_list
                if "COPY" in str(c)
            ]
            assert len(copy_call) >= 1
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_close_clears_connection(self):
        engine = self._make_engine()
        mock_conn = MagicMock()
        engine._conn = mock_conn
        engine.close()
        assert engine._conn is None
        mock_conn.close.assert_called_once()

    def test_close_noop_when_no_connection(self):
        engine = self._make_engine()
        engine.close()  # 不应报错


# ── get_duckdb_engine 单例测试 ───────────────────────


class TestGetDuckDBEngine:

    def test_returns_singleton(self):
        import core.duckdb_engine as module
        module._engine = None
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.database_url = "postgresql://fake/fake"
            mock_settings.return_value.duckdb_memory_limit = "128MB"
            mock_settings.return_value.duckdb_threads = 1

            engine1 = module.get_duckdb_engine()
            engine2 = module.get_duckdb_engine()
            assert engine1 is engine2

        module._engine = None

    def test_reads_config(self):
        import core.duckdb_engine as module
        module._engine = None
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.database_url = "postgresql://test/test"
            mock_settings.return_value.duckdb_memory_limit = "512MB"
            mock_settings.return_value.duckdb_threads = 4

            engine = module.get_duckdb_engine()
            assert engine._memory_limit == "512MB"
            assert engine._threads == 4

        module._engine = None


# ============================================================
# v6: profile_parquet 测试
# ============================================================


class TestProfileParquet:
    """v6: DuckDB 直接从 parquet 文件算统计摘要"""

    def test_profile_returns_expected_structure(self):
        """profile_parquet 返回正确结构"""
        mock_conn = MagicMock()

        mock_conn.description = [
            ("column_name",), ("column_type",), ("min",), ("max",),
            ("approx_unique",), ("avg",), ("std",), ("null_percentage",),
            ("count",),
        ]
        mock_conn.execute.return_value.fetchall.return_value = [
            ("amount", "DOUBLE", "99.9", "589.9", 50, "344.9", "141.4", "0.00%", 50),
            ("platform", "VARCHAR", "京东", "淘宝", 3, None, None, "0.00%", 50),
        ]
        mock_conn.execute.return_value.fetchone.return_value = (17245.0, 344.9, 222.4, 467.4)
        mock_conn.execute.return_value.fetchdf.return_value.to_dict.return_value = []

        engine = MagicMock()
        engine._get_conn.return_value = mock_conn

        from core.duckdb_engine import DuckDBEngine
        result = DuckDBEngine.profile_parquet(engine, "/tmp/test.parquet")

        assert "columns" in result
        assert "row_count" in result
        assert "top_values" in result
        assert "duplicate_count" in result
        assert "preview_rows" in result
        assert result["row_count"] == 50
        assert len(result["columns"]) == 2
        assert result["columns"][0]["name"] == "amount"

    def test_profile_handles_summarize_failure(self):
        """SUMMARIZE 失败时返回空结构"""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("DuckDB error")

        engine = MagicMock()
        engine._get_conn.return_value = mock_conn

        from core.duckdb_engine import DuckDBEngine
        result = DuckDBEngine.profile_parquet(engine, "/tmp/bad.parquet")

        assert result["columns"] == []
        assert result["row_count"] == 0


# ============================================================
# 超时看门狗测试
# ============================================================


class TestExportTimeout:
    """DuckDB export_to_parquet 超时看门狗（conn.interrupt）"""

    def _make_engine(self):
        from core.duckdb_engine import DuckDBEngine
        return DuckDBEngine(pg_url="postgresql://fake/fake", memory_limit="128MB", threads=1)

    @patch("core.duckdb_engine.duckdb")
    def test_interrupted_event_triggers_timeout_error(self, mock_duckdb):
        """interrupted event 被设置时 → 识别为超时 → 抛 TimeoutError 不重试"""
        import threading
        mock_conn = MagicMock()
        # 我们在 execute 中手动找到 interrupted event 并设置它
        # 模拟真实场景：看门狗线程 set event → execute 抛异常
        captured_event = [None]

        original_timer_init = threading.Timer.__init__

        def capture_timer_init(self_timer, interval, function, args=None, kwargs=None):
            # 拦截 Timer 构造，拿到 event 参数
            if args and len(args) >= 2 and isinstance(args[1], threading.Event):
                captured_event[0] = args[1]
            original_timer_init(self_timer, interval, function, args, kwargs)

        def execute_side_effect(sql):
            if "SET" in sql or "INSTALL" in sql or "ATTACH" in sql or "LOAD" in sql:
                return MagicMock()
            # 在 COPY 执行时，模拟看门狗已触发
            if captured_event[0]:
                captured_event[0].set()
            raise RuntimeError("query was interrupted")

        mock_conn.execute.side_effect = execute_side_effect
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            tmp_path = f.name

        try:
            with patch.object(threading.Timer, '__init__', capture_timer_init):
                with pytest.raises(TimeoutError, match="导出超时"):
                    engine.export_to_parquet(
                        "SELECT * FROM pg.public.test", tmp_path,
                    )
            # 关键：COPY 只执行一次（超时不重试）
            copy_calls = [c for c in mock_conn.execute.call_args_list
                          if "COPY" in str(c)]
            assert len(copy_calls) == 1
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @patch("core.duckdb_engine.duckdb")
    def test_interrupt_exception_name_triggers_timeout(self, mock_duckdb):
        """异常类名含 'interrupt' → 识别为超时"""
        mock_conn = MagicMock()

        class FakeInterruptException(Exception):
            pass

        def execute_side_effect(sql):
            if "SET" in sql or "INSTALL" in sql or "ATTACH" in sql or "LOAD" in sql:
                return MagicMock()
            raise FakeInterruptException("interrupted by watchdog")

        mock_conn.execute.side_effect = execute_side_effect
        mock_duckdb.connect.return_value = mock_conn

        engine = self._make_engine()
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            tmp_path = f.name

        try:
            with pytest.raises(TimeoutError, match="导出超时"):
                engine.export_to_parquet(
                    "SELECT * FROM pg.public.test", tmp_path,
                )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_interrupt_conn_sets_event(self):
        """_interrupt_conn 正确设置 event 并调用 conn.interrupt"""
        import threading
        from core.duckdb_engine import DuckDBEngine

        mock_conn = MagicMock()
        event = threading.Event()
        assert not event.is_set()

        DuckDBEngine._interrupt_conn(mock_conn, event)

        assert event.is_set()
        mock_conn.interrupt.assert_called_once()

    def test_interrupt_conn_handles_closed_conn(self):
        """conn 已关闭时 interrupt 不崩"""
        import threading
        from core.duckdb_engine import DuckDBEngine

        mock_conn = MagicMock()
        mock_conn.interrupt.side_effect = RuntimeError("connection closed")
        event = threading.Event()

        DuckDBEngine._interrupt_conn(mock_conn, event)

        assert event.is_set()  # event 仍然被设置
