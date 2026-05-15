"""会话级多文件清单管理（session_files.json）。

维护当前会话中所有已处理的文件清单 + 文件间潜在关联关系。
增量更新，列名匹配 + 数据采样匹配双重验证。

设计文档：docs/document/TECH_文件处理系统.md §八
"""
from __future__ import annotations

import json
import re
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

from loguru import logger

_SAMPLE_SIZE = 100        # 采样比对取多少个值
_OVERLAP_LOW = 0.05       # 值重叠率 < 此值 → 不认为是 JOIN 关联
_PATTERN_SIM_HIGH = 0.7   # 模式分布相似度 ≥ 此值 → 认为是 UNION 合并

# 模式分类预编译正则
_RE_DIGITS = re.compile(r'\d+$')
_RE_PREFIX_DIGITS = re.compile(r'([A-Za-z]+)(\d+)$')
_RE_DATE_DASH = re.compile(r'\d{6}-\d+$')
_RE_DATE_YMD = re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}$')
_RE_DECIMAL = re.compile(r'\d+\.\d+$')
_RE_CHINESE = re.compile(r'[\u4e00-\u9fff]')


def update_session_files(
    staging_dir: str,
    parquet_path: str,
    columns: list[str],
    row_count: int,
    source_file: str = "",
    source_sheet: str = "",
    source_region: str = "",
) -> None:
    """增量更新 session_files.json（新文件加入时调用）。"""
    sf_path = Path(staging_dir) / "session_files.json"
    data = _read_session_files(sf_path)

    rel_path = str(Path(parquet_path).relative_to(Path(staging_dir).parent.parent))
    entry: dict[str, Any] = {
        "path": rel_path,
        "abs_path": str(parquet_path),  # 采样匹配用，不写入 JSON
        "columns": columns,
        "row_count": row_count,
    }
    if source_file:
        entry["source_file"] = source_file
    if source_sheet:
        entry["source_sheet"] = source_sheet
    if source_region:
        entry["source_region"] = source_region

    # 去重
    files = [f for f in data.get("files", []) if f.get("path") != rel_path]
    files.append(entry)
    data["files"] = files

    # 列名匹配 + 数据采样匹配
    data["potential_relations"] = _detect_relations(files)

    # abs_path 不持久化
    for f in data["files"]:
        f.pop("abs_path", None)

    _write_session_files(sf_path, data)


def _detect_relations(files: list[dict]) -> list[dict]:
    """三层关联检测：L1 列名匹配 → L2 值重叠(JOIN) → L3 模式相似(UNION)。"""
    relations: list[dict] = []
    for fa, fb in combinations(files, 2):
        cols_a = {c for c in fa.get("columns", []) if not c.startswith("_")}
        cols_b = {c for c in fb.get("columns", []) if not c.startswith("_")}
        common = cols_a & cols_b
        if not common:
            continue

        name_a = Path(fa["path"]).stem
        name_b = Path(fb["path"]).stem
        path_a = fa.get("abs_path", "")
        path_b = fb.get("abs_path", "")

        if not path_a or not path_b:
            # 无绝对路径（从 JSON 读回），退回纯列名匹配
            min_cols = min(len(cols_a), len(cols_b))
            hint = f"共同列: {', '.join(sorted(common))}"
            if fa.get("source_file") and fa["source_file"] == fb.get("source_file"):
                hint += f"；来自同一文件 {Path(fa['source_file']).name}"
            relations.append({
                "files": [name_a, name_b],
                "common_columns": sorted(common),
                "relation_type": "column_match",
                "confidence": round(len(common) / max(min_cols, 1), 2),
                "hint": hint,
            })
            continue

        # L2: 值重叠检测 → JOIN 关联
        join_cols: list[str] = []
        # L3: 模式相似检测 → UNION 合并
        union_cols: list[str] = []
        dropped_cols: list[str] = []

        for col in sorted(common):
            overlap = _sample_overlap(path_a, path_b, col)
            if overlap >= _OVERLAP_LOW:
                join_cols.append(col)
            else:
                # 值不重叠 → 检查模式是否相似（可能是同类数据不同时间段）
                similarity = _pattern_similarity(path_a, path_b, col)
                if similarity >= _PATTERN_SIM_HIGH:
                    union_cols.append(col)
                else:
                    dropped_cols.append(col)

        if not join_cols and not union_cols:
            continue

        # 构造关联结果
        hint_parts: list[str] = []
        if join_cols:
            hint_parts.append(f"可 JOIN 列: {', '.join(join_cols)}")
        if union_cols:
            hint_parts.append(f"同类数据可合并: {', '.join(union_cols)}")
        if fa.get("source_file") and fa["source_file"] == fb.get("source_file"):
            hint_parts.append(f"来自同一文件 {Path(fa['source_file']).name}")
        if dropped_cols:
            hint_parts.append(f"同名但不匹配: {', '.join(dropped_cols)}")

        # 关联类型和 confidence
        if join_cols:
            rel_type = "join"
            all_verified = join_cols + union_cols
        else:
            rel_type = "union"
            all_verified = union_cols

        min_cols = min(len(cols_a), len(cols_b))
        confidence = round(len(all_verified) / max(min_cols, 1), 2)

        relations.append({
            "files": [name_a, name_b],
            "common_columns": join_cols + union_cols,
            "relation_type": rel_type,
            "confidence": confidence,
            "hint": "；".join(hint_parts),
        })
    return relations


def _sample_overlap(path_a: str, path_b: str, col: str) -> float:
    """采样两个 Parquet 文件同名列的值重叠率。"""
    try:
        import duckdb
        vals_a = set(
            duckdb.sql(
                f"SELECT DISTINCT CAST(\"{col}\" AS VARCHAR) FROM '{path_a}' "
                f"WHERE \"{col}\" IS NOT NULL LIMIT {_SAMPLE_SIZE}"
            ).fetchall()
        )
        vals_b = set(
            duckdb.sql(
                f"SELECT DISTINCT CAST(\"{col}\" AS VARCHAR) FROM '{path_b}' "
                f"WHERE \"{col}\" IS NOT NULL LIMIT {_SAMPLE_SIZE}"
            ).fetchall()
        )
        if not vals_a or not vals_b:
            return 0.0
        overlap = len(vals_a & vals_b)
        return overlap / min(len(vals_a), len(vals_b))
    except Exception:
        return 0.0  # 采样失败退回列名匹配（不排除）


def _pattern_similarity(path_a: str, path_b: str, col: str) -> float:
    """比较两个文件同名列的值格式模式分布相似度。

    将每个值归类为格式模式（如"18位纯数字"、"P+18位"、"日期-数字串"），
    比较两个文件的模式分布，用余弦相似度衡量。
    """
    try:
        import duckdb
        vals_a = [
            r[0] for r in duckdb.sql(
                f"SELECT CAST(\"{col}\" AS VARCHAR) FROM '{path_a}' "
                f"WHERE \"{col}\" IS NOT NULL LIMIT {_SAMPLE_SIZE}"
            ).fetchall() if r[0]
        ]
        vals_b = [
            r[0] for r in duckdb.sql(
                f"SELECT CAST(\"{col}\" AS VARCHAR) FROM '{path_b}' "
                f"WHERE \"{col}\" IS NOT NULL LIMIT {_SAMPLE_SIZE}"
            ).fetchall() if r[0]
        ]
        if not vals_a or not vals_b:
            return 0.0
        dist_a = _build_pattern_dist(vals_a)
        dist_b = _build_pattern_dist(vals_b)
        return _cosine_similarity(dist_a, dist_b)
    except Exception:
        return 0.0


def _classify_pattern(val: str) -> str:
    """将一个值归类为格式模式。"""
    s = val.strip()
    if not s:
        return "empty"
    if _RE_DIGITS.fullmatch(s):
        return f"digits_{len(s)}"
    m = _RE_PREFIX_DIGITS.fullmatch(s)
    if m:
        return f"prefix_{m.group(1).upper()}_{len(m.group(2))}"
    if _RE_DATE_DASH.fullmatch(s):
        return "date_dash_digits"
    if _RE_DATE_YMD.fullmatch(s):
        return "date_ymd"
    if _RE_DECIMAL.fullmatch(s):
        return f"decimal_{len(s.split('.')[0])}"
    if _RE_CHINESE.search(s):
        return f"chinese_{min(len(s), 20)}"
    return f"other_{min(len(s), 20)}"


def _build_pattern_dist(vals: list[str]) -> dict[str, float]:
    """构建模式频率分布。"""
    patterns = Counter(_classify_pattern(v) for v in vals)
    total = sum(patterns.values())
    return {k: v / total for k, v in patterns.items()}


def _cosine_similarity(dist_a: dict[str, float], dist_b: dict[str, float]) -> float:
    """两个分布的余弦相似度。"""
    all_keys = set(dist_a) | set(dist_b)
    dot = sum(dist_a.get(k, 0) * dist_b.get(k, 0) for k in all_keys)
    norm_a = sum(v ** 2 for v in dist_a.values()) ** 0.5
    norm_b = sum(v ** 2 for v in dist_b.values()) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def read_session_files(staging_dir: str) -> dict[str, Any]:
    """读取 session_files.json，不存在时返回空结构。"""
    sf_path = Path(staging_dir) / "session_files.json"
    return _read_session_files(sf_path)


def _read_session_files(sf_path: Path) -> dict[str, Any]:
    if sf_path.exists():
        try:
            return json.loads(sf_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"files": [], "potential_relations": []}


def _write_session_files(sf_path: Path, data: dict) -> None:
    try:
        sf_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Failed to write session_files.json: {e}")
