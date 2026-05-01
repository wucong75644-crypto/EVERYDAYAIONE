"""
FileExecutor 单元测试

测试文件操作执行器的安全校验、读写、搜索、元信息等核心功能。
所有测试在临时目录中运行，不依赖外部服务。
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from services.file_executor import FileExecutor


@pytest.fixture
def workspace(tmp_path):
    """创建临时 workspace 目录"""
    return str(tmp_path)


@pytest.fixture
def executor(workspace):
    """创建无用户隔离的 FileExecutor"""
    return FileExecutor(workspace_root=workspace)


@pytest.fixture
def user_executor(workspace):
    """创建有用户隔离的 FileExecutor（企业用户）"""
    return FileExecutor(
        workspace_root=workspace,
        user_id="user-abc-123",
        org_id="org-xyz-456",
    )


@pytest.fixture
def personal_executor(workspace):
    """创建散客用户的 FileExecutor"""
    return FileExecutor(
        workspace_root=workspace,
        user_id="user-personal-789",
    )


# ============================================================
# 初始化 + 用户隔离
# ============================================================


class TestInit:

    def test_no_user_uses_base(self, workspace):
        ex = FileExecutor(workspace_root=workspace)
        assert ex.workspace_root == str(Path(workspace).resolve())

    def test_org_user_creates_isolated_dir(self, workspace):
        ex = FileExecutor(workspace_root=workspace, user_id="u1", org_id="org1")
        assert "org/org1/u1" in ex.workspace_root
        assert Path(ex.workspace_root).is_dir()

    def test_personal_user_creates_hashed_dir(self, workspace):
        ex = FileExecutor(workspace_root=workspace, user_id="u1")
        assert "personal/" in ex.workspace_root
        assert Path(ex.workspace_root).is_dir()

    def test_two_personal_users_different_dirs(self, workspace):
        ex1 = FileExecutor(workspace_root=workspace, user_id="alice")
        ex2 = FileExecutor(workspace_root=workspace, user_id="bob")
        assert ex1.workspace_root != ex2.workspace_root


# ============================================================
# resolve_safe_path — 路径安全校验
# ============================================================


class TestResolveSafePath:

    def test_normal_path(self, executor):
        p = executor.resolve_safe_path("test.txt")
        assert p.name == "test.txt"

    def test_nested_path(self, executor):
        p = executor.resolve_safe_path("a/b/c.txt")
        assert p.name == "c.txt"

    def test_dot_resolves_to_root(self, executor):
        p = executor.resolve_safe_path(".")
        assert str(p) == executor.workspace_root

    def test_traversal_blocked(self, executor):
        with pytest.raises(PermissionError, match="路径越界"):
            executor.resolve_safe_path("../../etc/passwd")

    def test_absolute_path_outside_workspace_rejected(self, executor):
        """绝对路径不在 workspace 内时拒绝"""
        with pytest.raises(PermissionError, match="路径越界"):
            executor.resolve_safe_path("/subdir/file.txt")

    def test_blocked_name_env(self, executor):
        with pytest.raises(PermissionError, match="安全限制"):
            executor.resolve_safe_path(".env")

    def test_blocked_name_git(self, executor):
        with pytest.raises(PermissionError, match="安全限制"):
            executor.resolve_safe_path(".git")

    def test_blocked_name_in_parent(self, executor):
        with pytest.raises(PermissionError, match="安全限制"):
            executor.resolve_safe_path(".git/config")

    def test_blocked_extension_pem(self, executor):
        with pytest.raises(PermissionError, match="安全限制"):
            executor.resolve_safe_path("server.pem")

    def test_blocked_extension_key(self, executor):
        with pytest.raises(PermissionError, match="安全限制"):
            executor.resolve_safe_path("private.key")

    def test_symlink_blocked(self, executor, workspace):
        """符号链接被拦截"""
        real_file = Path(workspace) / "real.txt"
        real_file.write_text("hello")
        link = Path(workspace) / "link.txt"
        link.symlink_to(real_file)
        with pytest.raises(PermissionError, match="符号链接"):
            executor.resolve_safe_path("link.txt")


# ============================================================
# generate_unique_filename
# ============================================================


class TestGenerateUniqueFilename:

    def test_preserves_extension(self, executor):
        name = executor.generate_unique_filename("report.csv")
        assert name.endswith(".csv")
        assert name.startswith("report_")

    def test_preserves_stem(self, executor):
        name = executor.generate_unique_filename("data.xlsx")
        assert name.startswith("data_")

    def test_unique_each_call(self, executor):
        n1 = executor.generate_unique_filename("f.txt")
        n2 = executor.generate_unique_filename("f.txt")
        assert n1 != n2

    def test_no_extension(self, executor):
        name = executor.generate_unique_filename("README")
        assert name.startswith("README_")
        assert "." not in name.split("_")[-1]  # 只有随机ID，无扩展名


# ============================================================
# file_read
# ============================================================


class TestFileRead:

    @pytest.mark.asyncio
    async def test_read_normal(self, executor, workspace):
        Path(workspace, "hello.txt").write_text("line1\nline2\nline3")
        result = await executor.file_read("hello.txt")
        assert "hello.txt" in result
        assert "共 3 行" in result
        assert "line1" in result
        assert "line3" in result

    @pytest.mark.asyncio
    async def test_read_not_found(self, executor):
        result = await executor.file_read("nonexistent.txt")
        assert "文件不存在" in result

    @pytest.mark.asyncio
    async def test_read_directory(self, executor, workspace):
        (Path(workspace) / "subdir").mkdir()
        result = await executor.file_read("subdir")
        assert "不是文件" in result

    @pytest.mark.asyncio
    async def test_read_binary(self, executor, workspace):
        """非图片/非PDF 二进制文件仍被拒绝"""
        Path(workspace, "data.bin").write_bytes(b"\x00\x01\x02\x03")
        result = await executor.file_read("data.bin")
        assert "二进制文件" in result

    @pytest.mark.asyncio
    async def test_read_image_returns_file_read_result(self, executor, workspace):
        """图片文件返回 FileReadResult（多模态）"""
        from services.file_executor import FileReadResult
        Path(workspace, "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        result = await executor.file_read("image.png")
        assert isinstance(result, FileReadResult)
        assert result.type == "image"
        assert "图片" in result.text

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self, executor, workspace):
        lines = "\n".join(f"line{i}" for i in range(100))
        Path(workspace, "big.txt").write_text(lines)
        # offset=11 (1-based) → 从第11行开始（0-based index=10 → line10）
        result = await executor.file_read("big.txt", offset=11, limit=5)
        assert "显示: 11-15" in result
        assert "line10" in result  # 1-based offset=11 → 0-based[10] → line10
        assert "line14" in result
        assert "line15" not in result

    @pytest.mark.asyncio
    async def test_read_gbk_fallback(self, executor, workspace):
        content = "你好世界"
        Path(workspace, "gbk.txt").write_bytes(content.encode("gbk"))
        result = await executor.file_read("gbk.txt")
        assert "你好世界" in result

    @pytest.mark.asyncio
    async def test_read_large_file_rejected(self, executor, workspace):
        large = Path(workspace, "large.txt")
        # 创建超过 10MB 的文件
        large.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        result = await executor.file_read("large.txt")
        assert "文件过大" in result

    # ── 三级防线场景测试（对齐 Claude Code Read） ──

    @pytest.mark.asyncio
    async def test_l1_no_limit_blocks_large_file(self, executor, workspace):
        """L1: 不传 limit 读 300KB 文件 → 拒绝"""
        Path(workspace, "big.csv").write_text("x" * (300 * 1024))
        result = await executor.file_read("big.csv")  # limit=None
        assert "文件过大" in result
        assert "256" in result
        assert "offset/limit" in result

    @pytest.mark.asyncio
    async def test_l1_with_limit_allows_large_file(self, executor, workspace):
        """L1: 传 limit 读 300KB 文件 → 放行（跳过字节检查）"""
        lines = "\n".join(f"row,{i},data" for i in range(5000))
        Path(workspace, "big.csv").write_text(lines)
        result = await executor.file_read("big.csv", limit=10)
        assert "文件过大" not in result
        assert "row,0,data" in result
        assert "显示: 1-10" in result

    @pytest.mark.asyncio
    async def test_l1_no_limit_allows_small_file(self, executor, workspace):
        """L1: 不传 limit 读 100KB 文件 → 放行"""
        lines = "\n".join(f"line{i}" for i in range(500))
        Path(workspace, "small.txt").write_text(lines)
        result = await executor.file_read("small.txt")  # limit=None
        assert "文件过大" not in result
        assert "共 500 行" in result

    @pytest.mark.asyncio
    async def test_l2_caps_at_2000_lines(self, executor, workspace):
        """L2: 不传 limit 读 2500 行小文件 → 只返回前 2000 行"""
        lines = "\n".join(f"L{i}" for i in range(2500))
        Path(workspace, "many.txt").write_text(lines)
        result = await executor.file_read("many.txt")
        assert "共 2500 行" in result
        assert "显示: 1-2000" in result
        assert "L0" in result
        assert "L1999" in result
        assert "L2000" not in result

    @pytest.mark.asyncio
    async def test_l2_limit_exceeds_cap(self, executor, workspace):
        """L2: limit=9999 超过硬上限 → 自动截断到 2000"""
        lines = "\n".join(f"L{i}" for i in range(2500))
        Path(workspace, "many.txt").write_text(lines)
        result = await executor.file_read("many.txt", limit=9999)
        assert "显示: 1-2000" in result
        assert "L2000" not in result

    @pytest.mark.asyncio
    async def test_l3_token_blocks_dense_json(self, executor, workspace):
        """L3: JSON 文件用 2 bytes/token 估算，更容易触发 token 上限"""
        # 200KB JSON ≈ 100K tokens（200*1024/2），远超 25000
        Path(workspace, "dense.json").write_text('{"k":"' + "v" * (200 * 1024) + '"}')
        result = await executor.file_read("dense.json")
        assert "tokens" in result
        assert "超过上限" in result

    @pytest.mark.asyncio
    async def test_l3_fast_pass_small_file(self, executor, workspace):
        """L3: 小文件粗估 ≤ 1/4 阈值 → 快速放行"""
        # 10KB 文本 ≈ 2500 tokens (10*1024/4)，< 25000/4 = 6250
        Path(workspace, "tiny.txt").write_text("hello\n" * 100)
        result = await executor.file_read("tiny.txt")
        assert "tokens" not in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_offset_1indexed(self, executor, workspace):
        """offset=1 读第一行（1-based 对齐 Claude Code）"""
        Path(workspace, "abc.txt").write_text("AAA\nBBB\nCCC")
        result = await executor.file_read("abc.txt", offset=1, limit=1)
        assert "AAA" in result
        assert "BBB" not in result

    @pytest.mark.asyncio
    async def test_offset_0_treated_as_start(self, executor, workspace):
        """offset=0 保护（对齐 Claude: offset===0 ? 0 : offset-1）"""
        Path(workspace, "abc.txt").write_text("AAA\nBBB\nCCC")
        result = await executor.file_read("abc.txt", offset=0, limit=1)
        assert "AAA" in result

    @pytest.mark.asyncio
    async def test_offset_out_of_bounds(self, executor, workspace):
        """offset 超过总行数 → 提示超出范围"""
        Path(workspace, "short.txt").write_text("line1\nline2")
        result = await executor.file_read("short.txt", offset=100)
        assert "只有 2 行" in result
        assert "超出范围" in result

    @pytest.mark.asyncio
    async def test_empty_file(self, executor, workspace):
        """空文件 → 提示内容为空"""
        Path(workspace, "empty.txt").write_text("")
        result = await executor.file_read("empty.txt")
        assert "内容为空" in result

    @pytest.mark.asyncio
    async def test_bom_stripped(self, executor, workspace):
        """UTF-8 BOM 被剥离，不影响内容"""
        Path(workspace, "bom.txt").write_bytes(b"\xef\xbb\xbfhello\nworld")
        result = await executor.file_read("bom.txt")
        assert "hello" in result
        assert "\ufeff" not in result

    @pytest.mark.asyncio
    async def test_10mb_hard_limit_with_limit(self, executor, workspace):
        """超 10MB 文件即使传了 limit 也拒绝（防 OOM）"""
        large = Path(workspace, "huge.csv")
        large.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        result = await executor.file_read("huge.csv", limit=10)
        assert "文件过大" in result
        assert "硬上限" in result

    @pytest.mark.asyncio
    async def test_real_scenario_user_asks_read_csv(self, executor, workspace):
        """真实场景：用户上传大 CSV（>256KB），说"帮我读一下这个文件"
        LLM 调 file_read(path="sales.csv") 不传 limit → L1 拦住"""
        # 每行约 200 字符，2000 行 ≈ 400KB > 256KB
        header = "商品名称,平台,金额,备注,地址,联系人,电话,状态,日期,操作员"
        rows = "\n".join(
            f"超长商品名称测试{i:05d},平台{i%3},¥{i*10.5:.2f},"
            f"备注信息较长的文本内容{i},地址信息{i},张三{i},"
            f"138{i:08d},已完成,2026-04-{(i%28)+1:02d},操作员{i%10}"
            for i in range(2000)
        )
        Path(workspace, "sales.csv").write_text(f"{header}\n{rows}")
        result = await executor.file_read("sales.csv")  # 不传 limit
        assert "文件过大" in result
        assert "code_execute" in result

    @pytest.mark.asyncio
    async def test_real_scenario_llm_pages_large_csv(self, executor, workspace):
        """真实场景：L1 拦住后，LLM 改用分页读取前 50 行预览"""
        header = "商品名称,平台,金额,备注,地址,联系人,电话,状态,日期,操作员"
        rows = "\n".join(
            f"超长商品名称测试{i:05d},平台{i%3},¥{i*10.5:.2f},"
            f"备注信息较长的文本内容{i},地址信息{i},张三{i},"
            f"138{i:08d},已完成,2026-04-{(i%28)+1:02d},操作员{i%10}"
            for i in range(2000)
        )
        Path(workspace, "sales.csv").write_text(f"{header}\n{rows}")
        result = await executor.file_read("sales.csv", limit=50)
        assert "文件过大" not in result
        assert "商品名称" in result
        assert "显示: 1-50" in result

    @pytest.mark.asyncio
    async def test_real_scenario_small_report(self, executor, workspace):
        """真实场景：用户上传 200 行小报表，完整读取无阻碍"""
        rows = "\n".join(f"item{i},{i*5}" for i in range(200))
        Path(workspace, "report.csv").write_text(f"名称,数量\n{rows}")
        result = await executor.file_read("report.csv")
        assert "共 201 行" in result
        assert "item0" in result
        assert "item199" in result


# ============================================================
# file_write
# ============================================================


class TestFileWrite:

    @pytest.mark.asyncio
    async def test_write_create(self, executor, workspace):
        result = await executor.file_write("new.txt", "hello")
        assert "创建" in result
        assert Path(workspace, "new.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_write_overwrite(self, executor, workspace):
        Path(workspace, "exist.txt").write_text("old")
        result = await executor.file_write("exist.txt", "new")
        assert "覆盖写入" in result
        assert Path(workspace, "exist.txt").read_text() == "new"

    @pytest.mark.asyncio
    async def test_write_append_existing(self, executor, workspace):
        Path(workspace, "log.txt").write_text("line1\n")
        result = await executor.file_write("log.txt", "line2\n", mode="append")
        assert "追加" in result
        assert Path(workspace, "log.txt").read_text() == "line1\nline2\n"

    @pytest.mark.asyncio
    async def test_write_append_new_file(self, executor, workspace):
        result = await executor.file_write("brand_new.txt", "first", mode="append")
        assert "创建" in result
        assert Path(workspace, "brand_new.txt").read_text() == "first"

    @pytest.mark.asyncio
    async def test_write_create_only_rejects_existing(self, executor, workspace):
        Path(workspace, "exist.txt").write_text("old")
        result = await executor.file_write("exist.txt", "new", mode="create_only")
        assert "已存在" in result
        assert Path(workspace, "exist.txt").read_text() == "old"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, executor, workspace):
        result = await executor.file_write("a/b/c/deep.txt", "deep")
        assert "创建" in result
        assert Path(workspace, "a/b/c/deep.txt").read_text() == "deep"

    @pytest.mark.asyncio
    async def test_write_too_large_rejected(self, executor):
        huge = "x" * (5 * 1024 * 1024 + 1)
        result = await executor.file_write("huge.txt", huge)
        assert "内容过大" in result


# ============================================================
# file_list
# ============================================================


class TestFileList:

    @pytest.mark.asyncio
    async def test_list_root(self, executor, workspace):
        Path(workspace, "a.txt").write_text("a")
        Path(workspace, "b.txt").write_text("b")
        (Path(workspace) / "subdir").mkdir()
        result = await executor.file_list()
        assert "共 3 项" in result
        assert "a.txt" in result
        assert "subdir" in result

    @pytest.mark.asyncio
    async def test_list_empty(self, executor):
        result = await executor.file_list()
        assert "目录为空" in result

    @pytest.mark.asyncio
    async def test_list_hides_dotfiles(self, executor, workspace):
        Path(workspace, ".hidden").write_text("secret")
        Path(workspace, "visible.txt").write_text("ok")
        result = await executor.file_list()
        assert ".hidden" not in result
        assert "visible.txt" in result

    @pytest.mark.asyncio
    async def test_list_show_hidden(self, executor, workspace):
        Path(workspace, ".hidden").write_text("secret")
        result = await executor.file_list(show_hidden=True)
        assert ".hidden" in result

    @pytest.mark.asyncio
    async def test_list_nonexistent(self, executor):
        result = await executor.file_list("nonexistent")
        assert "目录不存在" in result

    @pytest.mark.asyncio
    async def test_list_file_not_dir(self, executor, workspace):
        Path(workspace, "file.txt").write_text("not a dir")
        result = await executor.file_list("file.txt")
        assert "不是目录" in result

    @pytest.mark.asyncio
    async def test_list_blocked_names_hidden(self, executor, workspace):
        """被禁文件名不出现在列表中"""
        (Path(workspace) / ".git").mkdir()
        Path(workspace, "ok.txt").write_text("visible")
        result = await executor.file_list()
        assert ".git" not in result
        assert "ok.txt" in result

    @pytest.mark.asyncio
    async def test_list_includes_abs_path(self, executor, workspace):
        """file_list 返回每个文件的 abs 绝对路径"""
        Path(workspace, "report.xlsx").write_bytes(b"fake excel")
        result = await executor.file_list()
        assert "abs:" in result
        assert str(Path(workspace, "report.xlsx")) in result


# ============================================================
# file_search
# ============================================================


class TestFileSearch:

    @pytest.mark.asyncio
    async def test_search_by_filename(self, executor, workspace):
        Path(workspace, "report.csv").write_text("data")
        Path(workspace, "notes.txt").write_text("text")
        result = await executor.file_search("report")
        assert "report.csv" in result
        assert "notes.txt" not in result

    @pytest.mark.asyncio
    async def test_search_by_content(self, executor, workspace):
        Path(workspace, "data.txt").write_text("secret_keyword_here")
        Path(workspace, "other.txt").write_text("nothing special")
        result = await executor.file_search(
            "secret_keyword", search_content=True,
        )
        assert "data.txt" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self, executor, workspace):
        Path(workspace, "file.txt").write_text("hello")
        result = await executor.file_search("nonexistent_term")
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_search_with_pattern(self, executor, workspace):
        Path(workspace, "a.csv").write_text("1")
        Path(workspace, "b.txt").write_text("2")
        result = await executor.file_search("a", file_pattern="*.csv")
        assert "a.csv" in result

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, executor, workspace):
        Path(workspace, "Report.CSV").write_text("data")
        result = await executor.file_search("report")
        assert "Report.CSV" in result

    @pytest.mark.asyncio
    async def test_search_skips_hidden_dirs(self, executor, workspace):
        hidden = Path(workspace) / ".secret"
        hidden.mkdir()
        (hidden / "leaked.txt").write_text("should not find")
        Path(workspace, "visible.txt").write_text("ok")
        result = await executor.file_search("leaked", search_content=True)
        assert "未找到" in result


# ============================================================
# file_info
# ============================================================


class TestFileInfo:

    @pytest.mark.asyncio
    async def test_info_file(self, executor, workspace):
        Path(workspace, "test.json").write_text('{"a":1}')
        result = await executor.file_info("test.json")
        assert "类型: 文件" in result
        assert "MIME: application/json" in result
        assert "可读文本: 是" in result

    @pytest.mark.asyncio
    async def test_info_directory(self, executor, workspace):
        sub = Path(workspace) / "mydir"
        sub.mkdir()
        (sub / "a.txt").write_text("a")
        result = await executor.file_info("mydir")
        assert "类型: 目录" in result
        assert "子项数量: 1" in result

    @pytest.mark.asyncio
    async def test_info_not_found(self, executor):
        result = await executor.file_info("ghost")
        assert "路径不存在" in result


# ============================================================
# get_cdn_url
# ============================================================


class TestGetCdnUrl:

    def test_cdn_url_with_domain(self, workspace):
        ex = FileExecutor(workspace_root=workspace, user_id="u1", org_id="org1")
        # 创建文件使 resolve_safe_path 不报错
        target = Path(ex.workspace_root) / "uploads" / "file.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("data")

        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.oss_cdn_domain = "cdn.example.com"
            url = ex.get_cdn_url("uploads/file.csv")

        assert url is not None
        assert url.startswith("https://cdn.example.com/workspace/")
        assert "org/org1/u1/uploads/file.csv" in url

    def test_cdn_url_without_domain(self, workspace):
        ex = FileExecutor(workspace_root=workspace)
        with patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value.oss_cdn_domain = None
            url = ex.get_cdn_url("any.txt")
        assert url is None


# ============================================================
# file_delete
# ============================================================


class TestFileDelete:

    @pytest.mark.asyncio
    async def test_delete_file(self, executor, workspace):
        """删除文件"""
        Path(workspace, "temp.txt").write_text("hello")
        result = await executor.file_delete("temp.txt")
        assert "已删除文件" in result
        assert not Path(workspace, "temp.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_empty_dir(self, executor, workspace):
        """删除空目录"""
        Path(workspace, "emptydir").mkdir()
        result = await executor.file_delete("emptydir")
        assert "已删除目录" in result
        assert not Path(workspace, "emptydir").exists()

    @pytest.mark.asyncio
    async def test_delete_nonempty_dir(self, executor, workspace):
        """非空目录拒绝删除"""
        d = Path(workspace, "fulldir")
        d.mkdir()
        (d / "file.txt").write_text("x")
        result = await executor.file_delete("fulldir")
        assert "不为空" in result
        assert d.exists()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, executor):
        """不存在的路径"""
        result = await executor.file_delete("ghost.txt")
        assert "不存在" in result


# ============================================================
# file_mkdir
# ============================================================


class TestFileMkdir:

    @pytest.mark.asyncio
    async def test_mkdir_simple(self, executor, workspace):
        """创建目录"""
        result = await executor.file_mkdir("newdir")
        assert "已创建目录" in result
        assert Path(workspace, "newdir").is_dir()

    @pytest.mark.asyncio
    async def test_mkdir_nested(self, executor, workspace):
        """创建嵌套目录"""
        result = await executor.file_mkdir("a/b/c")
        assert "已创建目录" in result
        assert Path(workspace, "a/b/c").is_dir()

    @pytest.mark.asyncio
    async def test_mkdir_exists(self, executor, workspace):
        """目录已存在"""
        Path(workspace, "existing").mkdir()
        result = await executor.file_mkdir("existing")
        assert "已存在" in result

    @pytest.mark.asyncio
    async def test_mkdir_conflict_with_file(self, executor, workspace):
        """同名文件已存在"""
        Path(workspace, "conflict").write_text("x")
        result = await executor.file_mkdir("conflict")
        assert "同名文件已存在" in result


# ============================================================
# file_rename
# ============================================================


class TestFileRename:

    @pytest.mark.asyncio
    async def test_rename_file(self, executor, workspace):
        """重命名文件"""
        Path(workspace, "old.txt").write_text("data")
        result = await executor.file_rename("old.txt", "new.txt")
        assert "已重命名" in result
        assert not Path(workspace, "old.txt").exists()
        assert Path(workspace, "new.txt").read_text() == "data"

    @pytest.mark.asyncio
    async def test_rename_target_exists(self, executor, workspace):
        """目标已存在返回错误"""
        Path(workspace, "a.txt").write_text("a")
        Path(workspace, "b.txt").write_text("b")
        result = await executor.file_rename("a.txt", "b.txt")
        assert "已存在" in result
        # 两个文件都应该还在
        assert Path(workspace, "a.txt").exists()
        assert Path(workspace, "b.txt").exists()

    @pytest.mark.asyncio
    async def test_rename_cross_dir_rejected(self, executor, workspace):
        """跨目录重命名被拒绝"""
        Path(workspace, "sub").mkdir()
        Path(workspace, "root.txt").write_text("x")
        result = await executor.file_rename("root.txt", "sub/moved.txt")
        assert "不允许跨目录" in result

    @pytest.mark.asyncio
    async def test_rename_src_not_found(self, executor):
        """源文件不存在"""
        result = await executor.file_rename("ghost.txt", "new.txt")
        assert "不存在" in result


# ============================================================
# file_move
# ============================================================


class TestFileMove:

    @pytest.mark.asyncio
    async def test_move_file(self, executor, workspace):
        """移动文件到子目录"""
        Path(workspace, "file.txt").write_text("data")
        Path(workspace, "dest").mkdir()
        result = await executor.file_move("file.txt", "dest")
        assert "已移动" in result
        assert not Path(workspace, "file.txt").exists()
        assert Path(workspace, "dest/file.txt").read_text() == "data"

    @pytest.mark.asyncio
    async def test_move_conflict(self, executor, workspace):
        """目标位置有同名文件"""
        Path(workspace, "dup.txt").write_text("src")
        dest = Path(workspace, "dest")
        dest.mkdir()
        (dest / "dup.txt").write_text("existing")
        result = await executor.file_move("dup.txt", "dest")
        assert "同名文件" in result
        # 源文件还在
        assert Path(workspace, "dup.txt").exists()

    @pytest.mark.asyncio
    async def test_move_dest_not_dir(self, executor, workspace):
        """目标不是目录"""
        Path(workspace, "file.txt").write_text("x")
        result = await executor.file_move("file.txt", "nonexistent")
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_move_src_not_found(self, executor, workspace):
        """源文件不存在"""
        Path(workspace, "dest").mkdir()
        result = await executor.file_move("ghost.txt", "dest")
        assert "不存在" in result


# ============================================================
# 对话级文件路径缓存（替代旧的 F1/F2 句柄系统）
# 完整测试见 test_data_query.py::TestFilePathCache
# ============================================================


class TestFilePathCacheCompat:
    """FilePathCache 基本功能验证（确保旧句柄模块替换后不影响其他功能）"""

    def test_register_and_resolve(self):
        """注册文件后可解析回绝对路径"""
        from services.agent.workspace_file_handles import FilePathCache
        cache = FilePathCache()
        cache.register("report.xlsx", "/mnt/workspace/report.xlsx")
        assert cache.resolve("report.xlsx") == "/mnt/workspace/report.xlsx"

    def test_resolve_with_spaces(self):
        """LLM 加空格后仍能匹配"""
        from services.agent.workspace_file_handles import FilePathCache
        cache = FilePathCache()
        cache.register("利润表-2026.xlsx", "/mnt/ws/利润表-2026.xlsx")
        assert cache.resolve("利润表 - 2026.xlsx") == "/mnt/ws/利润表-2026.xlsx"

    def test_resolve_returns_none_for_unknown(self):
        """未注册的文件名返回 None"""
        from services.agent.workspace_file_handles import FilePathCache
        cache = FilePathCache()
        assert cache.resolve("unknown.xlsx") is None

    def test_workspace_and_staging_unified(self):
        """workspace 和 staging 文件在同一个缓存里"""
        from services.agent.workspace_file_handles import FilePathCache
        cache = FilePathCache()
        cache.register("利润表.xlsx", "/workspace/利润表.xlsx")
        cache.register("trade.parquet", "/staging/trade.parquet")
        assert cache.count == 2
        assert cache.resolve("利润表.xlsx").endswith("利润表.xlsx")
        assert cache.resolve("trade.parquet").endswith("trade.parquet")


class TestResolveSafePathAbsolute:
    """测试 resolve_safe_path 绝对路径支持"""

    def test_absolute_path_inside_workspace(self, workspace):
        """workspace 内的绝对路径正常解析"""
        target = Path(workspace) / "info.txt"
        target.write_text("hello")
        ex = FileExecutor(workspace_root=workspace)
        resolved = ex.resolve_safe_path(str(target))
        assert resolved == target.resolve()

    def test_absolute_path_outside_workspace_rejected(self, workspace):
        """workspace 外的绝对路径被拒绝"""
        ex = FileExecutor(workspace_root=workspace)
        with pytest.raises(PermissionError, match="路径越界"):
            ex.resolve_safe_path("/etc/passwd")

    def test_relative_path_still_works(self, workspace):
        """相对路径依然正常工作"""
        target = Path(workspace) / "notes.txt"
        target.write_text("world")
        ex = FileExecutor(workspace_root=workspace)
        resolved = ex.resolve_safe_path("notes.txt")
        assert resolved == target.resolve()


# ============================================================
# file_list_entries（结构化返回）
# ============================================================


class TestFileListEntries:
    """测试 file_list_entries 返回结构化数据"""

    @pytest.mark.asyncio
    async def test_normal_directory(self, executor, workspace):
        """正常目录返回 dirs + files"""
        Path(workspace, "sub").mkdir()
        Path(workspace, "a.txt").write_text("hello")
        Path(workspace, "b.csv").write_text("x,y")

        data = await executor.file_list_entries()

        assert data["error"] is None
        assert data["path"] == "."
        assert len(data["dirs"]) == 1
        assert data["dirs"][0]["name"] == "sub"
        assert len(data["files"]) == 2
        # 文件带 abs_path
        assert all("abs_path" in f for f in data["files"])
        assert not data["truncated"]

    @pytest.mark.asyncio
    async def test_empty_directory(self, executor, workspace):
        """空目录返回空列表"""
        data = await executor.file_list_entries()
        assert data["error"] is None
        assert data["dirs"] == []
        assert data["files"] == []

    @pytest.mark.asyncio
    async def test_nonexistent_directory(self, executor):
        """不存在的目录返回 error"""
        data = await executor.file_list_entries("no_such_dir")
        assert data["error"] is not None
        assert "不存在" in data["error"]

    @pytest.mark.asyncio
    async def test_file_as_path(self, executor, workspace):
        """传入文件路径返回 error"""
        Path(workspace, "file.txt").write_text("x")
        data = await executor.file_list_entries("file.txt")
        assert data["error"] is not None
        assert "不是目录" in data["error"]

    @pytest.mark.asyncio
    async def test_entries_contain_metadata(self, executor, workspace):
        """每个文件条目包含 name/size/modified/abs_path"""
        Path(workspace, "data.json").write_text('{"k":1}')
        data = await executor.file_list_entries()
        f = data["files"][0]
        assert f["name"] == "data.json"
        assert f["size"] > 0
        assert f["modified"]  # 非空字符串
        assert f["abs_path"].endswith("data.json")


# ============================================================
# FilePathCache 对话级隔离（详细测试见 test_data_query.py::TestFilePathCache）
# ============================================================


# ============================================================
# file_edit 测试
# ============================================================


class TestFileEdit:
    """file_edit 精确字符串替换"""

    @pytest.fixture
    def executor(self, tmp_path):
        from services.file_executor import FileExecutor
        return FileExecutor(workspace_root=str(tmp_path))

    @pytest.mark.asyncio
    async def test_single_replace(self, executor, tmp_path):
        """单次替换"""
        (tmp_path / "test.txt").write_text("hello world")
        result = await executor.file_edit("test.txt", "hello", "goodbye")
        assert "已替换 1 处" in result
        assert (tmp_path / "test.txt").read_text() == "goodbye world"

    @pytest.mark.asyncio
    async def test_replace_all(self, executor, tmp_path):
        """replace_all 替换所有匹配"""
        (tmp_path / "test.txt").write_text("aa bb aa cc aa")
        result = await executor.file_edit("test.txt", "aa", "XX", replace_all=True)
        assert "已替换 3 处" in result
        assert (tmp_path / "test.txt").read_text() == "XX bb XX cc XX"

    @pytest.mark.asyncio
    async def test_multiple_matches_without_replace_all(self, executor, tmp_path):
        """多处匹配但未设 replace_all → 报错"""
        (tmp_path / "test.txt").write_text("aa bb aa")
        result = await executor.file_edit("test.txt", "aa", "XX")
        assert "找到 2 处匹配" in result
        assert "replace_all" in result
        # 文件未修改
        assert (tmp_path / "test.txt").read_text() == "aa bb aa"

    @pytest.mark.asyncio
    async def test_no_match(self, executor, tmp_path):
        """未找到匹配"""
        (tmp_path / "test.txt").write_text("hello world")
        result = await executor.file_edit("test.txt", "xyz", "abc")
        assert "未找到匹配" in result

    @pytest.mark.asyncio
    async def test_same_old_new(self, executor, tmp_path):
        """old_string == new_string"""
        (tmp_path / "test.txt").write_text("hello")
        result = await executor.file_edit("test.txt", "hello", "hello")
        assert "相同" in result

    @pytest.mark.asyncio
    async def test_file_not_exists(self, executor):
        """文件不存在"""
        result = await executor.file_edit("nonexistent.txt", "a", "b")
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_binary_file_rejected(self, executor, tmp_path):
        """二进制文件拒绝"""
        (tmp_path / "data.xlsx").write_bytes(b"\x00\x01\x02")
        result = await executor.file_edit("data.xlsx", "a", "b")
        assert "二进制" in result

    @pytest.mark.asyncio
    async def test_multiline_replace(self, executor, tmp_path):
        """多行文本替换"""
        content = "line1\nold_line2\nline3"
        (tmp_path / "test.txt").write_text(content)
        result = await executor.file_edit("test.txt", "old_line2", "new_line2")
        assert "已替换 1 处" in result
        assert (tmp_path / "test.txt").read_text() == "line1\nnew_line2\nline3"

    @pytest.mark.asyncio
    async def test_gbk_fallback(self, executor, tmp_path):
        """GBK 编码文件可编辑"""
        (tmp_path / "gbk.txt").write_bytes("你好世界".encode("gbk"))
        result = await executor.file_edit("gbk.txt", "你好", "再见")
        assert "已替换 1 处" in result


# ============================================================
# file_search 排除 VCS/临时目录测试
# ============================================================


class TestFileSearchSkipDirs:
    """搜索排除 staging/__pycache__/node_modules 等"""

    @pytest.fixture
    def executor(self, tmp_path):
        from services.file_executor import FileExecutor
        return FileExecutor(workspace_root=str(tmp_path))

    @pytest.mark.asyncio
    async def test_excludes_staging(self, executor, tmp_path):
        """staging 目录下的文件不出现在搜索结果中"""
        staging = tmp_path / "staging" / "conv001"
        staging.mkdir(parents=True)
        (staging / "data.csv").write_text("staging data")
        (tmp_path / "data.csv").write_text("workspace data")

        result = await executor.file_search("data")
        assert "staging" not in result

    @pytest.mark.asyncio
    async def test_excludes_pycache(self, executor, tmp_path):
        """__pycache__ 目录排除"""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "module.pyc").write_bytes(b"\x00")
        (tmp_path / "module.py").write_text("code")

        result = await executor.file_search("module")
        assert "__pycache__" not in result

    @pytest.mark.asyncio
    async def test_excludes_node_modules(self, executor, tmp_path):
        """node_modules 目录排除"""
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("code")
        (tmp_path / "app.js").write_text("code")

        result = await executor.file_search("index")
        assert "node_modules" not in result

    @pytest.mark.asyncio
    async def test_normal_dir_included(self, executor, tmp_path):
        """正常子目录不被排除"""
        sub = tmp_path / "reports"
        sub.mkdir()
        (sub / "q1.txt").write_text("report")

        result = await executor.file_search("q1")
        assert "q1.txt" in result

    @pytest.mark.asyncio
    async def test_search_limit_100(self, executor, tmp_path):
        """搜索上限为 100"""
        from services.file_executor import _MAX_SEARCH_RESULTS
        assert _MAX_SEARCH_RESULTS == 100
