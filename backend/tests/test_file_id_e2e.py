"""
文件三字段注册表 + 归一化匹配 E2E 测试

注册表结构：{name, workspace, parquet}
get_file(name, usage) 按用途返回对应路径 + 自检拦截
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from services.agent.file_path_cache import FilePathCache, FileEntry, get_file_cache, normalize_filename
from services.handlers.chat_tool_mixin import _resolve_file_ids


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "4月销售分析.xlsx").write_bytes(b"x" * 100)
    (ws / "产品库存表.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (ws / "利润表（1-4月）.xlsx").write_bytes(b"x" * 50)
    return ws


@pytest.fixture
def staging(tmp_path):
    s = tmp_path / "staging"
    s.mkdir()
    return s


@pytest.fixture
def conv_id():
    import uuid
    return f"test-3f-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cache(conv_id, staging):
    c = get_file_cache(conv_id)
    c.set_staging_dir(str(staging))
    return c


class TestNormalize:
    def test_spaces(self):
        assert normalize_filename("4月 销售分析.xlsx") == "4月销售分析.xlsx"

    def test_dashes(self):
        assert normalize_filename("4月--销售-分析.xlsx") == "4月销售分析.xlsx"

    def test_fullwidth(self):
        assert normalize_filename("利润表（1-4月）.xlsx") == "利润表14月.xlsx"

    def test_preserve_ext(self):
        assert normalize_filename("test.CSV") == "test.csv"


class TestThreeFieldRegister:
    """三字段注册/更新"""

    def test_register_workspace_only(self, cache, workspace):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        entry = cache._resolve_entry(f.name)
        assert entry.workspace == str(f)
        assert entry.parquet == ""

    def test_register_both(self, cache):
        cache.register("trade.parquet", workspace="/staging/trade.parquet", parquet="/staging/trade.parquet")
        entry = cache._resolve_entry("trade.parquet")
        assert entry.workspace == "/staging/trade.parquet"
        assert entry.parquet == "/staging/trade.parquet"

    def test_set_parquet_after_register(self, cache, workspace, staging):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        parquet = staging / "_cache_test.parquet"
        parquet.write_bytes(b"parquet")
        cache.set_parquet(f.name, str(parquet))

        entry = cache._resolve_entry(f.name)
        assert entry.workspace == str(f)
        assert entry.parquet == str(parquet)

    def test_register_no_overwrite_parquet(self, cache, workspace, staging):
        """重复 register 不清空已有的 parquet"""
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        parquet = staging / "_cache_test.parquet"
        parquet.write_bytes(b"parquet")
        cache.set_parquet(f.name, str(parquet))

        # 再次 register 同一文件（模拟 chat_context_mixin 重复注册）
        cache.register(f.name, workspace=str(f))

        # parquet 不被清空
        entry = cache._resolve_entry(f.name)
        assert entry.parquet == str(parquet)


class TestGetFile:
    """get_file 按 usage 返回 + 自检拦截"""

    def test_code_returns_parquet(self, cache, workspace, staging):
        f = workspace / "4月销售分析.xlsx"
        parquet = staging / "_cache.parquet"
        parquet.write_bytes(b"parquet")
        cache.register(f.name, workspace=str(f))
        cache.set_parquet(f.name, str(parquet))

        assert cache.resolve_path(f.name, "code") == str(parquet)

    def test_analyze_returns_workspace(self, cache, workspace):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        assert cache.resolve_path(f.name, "analyze") == str(f)

    def test_delete_returns_workspace(self, cache, workspace):
        f = workspace / "产品库存表.csv"
        cache.register(f.name, workspace=str(f))
        assert cache.resolve_path(f.name, "delete") == str(f)

    def test_code_no_parquet_raises(self, cache, workspace):
        """没 analyze 过，code 用途拦截"""
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        with pytest.raises(FileNotFoundError, match="尚未分析"):
            cache.resolve_path(f.name, "code")

    def test_unregistered_raises(self, cache):
        with pytest.raises(FileNotFoundError, match="未注册"):
            cache.resolve_path("不存在.xlsx", "code")

    def test_parquet_expired_raises(self, cache, workspace, staging):
        """parquet 文件被删了，拦截"""
        f = workspace / "4月销售分析.xlsx"
        parquet = staging / "_cache.parquet"
        parquet.write_bytes(b"parquet")
        cache.register(f.name, workspace=str(f))
        cache.set_parquet(f.name, str(parquet))
        # 模拟 staging 清理
        parquet.unlink()
        with pytest.raises(FileNotFoundError, match="已失效"):
            cache.resolve_path(f.name, "code")

    def test_erp_output_both_usages(self, cache, staging):
        """ERP 产出两个地址相同，任何 usage 都能用"""
        p = staging / "trade_123.parquet"
        p.write_bytes(b"parquet")
        cache.register("trade_123.parquet", workspace=str(p), parquet=str(p))
        assert cache.resolve_path("trade_123.parquet", "code") == str(p)
        assert cache.resolve_path("trade_123.parquet", "analyze") == str(p)


class TestResolve:
    """resolve 静默模式（返回 None 不拦截）"""

    def test_resolve_code_returns_parquet(self, cache, workspace, staging):
        f = workspace / "4月销售分析.xlsx"
        parquet = staging / "_cache.parquet"
        parquet.write_bytes(b"parquet")
        cache.register(f.name, workspace=str(f))
        cache.set_parquet(f.name, str(parquet))
        assert cache.resolve(f.name, "code") == str(parquet)

    def test_resolve_analyze_returns_workspace(self, cache, workspace):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        assert cache.resolve(f.name, "analyze") == str(f)

    def test_resolve_no_parquet_returns_none(self, cache, workspace):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        assert cache.resolve(f.name, "code") is None

    def test_fuzzy_match(self, cache, workspace):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        # LLM 加了空格
        assert cache.resolve("4月 销售分析.xlsx", "analyze") == str(f)

    def test_stem_match(self, cache, workspace):
        f = workspace / "产品库存表.csv"
        cache.register(f.name, workspace=str(f))
        assert cache.resolve("产品库存表", "analyze") == str(f)


class TestManifest:
    """manifest 只写 parquet"""

    def test_only_parquet_in_manifest(self, cache, workspace, staging):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        # 没 analyze，没 parquet → manifest 为空
        cache.write_manifest()
        assert not (staging / "_manifest.json").exists()

        # analyze 后有 parquet → 写入 manifest
        parquet = staging / "_cache.parquet"
        parquet.write_bytes(b"parquet")
        cache.set_parquet(f.name, str(parquet))
        cache.write_manifest()

        manifest = json.loads((staging / "_manifest.json").read_text())
        # manifest 里存的是 parquet 文件名（不是完整路径）
        for k, v in manifest.items():
            assert v == parquet.name

    def test_manifest_no_workspace(self, cache, workspace, staging):
        """manifest 里不会出现 workspace 路径"""
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))
        parquet = staging / "_cache.parquet"
        parquet.write_bytes(b"parquet")
        cache.set_parquet(f.name, str(parquet))
        cache.write_manifest()

        manifest = json.loads((staging / "_manifest.json").read_text())
        for v in manifest.values():
            assert "workspace" not in v or "staging" in v


class TestToolResolve:
    """_resolve_file_ids 按工具名选 usage"""

    def test_file_analyze_gets_workspace(self, cache, workspace, conv_id):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))

        args = {"path": f.name}
        _resolve_file_ids(args, conv_id, "file_analyze")
        assert args["path"] == str(f)

    def test_file_delete_gets_workspace(self, cache, workspace, conv_id):
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))

        args = {"files": [f.name]}
        _resolve_file_ids(args, conv_id, "file_delete")
        assert args["files"][0] == str(f)

    def test_code_execute_not_translated(self, cache, workspace, conv_id):
        """code_execute 不走翻译层（沙盒内用 get_file）"""
        f = workspace / "4月销售分析.xlsx"
        cache.register(f.name, workspace=str(f))

        args = {"code": "get_file('xxx')", "description": "test"}
        _resolve_file_ids(args, conv_id, "code_execute")
        # 不翻译
        assert args["code"] == "get_file('xxx')"

    def test_erp_agent_not_translated(self, cache, conv_id):
        args = {"query": "查订单"}
        _resolve_file_ids(args, conv_id, "erp_agent")
        assert args["query"] == "查订单"


class TestFullFlow:
    """完整流程：上传 → analyze → code_execute"""

    def test_upload_analyze_code(self, cache, workspace, staging, conv_id):
        xlsx = workspace / "4月销售分析.xlsx"

        # 1. 上传注册（只有 workspace）
        cache.register(xlsx.name, workspace=str(xlsx))
        assert cache.resolve(xlsx.name, "analyze") == str(xlsx)
        assert cache.resolve(xlsx.name, "code") is None

        # 2. file_analyze → set_parquet
        parquet = staging / "_cache.parquet"
        parquet.write_bytes(b"parquet")
        cache.set_parquet(xlsx.name, str(parquet))
        assert cache.resolve(xlsx.name, "code") == str(parquet)
        assert cache.resolve(xlsx.name, "analyze") == str(xlsx)

        # 3. write_manifest → manifest 存 parquet 文件名（沙盒拼 STAGING_DIR）
        cache.write_manifest()
        manifest = json.loads((staging / "_manifest.json").read_text())
        for v in manifest.values():
            assert v == parquet.name

        # 4. 重复注册不覆盖 parquet
        cache.register(xlsx.name, workspace=str(xlsx))
        assert cache.resolve(xlsx.name, "code") == str(parquet)
