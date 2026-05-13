"""
scoped_os 安全攻击面测试

覆盖 TECH_沙盒OS开放与工具精简.md §7.2 的全部攻击向量。
"""

import os
import pytest

from services.sandbox.scoped_os import build_scoped_os, build_scoped_shutil


@pytest.fixture
def workspace(tmp_path):
    """构建测试 workspace 目录结构"""
    ws = tmp_path / "workspace"
    staging = ws / "staging"
    output = ws / "下载"
    sub = ws / "子目录"
    for d in (ws, staging, output, sub):
        d.mkdir(parents=True)
    # 创建测试文件
    (ws / "销售报表.xlsx").write_text("fake")
    (ws / "合同.pdf").write_text("fake")
    (sub / "数据.csv").write_text("fake")
    (output / "旧报表.xlsx").write_text("old")
    # chdir 模拟沙盒
    old_cwd = os.getcwd()
    os.chdir(str(ws))
    yield {"ws": str(ws), "staging": str(staging), "output": str(output)}
    os.chdir(old_cwd)


@pytest.fixture
def scoped(workspace):
    scoped_os, check_path = build_scoped_os(
        workspace["ws"], workspace["staging"], workspace["output"],
    )
    return scoped_os


@pytest.fixture
def scoped_sh(workspace):
    _, check_path = build_scoped_os(
        workspace["ws"], workspace["staging"], workspace["output"],
    )
    return build_scoped_shutil(check_path)


# ============================================================
# 1. 正常操作
# ============================================================

class TestNormalOperations:

    def test_listdir_workspace(self, scoped):
        files = scoped.listdir(".")
        assert "销售报表.xlsx" in files
        assert "合同.pdf" in files

    def test_listdir_subdir(self, scoped):
        files = scoped.listdir("子目录")
        assert "数据.csv" in files

    def test_stat_file(self, scoped):
        st = scoped.stat("销售报表.xlsx")
        assert st.st_size > 0

    def test_getcwd(self, scoped, workspace):
        assert scoped.getcwd() == workspace["ws"]

    def test_path_join(self, scoped):
        assert scoped.path.join("a", "b") == os.path.join("a", "b")

    def test_path_exists(self, scoped):
        assert scoped.path.exists("销售报表.xlsx")

    def test_path_splitext(self, scoped):
        assert scoped.path.splitext("test.xlsx") == ("test", ".xlsx")

    def test_makedirs(self, scoped, workspace):
        scoped.makedirs("新目录")
        assert os.path.isdir(os.path.join(workspace["ws"], "新目录"))

    def test_rename(self, scoped, workspace):
        src = os.path.join(workspace["ws"], "销售报表.xlsx")
        scoped.rename("销售报表.xlsx", "销售报表_备份.xlsx")
        assert os.path.exists(os.path.join(workspace["ws"], "销售报表_备份.xlsx"))
        # 恢复
        scoped.rename("销售报表_备份.xlsx", "销售报表.xlsx")

    def test_sep_and_linesep(self, scoped):
        assert scoped.sep == os.sep
        assert scoped.linesep == os.linesep


# ============================================================
# 2. walk/scandir 路径格式验证（核心：输出相对路径）
# ============================================================

class TestWalkPathFormat:

    def test_walk_returns_relative_paths(self, scoped):
        """walk('.') 返回相对路径，不泄露绝对路径"""
        for root, dirs, files in scoped.walk("."):
            assert not os.path.isabs(root), f"walk root 应为相对路径: {root}"

    def test_walk_finds_nested_files(self, scoped):
        all_files = []
        for root, dirs, files in scoped.walk("."):
            for f in files:
                all_files.append(os.path.join(root, f))
        assert any("数据.csv" in f for f in all_files)

    def test_scandir_returns_relative_paths(self, scoped):
        """scandir('.') 返回相对路径"""
        for entry in scoped.scandir("."):
            assert not os.path.isabs(entry.path), f"scandir path 应为相对路径: {entry.path}"


# ============================================================
# 3. 路径越界攻击
# ============================================================

class TestPathTraversal:

    def test_listdir_etc(self, scoped):
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped.listdir("/etc")

    def test_listdir_parent_traversal(self, scoped):
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped.listdir("../..")

    def test_stat_etc_passwd(self, scoped):
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped.stat("/etc/passwd")

    def test_walk_root(self, scoped):
        with pytest.raises(PermissionError, match="不在允许范围"):
            list(scoped.walk("/"))

    def test_walk_outside(self, scoped, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir(exist_ok=True)
        with pytest.raises(PermissionError, match="不在允许范围"):
            list(scoped.walk(str(outside)))

    def test_makedirs_outside(self, scoped):
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped.makedirs("/tmp/evil")

    def test_rename_outside(self, scoped):
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped.rename("销售报表.xlsx", "/tmp/stolen.xlsx")


# ============================================================
# 4. 系统命令攻击
# ============================================================

class TestSystemCommandAttack:

    def test_no_system(self, scoped):
        assert not hasattr(scoped, "system")
        with pytest.raises(AttributeError):
            scoped.system("ls")

    def test_no_popen(self, scoped):
        assert not hasattr(scoped, "popen")
        with pytest.raises(AttributeError):
            scoped.popen("ls")

    def test_no_execv(self, scoped):
        assert not hasattr(scoped, "execv")

    def test_no_fork(self, scoped):
        assert not hasattr(scoped, "fork")

    def test_no_kill(self, scoped):
        assert not hasattr(scoped, "kill")


# ============================================================
# 5. 环境变量
# ============================================================

class TestEnvironmentVariables:

    def test_environ_empty(self, scoped):
        assert scoped.environ == {}

    def test_environ_keyerror(self, scoped):
        with pytest.raises(KeyError):
            _ = scoped.environ["OPENAI_API_KEY"]

    def test_getenv_returns_default(self, scoped):
        assert scoped.getenv("OPENAI_API_KEY") is None
        assert scoped.getenv("SECRET", "fallback") == "fallback"


# ============================================================
# 6. 删除操作
# ============================================================

class TestDeleteOperations:

    def test_remove_always_blocked(self, scoped, workspace):
        """沙盒内 os.remove 统一禁止，引导用 confirm_delete 参数"""
        with pytest.raises(PermissionError, match="沙盒内禁止直接删除文件"):
            scoped.remove("销售报表.xlsx")
        # 文件未被删除
        assert os.path.exists(os.path.join(workspace["ws"], "销售报表.xlsx"))

    def test_rmdir_always_blocked(self, scoped, workspace):
        """沙盒内 os.rmdir 统一禁止"""
        subdir = os.path.join(workspace["ws"], "空测试目录")
        os.makedirs(subdir, exist_ok=True)
        with pytest.raises(PermissionError, match="沙盒内禁止删除目录"):
            scoped.rmdir("空测试目录")
        assert os.path.exists(subdir)

    def test_unlink_is_remove(self, scoped, workspace):
        """unlink 和 remove 行为一致，同样被禁止"""
        with pytest.raises(PermissionError, match="沙盒内禁止直接删除文件"):
            scoped.unlink("销售报表.xlsx")
        assert os.path.exists(os.path.join(workspace["ws"], "销售报表.xlsx"))


# ============================================================
# 7. shutil 操作
# ============================================================

class TestScopedShutil:

    def test_copy(self, scoped_sh, workspace):
        scoped_sh.copy("销售报表.xlsx", "销售报表_copy.xlsx")
        assert os.path.exists(os.path.join(workspace["ws"], "销售报表_copy.xlsx"))

    def test_copy_outside_blocked(self, scoped_sh):
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped_sh.copy("销售报表.xlsx", "/tmp/stolen.xlsx")

    def test_move(self, scoped_sh, workspace):
        (workspace["ws"] + "/temp.txt")
        open(os.path.join(workspace["ws"], "temp.txt"), "w").write("test")
        scoped_sh.move("temp.txt", "moved.txt")
        assert os.path.exists(os.path.join(workspace["ws"], "moved.txt"))

    def test_rmtree_blocked(self, scoped_sh):
        with pytest.raises(PermissionError, match="沙盒内禁止递归删除目录"):
            scoped_sh.rmtree("子目录")


# ============================================================
# 8. 符号链接逃逸
# ============================================================

class TestSymlinkEscape:

    def test_symlink_outside_blocked(self, scoped, workspace, tmp_path):
        """符号链接指向 workspace 外 → PermissionError"""
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        link = os.path.join(workspace["ws"], "evil_link")
        os.symlink(str(outside), link)
        with pytest.raises(PermissionError, match="不在允许范围"):
            scoped.stat("evil_link")


# ============================================================
# 9. _register_files_from_output 文件缓存注册
# ============================================================

class TestRegisterFilesFromOutput:
    """code_execute 后处理：从 stdout 提取文件名并注册到路径缓存"""

    def test_extracts_quoted_filenames(self, workspace):
        """从 stdout 中提取引号包裹的文件名"""
        import re
        _DATA_EXTS = r"\.(?:xlsx|xls|csv|tsv|parquet|pdf|docx|pptx|txt|json|png|jpg)"
        _FILE_RE = re.compile(rf"['\"]([^'\"]*{_DATA_EXTS})['\"]", re.IGNORECASE)

        stdout = "['销售报表.xlsx', '合同.pdf', '子目录']"
        matches = [m.group(1) for m in _FILE_RE.finditer(stdout)]
        assert "销售报表.xlsx" in matches
        assert "合同.pdf" in matches
        assert "子目录" not in matches  # 无扩展名不匹配

    def test_extracts_from_read_excel_pattern(self, workspace):
        """从 pd.read_excel('file.xlsx') 模式中提取"""
        import re
        _DATA_EXTS = r"\.(?:xlsx|xls|csv|tsv|parquet|pdf|docx|pptx|txt|json|png|jpg)"
        _FILE_RE = re.compile(rf"['\"]([^'\"]*{_DATA_EXTS})['\"]", re.IGNORECASE)

        stdout = "df = pd.read_excel('数据.xlsx')\ndf2 = pd.read_csv('log.csv')"
        matches = [m.group(1) for m in _FILE_RE.finditer(stdout)]
        assert "数据.xlsx" in matches
        assert "log.csv" in matches

    # FilePathCache 测试已移除（workspace_file_handles 模块已删除）


# ============ Staging 隔离测试 ============


class TestStagingIsolation:
    """staging 目录隔离：父目录禁止，当前会话放行，其他会话禁止"""

    @pytest.fixture
    def staging_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        conv_id = "conv_current"
        staging_conv = ws / "staging" / conv_id
        staging_other = ws / "staging" / "conv_other"
        output = ws / "下载"
        for d in (ws, staging_conv, staging_other, output):
            d.mkdir(parents=True)
        (staging_conv / "data.parquet").write_text("current")
        (staging_other / "secret.parquet").write_text("other")
        old_cwd = os.getcwd()
        os.chdir(str(ws))
        yield {
            "ws": str(ws),
            "staging": str(staging_conv),
            "output": str(output),
        }
        os.chdir(old_cwd)

    def test_staging_parent_blocked(self, staging_workspace):
        """listing staging/ 父目录应被拒绝"""
        scoped_os, _ = build_scoped_os(
            staging_workspace["ws"], staging_workspace["staging"], staging_workspace["output"],
        )
        with pytest.raises(PermissionError):
            scoped_os.listdir("staging")

    def test_staging_other_conv_blocked(self, staging_workspace):
        """访问其他会话的 staging 子目录应被拒绝"""
        scoped_os, _ = build_scoped_os(
            staging_workspace["ws"], staging_workspace["staging"], staging_workspace["output"],
        )
        with pytest.raises(PermissionError):
            scoped_os.listdir("staging/conv_other")

    def test_staging_current_conv_allowed(self, staging_workspace):
        """当前会话的 staging 子目录应允许访问"""
        scoped_os, _ = build_scoped_os(
            staging_workspace["ws"], staging_workspace["staging"], staging_workspace["output"],
        )
        files = scoped_os.listdir("staging/conv_current")
        assert "data.parquet" in files


# ============================================================
# 10. scoped pathlib — Path.unlink 走 scoped_os.remove 拦截
# ============================================================


class TestScopedPathlib:

    @pytest.fixture
    def pathlib_env(self, workspace):
        """构建 scoped pathlib 环境"""
        from services.sandbox.scoped_os import build_scoped_pathlib
        scoped_os, _ = build_scoped_os(
            workspace["ws"], workspace["staging"], workspace["output"],
        )
        scoped_pl = build_scoped_pathlib(scoped_os)
        return scoped_pl, scoped_os, workspace

    def test_path_unlink_blocked(self, pathlib_env):
        """Path.unlink 沙盒内被禁止"""
        scoped_pl, _, ws = pathlib_env
        Path = scoped_pl.Path
        target = os.path.join(ws["ws"], "销售报表.xlsx")
        assert os.path.exists(target)
        with pytest.raises(PermissionError, match="沙盒内禁止直接删除文件"):
            Path(target).unlink()
        assert os.path.exists(target)  # 文件未被删除

    def test_path_unlink_missing_ok_still_blocked(self, pathlib_env):
        """Path.unlink(missing_ok=True) 对存在的文件仍然被禁止"""
        scoped_pl, _, ws = pathlib_env
        target = os.path.join(ws["ws"], "销售报表.xlsx")
        with pytest.raises(PermissionError, match="沙盒内禁止直接删除文件"):
            scoped_pl.Path(target).unlink(missing_ok=True)

    def test_path_rmdir_blocked(self, pathlib_env):
        """Path.rmdir 沙盒内被禁止"""
        scoped_pl, _, ws = pathlib_env
        subdir = os.path.join(ws["ws"], "空目录")
        os.makedirs(subdir, exist_ok=True)
        with pytest.raises(PermissionError, match="沙盒内禁止删除目录"):
            scoped_pl.Path(subdir).rmdir()
        assert os.path.exists(subdir)  # 目录未被删除

    def test_path_read_operations_work(self, pathlib_env):
        """Path 的只读操作正常工作"""
        scoped_pl, _, ws = pathlib_env
        Path = scoped_pl.Path
        p = Path(os.path.join(ws["ws"], "销售报表.xlsx"))
        assert p.exists()
        assert p.is_file()
        assert p.read_text() == "fake"
        assert p.name == "销售报表.xlsx"
        assert p.suffix == ".xlsx"

    def test_path_division_returns_scoped(self, pathlib_env):
        """/ 运算符返回 _ScopedPath 而非真实 Path"""
        scoped_pl, _, ws = pathlib_env
        Path = scoped_pl.Path
        p = Path(ws["ws"]) / "销售报表.xlsx"
        # 仍是受限 Path：路径白名单校验生效
        assert p.exists()
        assert p.is_file()

    def test_path_iterdir_returns_scoped(self, pathlib_env):
        """iterdir 返回的也是受限 Path"""
        scoped_pl, _, ws = pathlib_env
        Path = scoped_pl.Path
        items = list(Path(ws["ws"]).iterdir())
        assert len(items) > 0
        # 子项仍是受限 Path（exists/is_file 正常工作）
        for item in items:
            if item.is_file():
                assert item.exists()
                break
