"""session_files.py 单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from services.agent.session_files import (
    _build_pattern_dist,
    _classify_pattern,
    _cosine_similarity,
    _detect_relations,
    _pattern_similarity,
    _sample_overlap,
    read_session_files,
    update_session_files,
)


class TestDetectRelations:
    def test_common_columns(self):
        files = [
            {"path": "sales.parquet", "columns": ["order_id", "amount", "date"]},
            {"path": "products.parquet", "columns": ["order_id", "product_name"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        assert "order_id" in rels[0]["common_columns"]
        assert rels[0]["confidence"] > 0

    def test_no_common(self):
        files = [
            {"path": "a.parquet", "columns": ["x", "y"]},
            {"path": "b.parquet", "columns": ["m", "n"]},
        ]
        assert _detect_relations(files) == []

    def test_same_source_hint(self):
        files = [
            {"path": "orders.parquet", "columns": ["金额"], "source_file": "report.xlsx"},
            {"path": "returns.parquet", "columns": ["金额"], "source_file": "report.xlsx"},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        assert "同一文件" in rels[0]["hint"]

    def test_internal_cols_excluded(self):
        files = [
            {"path": "a.parquet", "columns": ["_sheet", "id", "name"]},
            {"path": "b.parquet", "columns": ["_sheet", "id", "email"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        assert "_sheet" not in rels[0]["common_columns"]
        assert "id" in rels[0]["common_columns"]

    def test_three_files(self):
        files = [
            {"path": "a.parquet", "columns": ["id", "x"]},
            {"path": "b.parquet", "columns": ["id", "y"]},
            {"path": "c.parquet", "columns": ["id", "z"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 3  # a-b, a-c, b-c


class TestUpdateSessionFiles:
    def test_create_new(self, tmp_path):
        staging = tmp_path / "staging" / "conv1"
        staging.mkdir(parents=True)
        parquet = staging / "sales.parquet"
        parquet.touch()

        update_session_files(
            str(staging), str(parquet),
            columns=["order_id", "amount"],
            row_count=100,
            source_file="sales.xlsx",
        )

        sf = staging / "session_files.json"
        assert sf.exists()
        data = json.loads(sf.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["row_count"] == 100
        assert data["files"][0]["source_file"] == "sales.xlsx"

    def test_incremental_update(self, tmp_path):
        staging = tmp_path / "staging" / "conv1"
        staging.mkdir(parents=True)

        p1 = staging / "sales.parquet"
        p1.touch()
        update_session_files(str(staging), str(p1), ["order_id", "amount"], 100)

        p2 = staging / "products.parquet"
        p2.touch()
        update_session_files(str(staging), str(p2), ["order_id", "name"], 50)

        data = json.loads((staging / "session_files.json").read_text())
        assert len(data["files"]) == 2
        assert len(data["potential_relations"]) == 1
        assert "order_id" in data["potential_relations"][0]["common_columns"]

    def test_dedup_same_path(self, tmp_path):
        staging = tmp_path / "staging" / "conv1"
        staging.mkdir(parents=True)
        p = staging / "sales.parquet"
        p.touch()

        update_session_files(str(staging), str(p), ["a", "b"], 100)
        update_session_files(str(staging), str(p), ["a", "b", "c"], 200)

        data = json.loads((staging / "session_files.json").read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["row_count"] == 200  # 最新值

    def test_read_empty(self, tmp_path):
        data = read_session_files(str(tmp_path / "nonexistent"))
        assert data == {"files": [], "potential_relations": []}

    def test_abs_path_not_persisted(self, tmp_path):
        """abs_path 不应该写入 JSON。"""
        staging = tmp_path / "staging" / "conv1"
        staging.mkdir(parents=True)
        p = staging / "test.parquet"
        p.touch()
        update_session_files(str(staging), str(p), ["a"], 10)
        data = json.loads((staging / "session_files.json").read_text())
        assert "abs_path" not in data["files"][0]


class TestSampleOverlap:
    def test_high_overlap(self, tmp_path):
        """相同值 → 高重叠率。"""
        df_a = pd.DataFrame({"order_id": ["TB001", "TB002", "TB003"]})
        df_b = pd.DataFrame({"order_id": ["TB001", "TB002", "TB004"]})
        pa = str(tmp_path / "a.parquet")
        pb = str(tmp_path / "b.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)
        overlap = _sample_overlap(pa, pb, "order_id")
        assert overlap >= 0.5  # TB001,TB002 重叠 2/3

    def test_no_overlap(self, tmp_path):
        """完全不同的值 → 零重叠。"""
        df_a = pd.DataFrame({"amount": [100, 200, 300]})
        df_b = pd.DataFrame({"amount": [5, 10, 15]})
        pa = str(tmp_path / "a.parquet")
        pb = str(tmp_path / "b.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)
        overlap = _sample_overlap(pa, pb, "amount")
        assert overlap == 0.0

    def test_all_null(self, tmp_path):
        """全 null → 零重叠。"""
        df_a = pd.DataFrame({"x": [None, None]})
        df_b = pd.DataFrame({"x": [None, None]})
        pa = str(tmp_path / "a.parquet")
        pb = str(tmp_path / "b.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)
        assert _sample_overlap(pa, pb, "x") == 0.0

    def test_missing_file(self, tmp_path):
        """文件不存在 → 返回 0（不崩溃）。"""
        assert _sample_overlap("/nonexistent/a.parquet", "/nonexistent/b.parquet", "x") == 0.0


class TestDetectRelationsWithSampling:
    def test_same_name_different_data_filtered(self, tmp_path):
        """同名列但数据完全不同 → 被采样过滤掉。"""
        df_a = pd.DataFrame({"金额": [100, 200, 300, 400, 500]})
        df_b = pd.DataFrame({"金额": [5, 10, 15, 20, 25]})
        pa = str(tmp_path / "orders.parquet")
        pb = str(tmp_path / "returns.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)

        files = [
            {"path": "orders.parquet", "abs_path": pa, "columns": ["金额"]},
            {"path": "returns.parquet", "abs_path": pb, "columns": ["金额"]},
        ]
        rels = _detect_relations(files)
        # 值完全不重叠 → 应该没有关联
        assert len(rels) == 0

    def test_same_name_same_data_kept(self, tmp_path):
        """同名列且数据匹配 → 保留关联。"""
        df_a = pd.DataFrame({"order_id": ["TB001", "TB002", "TB003"]})
        df_b = pd.DataFrame({"order_id": ["TB001", "TB002", "TB004"]})
        pa = str(tmp_path / "sales.parquet")
        pb = str(tmp_path / "logistics.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)

        files = [
            {"path": "sales.parquet", "abs_path": pa, "columns": ["order_id", "amount"]},
            {"path": "logistics.parquet", "abs_path": pb, "columns": ["order_id", "status"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        assert "order_id" in rels[0]["common_columns"]

    def test_mixed_match_reports_dropped(self, tmp_path):
        """多个同名列，部分匹配部分不匹配 → hint 里报告不匹配的列。"""
        df_a = pd.DataFrame({"id": ["A1", "A2"], "金额": [100, 200]})
        df_b = pd.DataFrame({"id": ["A1", "A3"], "金额": [5, 10]})
        pa = str(tmp_path / "a.parquet")
        pb = str(tmp_path / "b.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)

        files = [
            {"path": "a.parquet", "abs_path": pa, "columns": ["id", "金额"]},
            {"path": "b.parquet", "abs_path": pb, "columns": ["id", "金额"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        # id 有重叠（A1），金额无重叠 → 只保留 id
        assert "id" in rels[0]["common_columns"]
        assert "不匹配" in rels[0]["hint"]


# ── L3 模式识别 ──


class TestClassifyPattern:
    def test_pure_digits(self):
        assert _classify_pattern("123456789012345678") == "digits_18"
        assert _classify_pattern("1234567890123456") == "digits_16"

    def test_prefix_digits(self):
        assert _classify_pattern("P202401010000001234") == "prefix_P_18"
        assert _classify_pattern("FXG2024010100001234") == "prefix_FXG_16"

    def test_date_dash(self):
        assert _classify_pattern("260305-123456789") == "date_dash_digits"

    def test_date_ymd(self):
        assert _classify_pattern("2024-01-15") == "date_ymd"

    def test_decimal(self):
        assert _classify_pattern("299.50") == "decimal_3"

    def test_chinese(self):
        p = _classify_pattern("淘宝")
        assert p.startswith("chinese_")

    def test_empty(self):
        assert _classify_pattern("") == "empty"


class TestPatternDist:
    def test_multi_platform_orders(self):
        """多平台订单号应该产生多个模式。"""
        vals = [
            "202401010000001234",   # 18位纯数字（淘宝）
            "202401010000001235",
            "2024010100001234",     # 16位纯数字（京东）
            "P202401010000001234",  # P+18位（小红书）
            "260105-123456789",     # 日期-数字（拼多多）
        ]
        dist = _build_pattern_dist(vals)
        assert len(dist) >= 3  # 至少3种模式


class TestCosineSimilarity:
    def test_identical(self):
        d = {"a": 0.5, "b": 0.3, "c": 0.2}
        assert abs(_cosine_similarity(d, d) - 1.0) < 0.001

    def test_orthogonal(self):
        d1 = {"a": 1.0}
        d2 = {"b": 1.0}
        assert _cosine_similarity(d1, d2) == 0.0

    def test_similar(self):
        d1 = {"digits_18": 0.6, "digits_16": 0.2, "prefix_P_18": 0.2}
        d2 = {"digits_18": 0.55, "digits_16": 0.25, "prefix_P_18": 0.2}
        assert _cosine_similarity(d1, d2) > 0.9


class TestPatternSimilarity:
    def test_same_format_different_values(self, tmp_path):
        """同格式不同值（1月 vs 2月多平台订单）→ 高相似度。"""
        df_a = pd.DataFrame({"order_id": [
            "202401010000001234", "202401010000001235",
            "2024010100001234", "P202401010000001234",
            "260105-123456789",
        ]})
        df_b = pd.DataFrame({"order_id": [
            "202402010000005678", "202402010000005679",
            "2024020100005678", "P202402010000005678",
            "260205-987654321",
        ]})
        pa = str(tmp_path / "jan.parquet")
        pb = str(tmp_path / "feb.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)
        sim = _pattern_similarity(pa, pb, "order_id")
        assert sim >= 0.7  # 模式分布相似

    def test_different_format(self, tmp_path):
        """完全不同格式 → 低相似度。"""
        df_a = pd.DataFrame({"col": ["202401010000001234", "202401010000001235"]})
        df_b = pd.DataFrame({"col": ["张三", "李四"]})
        pa = str(tmp_path / "a.parquet")
        pb = str(tmp_path / "b.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)
        sim = _pattern_similarity(pa, pb, "col")
        assert sim < 0.3


class TestDetectRelationsL3:
    def test_union_detected(self, tmp_path):
        """1月 vs 2月全平台订单 → 值不重叠但模式相似 → UNION 关联。"""
        df_a = pd.DataFrame({"order_id": [
            "202401010000001234", "2024010100001234",
            "P202401010000001234", "260105-123456789",
        ], "amount": [100, 200, 300, 400]})
        df_b = pd.DataFrame({"order_id": [
            "202402010000005678", "2024020100005678",
            "P202402010000005678", "260205-987654321",
        ], "amount": [150, 250, 350, 450]})
        pa = str(tmp_path / "jan.parquet")
        pb = str(tmp_path / "feb.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)

        files = [
            {"path": "jan.parquet", "abs_path": pa, "columns": ["order_id", "amount"]},
            {"path": "feb.parquet", "abs_path": pb, "columns": ["order_id", "amount"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        assert rels[0]["relation_type"] == "union"
        assert "合并" in rels[0]["hint"]

    def test_join_still_works(self, tmp_path):
        """订单表 vs 物流表 → 值重叠 → JOIN 关联（不受 L3 影响）。"""
        df_a = pd.DataFrame({"order_id": ["TB001", "TB002", "TB003"]})
        df_b = pd.DataFrame({"order_id": ["TB001", "TB002", "TB004"]})
        pa = str(tmp_path / "orders.parquet")
        pb = str(tmp_path / "logistics.parquet")
        df_a.to_parquet(pa)
        df_b.to_parquet(pb)

        files = [
            {"path": "orders.parquet", "abs_path": pa, "columns": ["order_id"]},
            {"path": "logistics.parquet", "abs_path": pb, "columns": ["order_id"]},
        ]
        rels = _detect_relations(files)
        assert len(rels) == 1
        assert rels[0]["relation_type"] == "join"
        assert "JOIN" in rels[0]["hint"]
