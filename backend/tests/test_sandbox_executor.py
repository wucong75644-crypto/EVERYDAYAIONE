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
    async def test_open_outside_workspace_blocked(self, tmp_path):
        """open() 访问 workspace 外路径被 _scoped_open 拦截"""
        ws_executor = SandboxExecutor(
            timeout=5.0, max_result_chars=1000,
            workspace_dir=str(tmp_path),
        )
        result = await ws_executor.execute("open('/etc/passwd')", "open越界调用")
        assert "文件访问被拒绝" in result or "PermissionError" in result

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


class TestMatplotlibIntegration:
    """matplotlib 集成测试"""

    @pytest.mark.asyncio
    async def test_plt_injected_in_globals(self):
        """plt 变量注入到沙盒 globals"""
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute("print(str(plt))", "plt类型")
        assert "matplotlib" in result

    @pytest.mark.asyncio
    async def test_matplotlib_injected_in_globals(self):
        """matplotlib 模块注入到沙盒 globals"""
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "print(matplotlib.get_backend())", "matplotlib后端"
        )
        assert "agg" in result.lower()

    @pytest.mark.asyncio
    async def test_plt_close_called_after_execution(self):
        """每次执行后 plt.close('all') 被调用，防止内存泄露"""
        executor = SandboxExecutor(timeout=5.0)
        # 创建一个 figure 但不关闭
        await executor.execute(
            "fig, ax = plt.subplots()\nax.plot([1,2,3])\nprint('plotted')",
            "画图不关闭",
        )
        # 执行后 plt.close('all') 应已被调用，当前无残留 figure
        import matplotlib.pyplot as check_plt
        assert len(check_plt.get_fignums()) == 0

    @pytest.mark.asyncio
    async def test_plt_close_called_on_error(self):
        """执行出错时也清理 matplotlib figure"""
        executor = SandboxExecutor(timeout=5.0)
        await executor.execute(
            "fig, ax = plt.subplots()\nraise ValueError('boom')",
            "画图后报错",
        )
        import matplotlib.pyplot as check_plt
        assert len(check_plt.get_fignums()) == 0


class TestWorkspaceDirInjection:
    """WORKSPACE_DIR 注入测试"""

    @pytest.mark.asyncio
    async def test_workspace_dir_available(self):
        """workspace_dir 注入到沙盒 globals，输出中真实路径被替换为虚拟路径"""
        executor = SandboxExecutor(
            timeout=5.0, workspace_dir="/tmp/test_ws",
        )
        result = await executor.execute("print(WORKSPACE_DIR)", "读workspace")
        # 真实路径被隐藏，替换为"工作区"
        assert "工作区" in result
        assert "/tmp/test_ws" not in result

    @pytest.mark.asyncio
    async def test_workspace_dir_none_not_injected(self):
        """workspace_dir 为 None 时不注入，访问 WORKSPACE_DIR 报错"""
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute("print(WORKSPACE_DIR)", "访问未设workspace")
        assert "执行错误" in result


class TestExpandedWhitelist:
    """扩展白名单模块测试"""

    @pytest.mark.asyncio
    async def test_import_matplotlib(self):
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "import matplotlib\nprint(matplotlib.__name__)", "导入matplotlib"
        )
        assert "matplotlib" in result

    @pytest.mark.asyncio
    async def test_import_seaborn(self):
        executor = SandboxExecutor(timeout=10.0)
        result = await executor.execute(
            "import seaborn\nprint(seaborn.__name__)", "导入seaborn"
        )
        assert "seaborn" in result

    @pytest.mark.asyncio
    async def test_import_pil(self):
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "from PIL import Image\nprint(Image.__name__)", "导入PIL"
        )
        assert "Image" in result

    @pytest.mark.asyncio
    async def test_import_reportlab(self):
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "import reportlab\nprint(reportlab.__name__)", "导入reportlab"
        )
        assert "reportlab" in result

    @pytest.mark.asyncio
    async def test_import_docx(self):
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "from docx import Document\nprint(type(Document).__name__)",
            "导入docx",
        )
        assert "执行错误" not in result

    @pytest.mark.asyncio
    async def test_import_pptx(self):
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "from pptx import Presentation\nprint(type(Presentation).__name__)",
            "导入pptx",
        )
        assert "执行错误" not in result

    @pytest.mark.asyncio
    async def test_import_openpyxl(self):
        executor = SandboxExecutor(timeout=5.0)
        result = await executor.execute(
            "import openpyxl\nprint(openpyxl.__name__)", "导入openpyxl"
        )
        assert "openpyxl" in result


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


class TestSnapshotAndAutoUpload:
    """文件快照 + 新文件检测测试"""

    def test_snapshot_captures_existing_files(self, tmp_path):
        """快照捕获输出目录已有文件（key = dir/filename, value = (mtime, size)）"""
        (tmp_path / "old.xlsx").write_bytes(b"old")
        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        snapshot = executor._snapshot_output_files()
        key = f"{tmp_path}/old.xlsx"
        assert key in snapshot
        assert isinstance(snapshot[key], tuple)
        assert len(snapshot[key]) == 2  # (mtime, size)

    def test_snapshot_empty_dir(self, tmp_path):
        """空目录快照为空 dict"""
        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        snapshot = executor._snapshot_output_files()
        assert snapshot == {}

    @pytest.mark.asyncio
    async def test_auto_upload_only_new_files(self, tmp_path):
        """auto_upload 只处理新生成的文件，跳过未修改的"""
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
        """覆盖写入同名文件 → mtime/size 变化 → 检测为新文件"""
        (tmp_path / "report.xlsx").write_bytes(b"v1")

        results = []

        async def mock_upload(filename, size):
            results.append(filename)
            return f"✅ {filename}"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = executor._snapshot_output_files()

        import time; time.sleep(0.05)  # 确保 mtime 不同
        (tmp_path / "report.xlsx").write_bytes(b"v2 with more data")

        await executor._auto_upload_new_files()
        assert "report.xlsx" in results


class TestDedupOverwrittenFiles:
    """Google Drive 风格同名文件保护"""

    def test_backup_creates_dedup_bak_files(self, tmp_path):
        """备份为已有的可上传文件创建 .dedup_bak 副本"""
        (tmp_path / "report.xlsx").write_bytes(b"old data")
        (tmp_path / "data.csv").write_bytes(b"old csv")
        (tmp_path / "notes.parquet").write_bytes(b"skip me")  # 不在白名单

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        assert len(backups) == 2
        assert (tmp_path / "report.xlsx.dedup_bak").exists()
        assert (tmp_path / "data.csv.dedup_bak").exists()
        assert not (tmp_path / "notes.parquet.dedup_bak").exists()

    def test_dedup_renames_new_file_keeps_old(self, tmp_path):
        """覆盖写入 → 新文件重命名为 name (1).ext，旧文件恢复原名"""
        old_content = b"March sales data"
        new_content = b"April sales data - different"

        (tmp_path / "report.xlsx").write_bytes(old_content)

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        # 模拟沙箱代码覆盖写入
        import time; time.sleep(0.05)
        (tmp_path / "report.xlsx").write_bytes(new_content)

        executor._dedup_overwritten_files(backups)

        # 旧文件恢复原名
        assert (tmp_path / "report.xlsx").read_bytes() == old_content
        # 新文件被重命名
        assert (tmp_path / "report (1).xlsx").read_bytes() == new_content
        # 备份已清理
        assert not (tmp_path / "report.xlsx.dedup_bak").exists()

    def test_dedup_increments_suffix(self, tmp_path):
        """已有 (1) 时自动递增到 (2)"""
        (tmp_path / "report.xlsx").write_bytes(b"v1")
        (tmp_path / "report (1).xlsx").write_bytes(b"v2 from last time")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        import time; time.sleep(0.05)
        (tmp_path / "report.xlsx").write_bytes(b"v3 new")

        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == b"v1"
        assert (tmp_path / "report (1).xlsx").read_bytes() == b"v2 from last time"
        assert (tmp_path / "report (2).xlsx").read_bytes() == b"v3 new"

    def test_dedup_no_overwrite_cleans_backup(self, tmp_path):
        """文件未被覆盖 → 删除备份，不做任何重命名"""
        (tmp_path / "report.xlsx").write_bytes(b"untouched")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        # 沙箱代码没有写同名文件
        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == b"untouched"
        assert not (tmp_path / "report.xlsx.dedup_bak").exists()
        assert not (tmp_path / "report (1).xlsx").exists()

    def test_dedup_deleted_file_restored(self, tmp_path):
        """原文件被沙箱删除 → 从备份恢复"""
        (tmp_path / "report.xlsx").write_bytes(b"important data")

        executor = SandboxExecutor(timeout=5.0, output_dir=str(tmp_path))
        backups = executor._backup_existing_files()

        # 沙箱代码删除了文件
        (tmp_path / "report.xlsx").unlink()

        executor._dedup_overwritten_files(backups)

        assert (tmp_path / "report.xlsx").read_bytes() == b"important data"

    @pytest.mark.asyncio
    async def test_dedup_bak_not_uploaded(self, tmp_path):
        """.dedup_bak 文件不会被 auto_upload 扫描到"""
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

    @pytest.mark.asyncio
    async def test_dedup_renamed_file_gets_uploaded(self, tmp_path):
        """覆盖后重命名的新文件 report (1).xlsx 应被 auto_upload 检测到"""
        old_content = b"old"
        new_content = b"new data here"

        (tmp_path / "report.xlsx").write_bytes(old_content)

        results = []

        async def mock_upload(filename, size):
            results.append(filename)
            return f"✅ {filename}"

        executor = SandboxExecutor(
            timeout=5.0, output_dir=str(tmp_path), upload_fn=mock_upload,
        )
        executor._snapshot_before = executor._snapshot_output_files()
        backups = executor._backup_existing_files()

        import time; time.sleep(0.05)
        (tmp_path / "report.xlsx").write_bytes(new_content)

        # dedup 先跑 → 新文件变成 report (1).xlsx
        executor._dedup_overwritten_files(backups)

        # auto_upload 应该检测到 report (1).xlsx（新文件，不在 snapshot 里）
        await executor._auto_upload_new_files()
        assert "report (1).xlsx" in results
        # 旧文件 report.xlsx 恢复后 mtime 不变，不应被上传
        assert "report.xlsx" not in results

    def test_next_available_name(self, tmp_path):
        """_next_available_name 返回最小可用后缀"""
        p = tmp_path / "data.csv"
        assert SandboxExecutor._next_available_name(p).name == "data (1).csv"

        (tmp_path / "data (1).csv").write_bytes(b"x")
        assert SandboxExecutor._next_available_name(p).name == "data (2).csv"

        (tmp_path / "data (2).csv").write_bytes(b"x")
        assert SandboxExecutor._next_available_name(p).name == "data (3).csv"

    def test_backup_only_scans_output_dir(self, tmp_path):
        """backup 只扫 OUTPUT_DIR，STAGING_DIR 中间文件不参与"""
        out_dir = tmp_path / "output"
        stg_dir = tmp_path / "staging"
        out_dir.mkdir()
        stg_dir.mkdir()
        (out_dir / "a.xlsx").write_bytes(b"output file")
        (stg_dir / "b.csv").write_bytes(b"staging file")

        executor = SandboxExecutor(
            timeout=5.0,
            output_dir=str(out_dir),
            staging_dir=str(stg_dir),
        )
        backups = executor._backup_existing_files()

        assert len(backups) == 1
        assert (out_dir / "a.xlsx.dedup_bak").exists()
        assert not (stg_dir / "b.csv.dedup_bak").exists()

    def test_upload_scan_dirs_only_output(self, tmp_path):
        """_upload_scan_dirs 只包含 output_dir，不含 staging_dir"""
        out_dir = tmp_path / "output"
        stg_dir = tmp_path / "staging"
        executor = SandboxExecutor(
            timeout=5.0,
            output_dir=str(out_dir),
            staging_dir=str(stg_dir),
        )
        dirs = executor._upload_scan_dirs
        assert dirs == [str(out_dir)]
        assert str(stg_dir) not in dirs

    @pytest.mark.asyncio
    async def test_staging_dir_excluded_from_auto_upload(self, tmp_path):
        """STAGING_DIR 中的文件不被 auto_upload 推送"""
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
            timeout=5.0,
            output_dir=str(out_dir),
            staging_dir=str(stg_dir),
            upload_fn=mock_upload,
        )
        executor._snapshot_before = {}

        await executor._auto_upload_new_files()
        assert "report.xlsx" in uploaded
        assert "intermediate.xlsx" not in uploaded

    @pytest.mark.asyncio
    async def test_dedup_runs_on_timeout(self, tmp_path):
        """execute() 超时时仍执行 dedup，备份被正确清理"""
        (tmp_path / "report.xlsx").write_bytes(b"precious data")

        executor = SandboxExecutor(
            timeout=0.01,  # 极短超时
            output_dir=str(tmp_path),
        )
        # 执行一段会超时的代码
        result = await executor.execute("import time; time.sleep(10)")
        assert "超时" in result

        # 备份已被清理（未覆盖 → unlink）
        assert not (tmp_path / "report.xlsx.dedup_bak").exists()
        # 原文件完好
        assert (tmp_path / "report.xlsx").read_bytes() == b"precious data"

    @pytest.mark.asyncio
    async def test_dedup_runs_on_exception(self, tmp_path):
        """execute() 异常时仍执行 dedup，备份被正确清理"""
        (tmp_path / "data.csv").write_bytes(b"important csv")

        executor = SandboxExecutor(
            timeout=5.0,
            output_dir=str(tmp_path),
        )
        result = await executor.execute("raise ValueError('boom')")
        assert "执行错误" in result

        # 备份已被清理
        assert not (tmp_path / "data.csv.dedup_bak").exists()
        # 原文件完好
        assert (tmp_path / "data.csv").read_bytes() == b"important csv"
