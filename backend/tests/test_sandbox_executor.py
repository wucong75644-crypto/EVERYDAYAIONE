"""沙盒执行器测试

子进程隔离模式：SandboxExecutor.execute() 通过 spawn 子进程执行代码。
主进程负责 AST 验证、文件快照/上传检测。
"""

import asyncio
import shutil
import time
from pathlib import Path

import pytest

from services.sandbox.executor import SandboxExecutor


@pytest.fixture
def executor(tmp_path):
    """标准执行器（带 workspace，子进程需要 chdir 目标）"""
    return SandboxExecutor(
        timeout=30.0,  # 子进程 spawn + import pandas 需要时间
        max_result_chars=1000,
        workspace_dir=str(tmp_path),
    )


# ============================================================
# 基本执行功能（子进程隔离模式）
# ============================================================

class TestBasicExecution:
    """基本执行功能"""

    @pytest.mark.asyncio
    async def test_simple_arithmetic(self, executor):
        result = await executor.execute("1 + 1", "简单加法")
        assert "2" in result.summary

    @pytest.mark.asyncio
    async def test_print_output(self, executor):
        result = await executor.execute("print('hello')", "打印测试")
        assert "hello" in result.summary

    @pytest.mark.asyncio
    async def test_multi_line_code(self, executor):
        code = "x = 10\ny = 20\nprint(x + y)"
        result = await executor.execute(code, "多行代码")
        assert "30" in result.summary

    @pytest.mark.asyncio
    async def test_last_expression_returned(self, executor):
        code = "x = 42\nx"
        result = await executor.execute(code, "最后表达式")
        assert "42" in result.summary

    @pytest.mark.asyncio
    async def test_no_output(self, executor):
        result = await executor.execute("x = 1", "无输出")
        assert "无输出" in result.summary or "成功" in result.summary

    @pytest.mark.asyncio
    async def test_print_and_expression(self, executor):
        code = "print('line1')\n42"
        result = await executor.execute(code, "混合输出")
        assert "line1" in result.summary
        assert "42" in result.summary


# ============================================================
# 白名单模块测试
# ============================================================

class TestWhitelistModules:
    """白名单模块测试"""

    @pytest.mark.asyncio
    async def test_math_module(self, executor):
        result = await executor.execute("math.sqrt(144)", "math模块")
        assert "12" in result.summary

    @pytest.mark.asyncio
    async def test_json_module(self, executor):
        code = "json.dumps({'a': 1})"
        result = await executor.execute(code, "json模块")
        assert '"a"' in result.summary

    @pytest.mark.asyncio
    async def test_datetime_available(self, executor):
        code = "str(datetime.now().year)"
        result = await executor.execute(code, "datetime")
        assert "202" in result.summary

    @pytest.mark.asyncio
    async def test_decimal_available(self, executor):
        code = "str(Decimal('99.99') + Decimal('0.01'))"
        result = await executor.execute(code, "Decimal精度")
        assert "100.00" in result.summary

    @pytest.mark.asyncio
    async def test_counter_available(self, executor):
        code = "str(Counter([1, 1, 2, 3]))"
        result = await executor.execute(code, "Counter")
        assert "1" in result.summary

    @pytest.mark.asyncio
    async def test_pandas_available(self, executor):
        try:
            import pandas
        except ImportError:
            pytest.skip("pandas not installed")
        code = "df = pd.DataFrame({'a': [1, 2, 3]})\nstr(df['a'].sum())"
        result = await executor.execute(code, "pandas DataFrame")
        assert "6" in result.summary


# ============================================================
# 安全内置函数测试
# ============================================================

class TestSafeBuiltins:
    """安全内置函数测试"""

    @pytest.mark.asyncio
    async def test_len(self, executor):
        result = await executor.execute("len([1, 2, 3])", "len")
        assert "3" in result.summary

    @pytest.mark.asyncio
    async def test_sum(self, executor):
        result = await executor.execute("sum([10, 20, 30])", "sum")
        assert "60" in result.summary

    @pytest.mark.asyncio
    async def test_sorted(self, executor):
        result = await executor.execute("sorted([3, 1, 2])", "sorted")
        assert "[1, 2, 3]" in result.summary

    @pytest.mark.asyncio
    async def test_range(self, executor):
        result = await executor.execute("list(range(5))", "range")
        assert "[0, 1, 2, 3, 4]" in result.summary

    @pytest.mark.asyncio
    async def test_isinstance(self, executor):
        result = await executor.execute("isinstance(42, int)", "isinstance")
        assert "True" in result.summary


# ============================================================
# 安全拦截测试
# ============================================================

class TestSecurityBlocking:
    """安全拦截测试"""

    @pytest.mark.asyncio
    async def test_import_os_scoped(self, executor):
        """import os 放行，os.listdir('.') 返回工作区文件列表"""
        result = await executor.execute("import os\nprint(type(os.listdir('.')))", "os测试")
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_eval_blocked(self, executor):
        result = await executor.execute("eval('1+1')", "eval调用")
        assert "验证失败" in result.summary

    @pytest.mark.asyncio
    async def test_open_outside_workspace_blocked(self, tmp_path):
        """open() 访问 workspace 外路径被 _scoped_open 拦截"""
        ws_executor = SandboxExecutor(
            timeout=30.0, max_result_chars=1000,
            workspace_dir=str(tmp_path),
        )
        result = await ws_executor.execute("open('/etc/passwd')", "open越界调用")
        assert "文件访问被拒绝" in result.summary or "PermissionError" in result.summary

    @pytest.mark.asyncio
    async def test_dunder_escape_blocked(self, executor):
        result = await executor.execute(
            "x = [].__class__.__bases__", "元编程逃逸"
        )
        assert "验证失败" in result.summary

    @pytest.mark.asyncio
    async def test_empty_code(self, executor):
        result = await executor.execute("", "空代码")
        assert "验证失败" in result.summary


# ============================================================
# 允许的模块导入测试
# ============================================================

class TestAllowedImports:
    """允许的模块导入测试"""

    @pytest.mark.asyncio
    async def test_import_io_allowed(self, executor):
        result = await executor.execute(
            "import io\nbuf = io.BytesIO()\nbuf.write(b'hello')\nbuf.tell()",
            "io模块导入",
        )
        assert "5" in result.summary

    @pytest.mark.asyncio
    async def test_import_json_allowed(self, executor):
        result = await executor.execute(
            "import json\njson.dumps({'a': 1})",
            "json模块导入",
        )
        assert '"a"' in result.summary


# ============================================================
# 错误处理测试
# ============================================================

class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_syntax_error(self, executor):
        result = await executor.execute("def foo(", "语法错误")
        assert "验证失败" in result.summary

    @pytest.mark.asyncio
    async def test_runtime_error(self, executor):
        result = await executor.execute("1 / 0", "除零错误")
        assert "执行错误" in result.summary

    @pytest.mark.asyncio
    async def test_key_error(self, executor):
        result = await executor.execute("d = {}\nd['missing']", "KeyError")
        assert "执行错误" in result.summary

    @pytest.mark.asyncio
    async def test_timeout(self):
        executor = SandboxExecutor(timeout=2.0, workspace_dir="/tmp")
        code = "x = 0\nwhile True:\n    x += 1"
        result = await executor.execute(code, "死循环")
        assert "超时" in result.summary


# ============================================================
# 结果截断测试
# ============================================================

class TestResultTruncation:
    """结果截断测试"""

    @pytest.mark.asyncio
    async def test_long_output_truncated(self):
        executor = SandboxExecutor(
            timeout=30.0, max_result_chars=100, workspace_dir="/tmp",
        )
        code = "print('x' * 500)"
        result = await executor.execute(code, "长输出")
        assert "已截断" in result.summary

    @pytest.mark.asyncio
    async def test_short_output_not_truncated(self, executor):
        result = await executor.execute("print('short')", "短输出")
        assert "已截断" not in result.summary


# ============================================================
# 子进程隔离特有测试
# ============================================================

class TestSubprocessIsolation:
    """子进程隔离模式特有的测试"""

    @pytest.mark.asyncio
    async def test_workspace_dir_available(self, tmp_path):
        """workspace_dir 注入到子进程 globals"""
        executor = SandboxExecutor(
            timeout=30.0, workspace_dir=str(tmp_path),
        )
        result = await executor.execute("print(WORKSPACE_DIR)", "读workspace")
        assert result.status == "success"
        # WORKSPACE_DIR 应该有值（非空字符串）
        assert result.summary.strip() != ""

    @pytest.mark.asyncio
    async def test_chdir_to_workspace(self, tmp_path):
        """子进程 cwd 被 chdir 到 workspace"""
        (tmp_path / "test_file.txt").write_text("hello from workspace")
        executor = SandboxExecutor(
            timeout=30.0, workspace_dir=str(tmp_path),
        )
        # 直接用相对路径读文件 — chdir 后能读到
        result = await executor.execute(
            "print(open('test_file.txt').read())", "cwd读文件"
        )
        assert "hello from workspace" in result.summary

    @pytest.mark.asyncio
    async def test_isolated_globals_across_executions(self, tmp_path):
        """每次执行使用独立子进程，变量不跨执行共享"""
        executor = SandboxExecutor(
            timeout=30.0, workspace_dir=str(tmp_path),
        )
        await executor.execute("shared_var = 42", "设置变量")
        result = await executor.execute("shared_var", "读取变量")
        assert "执行错误" in result.summary or "NameError" in result.summary

    @pytest.mark.asyncio
    async def test_async_await_not_supported(self, tmp_path):
        """子进程模式不支持 async/await"""
        executor = SandboxExecutor(
            timeout=30.0, workspace_dir=str(tmp_path),
        )
        result = await executor.execute("await some_func()", "async代码")
        # AST 验证通过但子进程内检测到 await 返回错误
        assert "不支持" in result.summary or "验证失败" in result.summary


# ============================================================
# auto-upload 扩展名测试
# ============================================================

class TestAutoUploadExtensions:
    """auto-upload 扩展名测试"""

    def test_docx_in_auto_upload(self):
        assert ".docx" in SandboxExecutor._AUTO_UPLOAD_EXTENSIONS

    def test_pptx_in_auto_upload(self):
        assert ".pptx" in SandboxExecutor._AUTO_UPLOAD_EXTENSIONS

    def test_existing_extensions_preserved(self):
        exts = SandboxExecutor._AUTO_UPLOAD_EXTENSIONS
        for ext in [".xlsx", ".csv", ".png", ".pdf", ".json", ".txt"]:
            assert ext in exts


# ============================================================
# 文件快照 + 新文件检测测试
# ============================================================

class TestSnapshotAndAutoUpload:
    """文件快照 + 新文件检测测试"""

    def test_snapshot_captures_existing_files(self, tmp_path):
        (tmp_path / "old.xlsx").write_bytes(b"old")
        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        snapshot = executor._snapshot_output_files()
        key = f"{tmp_path}/old.xlsx"
        assert key in snapshot
        assert isinstance(snapshot[key], tuple)
        assert len(snapshot[key]) == 2

    def test_snapshot_empty_dir(self, tmp_path):
        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        snapshot = executor._snapshot_output_files()
        assert snapshot == {}

    @pytest.mark.asyncio
    async def test_auto_upload_only_new_files(self, tmp_path):
        (tmp_path / "old.xlsx").write_bytes(b"old data")

        results = []

        async def mock_upload(filename, size):
            results.append(filename)
            return f"✅ {filename}"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = executor._snapshot_output_files()

        (tmp_path / "new.xlsx").write_bytes(b"new data")

        await executor._auto_upload_new_files()
        assert "new.xlsx" in results
        assert "old.xlsx" not in results

    @pytest.mark.asyncio
    async def test_auto_upload_detects_overwritten_file(self, tmp_path):
        (tmp_path / "report.xlsx").write_bytes(b"v1")

        results = []

        async def mock_upload(filename, size):
            results.append(filename)
            return f"✅ {filename}"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = executor._snapshot_output_files()

        time.sleep(0.05)
        (tmp_path / "report.xlsx").write_bytes(b"v2 with more data")

        await executor._auto_upload_new_files()
        assert "report.xlsx" in results


# ============================================================
# Google Drive 风格同名文件保护
# ============================================================

class TestDedupOverwrittenFiles:
    """Google Drive 风格同名文件保护"""

    def test_backup_creates_dedup_bak_files(self, tmp_path):
        (tmp_path / "report.xlsx").write_bytes(b"old data")
        (tmp_path / "data.csv").write_bytes(b"old csv")
        (tmp_path / "notes.parquet").write_bytes(b"skip me")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        assert len(backups) == 2
        assert (tmp_path / "report.xlsx.dedup_bak").exists()
        assert (tmp_path / "data.csv.dedup_bak").exists()
        assert not (tmp_path / "notes.parquet.dedup_bak").exists()

    def test_dedup_renames_new_file_keeps_old(self, tmp_path):
        old_content = b"March sales data"
        new_content = b"April sales data - different"

        (tmp_path / "report.xlsx").write_bytes(old_content)

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        time.sleep(0.05)
        (tmp_path / "report.xlsx").write_bytes(new_content)

        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == old_content
        assert (tmp_path / "report (1).xlsx").read_bytes() == new_content
        assert not (tmp_path / "report.xlsx.dedup_bak").exists()

    def test_dedup_increments_suffix(self, tmp_path):
        (tmp_path / "report.xlsx").write_bytes(b"v1")
        (tmp_path / "report (1).xlsx").write_bytes(b"v2 from last time")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        time.sleep(0.05)
        (tmp_path / "report.xlsx").write_bytes(b"v3 new")

        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == b"v1"
        assert (tmp_path / "report (1).xlsx").read_bytes() == b"v2 from last time"
        assert (tmp_path / "report (2).xlsx").read_bytes() == b"v3 new"

    def test_dedup_no_overwrite_cleans_backup(self, tmp_path):
        (tmp_path / "report.xlsx").write_bytes(b"untouched")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == b"untouched"
        assert not (tmp_path / "report.xlsx.dedup_bak").exists()
        assert not (tmp_path / "report (1).xlsx").exists()

    def test_dedup_deleted_file_restored(self, tmp_path):
        (tmp_path / "report.xlsx").write_bytes(b"important data")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        (tmp_path / "report.xlsx").unlink()

        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == b"important data"

    @pytest.mark.asyncio
    async def test_dedup_bak_not_uploaded(self, tmp_path):
        (tmp_path / "report.xlsx.dedup_bak").write_bytes(b"backup")

        results = []

        async def mock_upload(filename, size):
            results.append(filename)
            return f"✅ {filename}"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()
        assert "report.xlsx.dedup_bak" not in results

    def test_next_available_name(self, tmp_path):
        p = tmp_path / "data.csv"
        assert SandboxExecutor._next_available_name(p).name == "data (1).csv"

        (tmp_path / "data (1).csv").write_bytes(b"x")
        assert SandboxExecutor._next_available_name(p).name == "data (2).csv"

    def test_backup_only_scans_output_dir(self, tmp_path):
        out_dir = tmp_path / "output"
        stg_dir = tmp_path / "staging"
        out_dir.mkdir()
        stg_dir.mkdir()
        (out_dir / "a.xlsx").write_bytes(b"output file")
        (stg_dir / "b.csv").write_bytes(b"staging file")

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(out_dir), staging_dir=str(stg_dir),
        )
        backups = executor._backup_existing_files()

        assert len(backups) == 1
        assert (out_dir / "a.xlsx.dedup_bak").exists()
        assert not (stg_dir / "b.csv.dedup_bak").exists()

    def test_upload_scan_dirs_only_output(self, tmp_path):
        out_dir = tmp_path / "output"
        stg_dir = tmp_path / "staging"
        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(out_dir), staging_dir=str(stg_dir),
        )
        dirs = executor._upload_scan_dirs
        assert dirs == [str(out_dir)]
        assert str(stg_dir) not in dirs

    @pytest.mark.asyncio
    async def test_staging_dir_excluded_from_auto_upload(self, tmp_path):
        out_dir = tmp_path / "output"
        stg_dir = tmp_path / "staging"
        out_dir.mkdir()
        stg_dir.mkdir()
        (stg_dir / "intermediate.xlsx").write_bytes(b"staging data")
        (out_dir / "report.xlsx").write_bytes(b"user output")

        uploaded = []

        async def mock_upload(filename, size):
            uploaded.append(filename)
            return f"✅ {filename}"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(out_dir), staging_dir=str(stg_dir),
            upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()
        assert "report.xlsx" in uploaded
        assert "intermediate.xlsx" not in uploaded


# ============================================================
# Workspace 原始文件备份（修改可回退）
# ============================================================

class TestWorkspaceBackup:
    """Workspace 文件备份与回退"""

    def test_backup_creates_bak_files_in_staging(self, tmp_path):
        """workspace 数据文件备份到 staging"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        (ws / "sales.xlsx").write_bytes(b"sales data")
        (ws / "data.csv").write_bytes(b"csv data")
        (ws / "notes.parquet").write_bytes(b"skip me")  # 非上传扩展名

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )
        backups = executor._backup_workspace_files()

        assert len(backups) == 2
        for backup_path in backups.values():
            p = Path(backup_path)
            assert p.exists()
            assert p.parent == stg
            assert p.name.startswith("_bak_")

    def test_backup_skips_non_upload_extensions(self, tmp_path):
        """非 _AUTO_UPLOAD_EXTENSIONS 的文件不备份"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        (ws / "data.parquet").write_bytes(b"parquet")
        (ws / "script.py").write_bytes(b"python")

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )
        backups = executor._backup_workspace_files()

        assert len(backups) == 0
        assert not stg.exists() or not list(stg.iterdir())

    def test_cleanup_removes_unmodified_backups(self, tmp_path):
        """未被修改的文件 → 备份被删除，返回空 dict"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        (ws / "report.xlsx").write_bytes(b"untouched")

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )
        backups = executor._backup_workspace_files()

        # 没有修改 workspace 文件
        modified = executor._cleanup_workspace_backups(backups)

        # 备份应被删除，返回空 dict
        assert modified == {}
        for backup_path in backups.values():
            assert not Path(backup_path).exists()

    def test_cleanup_keeps_modified_backups(self, tmp_path):
        """已修改的文件 → 备份保留，返回 {文件名: 备份路径}"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        (ws / "report.xlsx").write_bytes(b"original data")

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )
        backups = executor._backup_workspace_files()

        # 模拟沙盒修改了文件
        time.sleep(0.05)
        (ws / "report.xlsx").write_bytes(b"modified data - different")

        modified = executor._cleanup_workspace_backups(backups)

        # 返回 {文件名: 备份路径}
        assert "report.xlsx" in modified
        # 备份应保留，且内容是原始数据
        p = Path(modified["report.xlsx"])
        assert p.exists()
        assert p.read_bytes() == b"original data"

    def test_cleanup_keeps_backup_when_file_deleted(self, tmp_path):
        """原文件被删除 → 备份保留，返回 {文件名: 备份路径}"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        (ws / "report.xlsx").write_bytes(b"important data")

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )
        backups = executor._backup_workspace_files()

        # 模拟沙盒删除了文件
        (ws / "report.xlsx").unlink()

        modified = executor._cleanup_workspace_backups(backups)

        # 返回包含被删除的文件
        assert "report.xlsx" in modified
        assert Path(modified["report.xlsx"]).exists()
        assert Path(modified["report.xlsx"]).read_bytes() == b"important data"

    def test_multiple_executions_create_separate_backups(self, tmp_path):
        """多次执行产生独立时间戳备份"""
        ws = tmp_path / "workspace"
        stg = tmp_path / "staging"
        ws.mkdir()
        (ws / "report.xlsx").write_bytes(b"v1")

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )

        backups1 = executor._backup_workspace_files()
        time.sleep(1.1)  # 确保时间戳不同
        backups2 = executor._backup_workspace_files()

        # 两次备份路径不同（时间戳不同）
        paths1 = set(backups1.values())
        paths2 = set(backups2.values())
        assert paths1.isdisjoint(paths2)

    def test_no_workspace_dir_returns_empty(self, tmp_path):
        """无 workspace_dir 时返回空"""
        executor = SandboxExecutor(
            timeout=5.0, staging_dir=str(tmp_path / "staging"),
        )
        assert executor._backup_workspace_files() == {}

    def test_no_staging_dir_returns_empty(self, tmp_path):
        """无 staging_dir 时返回空"""
        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(tmp_path / "workspace"),
        )
        assert executor._backup_workspace_files() == {}

    def test_backup_only_scans_top_level(self, tmp_path):
        """只扫描 workspace 顶层，不递归子目录"""
        ws = tmp_path / "workspace"
        sub = ws / "subdir"
        stg = tmp_path / "staging"
        ws.mkdir()
        sub.mkdir()
        (ws / "top.xlsx").write_bytes(b"top level")
        (sub / "nested.xlsx").write_bytes(b"nested")

        executor = SandboxExecutor(
            timeout=5.0, workspace_dir=str(ws), staging_dir=str(stg),
        )
        backups = executor._backup_workspace_files()

        assert len(backups) == 1
        assert str(ws / "top.xlsx") in backups
