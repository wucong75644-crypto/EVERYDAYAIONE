"""Cache parquet 文件名生成 — 强制 ASCII 单点入口

设计原则:
  LLM 在长上下文里复制中英混排路径时会"美化"加空格(中文排版 deep prior),
  导致 pd.read_parquet 路径不存在 → IOException → 用户体验差。

  实测数据(scripts/poc_real_filename_qwen.py):
  - 含中文 stem 的 cache 路径: qwen-plus 20% 概率美化加空格
  - 纯 ASCII hash 的 cache 路径: 20/20 + 多表多轮 15/15 = 100% 字面 copy

  行业对标:
  - OpenAI Code Interpreter: 沙盒挂载 /mnt/data/file-{id} 无扩展名无中文
  - Anthropic Files API: file_id 引用,沙盒内 cwd 隔离
  - 我们: cache 文件名走此函数 → 强制 ASCII,LLM 看到必然字面 copy

防破窗:
  - 任何 cache 文件名生成必须走 make_cache_parquet_name
  - 函数内运行时校验 ASCII,违反直接 raise(开发期就报错)
  - 守护测试 test_no_chinese_in_cache_naming_codebase 扫描全 codebase
"""
from __future__ import annotations

import re

# 文件名安全字符集: 字母 / 数字 / 下划线 / 连字符 / 点 (parquet 扩展名需要)
_ASCII_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]+$")
_ASCII_SUFFIX_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def make_cache_parquet_name(
    version: str,
    fingerprint: str,
    suffix: str = "",
) -> str:
    """生成 LLM 安全的 cache parquet 文件名(强制纯 ASCII)。

    Args:
        version: cache schema 版本,如 "v3.0"
        fingerprint: 内容指纹 hash (必须 ASCII)
        suffix: 可选后缀,如 "sheet0" / "csv" / "structured"
                必须 ASCII,违反直接 ValueError

    Returns:
        形如 "_cache_v3.0_037237fcf9f7_sheet0.parquet" 的纯 ASCII 文件名

    Raises:
        ValueError: suffix 含非 ASCII 字符(中文/空格/特殊符号)

    Example:
        >>> make_cache_parquet_name("v3.0", "037237fcf9f7", "sheet0")
        '_cache_v3.0_037237fcf9f7_sheet0.parquet'
        >>> make_cache_parquet_name("v3.0", "abc", "销售")
        Traceback (most recent call last):
        ValueError: cache suffix 必须 ASCII: '销售'
    """
    if not _ASCII_SUFFIX_RE.match(version.replace(".", "")):
        raise ValueError(f"cache version 必须 ASCII: {version!r}")
    if not _ASCII_SUFFIX_RE.match(fingerprint):
        raise ValueError(f"cache fingerprint 必须 ASCII: {fingerprint!r}")
    if suffix and not _ASCII_SUFFIX_RE.match(suffix):
        raise ValueError(f"cache suffix 必须 ASCII: {suffix!r}")

    parts = [f"_cache_{version}", fingerprint]
    if suffix:
        parts.append(suffix)
    name = "_".join(parts) + ".parquet"

    # 最终防御性校验(理论上不可能失败,因为上面已检查各部分)
    assert _ASCII_FILENAME_RE.match(name), f"cache name 必须 ASCII: {name!r}"
    return name


def make_tmp_parquet_name(uuid_hex: str) -> str:
    """生成临时 parquet 文件名(原子写入用)。

    与 make_cache_parquet_name 的区别:tmp 文件是写入中转,
    用 uuid hex 保证唯一,不需要 fingerprint。
    """
    if not _ASCII_SUFFIX_RE.match(uuid_hex):
        raise ValueError(f"uuid_hex 必须 ASCII: {uuid_hex!r}")
    return f"_tmp_{uuid_hex}.parquet"
