"""cache_naming 守护测试 — 强制 ASCII 防止 LLM 美化中英混排路径

设计文档: services/agent/cache_naming.py
POC: scripts/poc_real_filename_qwen.py (验证 ASCII 100%, 中英混排 80%)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.agent.cache_naming import (
    _ASCII_FILENAME_RE,
    make_cache_parquet_name,
    make_tmp_parquet_name,
)


class TestCacheNamingASCII:
    """生成的 cache 文件名必须纯 ASCII"""

    def test_basic_ascii(self):
        name = make_cache_parquet_name("v3.0", "037237fcf9f7", "sheet0")
        assert name == "_cache_v3.0_037237fcf9f7_sheet0.parquet"
        assert _ASCII_FILENAME_RE.match(name)

    def test_no_suffix(self):
        name = make_cache_parquet_name("v3.0", "abc123")
        assert name == "_cache_v3.0_abc123.parquet"

    def test_csv_suffix(self):
        name = make_cache_parquet_name("v3.0", "abc", "csv")
        assert name == "_cache_v3.0_abc_csv.parquet"

    def test_structured_suffix(self):
        name = make_cache_parquet_name("v3.0", "abc", "structured")
        assert name == "_cache_v3.0_abc_structured.parquet"


class TestCacheNamingRejectsNonASCII:
    """非 ASCII 输入必须 raise ValueError"""

    def test_chinese_suffix_rejected(self):
        with pytest.raises(ValueError, match="ASCII"):
            make_cache_parquet_name("v3.0", "abc", "销售")

    def test_space_suffix_rejected(self):
        with pytest.raises(ValueError, match="ASCII"):
            make_cache_parquet_name("v3.0", "abc", "with space")

    def test_chinese_fingerprint_rejected(self):
        with pytest.raises(ValueError, match="ASCII"):
            make_cache_parquet_name("v3.0", "指纹123", "sheet0")

    def test_special_chars_rejected(self):
        # @、/、! 等特殊字符也拒绝(可能让 LLM 困惑)
        for bad in ["sheet@0", "sheet/0", "sheet!"]:
            with pytest.raises(ValueError, match="ASCII"):
                make_cache_parquet_name("v3.0", "abc", bad)


class TestTmpParquetName:
    def test_tmp_ascii(self):
        name = make_tmp_parquet_name("abc12345")
        assert name == "_tmp_abc12345.parquet"
        assert _ASCII_FILENAME_RE.match(name)

    def test_tmp_chinese_rejected(self):
        with pytest.raises(ValueError, match="ASCII"):
            make_tmp_parquet_name("临时")


class TestCodebaseNoChinesePromptedCacheNaming:
    """守护测试: 全 codebase 不应再有'拼 Excel 中文 stem 进 cache' 反模式。

    检测模式: f'..._cache_..._{path.stem}.parquet' 形式
    防止开发者绕过 cache_naming.py 自己拼中文文件名。
    """

    # 反模式正则: 字符串里同时含 "_cache" 和 ".stem"
    _ANTIPATTERN = re.compile(
        r'_cache[^"\']*\{[^}]*\.stem[^}]*\}',
    )

    def test_no_stem_in_cache_naming(self):
        backend_root = Path(__file__).parent.parent
        services_dir = backend_root / "services"
        violators = []
        for py_file in services_dir.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            # 跳过 cache_naming.py 自己的 docstring 提到的反模式
            if py_file.name == "cache_naming.py":
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if self._ANTIPATTERN.search(content):
                violators.append(str(py_file.relative_to(backend_root)))
        assert not violators, (
            f"发现反模式: f-string 里拼 'cache' + '.stem' (LLM 美化诱因),"
            f"必须改用 make_cache_parquet_name(). 违规文件: {violators}"
        )
