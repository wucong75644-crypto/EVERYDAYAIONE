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

    def test_absolute_path_stripped(self, executor):
        """前导 / 被去掉，不会穿越"""
        p = executor.resolve_safe_path("/subdir/file.txt")
        assert executor.workspace_root in str(p)

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
        Path(workspace, "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        result = await executor.file_read("image.png")
        assert "二进制文件" in result

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self, executor, workspace):
        lines = "\n".join(f"line{i}" for i in range(100))
        Path(workspace, "big.txt").write_text(lines)
        result = await executor.file_read("big.txt", offset=10, limit=5)
        assert "显示: 11-15" in result
        assert "line10" in result  # 0-based offset=10 → line10
        assert "line15" not in result or "line14" in result

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
