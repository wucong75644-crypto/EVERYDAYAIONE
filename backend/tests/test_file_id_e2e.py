"""
文件归一化匹配 E2E 测试

核心机制：get_file('文件名') → 归一化匹配 → 返回正确绝对路径
归一化规则：NFKC + 只保留中文/字母/数字 + 扩展名点
匹配策略：精确 → 归一化 → stem（无扩展名）→ 前缀（截断）
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from services.agent.file_path_cache import FilePathCache, get_file_cache, normalize_filename
from services.handlers.chat_tool_mixin import _resolve_file_ids


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "4月金东区部门--销售主题分析-按订单商品明细-20260508161623_ad46a059fc470bc2_e131fc.xlsx").write_bytes(b"x" * 100)
    (ws / "产品库存表-2024年度.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (ws / "利润表（1-4月）汇总.xlsx").write_bytes(b"x" * 50)
    return ws


@pytest.fixture
def staging(tmp_path):
    s = tmp_path / "staging"
    s.mkdir()
    return s


@pytest.fixture
def conv_id():
    import uuid
    return f"test-norm-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cache(conv_id, staging):
    c = get_file_cache(conv_id)
    c.set_staging_dir(str(staging))
    return c


class TestNormalizeFilename:
    """归一化函数测试"""

    def test_remove_spaces(self):
        assert normalize_filename("4月 销售分析.xlsx") == "4月销售分析.xlsx"

    def test_remove_dashes(self):
        assert normalize_filename("4月--销售-分析.xlsx") == "4月销售分析.xlsx"

    def test_remove_underscores(self):
        assert normalize_filename("ad46a059_fc470bc2.parquet") == "ad46a059fc470bc2.parquet"

    def test_fullwidth_brackets(self):
        assert normalize_filename("利润表（1-4月）.xlsx") == "利润表14月.xlsx"

    def test_preserve_ext(self):
        assert normalize_filename("test.CSV") == "test.csv"

    def test_real_filename(self):
        result = normalize_filename(
            "4月金东区部门--销售主题分析-按订单商品明细-20260508161623_ad46a059fc470bc2_e131fc.xlsx"
        )
        assert result == "4月金东区部门销售主题分析按订单商品明细20260508161623ad46a059fc470bc2e131fc.xlsx"

    def test_nfkc_fullwidth_digit(self):
        assert normalize_filename("４月报表.xlsx") == "4月报表.xlsx"


class TestResolve:
    """四级匹配测试"""

    def test_exact_match(self, cache, workspace):
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        assert cache.resolve(f.name) == str(f)

    def test_normalized_match(self, cache, workspace):
        """LLM 加了空格/改了破折号，归一化后匹配"""
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        # 模拟 LLM 写错
        wrong_name = "4月金东区部门 销售主题分析 按订单商品明细-20260508161623_ad46a059fc470bc2_e131fc.xlsx"
        assert cache.resolve(wrong_name) == str(f)

    def test_stem_match(self, cache, workspace):
        """用户没带扩展名"""
        f = list(workspace.glob("*.csv"))[0]
        cache.register(f.name, str(f))
        assert cache.resolve("产品库存表-2024年度") == str(f)

    def test_prefix_match(self, cache, workspace):
        """LLM 截断了长文件名"""
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        assert cache.resolve("4月金东区部门销售主题分析") == str(f)

    def test_no_match_returns_none(self, cache):
        assert cache.resolve("不存在的文件.xlsx") is None

    def test_short_prefix_no_match(self, cache, workspace):
        """前缀太短（<6字符）不匹配，防误匹配"""
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        assert cache.resolve("4月") is None


class TestUpdatePath:
    """file_analyze 后路径升级"""

    def test_update_path_basic(self, cache, workspace, staging):
        f = list(workspace.glob("*.xlsx"))[0]
        cache.register(f.name, str(f))

        # analyze 前指向 xlsx
        assert cache.resolve(f.name).endswith(".xlsx")

        # analyze 后升级为 parquet
        parquet = staging / "_cache_test.parquet"
        parquet.write_bytes(b"parquet")
        cache.update_path(f.name, str(parquet))

        # 同一文件名，路径自动升级
        assert cache.resolve(f.name) == str(parquet)
        assert cache.resolve(f.name).endswith(".parquet")

    def test_update_path_fuzzy_still_works(self, cache, workspace, staging):
        """升级后归一化匹配仍有效"""
        f = list(workspace.glob("*.xlsx"))[0]
        cache.register(f.name, str(f))
        parquet = staging / "_cache_test.parquet"
        parquet.write_bytes(b"parquet")
        cache.update_path(f.name, str(parquet))

        # 用错误文件名查，归一化后也能拿到 parquet
        wrong_name = f.stem.replace("-", " ") + ".xlsx"
        resolved = cache.resolve(wrong_name)
        assert resolved == str(parquet)


class TestManifestAndSandboxGetFile:
    """manifest + 沙盒 get_file"""

    def _build_get_file(self, staging):
        import builtins
        manifest_path = str(staging / "_manifest.json")

        def _normalize_fn(name):
            import unicodedata, re
            stem, ext = os.path.splitext(name)
            stem = unicodedata.normalize("NFKC", stem)
            stem = re.sub(r'[^\u4e00-\u9fff\da-zA-Z]', '', stem)
            return (stem + ext).lower()

        def get_file(name):
            with builtins.open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
            if name in manifest:
                return manifest[name]
            norm_input = _normalize_fn(name)
            for key, path in manifest.items():
                if _normalize_fn(key) == norm_input:
                    return path
            input_stem = os.path.splitext(norm_input)[0]
            if input_stem:
                for key, path in manifest.items():
                    if os.path.splitext(_normalize_fn(key))[0] == input_stem:
                        return path
            if len(input_stem) >= 6:
                for key, path in manifest.items():
                    reg_stem = os.path.splitext(_normalize_fn(key))[0]
                    if reg_stem.startswith(input_stem) or input_stem.startswith(reg_stem):
                        return path
            raise FileNotFoundError(f"文件 '{name}' 未找到")

        return get_file

    def test_manifest_write_and_read(self, cache, workspace, staging):
        for f in workspace.iterdir():
            cache.register(f.name, str(f))
        cache.write_manifest()

        manifest = json.loads((staging / "_manifest.json").read_text())
        assert len(manifest) >= 3

    def test_sandbox_get_file_exact(self, cache, workspace, staging):
        for f in workspace.iterdir():
            cache.register(f.name, str(f))
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        csv = list(workspace.glob("*.csv"))[0]
        assert get_file(csv.name) == str(csv)

    def test_sandbox_get_file_fuzzy(self, cache, workspace, staging):
        for f in workspace.iterdir():
            cache.register(f.name, str(f))
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        # 文件名有误差
        result = get_file("产品库存表 2024年度.csv")
        assert "产品库存表" in result

    def test_sandbox_get_file_after_analyze(self, cache, workspace, staging):
        xlsx = list(workspace.glob("*.xlsx"))[0]
        cache.register(xlsx.name, str(xlsx))
        parquet = staging / "_cache_test.parquet"
        parquet.write_bytes(b"parquet")
        cache.update_path(xlsx.name, str(parquet))
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        # 用原始文件名拿到 parquet 路径
        assert get_file(xlsx.name) == str(parquet)

    def test_sandbox_get_file_not_found(self, cache, workspace, staging):
        cache.register("a.xlsx", "/ws/a.xlsx")
        cache.write_manifest()

        get_file = self._build_get_file(staging)
        with pytest.raises(FileNotFoundError, match="未找到"):
            get_file("不存在.xlsx")


class TestToolParamResolve:
    """工具参数归一化翻译"""

    def test_path_resolve(self, cache, workspace, conv_id):
        f = list(workspace.glob("*.csv"))[0]
        cache.register(f.name, str(f))

        args = {"path": f.name}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == str(f)

    def test_path_fuzzy_resolve(self, cache, workspace, conv_id):
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))

        args = {"path": "4月金东区部门 销售主题分析.xlsx"}
        _resolve_file_ids(args, conv_id)
        # 归一化后前缀匹配
        assert args["path"] == str(f)

    def test_files_array_resolve(self, cache, workspace, conv_id):
        files = sorted(workspace.iterdir())
        for f in files:
            cache.register(f.name, str(f))

        args = {"files": [files[0].name, files[1].name]}
        _resolve_file_ids(args, conv_id)
        assert args["files"][0] == str(files[0])
        assert args["files"][1] == str(files[1])

    def test_unrelated_args_untouched(self, cache, conv_id):
        args = {"query": "查订单", "platform": "taobao"}
        original = args.copy()
        _resolve_file_ids(args, conv_id)
        assert args == original

    def test_unknown_file_passthrough(self, cache, conv_id):
        args = {"path": "不存在.xlsx"}
        _resolve_file_ids(args, conv_id)
        assert args["path"] == "不存在.xlsx"


class TestEdgeCases:

    def test_same_file_register_twice(self, cache, workspace):
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        cache.register(f.name, str(f))
        assert cache.resolve(f.name) == str(f)

    def test_backward_compat_filename(self, cache, workspace):
        f = sorted(workspace.iterdir())[0]
        cache.register(f.name, str(f))
        assert cache.resolve(f.name) == str(f)

    def test_chinese_in_manifest(self, cache, staging):
        cache.register("销售报表（含退款）.xlsx", "/ws/销售报表（含退款）.xlsx")
        cache.write_manifest()
        raw = (staging / "_manifest.json").read_text(encoding="utf-8")
        assert "销售报表" in raw
